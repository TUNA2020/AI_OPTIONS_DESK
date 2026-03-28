from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


def _get_ist_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=5, minutes=30), "IST")
    )


from typing import Any, Callable

from execution.kite_client import KiteClient


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PnLMonitor:
    kite_client: KiteClient
    profit_target: float
    stoploss: float
    force_exit_time: str = "14:45"
    mode: str = "paper"
    open_trades_provider: Callable[[], list[dict[str, Any]]] | None = None

    def current_pnl(self) -> float:
        if self.mode.lower() == "paper":
            return self._paper_pnl_from_open_trades()

        positions = self.kite_client.positions()
        if not positions:
            return 0.0
        symbols = [
            f"NFO:{p['tradingsymbol']}" for p in positions if p.get("tradingsymbol")
        ]
        quotes = self.kite_client.quote(symbols) if symbols else {}
        pnl = 0.0
        for p in positions:
            symbol = str(p.get("tradingsymbol", ""))
            qty = int(p.get("quantity", 0) or 0)
            if qty == 0:
                continue
            avg_price = float(p.get("average_price", 0.0))
            ltp = float(quotes.get(f"NFO:{symbol}", {}).get("last_price", avg_price))
            # Kite net positions use signed quantity: +long, -short.
            leg_pnl = (ltp - avg_price) * qty
            pnl += leg_pnl
        return float(pnl)

    def _paper_pnl_from_open_trades(self) -> float:
        if self.open_trades_provider is None:
            return 0.0
        open_trades = self.open_trades_provider()
        if not open_trades:
            return 0.0
        symbols = [f"NFO:{t['symbol']}" for t in open_trades if t.get("symbol")]
        quotes = self.kite_client.quote(symbols) if symbols else {}
        pnl = 0.0
        for trade in open_trades:
            symbol = str(trade.get("symbol", ""))
            side = str(trade.get("side", "BUY")).upper()
            qty = int(trade.get("qty", 0))
            entry_price = float(trade.get("price", 0.0))
            ltp = float(quotes.get(f"NFO:{symbol}", {}).get("last_price", entry_price))
            leg_pnl = (
                (entry_price - ltp) * qty
                if side == "SELL"
                else (ltp - entry_price) * qty
            )
            pnl += leg_pnl
        return float(pnl)

    def should_exit(self) -> tuple[bool, str]:
        pnl = self.current_pnl()
        if pnl >= self.profit_target:
            return True, f"Profit target reached: {pnl:.2f}"
        if pnl <= self.stoploss:
            return True, f"Stoploss hit: {pnl:.2f}"

        force_exit = datetime.strptime(self.force_exit_time, "%H:%M").time()
        if _get_ist_now().time() >= force_exit:
            return True, f"Force exit time reached: {force_exit.isoformat()}"
        return False, f"Running pnl={pnl:.2f}"
