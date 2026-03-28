from __future__ import annotations

from math import erf, exp, log, pi, sqrt
from typing import Any


def _normal_pdf(x: float) -> float:
    return exp(-0.5 * x * x) / sqrt(2 * pi)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))


def black_scholes_greeks(
    spot: float, strike: float, t: float, vol: float, r: float, option_type: str
) -> dict[str, float]:
    if min(spot, strike, t, vol) <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1 = (log(spot / strike) + (r + 0.5 * vol**2) * t) / (vol * sqrt(t))
    d2 = d1 - vol * sqrt(t)
    pdf = _normal_pdf(d1)
    if option_type.upper() == "CE":
        delta = _normal_cdf(d1)
        theta = (-(spot * pdf * vol) / (2 * sqrt(t))) - r * strike * exp(-r * t) * _normal_cdf(d2)
    else:
        delta = _normal_cdf(d1) - 1
        theta = (-(spot * pdf * vol) / (2 * sqrt(t))) + r * strike * exp(-r * t) * _normal_cdf(-d2)
    gamma = pdf / (spot * vol * sqrt(t))
    vega = (spot * pdf * sqrt(t)) / 100
    return {"delta": float(delta), "gamma": float(gamma), "theta": float(theta / 365), "vega": float(vega)}


def aggregate_greeks(context: dict[str, Any], legs: list[dict[str, Any]]) -> dict[str, float]:
    spot = float(context.get("nifty_price", 0.0))
    chain = context.get("option_chain", [])
    symbol_map = {
        str(r.get("ce_symbol")): (float(r.get("strike", 0)), float(r.get("ce_iv", 0.2)), "CE")
        for r in chain
    }
    symbol_map.update(
        {
            str(r.get("pe_symbol")): (float(r.get("strike", 0)), float(r.get("pe_iv", 0.2)), "PE")
            for r in chain
        }
    )
    t = max(float(context.get("expiry_days", 1)) / 365.0, 1 / 365)
    total = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for leg in legs:
        symbol = str(leg["symbol"])
        side = str(leg["side"]).upper()
        qty = int(leg["qty"])
        strike, vol, option_type = symbol_map.get(symbol, (spot, 0.2, "CE"))
        greeks = black_scholes_greeks(spot, strike, t, vol, 0.07, option_type)
        direction = -1 if side == "SELL" else 1
        for k in total:
            total[k] += greeks[k] * qty * direction
    return {k: float(v) for k, v in total.items()}
