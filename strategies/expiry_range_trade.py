from __future__ import annotations

from strategies.short_strangle import Strategy as ShortStrangle


class Strategy:
    name = "expiry_range_trade"

    def build_trade(self, context, **kwargs):
        return ShortStrangle().build_trade(context, **kwargs)
