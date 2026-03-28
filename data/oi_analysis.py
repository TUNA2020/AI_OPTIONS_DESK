from __future__ import annotations

from typing import Any


def find_delta_strike(
    option_chain: list[dict[str, Any]], target_delta: float, option_type: str
) -> int | None:
    key = "ce_delta" if option_type.upper() == "CE" else "pe_delta"
    best_row = min(
        option_chain,
        key=lambda row: abs(abs(float(row.get(key, 0.0))) - abs(target_delta)),
        default=None,
    )
    return int(best_row["strike"]) if best_row else None


def find_oi_wall(option_chain: list[dict[str, Any]], side: str) -> int | None:
    if side.upper() == "CE":
        best = max(option_chain, key=lambda row: int(row.get("ce_oi", 0)), default=None)
    else:
        best = max(option_chain, key=lambda row: int(row.get("pe_oi", 0)), default=None)
    return int(best["strike"]) if best else None


def analyze_oi_structure(option_chain: list[dict[str, Any]]) -> dict[str, Any]:
    ce_wall = find_oi_wall(option_chain, "CE")
    pe_wall = find_oi_wall(option_chain, "PE")
    if not ce_wall or not pe_wall:
        return {"ce_wall": 0, "pe_wall": 0, "bias": "neutral"}

    if pe_wall > ce_wall:
        bias = "bullish"
    elif ce_wall > pe_wall:
        bias = "bearish"
    else:
        bias = "neutral"
    return {"ce_wall": ce_wall, "pe_wall": pe_wall, "bias": bias}
