from __future__ import annotations

from strategies.common import build_condor_legs


class Strategy:
    name = "iron_condor"

    def build_trade(self, context, width=200, **kwargs):
        return build_condor_legs(context, width=width)
