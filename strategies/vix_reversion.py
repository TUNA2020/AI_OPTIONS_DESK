from __future__ import annotations

from strategies.common import build_short_legs


class Strategy:
    name = "vix_reversion"

    def build_trade(self, context, ce_delta=None, pe_delta=None, **kwargs):
        vix = float(context.get("vix", 0))
        if ce_delta is None or pe_delta is None:
            # Default logic based on VIX
            if vix > 18:
                ce_delta = 0.15
                pe_delta = 0.15
            else:
                ce_delta = 0.25
                pe_delta = 0.25
        return build_short_legs(context, ce_delta=ce_delta, pe_delta=pe_delta)
