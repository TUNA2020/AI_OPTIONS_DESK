from __future__ import annotations

from data.oi_analysis import find_oi_wall
from strategies.common import _nearest_symbol


class Strategy:
    name = "oi_wall_strategy"

    def build_trade(self, context, **kwargs):
        chain = context["option_chain"]
        qty = int(context.get("lot_size", 50))
        ce_wall = find_oi_wall(chain, "CE") or int(context["nifty_price"]) + 150
        pe_wall = find_oi_wall(chain, "PE") or int(context["nifty_price"]) - 150
        return [
            {
                "symbol": _nearest_symbol(chain, ce_wall, "CE"),
                "side": "SELL",
                "qty": qty,
            },
            {
                "symbol": _nearest_symbol(chain, pe_wall, "PE"),
                "side": "SELL",
                "qty": qty,
            },
        ]
