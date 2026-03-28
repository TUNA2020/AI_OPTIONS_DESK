from __future__ import annotations

from typing import Any

import numpy as np

from risk.monte_carlo import parse_leg_details


def option_intrinsic(spot: float, strike: float, option_type: str) -> float:
    if strike <= 0:
        return 0.0
    option_type = str(option_type).upper()
    if option_type == "PE":
        return float(max(0.0, strike - spot))
    return float(max(0.0, spot - strike))


def generate_payoff_curve(
    context: dict[str, Any], legs: list[dict[str, Any]], points: int = 80
) -> list[dict[str, float]]:
    spot = float(context.get("nifty_price", 0.0))
    chain = context.get("option_chain", [])
    premium_map = {}
    for row in chain:
        premium_map[str(row.get("ce_symbol", ""))] = float(row.get("ce_ltp", 0.0))
        premium_map[str(row.get("pe_symbol", ""))] = float(row.get("pe_ltp", 0.0))

    prices = np.linspace(max(100, spot * 0.85), spot * 1.15, points)
    out: list[dict[str, float]] = []
    for p in prices:
        pnl = 0.0
        for leg in legs:
            strike, option_type = parse_leg_details(str(leg["symbol"]))
            side = str(leg["side"]).upper()
            qty = int(leg["qty"])
            premium = premium_map.get(str(leg["symbol"]), 0.0)
            intrinsic = option_intrinsic(float(p), strike, option_type)
            pnl += (premium - intrinsic) * qty if side == "SELL" else (intrinsic - premium) * qty
        out.append({"price": float(p), "pnl": float(pnl)})
    return out
