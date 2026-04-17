from __future__ import annotations

import re
from statistics import median
from typing import Any

from strategies.bear_call_spread import Strategy as BearCallSpread
from strategies.broken_wing_butterfly import Strategy as BrokenWingButterfly
from strategies.bull_put_spread import Strategy as BullPutSpread
from strategies.calendar_spread import Strategy as CalendarSpread
from strategies.delta_neutral_condor import Strategy as DeltaNeutralCondor
from strategies.expiry_range_trade import Strategy as ExpiryRangeTrade
from strategies.gamma_scalping import Strategy as GammaScalping
from strategies.iron_condor import Strategy as IronCondor
from strategies.momentum_volatility import Strategy as MomentumVolatility
from strategies.oi_wall_strategy import Strategy as OIWallStrategy
from strategies.option_buying_vwap_put import Strategy as OptionBuyingVwapPut
from strategies.ratio_spread import Strategy as RatioSpread
from strategies.short_strangle import Strategy as ShortStrangle
from strategies.skew_arbitrage import Strategy as SkewArbitrage
from strategies.trend_credit_spread import Strategy as TrendCreditSpread
from strategies.vix_reversion import Strategy as VIXReversion


STRATEGY_REGISTRY = {
    "short_strangle": ShortStrangle,
    "iron_condor": IronCondor,
    "bull_put_spread": BullPutSpread,
    "bear_call_spread": BearCallSpread,
    "broken_wing_butterfly": BrokenWingButterfly,
    "calendar_spread": CalendarSpread,
    "ratio_spread": RatioSpread,
    "gamma_scalping": GammaScalping,
    "vix_reversion": VIXReversion,
    "oi_wall_strategy": OIWallStrategy,
    "trend_credit_spread": TrendCreditSpread,
    "delta_neutral_condor": DeltaNeutralCondor,
    "skew_arbitrage": SkewArbitrage,
    "expiry_range_trade": ExpiryRangeTrade,
    "momentum_volatility": MomentumVolatility,
    "option_buying_vwap_put": OptionBuyingVwapPut,
}

# Default strike selection parameters for each strategy (tunable by AI)
STRATEGY_DEFAULTS: dict[str, dict[str, Any]] = {
    "short_strangle": {"ce_delta": 0.20, "pe_delta": 0.20},
    "iron_condor": {"width": 200},
    "delta_neutral_condor": {"width": 150},
    "bull_put_spread": {"width": 200},  # spread width between short and long PE
    "bear_call_spread": {"width": 200},  # spread width between short and long CE
    "vix_reversion": {
        "ce_delta": 0.20,
        "pe_delta": 0.20,
    },  # actual logic uses 0.15 if vix>18 else 0.25
    "oi_wall_strategy": {},  # uses OI walls directly
    "gamma_scalping": {},  # buys ATM
    "ratio_spread": {},  # ATM + 200 for short leg
    "calendar_spread": {},  # ATM approximations
    "broken_wing_butterfly": {},  # fixed offsets
    "skew_arbitrage": {},  # max skew strike
    "trend_credit_spread": {},  # delegates to bull_put or bear_call
    "expiry_range_trade": {},  # delegates to short_strangle
    "momentum_volatility": {},  # delegates to gamma_scalping or trend_credit
    "option_buying_vwap_put": {},  # ATM PE single-leg buy
}


def get_strategy_default_params(strategy_name: str) -> dict[str, Any]:
    """Return a copy of default strike selection parameters for a strategy."""
    canonical = canonical_strategy_name(strategy_name)
    if not canonical:
        return {}
    return STRATEGY_DEFAULTS.get(canonical, {}).copy()


