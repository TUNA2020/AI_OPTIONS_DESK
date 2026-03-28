from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ai.deep_thinking import build_deep_context
from ai.llm_reasoner import LLMReasoner
from ai.quant_validator import QuantValidator
from ai.strategy_generator import build_trade_from_decision
from ai.strategy_generator import rank_strategy_candidates
from analytics.pnl_monitor import PnLMonitor
from data.iv_surface import estimate_iv_skew, build_iv_surface
from data.kite_market_data import KiteMarketData
from data.market_features import (
    compute_rsi,
    compute_macd,
    compute_bollinger_bands,
    compute_adr,
    compute_support_resistance,
    compute_volume_percentile,
    compute_pcr,
    to_dataframe,
)
from data.oi_analysis import analyze_oi_structure
from data.option_chain_fetcher import OptionChainFetcher
from database.sqlite_manager import SQLiteManager
from execution.order_manager import OrderManager
from execution.websocket_stream import WebSocketStream
from models.volatility_regime_model import VolatilityRegimeModel
from risk.risk_manager import RiskManager


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SystemController:
    settings: dict[str, Any]
    db: SQLiteManager
    market_data: KiteMarketData
    chain_fetcher: OptionChainFetcher
    llm: LLMReasoner
    risk_manager: RiskManager
    order_manager: OrderManager
    pnl_monitor: PnLMonitor
    websocket_stream: WebSocketStream
    strategy_optimizer: StrategyOptimizer
    quant_validator: QuantValidator
    notifier: Any | None = None
    regime_model: VolatilityRegimeModel = field(default_factory=VolatilityRegimeModel)
    latest_context: dict[str, Any] = field(default_factory=dict)
    latest_decision: dict[str, Any] = field(default_factory=dict)
    latest_risk: dict[str, Any] = field(default_factory=dict)
    latest_quant: dict[str, Any] = field(default_factory=dict)
    latest_order_results: list[dict[str, Any]] = field(default_factory=list)
    _last_tick_write_ts: float = field(init=False, default=0.0, repr=False)
    _tick_lock: threading.Lock = field(
        init=False, default_factory=threading.Lock, repr=False
    )

    def __post_init__(self) -> None:
        self.regime_model.ensure_ready()

    def _notify(
        self, title: str, message: str, payload: dict[str, Any] | None = None
    ) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.send(title, message, payload=payload)
        except Exception:
            LOGGER.exception("Notifier send failed for %s", title)

    def _kill_switch_enabled(self) -> bool:
        return bool(self.db.get_runtime_control("kill_switch", False))

    def _auto_trading_paused(self) -> bool:
        return bool(self.db.get_runtime_control("auto_trading_paused", False))

    def _quant_gate_enabled(self) -> bool:
        return bool(self.db.get_runtime_control("quant_gate_enabled", False))

    def _risk_engine_enabled(self) -> bool:
        return bool(self.db.get_runtime_control("risk_engine_enabled", False))

    def _current_trading_mode(self) -> str:
        return (
            str(
                self.db.get_runtime_control(
                    "trading_mode", self.settings["app"].get("mode", "paper")
                )
            )
            .lower()
            .strip()
            or str(self.settings["app"].get("mode", "paper")).lower()
        )

    def _has_trade_entry_today(self) -> tuple[bool, int, int]:
        today_local = date.today().isoformat()
        with self.db.connection() as conn:
            traded_row = conn.execute(
                "SELECT COUNT(*) AS count FROM trades WHERE substr(timestamp, 1, 10) = ?",
                (today_local,),
            ).fetchone()
        today_trade_count = int(traded_row["count"] if traded_row else 0)
        open_trade_count = len(self.db.fetch_open_trades())
        return (
            today_trade_count > 0 or open_trade_count > 0,
            today_trade_count,
            open_trade_count,
        )

    def build_market_context(self) -> dict[str, Any]:
        snapshot = self.market_data.fetch_market_snapshot()
        candle_context = self.market_data.fetch_5m_candle_context(days_back=7)
        symbol = str(self.settings["app"]["symbol"]).upper()
        underlying = "BANKNIFTY" if "BANK" in symbol else "NIFTY"
        option_chain = self.chain_fetcher.fetch_option_chain(
            snapshot["nifty_price"],
            strikes_each_side=20,
            underlying=underlying,
        )
        if not option_chain:
            raise RuntimeError("Option chain is empty from Kite data.")

        # Keep exactly ~40 strikes around ATM for LLM context.
        center_idx = len(option_chain) // 2
        context_chain = option_chain[
            max(0, center_idx - 20) : min(len(option_chain), center_idx + 20)
        ]
        oi_view = analyze_oi_structure(option_chain)
        iv_skew = estimate_iv_skew(option_chain)
        regime = self.regime_model.predict(
            {
                "atr": float(snapshot["atr"]),
                "vix": float(snapshot["vix"]),
                "iv_skew": float(iv_skew),
                "volume": float(snapshot["volume"]),
                "trend_strength": float(snapshot["trend_strength"]),
            }
        )

        # ===== NEW: Enhanced data for AI decision =====
        # 1. Technical Indicators from candle data
        df_candles = to_dataframe(candle_context.get("history_5m_7d", []))
        technicals = {}
        if not df_candles.empty and len(df_candles) > 20:
            technicals = {
                "rsi_14": round(float(compute_rsi(df_candles)), 2),
                "macd": compute_macd(df_candles),
                "bollinger": compute_bollinger_bands(df_candles),
                "adr_10d": round(float(compute_adr(df_candles)), 2),
                "support_resistance": compute_support_resistance(df_candles),
                "volume_percentile": round(
                    float(compute_volume_percentile(df_candles)), 3
                ),
            }

        # 2. Liquidity Metrics
        liquidity: dict[str, Any] = {"pcr": round(compute_pcr(option_chain), 3)}
        atm_row = None
        if option_chain:
            spot = float(snapshot["nifty_price"])
            # Find ATM row
            atm_row = min(option_chain, key=lambda r: abs(float(r["strike"]) - spot))
            liquidity["atm_oi"] = int(atm_row.get("ce_oi", 0) + atm_row.get("pe_oi", 0))
            liquidity["total_oi"] = sum(
                int(r.get("ce_oi", 0)) + int(r.get("pe_oi", 0)) for r in option_chain
            )
            liquidity["volume_rank"] = (
                "top_5%" if snapshot["volume"] > 1e8 else "average"
            )

        # 3. IV Surface Details
        iv_details = {}
        if option_chain:
            iv_surface_df = build_iv_surface(option_chain)
            atm_iv = 0.0
            if atm_row and not iv_surface_df.empty:
                atm_strike_val = int(atm_row.get("strike", 0))
                atm_match = iv_surface_df[iv_surface_df["strike"] == atm_strike_val]
                if not atm_match.empty:
                    atm_iv = float(atm_match["mean_iv"].iloc[0])
            iv_details = {
                "atm_iv": round(atm_iv, 2),
                "skew_slope": round(float(iv_skew), 3),
                "term_structure": "contango"
                if len(context_chain) > 0
                and context_chain[0].get("days_to_expiry", 0) > 7
                else "flat",
                "iv_rank_30d": 0.5,  # Placeholder for historical IV rank
                "iv_vix_premium": round(atm_iv / max(float(snapshot["vix"]), 0.01), 3)
                if atm_iv > 0
                else 1.0,
            }
        # ============================================

        context = {
            "nifty_price": snapshot["nifty_price"],
            "vix": snapshot["vix"],
            "trend": snapshot["trend"],
            "atr": snapshot["atr"],
            "expiry_days": int(context_chain[0]["days_to_expiry"])
            if context_chain
            else 0,
            "capital_available": float(self.settings["risk"]["max_capital_per_trade"]),
            "option_chain": context_chain,
            "volume": snapshot["volume"],
            "vwap": snapshot["vwap"],
            "trend_strength": snapshot["trend_strength"],
            "oi_analysis": oi_view,
            "iv_skew": iv_skew,
            "model_regime": regime["regime"],
            "model_regime_confidence": regime["confidence"],
            "lot_size": int(self.settings["app"]["lot_size"]),
            "market_open": bool(snapshot.get("market_open", False)),
            "market_status": str(snapshot.get("market_status", "UNKNOWN")),
            "market_status_reason": str(snapshot.get("market_status_reason", "")),
            "market_local_time": str(snapshot.get("market_local_time", "")),
            "market_open_time": str(snapshot.get("market_open_time", "09:15")),
            "market_close_time": str(snapshot.get("market_close_time", "15:30")),
            "candles_5m_today": candle_context.get("today_candles_5m", []),
            "candle_context": candle_context,
            "history_5m_7d": candle_context.get("history_5m_7d", []),
            "today_5m_summary": candle_context.get("today", {}),
            # Enhanced AI data
            "technicals": technicals,
            "liquidity": liquidity,
            "iv_details": iv_details,
        }
        self.db.insert_market_context(context)
        self.db.insert_option_chain(option_chain)
        self.latest_context = context
        return context

    def run_strategy_decision_cycle(self) -> None:
        self.db.insert_audit_event(
            level="INFO",
            event_type="strategy_cycle_started",
            message="Strategy decision cycle started",
        )
        if self._kill_switch_enabled():
            self.db.insert_audit_event(
                level="WARNING",
                event_type="strategy_cycle_blocked_kill_switch",
                message="Kill switch is ON. New orders are blocked.",
            )
            LOGGER.warning("Kill switch is ON. Strategy cycle blocked.")
            return
        if self._auto_trading_paused():
            self.db.insert_audit_event(
                level="INFO",
                event_type="strategy_cycle_paused",
                message="Auto trading paused. Strategy cycle skipped.",
            )
            LOGGER.info("Auto trading paused. Strategy cycle skipped.")
            return
        try:
            context = self.build_market_context()
        except Exception:
            self.db.insert_audit_event(
                level="ERROR",
                event_type="market_context_error",
                message="Market context build failed. Strategy cycle skipped.",
            )
            self._notify(
                "MARKET_CONTEXT_ERROR",
                "Market context build failed. Strategy cycle skipped.",
            )
            LOGGER.exception("Market context build failed. Skipping strategy cycle.")
            return

        if not bool(context.get("market_open", False)):
            self.db.insert_audit_event(
                level="INFO",
                event_type="strategy_cycle_skipped_market_closed",
                message="Market closed. Strategy cycle skipped.",
                payload={
                    "market_status": context.get("market_status"),
                    "reason": context.get("market_status_reason"),
                },
            )
            LOGGER.info(
                "Market is %s (%s). Strategy cycle skipped.",
                context.get("market_status", "UNKNOWN"),
                context.get("market_status_reason", ""),
            )
            return

        has_entry_today, today_trade_count, open_trade_count = (
            self._has_trade_entry_today()
        )
        if has_entry_today:
            self.db.insert_audit_event(
                level="INFO",
                event_type="strategy_cycle_skipped_daily_limit",
                message="Daily strategy entry limit reached. New entry skipped.",
                payload={
                    "today_trade_count": today_trade_count,
                    "open_trade_count": open_trade_count,
                },
            )
            LOGGER.info(
                "Daily strategy entry limit reached. Skipping new entry. trades_today=%d open=%d",
                today_trade_count,
                open_trade_count,
            )
            return

        use_deep_thinking = bool(
            self.settings["dashboard"].get("deep_thinking_mode", True)
        )
        if use_deep_thinking:
            deep_payload = build_deep_context(
                current_context=context,
                candle_context=context.get("candle_context", {}),
                recent_decisions=self._recent_ai_decisions(),
                recent_performance=self._recent_strategy_performance(),
            )
            regime = self.llm.infer_market_regime(deep_payload)
            try:
                proposal = self.llm.propose_strategies(deep_payload, regime)
            except Exception as exc:
                self.db.insert_audit_event(
                    level="INFO",
                    event_type="ai_proposal_unavailable",
                    message="AI proposal unavailable. Waiting for next cycle.",
                    payload={"reason": str(exc)},
                )
                LOGGER.info("AI proposal unavailable: %s", exc)
                return
        else:
            regime = self.llm.infer_market_regime(context)
            try:
                proposal = self.llm.propose_strategies(context, regime)
            except Exception as exc:
                self.db.insert_audit_event(
                    level="INFO",
                    event_type="ai_proposal_unavailable",
                    message="AI proposal unavailable. Waiting for next cycle.",
                    payload={"reason": str(exc)},
                )
                LOGGER.info("AI proposal unavailable: %s", exc)
                return

        candidate_rows = (
            proposal.get("candidates", []) if isinstance(proposal, dict) else []
        )
        if not isinstance(candidate_rows, list) or not candidate_rows:
            candidate_rows = []
        candidate_rankings = rank_strategy_candidates(
            context,
            regime=regime,
            recent_performance=self._recent_strategy_performance(),
        )
        proposal_snapshot = self._proposal_snapshot(proposal, regime)
        self.db.insert_ai_decision(proposal_snapshot)
        self.latest_decision = proposal_snapshot
        quant_enabled = self._quant_gate_enabled()
        ranked_candidates = self.quant_validator.rank_candidate_payloads(
            context,
            candidate_rows
            if candidate_rows
            else [proposal.get("primary", {}), proposal.get("secondary", {})],
            regime=regime,
            recent_performance=self._recent_strategy_performance(),
        )
        if quant_enabled:
            quant_result = self.quant_validator.validate_candidates(
                context,
                ranked_candidates,
                regime=regime,
            )
        else:
            quant_result = {
                "allowed": True,
                "selected": proposal_snapshot,
                "selected_strategy": str(proposal_snapshot.get("strategy", "NO TRADE")),
                "candidates": [],
                "bypassed": True,
                "enabled": False,
                "reason": "Quant gate disabled via UI",
            }
        self.latest_quant = quant_result

        if not quant_result.get("selected"):
            if quant_enabled:
                selected_strategy = str(
                    quant_result.get("selected_strategy", "NO TRADE")
                )
                self.db.insert_audit_event(
                    level="INFO",
                    event_type="quant_no_trade",
                    message=f"Quant scoring engine returned {selected_strategy}. Waiting for next cycle.",
                    payload={
                        "proposal": proposal,
                        "quant": quant_result,
                        "regime": regime,
                        "candidates": ranked_candidates,
                        "selected_strategy": selected_strategy,
                    },
                )
                LOGGER.info(
                    "Quant scoring engine returned %s. Waiting for next cycle.",
                    selected_strategy,
                )
                return
            quant_result["selected"] = proposal_snapshot

        if not quant_enabled:
            self.db.insert_audit_event(
                level="INFO",
                event_type="quant_gate_bypassed",
                message="Quant gate disabled. Continuing with AI proposal.",
                payload={"proposal": proposal_snapshot},
            )
            approved_candidates = [proposal_snapshot]
        else:
            approved_candidates = sorted(
                [
                    row
                    for row in quant_result.get("candidates", [])
                    if bool(row.get("allowed", False))
                ],
                key=lambda row: float(row.get("score", 0.0) or 0.0),
                reverse=True,
            )
            if not approved_candidates:
                self.db.insert_audit_event(
                    level="INFO",
                    event_type="quant_rejected",
                    message="Quant gate rejected all AI proposals. Waiting for next cycle.",
                    payload={
                        "proposal": proposal,
                        "quant": quant_result,
                        "regime": regime,
                        "candidates": ranked_candidates,
                    },
                )
                LOGGER.info(
                    "Quant gate rejected all AI proposals. Waiting for next cycle."
                )
                return

        insight_payload = {
            "strategy": quant_result["selected"].get("strategy"),
            "reason": quant_result["selected"].get("reason"),
            "confidence": float(quant_result["selected"].get("ai_confidence", 0.0)),
            "model": str(self.settings.get("openrouter", {}).get("model", "unknown")),
            "regime": regime,
            "quant": quant_result,
            "candidate_strategies": candidate_rankings[:5],
        }
        self.db.insert_audit_event(
            level="INFO",
            event_type="ai_insight_ready",
            message=f"AI proposed {insight_payload['strategy']} ({int(insight_payload['confidence'] * 100)}% confidence)",
            payload=insight_payload,
        )
        last_rejection: dict[str, Any] = {}
        for candidate in approved_candidates:
            candidate_decision = {
                **candidate,
                "strategy": str(candidate.get("strategy", "")),
                "confidence": float(
                    candidate.get("ai_confidence", candidate.get("confidence", 0.5))
                    or 0.5
                ),
                "reason": str(candidate.get("reason", "Quant-approved AI proposal")),
            }
            strategy_name, legs = build_trade_from_decision(candidate_decision, context)
            risk_enabled = self._risk_engine_enabled()
            if risk_enabled:
                risk_result = self.risk_manager.evaluate_trade(context, legs)
            else:
                risk_result = {
                    "allowed": True,
                    "bypassed": True,
                    "enabled": False,
                    "reason": "Risk engine disabled via UI",
                    "stats": {},
                }
            self.latest_risk = risk_result
            if not risk_result["allowed"]:
                last_rejection = {"candidate": candidate_decision, "risk": risk_result}
                self.db.insert_audit_event(
                    level="WARNING",
                    event_type="trade_rejected",
                    message="Trade rejected by risk manager",
                    payload={
                        "reason": risk_result.get("reason"),
                        "stats": risk_result.get("stats", {}),
                        "strategy": candidate_decision.get("strategy"),
                    },
                )
                continue

            if self._kill_switch_enabled():
                self.db.insert_audit_event(
                    level="WARNING",
                    event_type="trade_blocked_kill_switch",
                    message="Kill switch activated before order execution. Trade blocked.",
                    payload={"strategy": candidate_decision.get("strategy")},
                )
                LOGGER.warning("Kill switch activated before execution. Trade blocked.")
                return

            self.order_manager.refresh_mode(self._current_trading_mode())
            results = self.order_manager.execute_legs(strategy_name, legs)
            self.latest_order_results = results
            self.db.insert_audit_event(
                level="INFO",
                event_type="trade_executed",
                message=f"Executed strategy={strategy_name}",
                payload={
                    "strategy": strategy_name,
                    "legs": len(legs),
                    "confidence": float(candidate_decision.get("confidence", 0.0)),
                    "results": results,
                    "quant": quant_result,
                },
            )
            self._notify(
                "TRADE_EXECUTED",
                f"Executed strategy={strategy_name} legs={len(legs)} confidence={float(candidate_decision.get('confidence', 0.0)):.2f}",
                payload={"results": results},
            )
            LOGGER.info(
                "Executed strategy=%s legs=%d confidence=%.2f",
                strategy_name,
                len(legs),
                float(candidate_decision.get("confidence", 0.0)),
            )
            return

        if quant_enabled:
            self.db.insert_audit_event(
                level="INFO",
                event_type="trade_deferred_risk",
                message="Risk manager rejected all quant-approved candidates. Waiting for next cycle.",
                payload={"last_rejection": last_rejection, "quant": quant_result},
            )
            LOGGER.info(
                "Risk manager rejected all quant-approved candidates. Waiting for next cycle."
            )

    def handle_market_tick(self, tick: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._tick_lock:
            if now - self._last_tick_write_ts < 1.0:
                return
            self._last_tick_write_ts = now

        try:
            price = float(tick.get("nifty_price", tick.get("last_price", 0.0)) or 0.0)
            vix = float(tick.get("vix", self.latest_context.get("vix", 0.0)) or 0.0)
            volume = int(tick.get("volume", 0) or 0)
            if price <= 0:
                return
            session = self.market_data.market_session_status()
            option_ltps = self._fetch_open_trade_ltps()
            realtime_payload = {
                "nifty_price": price,
                "vix": vix,
                "volume": volume,
                "market_status": str(session["status"]),
                "market_status_reason": str(session["reason"]),
                "market_local_time": str(session["local_time"]),
                "option_ltps": option_ltps,
            }
            self.db.insert_realtime_tick(realtime_payload)

            had_context = bool(self.latest_context)
            if not had_context:
                live_context = {
                    "nifty_price": price,
                    "vix": vix,
                    "trend": "sideways",
                    "atr": 0.0,
                    "expiry_days": 0,
                    "capital_available": float(
                        self.settings["risk"]["max_capital_per_trade"]
                    ),
                    "option_chain": [],
                    "volume": volume,
                    "vwap": price,
                    "trend_strength": 0.0,
                    "oi_analysis": {"ce_wall": 0, "pe_wall": 0, "bias": "neutral"},
                    "iv_skew": 0.0,
                    "model_regime": "unknown",
                    "model_regime_confidence": 0.0,
                    "lot_size": int(self.settings["app"]["lot_size"]),
                }
            else:
                live_context = dict(self.latest_context)
            live_context["nifty_price"] = price
            if vix > 0:
                live_context["vix"] = vix
            if volume > 0:
                live_context["volume"] = volume
            live_context["market_open"] = bool(session["is_open"])
            live_context["market_status"] = str(session["status"])
            live_context["market_status_reason"] = str(session["reason"])
            live_context["market_local_time"] = str(session["local_time"])
            self.latest_context = live_context
            if not had_context:
                self.db.insert_market_context(live_context)
        except Exception:
            LOGGER.exception("Failed processing realtime market tick")

    def _fetch_open_trade_ltps(self) -> dict[str, float]:
        try:
            open_trades = self.db.fetch_open_trades()
            symbols = sorted(
                {
                    str(row.get("symbol", "")).strip()
                    for row in open_trades
                    if str(row.get("symbol", "")).strip()
                }
            )
            if not symbols:
                return {}
            quote_symbols = [f"NFO:{symbol}" for symbol in symbols]
            quotes = self.order_manager.kite_client.quote(quote_symbols)
            ltps: dict[str, float] = {}
            for symbol in symbols:
                price = float(
                    quotes.get(f"NFO:{symbol}", {}).get("last_price", 0.0) or 0.0
                )
                if price > 0:
                    ltps[symbol] = price
            return ltps
        except Exception:
            LOGGER.exception("Failed to fetch open-trade LTPs from quote stream")
            return {}

    def monitor_and_exit_if_needed(self) -> None:
        if self._kill_switch_enabled():
            if self.db.fetch_open_trades():
                self.order_manager.close_all_positions()
                self.db.insert_audit_event(
                    level="WARNING",
                    event_type="kill_switch_forced_close",
                    message="Kill switch is ON. Open positions were force-closed.",
                )
                self._notify(
                    "KILL_SWITCH",
                    "Kill switch is ON. Open positions were force-closed.",
                )
            return

        session = self.market_data.market_session_status()
        if not bool(session["is_open"]):
            LOGGER.info(
                "PnL monitor skipped because market is %s (%s)",
                session["status"],
                session["reason"],
            )
            return
        should_exit, reason = self.pnl_monitor.should_exit()
        LOGGER.info("PnL monitor: %s", reason)
        if should_exit:
            self.order_manager.close_all_positions()

    def force_exit_all_positions(self) -> None:
        self.order_manager.refresh_mode(self._current_trading_mode())
        self.order_manager.close_all_positions()
        self.db.insert_audit_event(
            level="INFO",
            event_type="force_exit",
            message="Forced exit completed",
        )
        self._notify("FORCE_EXIT", "Forced exit completed.")
        LOGGER.info("Forced exit completed")

    def refresh_kite_session(self) -> None:
        try:
            self.order_manager.kite_client.refresh_session(force=True)
            self.db.insert_audit_event(
                level="INFO",
                event_type="kite_token_refresh_ok",
                message="Morning Kite token refresh completed",
            )
            self._notify("TOKEN_REFRESH", "Morning Kite token refresh completed.")
            LOGGER.info("Morning Kite token refresh completed")
        except Exception:
            self.db.insert_audit_event(
                level="ERROR",
                event_type="kite_token_refresh_failed",
                message="Morning Kite token refresh failed",
            )
            self._notify("TOKEN_REFRESH_FAILED", "Morning Kite token refresh failed.")
            LOGGER.exception("Morning Kite token refresh failed")

    def run_day_end_optimization(self) -> None:
        if not bool(self.settings["dashboard"].get("auto_optimize", True)):
            self.db.insert_audit_event(
                level="INFO",
                event_type="day_end_optimization_skipped",
                message="Day-end optimization skipped because auto_optimize is disabled",
            )
            LOGGER.info(
                "Day-end optimization skipped because auto_optimize is disabled"
            )
            return
        rankings = self.strategy_optimizer.run_day_end_optimization()
        llm_rankings = self.llm.optimize_strategy_rankings(rankings)
        if llm_rankings:
            LOGGER.info(
                "LLM-adjusted rankings: %s", json.dumps(llm_rankings[:5], default=str)
            )
        LOGGER.info(
            "Day-end strategy rankings: %s", json.dumps(rankings[:5], default=str)
        )
        self.db.insert_audit_event(
            level="INFO",
            event_type="day_end_optimization_done",
            message="Day-end optimization completed",
            payload={"top_rankings": rankings[:5]},
        )
        self._notify("DAY_END_OPTIMIZATION", "Day-end optimization completed.")

    def _recent_ai_decisions(self) -> list[dict[str, Any]]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM ai_decisions ORDER BY id DESC LIMIT 25"
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def _recent_strategy_performance(self) -> list[dict[str, Any]]:
        lookback_date = (date.today() - timedelta(days=20)).isoformat()
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT strategy, date, trades_count, win_rate, avg_pnl, drawdown, sharpe, rank_score
                FROM strategy_performance
                WHERE date >= ?
                ORDER BY id DESC
                LIMIT 40
                """,
                (lookback_date,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _proposal_snapshot(
        self, proposal: dict[str, Any], regime: dict[str, Any]
    ) -> dict[str, Any]:
        primary = (
            proposal.get("primary") if isinstance(proposal.get("primary"), dict) else {}
        )
        secondary = (
            proposal.get("secondary")
            if isinstance(proposal.get("secondary"), dict)
            else {}
        )
        candidates = (
            proposal.get("candidates")
            if isinstance(proposal.get("candidates"), list)
            else []
        )
        snapshot_source = primary or secondary
        if not snapshot_source and candidates:
            first_candidate = candidates[0]
            if isinstance(first_candidate, dict):
                snapshot_source = first_candidate
        snapshot = dict(snapshot_source) if isinstance(snapshot_source, dict) else {}
        snapshot["proposal"] = proposal
        snapshot["regime"] = regime
        snapshot["candidates"] = [item for item in candidates if isinstance(item, dict)]
        snapshot.setdefault("reason", str(snapshot.get("reason", "Fresh AI proposal")))
        snapshot.setdefault("confidence", float(snapshot.get("confidence", 0.0) or 0.0))
        snapshot.setdefault(
            "capital_to_use", float(snapshot.get("capital_to_use", 0.0) or 0.0)
        )
        snapshot.setdefault("ce_strike", int(snapshot.get("ce_strike", 0) or 0))
        snapshot.setdefault("pe_strike", int(snapshot.get("pe_strike", 0) or 0))
        return snapshot
