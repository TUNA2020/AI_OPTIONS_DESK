from __future__ import annotations

from strategies.common import _nearest_symbol


class Strategy:
    name = "option_buying_vwap_put"

    def build_trade(self, context, **kwargs):
        chain = context["option_chain"]
        qty = int(context.get("lot_size", 50))
        spot = int(round(float(context["nifty_price"]) / 50.0) * 50)
        return [
            {"symbol": _nearest_symbol(chain, spot, "PE"), "side": "BUY", "qty": qty},
        ]
