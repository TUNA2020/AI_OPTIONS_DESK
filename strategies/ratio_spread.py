from __future__ import annotations

from strategies.common import _nearest_symbol


class Strategy:
    name = "ratio_spread"

    def build_trade(self, context, **kwargs):
        chain = context["option_chain"]
        qty = int(context.get("lot_size", 50))
        spot = int(round(context["nifty_price"] / 50) * 50)
        return [
            {"symbol": _nearest_symbol(chain, spot, "CE"), "side": "BUY", "qty": qty},
            {
                "symbol": _nearest_symbol(chain, spot + 200, "CE"),
                "side": "SELL",
                "qty": qty * 2,
            },
        ]