STRATEGY_ALIASES = {
    "short_strangle": "short_strangle",
    "strangle": "short_strangle",
    "iron_condor": "iron_condor",
    "iron condor": "iron_condor",
    "iron_condor_short_otm_call_put": "iron_condor",
    "iron_condor_short_otm_call_and_put": "iron_condor",
    "iron_condor_short_otm": "iron_condor",
    "bull_put_spread": "bull_put_spread",
    "bull put spread": "bull_put_spread",
    "bull_put": "bull_put_spread",
    "bear_call_spread": "bear_call_spread",
    "bear call spread": "bear_call_spread",
    "bear_call": "bear_call_spread",
    "broken_wing_butterfly": "broken_wing_butterfly",
    "broken wing butterfly": "broken_wing_butterfly",
    "bwb": "broken_wing_butterfly",
    "calendar_spread": "calendar_spread",
    "calendar spread": "calendar_spread",
    "ratio_spread": "ratio_spread",
    "ratio spread": "ratio_spread",
    "gamma_scalping": "gamma_scalping",
    "gamma scalping": "gamma_scalping",
    "vix_reversion": "vix_reversion",
    "vix reversion": "vix_reversion",
    "oi_wall_strategy": "oi_wall_strategy",
    "oi wall strategy": "oi_wall_strategy",
    "trend_credit_spread": "trend_credit_spread",
    "trend credit spread": "trend_credit_spread",
    "delta_neutral_condor": "delta_neutral_condor",
    "delta neutral condor": "delta_neutral_condor",
    "skew_arbitrage": "skew_arbitrage",
    "skew arbitrage": "skew_arbitrage",
    "expiry_range_trade": "expiry_range_trade",
    "expiry range trade": "expiry_range_trade",
    "range_trade": "expiry_range_trade",
    "momentum_volatility": "momentum_volatility",
    "momentum volatility": "momentum_volatility",
    "option_buying_vwap_put": "option_buying_vwap_put",
    "option buying vwap put": "option_buying_vwap_put",
    "atm_put_buy": "option_buying_vwap_put",
}

REGIME_PRIORS = {
    "range": [
        "iron_condor",
        "delta_neutral_condor",
        "short_strangle",
        "vix_reversion",
        "expiry_range_trade",
    ],
    "sideways": [
        "iron_condor",
        "delta_neutral_condor",
        "short_strangle",
        "vix_reversion",
        "expiry_range_trade",
    ],
    "volatile": [
        "iron_condor",
        "delta_neutral_condor",
        "broken_wing_butterfly",
        "vix_reversion",
        "short_strangle",
    ],
    "trend": [
        "trend_credit_spread",
        "momentum_volatility",
        "bull_put_spread",
        "bear_call_spread",
        "iron_condor",
    ],
}


def _normalize_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def canonical_strategy_name(name: str) -> str:
    normalized = _normalize_key(name)
    if normalized in STRATEGY_REGISTRY:
        return normalized
    if normalized in STRATEGY_ALIASES:
        return STRATEGY_ALIASES[normalized]
    for alias, canonical in STRATEGY_ALIASES.items():
        alias_key = _normalize_key(alias)
        if alias_key and alias_key in normalized:
            return canonical
    if "iron_condor" in normalized:
        return "iron_condor"
    if "strangle" in normalized:
        return "short_strangle"
    if "bull_put" in normalized or ("bull" in normalized and "put" in normalized):
        return "bull_put_spread"
    if "bear_call" in normalized or ("bear" in normalized and "call" in normalized):
        return "bear_call_spread"
    if "calendar" in normalized:
        return "calendar_spread"
    if "ratio" in normalized:
        return "ratio_spread"
    if "gamma" in normalized:
        return "gamma_scalping"
    if "vix" in normalized:
        return "vix_reversion"
    if "momentum" in normalized:
        return "momentum_volatility"
    return ""


def _extract_market_context(context: dict[str, Any]) -> dict[str, Any]:
    nested = context.get("current_market_context")
    if isinstance(nested, dict) and nested:
        return nested
    nested = context.get("market_context")
    if isinstance(nested, dict) and nested:
        return nested
    return context


def _recent_performance_lookup(
    recent_performance: list[dict[str, Any]],
) -> dict[str, float]:
    """
    Compute exponentially weighted average rank_score for each strategy.
    Weights decay with age: weight = exp(-0.2 * days_ago), giving more importance to recent performance.
    Considers up to last 20 days of data.
    """
    import math
    from datetime import datetime

    grouped: dict[str, list[tuple[float, datetime]]] = {}
    for row in recent_performance or []:
        strategy = canonical_strategy_name(str(row.get("strategy", "")))
        if not strategy:
            continue
        try:
            rank_score = float(row.get("rank_score", 0.0))
        except (ValueError, TypeError):
            continue
        date_val = row.get("date")
        if not date_val:
            continue
        try:
            # Parse ISO date string (e.g., "2024-03-27" or "2024-03-27T10:00:00")
            if isinstance(date_val, str):
                date_str = date_val.split("T")[0]
                row_date = datetime.fromisoformat(date_str)
            elif isinstance(date_val, datetime):
                row_date = date_val
            else:
                continue
        except Exception:
            continue
        grouped.setdefault(strategy, []).append((rank_score, row_date))

    lookup: dict[str, float] = {}
    today = datetime.now()
    decay_lambda = 0.2  # weight halves every ~3.5 days

    for strategy, records in grouped.items():
        if not records:
            continue
        # Sort by date descending (most recent first)
        records.sort(key=lambda x: x[1], reverse=True)
        # Take up to last 20 records
        recent = records[:20]
        weighted_sum = 0.0
        weight_sum = 0.0
        for score, dt in recent:
            days_ago = max(0, (today - dt).days)
            weight = math.exp(-decay_lambda * days_ago)
            weighted_sum += score * weight
            weight_sum += weight
        if weight_sum > 0:
            lookup[strategy] = round(weighted_sum / weight_sum, 3)
    return lookup


