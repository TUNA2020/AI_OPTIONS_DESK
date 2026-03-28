from __future__ import annotations

from strategies.common import build_short_legs


class Strategy:
    name = "short_strangle"

    def build_trade(self, context, ce_delta=0.20, pe_delta=0.20, **kwargs):
        return build_short_legs(context, ce_delta=ce_delta, pe_delta=pe_delta)
