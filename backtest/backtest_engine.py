from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ai.strategy_generator import get_strategy


def _compute_drawdown(equity_curve: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity_curve)
    dd = (equity_curve - peak) / np.maximum(peak, 1e-9)
    return float(abs(np.min(dd)))


@dataclass(slots=True)
class BacktestEngine:
    def run(self, strategy_name: str, historical_df: pd.DataFrame) -> dict[str, float]:
        strategy = get_strategy(strategy_name)
        if historical_df.empty:
            return {"win_rate": 0.0, "drawdown": 0.0, "sharpe": 0.0}

        pnls = []
        for _, row in historical_df.iterrows():
            spot = float(row["close"])
            context = {
                "nifty_price": spot,
                "vix": float(row.get("vix", 14)),
                "atr": float(row.get("atr", 120)),
                "trend": "uptrend" if spot >= float(row["open"]) else "downtrend",
                "trend_strength": float(row.get("trend_strength", 2)),
                "option_chain": self._mock_option_chain(spot),
                "lot_size": 50,
            }
            _ = strategy.build_trade(context)
            # Lightweight proxy PnL model for framework backtests.
            move = float(row["close"] - row["open"])
            vol = float(row.get("vix", 14)) / 20
            pnl = (move * 10) - abs(vol * 20)
            pnls.append(pnl)

        pnl_arr = np.array(pnls, dtype=float)
        if pnl_arr.size == 0:
            return {"win_rate": 0.0, "drawdown": 0.0, "sharpe": 0.0}
        win_rate = float(np.mean(pnl_arr > 0))
        equity = np.cumsum(pnl_arr)
        drawdown = _compute_drawdown(equity)
        sharpe = float((np.mean(pnl_arr) / max(np.std(pnl_arr), 1e-9)) * np.sqrt(252))
        return {"win_rate": win_rate, "drawdown": drawdown, "sharpe": sharpe}

    def _mock_option_chain(self, spot: float) -> list[dict[str, Any]]:
        atm = int(round(spot / 50) * 50)
        rows: list[dict[str, Any]] = []
        for offset in range(-10, 11):
            strike = atm + offset * 50
            rows.append(
                {
                    "strike": strike,
                    "ce_symbol": f"NIFTY26MAR{strike}CE",
                    "pe_symbol": f"NIFTY26MAR{strike}PE",
                    "ce_oi": 100000 + abs(offset) * 1000,
                    "pe_oi": 100000 + abs(offset) * 1200,
                    "ce_iv": 0.16,
                    "pe_iv": 0.17,
                    "ce_delta": 0.5 - (offset * 0.03),
                    "pe_delta": -0.5 - (offset * 0.03),
                    "volume": 10000,
                    "ce_ltp": max(5, spot - strike + 20),
                    "pe_ltp": max(5, strike - spot + 20),
                }
            )
        return rows
