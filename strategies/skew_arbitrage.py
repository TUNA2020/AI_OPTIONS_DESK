from __future__ import annotations

import numpy as np

from strategies.common import _nearest_symbol


class Strategy:
    name = "skew_arbitrage"

    def build_trade(self, context, **kwargs):
        chain = context["option_chain"]
        qty = int(context.get("lot_size", 50))
        skew_values = [float(r["pe_iv"]) - float(r["ce_iv"]) for r in chain]
        if not skew_values:
            spot = int(context["nifty_price"])
            return [
                {
                    "symbol": _nearest_symbol(chain, spot, "CE"),
                    "side": "SELL",
                    "qty": qty,
                }
            ]
        idx = int(np.argmax(skew_values))
        row = chain[idx]
        return [
            {"symbol": str(row["pe_symbol"]), "side": "SELL", "qty": qty},
            {"symbol": str(row["ce_symbol"]), "side": "BUY", "qty": qty},
        ]
