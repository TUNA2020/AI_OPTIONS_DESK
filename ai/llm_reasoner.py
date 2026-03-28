from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from core.retry import retry
from ai.strategy_generator import canonical_strategy_name, rank_strategy_candidates


LOGGER = logging.getLogger(__name__)


DEFAULT_DECISION = {
    "strategy": "",
    "capital_to_use": 50000,
    "ce_strike": 0,
    "pe_strike": 0,
    "confidence": 0.55,
    "reason": "LLM output unavailable.",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    raise TypeError(
        f"Object of type {value.__class__.__name__} is not JSON serializable"
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


@dataclass(slots=True)
class LLMReasoner:
    settings: dict[str, Any]

    def _heuristic_regime(self, context: dict[str, Any]) -> dict[str, Any]:
        trend = context.get("trend", "sideways")
        vix = float(context.get("vix", 0.0))
        regime = "volatile" if vix > 18 else "range"
        if trend in {"uptrend", "downtrend"} and vix < 18:
            regime = "trend"
        return {"regime": regime, "confidence": 0.5, "summary": "Heuristic regime"}

    def _openrouter_api_key(self) -> str:
        key = str(self.settings.get("openrouter", {}).get("api_key", "")).strip()
        return "" if key.lower().startswith("replace") else key

    def _openrouter_endpoint(self) -> str:
        return str(self.settings.get("openrouter", {}).get("endpoint", "")).strip()

    def _openrouter_model(self) -> str:
        model = str(self.settings.get("openrouter", {}).get("model", "")).strip()
        return model or "openrouter/free"

    def _llm_enabled(self) -> bool:
        return bool(self._openrouter_api_key() and self._openrouter_endpoint())

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._openrouter_api_key()}",
            "Content-Type": "application/json",
        }

    @retry(attempts=3, delay_seconds=1.0)
    def _chat(self, messages: list[dict[str, str]]) -> str:
        endpoint = self._openrouter_endpoint()
        primary_model = self._openrouter_model()
        candidates = [primary_model]

        last_error: str = ""
        for model in candidates:
            payload = {
                "model": model,
                "messages": messages,
                "response_format": {"type": "json_object"},
            }
            response = requests.post(
                endpoint,
                headers=self._headers,
                json=payload,
                timeout=30,
            )
            if response.ok:
                data = response.json()
                return data["choices"][0]["message"]["content"]

            body = str(response.text or "").strip()
            last_error = f"HTTP {response.status_code} for model {model}: {body[:300]}"
            if response.status_code == 404 and model != "openrouter/free":
                LOGGER.warning(
                    "OpenRouter model %s unavailable; falling back to openrouter/free.",
                    model,
                )
                candidates = ["openrouter/free"]
                continue
            response.raise_for_status()

        raise RuntimeError(last_error or "OpenRouter request failed.")

    def _market_context(self, context: dict[str, Any]) -> dict[str, Any]:
        nested = context.get("current_market_context")
        if isinstance(nested, dict) and nested:
            return nested
        nested = context.get("market_context")
        if isinstance(nested, dict) and nested:
            return nested
        return context

    def _build_candidate_payload(
        self,
        market_context: dict[str, Any],
        regime: dict[str, Any],
        recent_performance: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ranked_candidates = rank_strategy_candidates(
            market_context,
            regime=regime,
            recent_performance=recent_performance
            if isinstance(recent_performance, list)
            else [],
        )
        return {
            "market_context": market_context,
            "regime": regime,
            "candidate_strategies": ranked_candidates[:6],
        }

    def infer_market_regime(self, context: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "You are an options market regime classifier for Indian index options (NIFTY/BANKNIFTY).\n\n"
            "TASK: Analyze the market context and classify the current market regime.\n\n"
            "INPUT ANALYSIS:\n"
            "- VIX level: < 14 = low volatility, 14-18 = normal, 18-25 = elevated, > 25 = high volatility\n"
            "- Trend direction and strength from price action\n"
            "- IV skew pattern (put skew vs call skew vs smile)\n"
            "- Volume patterns and OI buildup (PCR, OI walls)\n"
            "- Technical indicators: RSI, MACD, Bollinger Bands, ADR\n"
            "- Support/resistance levels\n\n"
            "REGIME CLASSIFICATION:\n"
            "1. TRENDING: Clear directional movement (uptrend/downtrend) with sustained momentum; VIX usually moderate\n"
            "2. VOLATILE: High VIX (>25) with large price swings; sharp IV skew changes; panic or uncertainty\n"
            "3. RANGE: Sideways price action with defined support/resistance; low-to-moderate VIX; mean-reverting behavior\n"
            "4. MIXED: Conflicting signals; could be transitioning between regimes\n\n"
            "OUTPUT FORMAT (strict JSON):\n"
            "{\n"
            '  "regime": "trending|volatile|range|mixed",\n'
            '  "confidence": float (0.0-1.0; 0.8+ for clear regimes, 0.5-0.7 for mixed),\n'
            '  "summary": "1-2 sentence explanation of your classification and key factors"\n'
            "}\n\n"
            "Consider:\n"
            "- Trend + low VIX = trending regime\n"
            "- High VIX + sharp moves = volatile regime\n"
            "- Sideways price + low VIX = range regime\n"
            "- Conflicting indicators = mixed regime\n"
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": _json_dumps(context)},
        ]
        if not self._llm_enabled():
            LOGGER.warning("OpenRouter API key not configured; using heuristic regime.")
            return self._heuristic_regime(context)
        try:
            content = self._chat(messages)
            return json.loads(content)
        except Exception:
            LOGGER.warning("Regime inference failed; using heuristic fallback.")
            return self._heuristic_regime(context)

    def propose_strategies(
        self, context: dict[str, Any], regime: dict[str, Any]
    ) -> dict[str, Any]:
        market_context = self._market_context(context)
        recent_performance = context.get("recent_strategy_performance", [])
        ranked_candidates = rank_strategy_candidates(
            market_context,
            regime=regime,
            recent_performance=recent_performance
            if isinstance(recent_performance, list)
            else [],
        )
        candidate_names = [str(item["strategy"]) for item in ranked_candidates[:6]]
        prompt = (
            "You are an expert options trading strategist for Indian index options (NIFTY/BANKNIFTY).\n\n"
            "TASK: Select exactly TWO strategies from the provided candidate list - one as PRIMARY and one as SECONDARY.\n\n"
            "INPUTS:\n"
            "1. Market Context: Current prices, volatility (VIX), trend, IV skew, OI analysis, technical indicators, liquidity metrics\n"
            "2. Market Regime: Classified regime with confidence and summary\n"
            "3. Candidate Strategies: Ranked list of 6 strategies with scores based on regime fit and recent performance\n"
            "4. Recent Performance: Historical performance metrics for strategies over the last 20 days\n"
            "5. Portfolio State: Current exposure (delta, vega, theta) if provided\n\n"
            "SELECTION CRITERIA (in order of importance):\n"
            "a) REGIME ALIGNMENT: How well the strategy matches the current market regime (trend/range/volatile)\n"
            "b) RECENT PERFORMANCE: Recent win rates and Sharpe ratios for the strategy\n"
            "c) RISK-REWARD: Probability of profit, max loss potential, and risk-adjusted returns\n"
            "d) VOLATILITY SUITABILITY: appropriateness given current VIX levels\n"
            "e) STRIKE SELECTION LOGIC: Rationale for chosen strikes based on support/resistance, IV skew, and delta targets\n"
            "f) CAPITAL EFFICIENCY: Appropriate position sizing for the strategy type and risk budget\n"
            "g) PORTFOLIO FIT: Avoid over-concentration in Greeks when portfolio state is available\n"
            "h) DIVERSIFICATION: Secondary strategy should differ from primary (e.g., not two income strategies simultaneously)\n\n"
            "OUTPUT FORMAT (strict JSON):\n"
            "{\n"
            '  "primary": {\n'
            '    "strategy": "strategy_name",\n'
            '    "confidence": float (0.0-1.0),\n'
            '    "reason": "detailed explanation including: regime fit, strike rationale, risk-reward, capital justification",\n'
            '    "capital_to_use": float (total capital allocated),\n'
            "    // At least ONE of the following strike control methods:\n"
            '    "ce_strike": int (call strike price) [legacy],\n'
            '    "pe_strike": int (put strike price) [legacy],\n'
            "    // Param-based overrides (preferred):\n"
            '    "ce_delta": float (0.05-0.5) for CE delta target,\n'
            '    "pe_delta": float (0.05-0.5) for PE delta target,\n'
            '    "width": int (spread width in points) for spreads/condors,\n'
            '    "atm_offset_ce": int (offset from ATM for CE),\n'
            '    "atm_offset_pe": int (offset from ATM for PE)\n'
            "  },\n"
            '  "secondary": { /* same fields as primary */ },\n'
            '  "candidates": [primary, secondary, ...],\n'
            '  "rejection_reason": "optional: why others rejected"\n'
            "}\n\n"
            "REQUIREMENTS:\n"
            "- primary and secondary MUST be different strategies from the candidate list\n"
            "- Provide specific strike rationales (e.g., 'ATM for delta neutrality', '25-delta for directionality', '50% retracement level')\n"
            "- Prefer param-based controls (ce_delta, pe_delta, width) over explicit ce_strike/pe_strike to respect strategy-specific logic\n"
            "- ce_strike/pe_strike are fallbacks for direct override (bypasses strategy defaults)\n"
            "- confidence should reflect both regime fit and your certainty about the selection (0.6-0.9 typical)\n"
            "- capital_to_use should respect max_capital_per_trade and not exceed available capital\n"
            "- Each strategy in candidates must be one of: ["
            + ", ".join(candidate_names)
            + "]\n"
            "- If market conditions are uncertain, lower confidence and suggest cash/near-cash strategies\n"
            "- Consider expiry proximity when selecting strikes (prefer nearer expiries for income, further for directional)\n"
        )
        payload = self._build_candidate_payload(
            market_context,
            regime,
            recent_performance if isinstance(recent_performance, list) else [],
        )
        payload["available_strategies"] = candidate_names
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": _json_dumps(payload)},
        ]
        if not self._llm_enabled():
            raise RuntimeError("OpenRouter API key not configured.")
        try:
            content = self._chat(messages)
            decision = json.loads(content)
            candidates = decision.get("candidates")
            if not isinstance(candidates, list) or len(candidates) < 2:
                primary = decision.get("primary") or {}
                secondary = decision.get("secondary") or {}
                candidates = [primary, secondary]
            normalized: list[dict[str, Any]] = []
            for item in candidates[:2]:
                if not isinstance(item, dict):
                    continue
                candidate = dict(item)
                candidate["strategy"] = canonical_strategy_name(
                    str(candidate.get("strategy", ""))
                )
                if candidate_names and candidate["strategy"] not in candidate_names:
                    candidate["strategy"] = candidate_names[0]
                normalized.append(self._normalize_decision(candidate, market_context))
            if len(normalized) == 1:
                normalized.append(normalized[0])
            if not normalized:
                raise RuntimeError("No valid strategy candidates returned by LLM")
            return {
                "primary": normalized[0],
                "secondary": normalized[1],
                "candidates": normalized,
                "regime": regime,
            }
        except Exception:
            LOGGER.warning("Strategy proposal failed.")
            raise

    def choose_strategy(
        self, context: dict[str, Any], regime: dict[str, Any]
    ) -> dict[str, Any]:
        proposal = self.propose_strategies(context, regime)
        if isinstance(proposal, dict) and isinstance(proposal.get("primary"), dict):
            return proposal["primary"]
        raise RuntimeError("No AI proposal available.")

    def _normalize_decision(
        self, decision: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        chain = context.get("option_chain", [])
        strikes = [int(row["strike"]) for row in chain if "strike" in row]
        atm = (
            strikes[len(strikes) // 2]
            if strikes
            else int(context.get("nifty_price", 0))
        )
        decision["strategy"] = canonical_strategy_name(
            str(decision.get("strategy", ""))
        )
        if not decision["strategy"]:
            raise RuntimeError("Unable to resolve a strategy from the AI proposal.")
        decision["capital_to_use"] = float(decision.get("capital_to_use", 50000))
        decision["ce_strike"] = int(decision.get("ce_strike") or atm + 200)
        decision["pe_strike"] = int(decision.get("pe_strike") or atm - 200)
        decision["confidence"] = float(decision.get("confidence", 0.5))
        decision["reason"] = str(decision.get("reason", "No reason provided"))
        return decision

    def optimize_strategy_rankings(
        self, stats: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        prompt = (
            "You are a quantitative options strategy optimizer. "
            "Given strategy metrics, return JSON with key 'rankings' as ordered list. "
            "Each item keys: strategy, rank_score, note."
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": _json_dumps({"stats": stats})},
        ]
        if not self._llm_enabled():
            LOGGER.warning(
                "OpenRouter API key not configured; keeping quantitative ranking."
            )
            return []
        try:
            content = self._chat(messages)
            out = json.loads(content)
            rankings = out.get("rankings", [])
            if isinstance(rankings, list):
                normalized: list[dict[str, Any]] = []
                for row in rankings:
                    if not isinstance(row, dict):
                        continue
                    item = dict(row)
                    item["strategy"] = canonical_strategy_name(
                        str(item.get("strategy", ""))
                    )
                    normalized.append(item)
                return normalized
            return []
        except Exception:
            LOGGER.warning("Ranking optimization failed; keeping quantitative ranking.")
            return []
