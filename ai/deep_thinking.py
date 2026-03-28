from __future__ import annotations

from typing import Any


def build_deep_context(
    current_context: dict[str, Any],
    candle_context: dict[str, Any] | None,
    recent_decisions: list[dict[str, Any]],
    recent_performance: list[dict[str, Any]],
    portfolio_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "current_market_context": current_context,
        "candle_context": candle_context or {},
        "recent_ai_decisions": recent_decisions[-10:],
        "recent_strategy_performance": recent_performance[-20:],
        "portfolio_state": portfolio_state or {},
        "instructions": (
            "Use today's 5-minute candles and the last 7 trading sessions as the main market inputs. "
            "Think over volatility regime, OI walls, IV skew, and risk budget, then propose the two best strategies with strike rationale. "
            "Consider current portfolio exposure (delta, vega, theta) to avoid over-concentration."
        ),
    }
