from __future__ import annotations

from data.oi_analysis import find_oi_wall
from strategies.common import _nearest_symbol


class Strategy:
    name = "bear_call_spread"

    def build_trade(self, context, width=200, **kwargs):
        chain = context["option_chain"]
        qty = int(context.get("lot_size", 50))
        short_strike = find_oi_wall(chain, "CE") or int(context["nifty_price"]) + 100
        long_strike = short_strike + width
        return [
            {
                "symbol": _nearest_symbol(chain, short_strike, "CE"),
                "side": "SELL",
                "qty": qty,
            },
            {
                "symbol": _nearest_symbol(chain, long_strike, "CE"),
                "side": "BUY",
                "qty": qty,
            },
        ]