def rank_strategy_candidates(
    context: dict[str, Any],
    regime: dict[str, Any] | None = None,
    recent_performance: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    market = _extract_market_context(context)
    regime_name = (
        str((regime or {}).get("regime") or market.get("model_regime") or "range")
        .lower()
        .strip()
    )
    trend = str(market.get("trend", "")).lower().strip()
    bias = str((market.get("oi_analysis") or {}).get("bias", "")).lower().strip()
    vix = float(market.get("vix", 0.0) or 0.0)

    candidates: list[str] = []
    if regime_name in {"trend", "trending"}:
        candidates.extend(REGIME_PRIORS["trend"])
    elif regime_name in {"volatile", "high_volatility"}:
        candidates.extend(REGIME_PRIORS["volatile"])
    else:
        candidates.extend(REGIME_PRIORS["range"])

    if trend == "uptrend" or bias == "bullish":
        candidates = [
            "bull_put_spread",
            "trend_credit_spread",
            "momentum_volatility",
        ] + candidates
    elif trend == "downtrend" or bias == "bearish":
        candidates = [
            "bear_call_spread",
            "trend_credit_spread",
            "momentum_volatility",
        ] + candidates

    if vix >= 22.0:
        candidates = [
            "iron_condor",
            "delta_neutral_condor",
            "broken_wing_butterfly",
            "vix_reversion",
        ] + candidates
    elif vix <= 14.0:
        candidates = [
            "trend_credit_spread",
            "bull_put_spread",
            "bear_call_spread",
        ] + candidates

    ordered: list[str] = []
    seen: set[str] = set()
    for name in candidates:
        canonical = canonical_strategy_name(name)
        if canonical not in seen:
            seen.add(canonical)
            ordered.append(canonical)

    perf_lookup = _recent_performance_lookup(recent_performance or [])
    ranked: list[dict[str, Any]] = []
    for idx, name in enumerate(ordered):
        base_score = float((len(ordered) - idx) * 10.0)
        perf = perf_lookup.get(name, 50.0)
        perf_bias = float((perf - 50.0) / 8.0)
        ranked.append(
            {
                "strategy": name,
                "score": round(base_score + perf_bias, 3),
                "perf_score": round(float(perf), 3),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def best_strategy_for_context(
    context: dict[str, Any],
    regime: dict[str, Any] | None = None,
    recent_performance: list[dict[str, Any]] | None = None,
) -> str:
    ranked = rank_strategy_candidates(
        context, regime=regime, recent_performance=recent_performance
    )
    if ranked:
        return str(ranked[0]["strategy"])
    return ""


def get_strategy(name: str):
    canonical = canonical_strategy_name(name)
    if not canonical:
        raise ValueError(f"Unknown strategy name: {name}")
    strategy_cls = STRATEGY_REGISTRY.get(canonical)
    if strategy_cls is None:
        raise ValueError(f"Unsupported strategy: {name}")
    return strategy_cls()


def _nearest_symbol(
    context: dict[str, Any], strike: int, option_type: str
) -> str | None:
    chain = context.get("option_chain", [])
    if not chain:
        return None
    key = "ce_symbol" if option_type == "CE" else "pe_symbol"
    row = min(chain, key=lambda r: abs(int(r["strike"]) - strike))
    return str(row.get(key))


def _option_type_from_symbol(symbol: str) -> str | None:
    match = re.search(r"(CE|PE)$", symbol.upper())
    return match.group(1) if match else None


def _symbol_strike(symbol: str) -> int | None:
    match = re.search(r"(\d+)(?:CE|PE)$", symbol.upper())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _available_strikes(context: dict[str, Any], option_type: str) -> list[int]:
    chain = context.get("option_chain", [])
    strikes: list[int] = []
    key = "ce_symbol" if option_type == "CE" else "pe_symbol"
    for row in chain:
        if key not in row:
            continue
        try:
            strike = int(float(row.get("strike", 0) or 0))
        except Exception:
            continue
        if strike > 0:
            strikes.append(strike)
    return sorted(set(strikes))


def _shift_strike(context: dict[str, Any], strike: int, option_type: str, side: str) -> int:
    strikes = _available_strikes(context, option_type)
    if not strikes:
        return int(strike)
    side = str(side).upper()
    ordered = sorted(strikes)
    if option_type == "CE":
        candidates = [value for value in ordered if value >= strike] if side == "BUY" else [value for value in ordered if value <= strike]
        if candidates:
            return min(candidates) if side == "BUY" else max(candidates)
    else:
        candidates = [value for value in ordered if value <= strike] if side == "BUY" else [value for value in ordered if value >= strike]
        if candidates:
            return max(candidates) if side == "BUY" else min(candidates)

    chosen = min(ordered, key=lambda value: (abs(value - strike), value))
    if chosen == strike and len(ordered) > 1:
        alternatives = [value for value in ordered if value != strike]
        if alternatives:
            if option_type == "CE":
                return min(alternatives) if side == "BUY" else max(alternatives)
            return max(alternatives) if side == "BUY" else min(alternatives)
    return chosen


def _apply_strike_plan(
    legs: list[dict[str, Any]],
    decision: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    strike_plan = decision.get("strike_plan")
    if not isinstance(strike_plan, dict) or not strike_plan:
        return legs

    updated: list[dict[str, Any]] = []
    for leg in legs:
        symbol = str(leg.get("symbol", ""))
        side = str(leg.get("side", "")).upper()
        option_type = _option_type_from_symbol(symbol)
        if not option_type:
            updated.append(dict(leg))
            continue

        target_strike: Any = None
        option_plan = strike_plan.get(option_type)
        if isinstance(option_plan, dict):
            target_strike = option_plan.get(side) or option_plan.get("strike") or option_plan.get("default")
        if target_strike is None:
            target_strike = decision.get(f"{option_type.lower()}_strike")

        try:
            target_int = int(float(target_strike))
        except Exception:
            target_int = 0

        if target_int > 0:
            resolved = _nearest_symbol(context, target_int, option_type)
            if resolved:
                symbol = resolved
        updated.append({**leg, "symbol": symbol})

    return _ensure_unique_leg_symbols(updated, context)


def _ensure_unique_leg_symbols(
    legs: list[dict[str, Any]],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    updated: list[dict[str, Any]] = []
    for leg in legs:
        symbol = str(leg.get("symbol", ""))
        option_type = _option_type_from_symbol(symbol)
        side = str(leg.get("side", "")).upper()
        if symbol and symbol in seen and option_type:
            strike = _symbol_strike(symbol)
            if strike is not None:
                shifted = _shift_strike(context, strike, option_type, side)
                resolved = _nearest_symbol(context, shifted, option_type)
                if resolved:
                    symbol = resolved
        if symbol:
            seen.add(symbol)
        updated.append({**leg, "symbol": symbol})
    return updated


def _apply_strike_overrides(
    legs: list[dict[str, Any]], decision: dict[str, Any], context: dict[str, Any]
) -> list[dict[str, Any]]:
    ce_override = int(decision.get("ce_strike", 0) or 0)
    pe_override = int(decision.get("pe_strike", 0) or 0)
    updated: list[dict[str, Any]] = []
    for leg in legs:
        symbol = str(leg["symbol"])
        side = str(leg["side"]).upper()
        option_type = _option_type_from_symbol(symbol)
        if side == "SELL" and option_type == "CE" and ce_override:
            symbol = _nearest_symbol(context, ce_override, "CE") or symbol
        if side == "SELL" and option_type == "PE" and pe_override:
            symbol = _nearest_symbol(context, pe_override, "PE") or symbol
        updated.append({**leg, "symbol": symbol})
    return _ensure_unique_leg_symbols(updated, context)


def build_trade_from_decision(
    decision: dict[str, Any], context: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    strategy_name = canonical_strategy_name(str(decision.get("strategy", "")))
    if not strategy_name:
        raise ValueError("Unable to resolve a strategy from the AI proposal.")
    strategy = get_strategy(strategy_name)

    # Get strategy default parameters
    params = get_strategy_default_params(strategy_name)

    # Apply AI param-based overrides (preferred method)
    override_keys = [
        "ce_delta",
        "pe_delta",
        "width",
        "atm_offset_ce",
        "atm_offset_pe",
        "delta_target_ce",
        "delta_target_pe",
        "strike_offset",
    ]
    for key in override_keys:
        if key in decision:
            params[key] = decision[key]

    # Build trade with parameters
    legs = strategy.build_trade(context, **params)

    legs = _apply_strike_plan(legs, decision, context)

    # Legacy: Apply explicit strike overrides if provided (direct strike control)
    if "ce_strike" in decision or "pe_strike" in decision:
        legs = _apply_strike_overrides(legs, decision, context)

    legs = _ensure_unique_leg_symbols(legs, context)

    return strategy.name, legs
