from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ai.strategy_generator import canonical_strategy_name, rank_strategy_candidates


RANGE_STRATEGIES = {
    "iron_condor",
    "delta_neutral_condor",
    "short_strangle",
    "vix_reversion",
    "expiry_range_trade",
}

DIRECTIONAL_BULL = {
    "bull_put_spread",
    "trend_credit_spread",
    "momentum_volatility",
}

DIRECTIONAL_BEAR = {
    "bear_call_spread",
    "trend_credit_spread",
    "momentum_volatility",
}

VOLATILITY_CONTROL = {
    "broken_wing_butterfly",
    "iron_condor",
    "delta_neutral_condor",
    "vix_reversion",
}


def _extract_market_context(context: dict[str, Any]) -> dict[str, Any]:
    nested = context.get("current_market_context")
    if isinstance(nested, dict) and nested:
        return nested
    nested = context.get("market_context")
    if isinstance(nested, dict) and nested:
        return nested
    return context


def _candles_to_frame(candles: list[dict[str, Any]]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(candles)
    columns = [col for col in ["date", "open", "high", "low", "close", "volume"] if col in df.columns]
    return df[columns].copy() if columns else pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _candle_profile(context: dict[str, Any]) -> dict[str, float | str]:
    market = _extract_market_context(context)
    today = market.get("today_candles_5m") or market.get("candles_5m_today") or []
    history = market.get("history_5m_7d") or market.get("candles_5m_history_7d") or []
    today_df = _candles_to_frame(today)
    history_rows = [row for row in history if isinstance(row, dict)]

    if today_df.empty:
        return {
            "today_trend": str(market.get("trend", "sideways")),
            "today_change_pct": 0.0,
            "today_range_pct": 0.0,
            "avg_7d_change_pct": 0.0,
            "avg_7d_range_pct": 0.0,
            "avg_7d_trend_strength": 0.0,
        }

    first = today_df.iloc[0]
    last = today_df.iloc[-1]
    open_price = float(first["open"] or 0.0)
    today_change_pct = ((float(last["close"]) - open_price) / open_price * 100.0) if open_price else 0.0
    today_range_pct = ((float(today_df["high"].max()) - float(today_df["low"].min())) / open_price * 100.0) if open_price else 0.0

    avg_change = 0.0
    avg_range = 0.0
    avg_trend_strength = 0.0
    if history_rows:
        changes: list[float] = []
        ranges: list[float] = []
        strengths: list[float] = []
        for row in history_rows:
            try:
                changes.append(float(row.get("change_pct", 0.0) or 0.0))
                ranges.append(float(row.get("range_pct", 0.0) or 0.0))
                strengths.append(abs(float(row.get("trend_strength", 0.0) or 0.0)))
            except Exception:
                continue
        if changes:
            avg_change = sum(changes) / len(changes)
        if ranges:
            avg_range = sum(ranges) / len(ranges)
        if strengths:
            avg_trend_strength = sum(strengths) / len(strengths)

    today_trend = str(market.get("trend", "sideways")).lower()
    if today_change_pct > 0.25:
        today_trend = "uptrend"
    elif today_change_pct < -0.25:
        today_trend = "downtrend"
    elif abs(today_change_pct) <= 0.12:
        today_trend = "sideways"

    return {
        "today_trend": today_trend,
        "today_change_pct": round(today_change_pct, 3),
        "today_range_pct": round(today_range_pct, 3),
        "avg_7d_change_pct": round(avg_change, 3),
        "avg_7d_range_pct": round(avg_range, 3),
        "avg_7d_trend_strength": round(avg_trend_strength, 3),
    }


def _strategy_family(strategy: str) -> str:
    name = canonical_strategy_name(strategy)
    if name in DIRECTIONAL_BULL and name in DIRECTIONAL_BEAR:
        return "trend"
    if name in DIRECTIONAL_BULL:
        return "bullish"
    if name in DIRECTIONAL_BEAR:
        return "bearish"
    if name in VOLATILITY_CONTROL:
        return "defensive"
    if name in RANGE_STRATEGIES:
        return "range"
    if name in {"gamma_scalping"}:
        return "volatile"
    return "range"


def _regime_name(regime: dict[str, Any] | None, market: dict[str, Any]) -> str:
    return str((regime or {}).get("regime") or market.get("model_regime") or market.get("trend") or "range").lower().strip()


def _avg_option_iv(option_chain: list[dict[str, Any]]) -> float:
    ivs: list[float] = []
    for row in option_chain or []:
        for key in ("ce_iv", "pe_iv"):
            try:
                value = float(row.get(key, 0.0) or 0.0)
            except Exception:
                continue
            if value > 0:
                ivs.append(value)
    if not ivs:
        return 0.0
    return float(sum(ivs) / len(ivs))


def _oi_totals(option_chain: list[dict[str, Any]]) -> tuple[float, float]:
    ce = 0.0
    pe = 0.0
    for row in option_chain or []:
        ce += float(row.get("ce_oi", 0.0) or 0.0)
        pe += float(row.get("pe_oi", 0.0) or 0.0)
    return ce, pe


def _feature_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    market = _extract_market_context(context)
    candles_today = market.get("today_candles_5m") or market.get("candles_5m_today") or []
    history = market.get("history_5m_7d") or market.get("candles_5m_history_7d") or []
    candle_profile = _candle_profile(context)
    option_chain = market.get("option_chain", []) if isinstance(market.get("option_chain", []), list) else []
    price = float(market.get("nifty_price", 0.0) or 0.0)
    atr = float(market.get("atr", 0.0) or 0.0)
    vwap = float(market.get("vwap", price) or price or 0.0)
    vix = float(market.get("vix", 0.0) or 0.0)
    iv = _avg_option_iv(option_chain) or float(market.get("iv", 0.0) or 0.0)
    time_to_expiry = int(market.get("expiry_days", market.get("time_to_expiry", 0)) or 0)
    trend_strength = float(market.get("trend_strength", 0.0) or 0.0)
    iv_skew = float(market.get("iv_skew", 0.0) or 0.0)
    total_ce_oi, total_pe_oi = _oi_totals(option_chain)
    pcr = float(total_pe_oi / total_ce_oi) if total_ce_oi > 0 else 0.0
    today_volume = float(market.get("volume", 0.0) or 0.0)
    hist_volumes = [float(row.get("volume", 0.0) or 0.0) for row in history if isinstance(row, dict) and float(row.get("volume", 0.0) or 0.0) > 0]
    avg_hist_volume = float(sum(hist_volumes) / len(hist_volumes)) if hist_volumes else 0.0
    volume_spike = bool(avg_hist_volume > 0 and today_volume >= avg_hist_volume * 1.25)
    oi_analysis = market.get("oi_analysis") if isinstance(market.get("oi_analysis"), dict) else {}
    oi_call_wall = float(oi_analysis.get("ce_wall", market.get("oi_call_wall", 0.0)) or 0.0)
    oi_put_wall = float(oi_analysis.get("pe_wall", market.get("oi_put_wall", 0.0)) or 0.0)
    return {
        "price": price,
        "atr_pct": float(atr / price) if price > 0 else 0.0,
        "trend_strength": trend_strength,
        "vix": vix,
        "iv": iv,
        "time_to_expiry": time_to_expiry,
        "vwap": vwap,
        "pcr": pcr,
        "volume_spike": volume_spike,
        "oi_call_wall": oi_call_wall,
        "oi_put_wall": oi_put_wall,
        "iv_skew": iv_skew,
        "today_trend": candle_profile["today_trend"],
        "today_change_pct": float(candle_profile["today_change_pct"]),
        "today_range_pct": float(candle_profile["today_range_pct"]),
        "avg_7d_change_pct": float(candle_profile["avg_7d_change_pct"]),
        "avg_7d_range_pct": float(candle_profile["avg_7d_range_pct"]),
        "avg_7d_trend_strength": float(candle_profile["avg_7d_trend_strength"]),
    }


def _score_checks(checks: list[tuple[bool, str]]) -> tuple[int, bool, list[str]]:
    if not checks:
        return 0, False, ["No checks available"]
    passed = sum(1 for ok, _ in checks if ok)
    failed_notes = [note for ok, note in checks if not ok]
    return passed, passed == len(checks), failed_notes


def _global_reject(f: dict[str, Any]) -> bool:
    return float(f.get("vix", 0.0) or 0.0) > 22.0 or abs(float(f.get("trend_strength", 0.0) or 0.0)) > 50.0


def _context_fit_bonus(strategy: str, f: dict[str, Any], regime_name: str) -> tuple[int, list[str]]:
    name = canonical_strategy_name(strategy)
    family = _strategy_family(name)
    trend_strength = float(f.get("trend_strength", 0.0) or 0.0)
    vix = float(f.get("vix", 0.0) or 0.0)
    price = float(f.get("price", 0.0) or 0.0)
    vwap = float(f.get("vwap", price) or price or 0.0)
    today_trend = str(f.get("today_trend", "")).lower().strip()
    avg_7d_trend_strength = float(f.get("avg_7d_trend_strength", 0.0) or 0.0)
    today_range_pct = float(f.get("today_range_pct", 0.0) or 0.0)
    avg_7d_range_pct = float(f.get("avg_7d_range_pct", 0.0) or 0.0)
    volume_spike = bool(f.get("volume_spike", False))

    bonus = 0
    notes: list[str] = []

    if family == "range":
        if regime_name in {"range", "sideways"} or today_trend == "sideways" or abs(trend_strength) < 20:
            bonus += 1
            notes.append("range-family alignment")
        if vix >= 14 and vix <= 22:
            bonus += 1
            notes.append("range-family volatility fit")
    elif family == "bullish":
        if trend_strength > 20 and price > vwap:
            bonus += 1
            notes.append("bullish trend fit")
        if today_trend == "uptrend" or avg_7d_trend_strength > 20:
            bonus += 1
            notes.append("uptrend confirmation")
    elif family == "bearish":
        if trend_strength < -20 and price < vwap:
            bonus += 1
            notes.append("bearish trend fit")
        if today_trend == "downtrend" or avg_7d_trend_strength > 20:
            bonus += 1
            notes.append("downtrend confirmation")
    elif family == "trend":
        if abs(trend_strength) > 25 and abs(trend_strength) <= 50:
            bonus += 1
            notes.append("trend-strength alignment")
        if (trend_strength > 0 and today_trend == "uptrend") or (trend_strength < 0 and today_trend == "downtrend"):
            bonus += 1
            notes.append("trend-direction confirmation")
    elif family == "defensive":
        if vix >= 16 or float(f.get("atr_pct", 0.0) or 0.0) > 0.008:
            bonus += 1
            notes.append("defensive volatility fit")
        if abs(trend_strength) < 35 or today_trend == "sideways":
            bonus += 1
            notes.append("defensive structure fit")
    elif family == "volatile":
        if vix > 16 or float(f.get("atr_pct", 0.0) or 0.0) > 0.01:
            bonus += 1
            notes.append("volatile-market fit")
        if volume_spike or today_range_pct > avg_7d_range_pct * 1.05:
            bonus += 1
            notes.append("intraday expansion fit")

    if family in {"range", "defensive"} and abs(trend_strength) < 15 and today_trend == "sideways":
        bonus += 1
        notes.append("sideways confirmation")
    if family in {"bullish", "bearish", "trend"} and today_range_pct > 0 and avg_7d_range_pct > 0 and today_range_pct <= avg_7d_range_pct * 1.25:
        bonus += 1
        notes.append("range discipline fit")

    return min(bonus, 2), notes


def _distance_score(distance: float, threshold: float = 100.0, max_distance: float = 300.0) -> tuple[float, bool, list[str]]:
    if distance <= threshold:
        return 100.0, True, []
    if distance >= max_distance:
        return 0.0, False, [f"distance {distance:.2f} is beyond the wall threshold"]
    span = max_distance - threshold
    score = max(0.0, 100.0 * (1.0 - (distance - threshold) / span))
    return round(score, 3), False, [f"distance {distance:.2f} is outside the wall threshold"]


def _score_short_strangle(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (float(f["atr_pct"]) < 0.006, "atr_pct < 0.006"),
        (abs(float(f["trend_strength"])) < 20, "abs(trend_strength) < 20"),
        (12 <= float(f["vix"]) <= 18, "12 <= vix <= 18"),
        (float(f["iv"]) > 12, "iv > 12"),
        (int(f["time_to_expiry"]) > 0, "time_to_expiry > 0"),
    ]
    return {"checks": checks}


def _score_iron_condor(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (float(f["atr_pct"]) < 0.008, "atr_pct < 0.008"),
        (abs(float(f["trend_strength"])) < 25, "abs(trend_strength) < 25"),
        (float(f["vix"]) >= 14, "vix >= 14"),
        (float(f["iv"]) >= 12, "iv >= 12"),
    ]
    return {"checks": checks}


def _score_bull_put_spread(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (float(f["trend_strength"]) > 25, "trend_strength > 25"),
        (float(f["price"]) > float(f["vwap"]), "price > vwap"),
        (float(f["pcr"]) > 1, "pcr > 1"),
        (float(f["vix"]) < 18, "vix < 18"),
    ]
    return {"checks": checks}


def _score_bear_call_spread(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (float(f["trend_strength"]) < -25, "trend_strength < -25"),
        (float(f["price"]) < float(f["vwap"]), "price < vwap"),
        (float(f["pcr"]) < 1, "pcr < 1"),
        (float(f["vix"]) < 18, "vix < 18"),
    ]
    return {"checks": checks}


def _score_broken_wing_butterfly(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (float(f["vix"]) > 16, "vix > 16"),
        (float(f["atr_pct"]) > 0.008, "atr_pct > 0.008"),
        (abs(float(f["trend_strength"])) < 40, "abs(trend_strength) < 40"),
    ]
    return {"checks": checks}


def _score_calendar_spread(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (float(f["iv"]) < 11, "iv < 11"),
        (abs(float(f["trend_strength"])) < 20, "abs(trend_strength) < 20"),
        (int(f["time_to_expiry"]) <= 5, "time_to_expiry <= 5"),
    ]
    return {"checks": checks}


def _score_ratio_spread(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (abs(float(f["trend_strength"])) > 20, "abs(trend_strength) > 20"),
        (float(f["iv"]) > 13, "iv > 13"),
    ]
    return {"checks": checks}


def _score_gamma_scalping(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (float(f["atr_pct"]) > 0.012, "atr_pct > 0.012"),
        (bool(f["volume_spike"]), "volume_spike is true"),
        (float(f["vix"]) > 16, "vix > 16"),
    ]
    return {"checks": checks}


def _score_vix_reversion(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (float(f["vix"]) > 18, "vix > 18"),
        (float(f["atr_pct"]) > 0.01, "atr_pct > 0.01"),
    ]
    return {"checks": checks}


def _score_oi_wall_strategy(f: dict[str, Any]) -> dict[str, Any]:
    call_distance = abs(float(f["price"]) - float(f["oi_call_wall"])) if float(f["oi_call_wall"]) > 0 else 9999.0
    put_distance = abs(float(f["price"]) - float(f["oi_put_wall"])) if float(f["oi_put_wall"]) > 0 else 9999.0
    distance = min(call_distance, put_distance)
    checks = [
        (distance < 100, "abs(price - oi wall) < 100"),
        (float(f["pcr"]) >= 0.9, "pcr >= 0.9"),
        (abs(float(f["trend_strength"])) < 30, "abs(trend_strength) < 30"),
    ]
    return {"checks": checks}


def _score_trend_credit_spread(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (abs(float(f["trend_strength"])) > 30, "abs(trend_strength) > 30"),
        (float(f["atr_pct"]) < 0.01, "atr_pct < 0.01"),
        (float(f["vix"]) < 20, "vix < 20"),
    ]
    return {"checks": checks}


def _score_delta_neutral_condor(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (float(f["atr_pct"]) < 0.008, "atr_pct < 0.008"),
        (abs(float(f["trend_strength"])) < 25, "abs(trend_strength) < 25"),
        (float(f["vix"]) >= 14, "vix >= 14"),
    ]
    return {"checks": checks}


def _score_skew_arbitrage(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (abs(float(f["iv_skew"])) > 2, "abs(iv_skew) > 2"),
        (float(f["iv"]) > 0, "iv available"),
    ]
    return {"checks": checks}


def _score_expiry_range_trade(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (int(f["time_to_expiry"]) == 0, "time_to_expiry == 0"),
        (float(f["atr_pct"]) < 0.006, "atr_pct < 0.006"),
        (abs(float(f["trend_strength"])) < 18, "abs(trend_strength) < 18"),
    ]
    return {"checks": checks}


def _score_momentum_volatility(f: dict[str, Any]) -> dict[str, Any]:
    checks = [
        (abs(float(f["trend_strength"])) > 35, "abs(trend_strength) > 35"),
        (float(f["atr_pct"]) > 0.01, "atr_pct > 0.01"),
        (float(f["vix"]) > 14, "vix > 14"),
    ]
    return {"checks": checks}


STRATEGY_SCORERS = {
    "short_strangle": _score_short_strangle,
    "iron_condor": _score_iron_condor,
    "bull_put_spread": _score_bull_put_spread,
    "bear_call_spread": _score_bear_call_spread,
    "broken_wing_butterfly": _score_broken_wing_butterfly,
    "calendar_spread": _score_calendar_spread,
    "ratio_spread": _score_ratio_spread,
    "gamma_scalping": _score_gamma_scalping,
    "vix_reversion": _score_vix_reversion,
    "oi_wall_strategy": _score_oi_wall_strategy,
    "trend_credit_spread": _score_trend_credit_spread,
    "delta_neutral_condor": _score_delta_neutral_condor,
    "skew_arbitrage": _score_skew_arbitrage,
    "expiry_range_trade": _score_expiry_range_trade,
    "momentum_volatility": _score_momentum_volatility,
}


def _score_strategy(strategy: str, f: dict[str, Any], regime_name: str) -> dict[str, Any]:
    name = canonical_strategy_name(strategy)
    scorer = STRATEGY_SCORERS.get(name)
    if scorer is None:
        return {
            "strategy": name,
            "allowed": False,
            "score": 0,
            "rule_score": 0,
            "fit_bonus": 0,
            "reason": "Unknown strategy",
            "notes": ["Unknown strategy"],
            "checks": [],
            "fit_notes": [],
            "global_reject": _global_reject(f),
        }

    scoring = scorer(f)
    checks = list(scoring.get("checks", []))
    rule_score, rule_allowed, failed_notes = _score_checks(checks)
    fit_bonus, fit_notes = _context_fit_bonus(name, f, regime_name)
    score = rule_score + fit_bonus
    allowed = score >= 3 and not _global_reject(f)
    if failed_notes and not allowed:
        reason = "Quant score below threshold: " + "; ".join(failed_notes)
    elif not allowed:
        reason = "Quant score below threshold"
    else:
        reason = f"Quant score {score} with {rule_score} rule points and {fit_bonus} context points"
    return {
        "strategy": name,
        "allowed": allowed,
        "score": score,
        "rule_score": rule_score,
        "fit_bonus": fit_bonus,
        "reason": reason,
        "notes": [note for ok, note in checks if ok],
        "fit_notes": fit_notes,
        "checks": [{"check": note, "passed": ok} for ok, note in checks],
        "global_reject": _global_reject(f),
    }


@dataclass(slots=True)
class QuantValidator:
    min_score: float = 3.0

    def select_strategy(
        self,
        features: dict[str, Any],
        strategies: list[str] | None = None,
        regime_name: str = "",
    ) -> dict[str, Any]:
        strategy_names = strategies or list(STRATEGY_SCORERS.keys())
        if _global_reject(features):
            return {
                "strategy": "NO TRADE",
                "score": 0,
                "allowed": False,
                "reason": "Global reject triggered",
                "candidates": [],
                "global_reject": True,
            }

        scored: list[dict[str, Any]] = []
        for strategy in strategy_names:
            if not canonical_strategy_name(strategy):
                continue
            result = _score_strategy(strategy, features, regime_name)
            scored.append(result)

        if not scored:
            return {
                "strategy": "NO TRADE",
                "score": 0,
                "allowed": False,
                "reason": "No scored strategies available",
                "candidates": [],
                "global_reject": False,
            }

        best = max(scored, key=lambda row: float(row.get("score", 0.0) or 0.0))
        if float(best.get("score", 0.0) or 0.0) < self.min_score:
            return {
                "strategy": "NO TRADE",
                "score": float(best.get("score", 0.0) or 0.0),
                "allowed": False,
                "reason": "No strategy met the minimum quant score",
                "candidates": scored,
                "global_reject": False,
            }
        return {
            "strategy": str(best.get("strategy", "")),
            "score": float(best.get("score", 0.0) or 0.0),
            "allowed": True,
            "reason": str(best.get("reason", "")),
            "candidates": scored,
            "global_reject": False,
        }

    def validate_candidates(
        self,
        context: dict[str, Any],
        proposals: list[dict[str, Any]],
        regime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        market = _extract_market_context(context)
        regime_name = _regime_name(regime, market)
        features = _feature_snapshot(context)

        if _global_reject(features):
            rejected_candidates: list[dict[str, Any]] = []
            for idx, item in enumerate(proposals[:2]):
                strategy = canonical_strategy_name(str(item.get("strategy", "")))
                rejected_candidates.append(
                    {
                        "strategy": strategy,
                        "allowed": False,
                        "score": 0,
                        "rule_score": 0,
                        "fit_bonus": 0,
                        "reason": "Global reject triggered",
                        "notes": [],
                        "fit_notes": [],
                        "checks": [],
                        "ai_confidence": round(float(item.get("confidence", 0.5) or 0.5), 3),
                        "family": _strategy_family(strategy),
                        "rank": idx + 1,
                        "global_reject": True,
                    }
                )
            return {
                "allowed": False,
                "selected": None,
                "selected_strategy": "NO TRADE",
                "candidates": rejected_candidates,
                "candle_profile": _candle_profile(context),
                "feature_snapshot": features,
                "regime": regime_name,
                "global_reject": True,
            }

        scored: list[dict[str, Any]] = []
        for idx, item in enumerate(proposals[:2]):
            strategy = canonical_strategy_name(str(item.get("strategy", "")))
            ai_conf = float(item.get("confidence", 0.5) or 0.5)
            score_result = _score_strategy(strategy, features, regime_name)
            final_score = float(score_result.get("score", 0.0) or 0.0)
            allowed = final_score >= self.min_score and not bool(score_result.get("global_reject", False))
            scored.append(
                {
                    "strategy": strategy,
                    "allowed": allowed,
                    "score": final_score,
                    "rule_score": int(score_result.get("rule_score", 0) or 0),
                    "fit_bonus": int(score_result.get("fit_bonus", 0) or 0),
                    "reason": score_result["reason"],
                    "notes": score_result.get("notes", []),
                    "fit_notes": score_result.get("fit_notes", []),
                    "checks": score_result.get("checks", []),
                    "ai_confidence": round(ai_conf, 3),
                    "family": _strategy_family(strategy),
                    "rank": idx + 1,
                    "global_reject": False,
                }
            )

        valid = [row for row in scored if row["allowed"]]
        selected = max(valid, key=lambda row: row["score"], default=None)
        selected_strategy = str(selected.get("strategy", "NO TRADE")) if selected else "NO TRADE"
        return {
            "allowed": bool(selected),
            "selected": selected,
            "selected_strategy": selected_strategy,
            "candidates": scored,
            "candle_profile": _candle_profile(context),
            "feature_snapshot": features,
            "regime": regime_name,
            "global_reject": False,
        }

    def rank_candidate_payloads(
        self,
        context: dict[str, Any],
        proposals: list[dict[str, Any]],
        regime: dict[str, Any] | None = None,
        recent_performance: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        ranked = rank_strategy_candidates(
            _extract_market_context(context),
            regime=regime,
            recent_performance=recent_performance or [],
        )
        ranking_map = {str(row["strategy"]): row for row in ranked}
        out: list[dict[str, Any]] = []
        for item in proposals:
            strategy = canonical_strategy_name(str(item.get("strategy", "")))
            base = ranking_map.get(strategy, {})
            out.append(
                {
                    **item,
                    "strategy": strategy,
                    "rank_score": float(base.get("score", 0.0) or 0.0),
                }
            )
        return out
