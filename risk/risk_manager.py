from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from risk.monte_carlo import MonteCarloRiskEngine


@dataclass(slots=True)
class RiskManager:
    max_loss_per_trade: float
    monte_carlo_paths: int = 1000

    def evaluate_trade(self, context: dict[str, Any], trade_legs: list[dict[str, Any]]) -> dict[str, Any]:
        engine = MonteCarloRiskEngine(paths=self.monte_carlo_paths)
        stats = engine.simulate(context, trade_legs)
        max_loss = abs(self.max_loss_per_trade)
        # Use the 5th percentile rather than the absolute minimum to avoid rejecting based on extreme tail moves.
        percentile_p5 = float(stats.get("p5", stats.get("worst_case", 0.0)))
        allowed = percentile_p5 >= -max_loss
        reason = "Accepted"
        if not allowed:
            reason = (
                "Rejected: 5th percentile loss exceeds risk limit"
                if percentile_p5 < -max_loss
                else "Rejected: risk stats unavailable"
            )
        return {
            "allowed": allowed,
            "reason": reason,
            "stats": stats,
        }
