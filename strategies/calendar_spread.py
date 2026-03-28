from __future__ import annotations

from strategies.common import _nearest_symbol


class Strategy:
    name = "calendar_spread"

    def build_trade(self, context, **kwargs):
        chain = context["option_chain"]
        qty = int(context.get("lot_size", 50))
        spot = int(round(context["nifty_price"] / 50) * 50)
        # Single-expiry approximation for framework consistency.
        return [
            {"symbol": _nearest_symbol(chain, spot, "CE"), "side": "BUY", "qty": qty},
            {"symbol": _nearest_symbol(chain, spot, "PE"), "side": "SELL", "qty": qty},
        ]
