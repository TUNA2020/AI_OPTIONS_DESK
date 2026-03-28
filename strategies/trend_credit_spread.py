from __future__ import annotations

from strategies.bear_call_spread import Strategy as BearCall
from strategies.bull_put_spread import Strategy as BullPut


class Strategy:
    name = "trend_credit_spread"

    def build_trade(self, context, **kwargs):
        trend = context.get("trend", "sideways")
        if trend == "uptrend":
            return BullPut().build_trade(context, **kwargs)
        if trend == "downtrend":
            return BearCall().build_trade(context, **kwargs)
        return BullPut().build_trade(context, **kwargs)
