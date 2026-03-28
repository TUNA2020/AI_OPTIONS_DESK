from __future__ import annotations

from typing import Any

from data.oi_analysis import find_delta_strike, find_oi_wall


def _nearest_symbol(option_chain: list[dict[str, Any]], strike: int, option_type: str) -> str:
    key = "ce_symbol" if option_type == "CE" else "pe_symbol"
    row = min(option_chain, key=lambda r: abs(int(r["strike"]) - strike))
    return str(row[key])


def build_short_legs(context: dict[str, Any], ce_delta: float = 0.2, pe_delta: float = 0.2) -> list[dict[str, Any]]:
    chain = context["option_chain"]
    qty = int(context.get("lot_size", 50))
    ce_strike = find_delta_strike(chain, ce_delta, "CE") or int(context["nifty_price"]) + 200
    pe_strike = find_delta_strike(chain, pe_delta, "PE") or int(context["nifty_price"]) - 200
    return [
        {"symbol": _nearest_symbol(chain, ce_strike, "CE"), "side": "SELL", "qty": qty},
        {"symbol": _nearest_symbol(chain, pe_strike, "PE"), "side": "SELL", "qty": qty},
    ]


def build_condor_legs(context: dict[str, Any], width: int = 200) -> list[dict[str, Any]]:
    chain = context["option_chain"]
    qty = int(context.get("lot_size", 50))
    ce_wall = find_oi_wall(chain, "CE") or int(context["nifty_price"]) + 100
    pe_wall = find_oi_wall(chain, "PE") or int(context["nifty_price"]) - 100
    return [
        {"symbol": _nearest_symbol(chain, ce_wall, "CE"), "side": "SELL", "qty": qty},
        {"symbol": _nearest_symbol(chain, ce_wall + width, "CE"), "side": "BUY", "qty": qty},
        {"symbol": _nearest_symbol(chain, pe_wall, "PE"), "side": "SELL", "qty": qty},
        {"symbol": _nearest_symbol(chain, pe_wall - width, "PE"), "side": "BUY", "qty": qty},
    ]
