from __future__ import annotations

from strategies.common import _nearest_symbol


class Strategy:
    name = "broken_wing_butterfly"

    def build_trade(self, context, **kwargs):
        chain = context["option_chain"]
        qty = int(context.get("lot_size", 50))
        spot = int(context["nifty_price"])
        center = int(round(spot / 50) * 50)
        return [
            {
                "symbol": _nearest_symbol(chain, center - 100, "PE"),
                "side": "BUY",
                "qty": qty,
            },
            {
                "symbol": _nearest_symbol(chain, center, "PE"),
                "side": "SELL",
                "qty": qty * 2,
            },
            {
                "symbol": _nearest_symbol(chain, center + 200, "PE"),
                "side": "BUY",
                "qty": qty,
            },
        ]
