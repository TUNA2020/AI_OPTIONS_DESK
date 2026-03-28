from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from database.sqlite_manager import SQLiteManager


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class StrategyOptimizer:
    db: SQLiteManager

    def run_day_end_optimization(self) -> list[dict[str, Any]]:
        rows = self.db.strategy_daily_pnls()
        scores: list[dict[str, Any]] = []
        for row in rows:
            avg_pnl = float(row.get("avg_pnl", 0.0))
            trades = int(row.get("trades_count", 0))
            win_rate = float(np.clip(0.5 + np.tanh(avg_pnl / 500.0) * 0.25, 0.0, 1.0))
            drawdown = float(abs(min(avg_pnl, 0.0)) * 1.8)
            sharpe = float((avg_pnl / max(drawdown, 1.0)) * np.sqrt(max(trades, 1)))
            rank_score = float((win_rate * 100.0) + sharpe - drawdown / 1000.0)
            item = {
                "strategy": str(row["strategy"]),
                "date": str(row.get("date", date.today().isoformat())),
                "trades_count": trades,
                "win_rate": win_rate,
                "avg_pnl": avg_pnl,
                "drawdown": drawdown,
                "sharpe": sharpe,
                "rank_score": rank_score,
            }
            self.db.upsert_strategy_performance(**item)
            scores.append(item)

        scores.sort(key=lambda x: x["rank_score"], reverse=True)
        LOGGER.info("Day-end optimization completed for %d strategies", len(scores))
        return scores
