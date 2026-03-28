from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np


STRIKE_RE = re.compile(r"(\d+)(CE|PE)$")


def parse_leg_details(symbol: str) -> tuple[int, str]:
    match = STRIKE_RE.search(symbol.upper())
    if not match:
        return 0, "CE"
    digits = match.group(1)
    # Trading symbols can include expiry digits before strike (e.g. NIFTY2631722200CE).
    # For fallback parsing, keep a realistic trailing strike window.
    if len(digits) > 6:
        digits = digits[-5:]
    return int(digits), match.group(2)

@dataclass(slots=True)
class MonteCarloRiskEngine:
    paths: int = 1000

    def simulate(self, context: dict[str, Any], trade_legs: list[dict[str, Any]]) -> dict[str, Any]:
        s0 = float(context.get("nifty_price", 0.0))
        if s0 <= 0:
            return {"worst_case": 0.0, "expected_pnl": 0.0, "p5": 0.0, "p95": 0.0}

        vix = max(float(context.get("vix", 15.0)), 1.0)
        annual_vol = vix / 100.0
        t = max(int(context.get("expiry_days", 1)), 1) / 365.0
        dt = t / 20
        drift = 0.00
        rng = np.random.default_rng()

        prices = np.full(self.paths, s0, dtype=float)
        for _ in range(20):
            z = rng.normal(0, 1, self.paths)
            prices *= np.exp((drift - 0.5 * annual_vol**2) * dt + annual_vol * np.sqrt(dt) * z)

        symbol_to_meta: dict[str, tuple[int, str, float]] = {}
        for row in context.get("option_chain", []):
            strike = int(row.get("strike", 0) or 0)
            ce_symbol = str(row.get("ce_symbol", ""))
            pe_symbol = str(row.get("pe_symbol", ""))
            if ce_symbol:
                symbol_to_meta[ce_symbol] = (strike, "CE", float(row.get("ce_ltp", 0.0)))
            if pe_symbol:
                symbol_to_meta[pe_symbol] = (strike, "PE", float(row.get("pe_ltp", 0.0)))

        pnl = np.zeros(self.paths, dtype=float)
        for leg in trade_legs:
            symbol = str(leg["symbol"])
            strike, option_type, premium = symbol_to_meta.get(symbol, (0, "CE", 0.0))
            if strike <= 0:
                strike, option_type = parse_leg_details(symbol)
            side = str(leg["side"]).upper()
            qty = int(leg["qty"])
            if option_type == "CE":
                intrinsic = np.maximum(0.0, prices - strike)
            else:
                intrinsic = np.maximum(0.0, strike - prices)
            leg_pnl = (premium - intrinsic) * qty if side == "SELL" else (intrinsic - premium) * qty
            pnl += leg_pnl

        return {
            "worst_case": float(np.min(pnl)),
            "expected_pnl": float(np.mean(pnl)),
            "p5": float(np.percentile(pnl, 5)),
            "p95": float(np.percentile(pnl, 95)),
        }
