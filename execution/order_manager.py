from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from database.sqlite_manager import SQLiteManager
from execution.kite_client import KiteClient


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class OrderManager:
    kite_client: KiteClient
    db: SQLiteManager
    mode: str
    product: str = "NRML"
    notifier: Any | None = None

    def _current_mode(self) -> str:
        return str(self.mode).lower().strip()

    def refresh_mode(self, mode: str | None = None) -> None:
        if mode:
            self.mode = str(mode).strip() or self.mode
        self.kite_client.set_mode(self.mode)

    def _notify(self, title: str, message: str, payload: dict[str, Any] | None = None) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.send(title, message, payload=payload)
        except Exception:
            LOGGER.exception("Notifier send failed for %s", title)

    def _validate_legs(self, strategy_name: str, legs: list[dict[str, Any]]) -> None:
        if not isinstance(legs, list) or not legs:
            raise ValueError(f"{strategy_name}: no legs to execute")
        seen_symbols: set[str] = set()
        for idx, leg in enumerate(legs):
            if not isinstance(leg, dict):
                raise ValueError(f"{strategy_name}: leg {idx + 1} is not an object")
            symbol = str(leg.get("symbol", "")).strip()
            side = str(leg.get("side", "")).upper().strip()
            qty = int(leg.get("qty", 0) or 0)
            if not symbol:
                raise ValueError(f"{strategy_name}: leg {idx + 1} is missing a symbol")
            if symbol in seen_symbols:
                raise ValueError(f"{strategy_name}: duplicate symbol detected: {symbol}")
            if side not in {"BUY", "SELL"}:
                raise ValueError(f"{strategy_name}: leg {idx + 1} has invalid side {side!r}")
            if qty <= 0:
                raise ValueError(f"{strategy_name}: leg {idx + 1} has invalid qty {qty}")
            seen_symbols.add(symbol)

    def execute_legs(self, strategy_name: str, legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.refresh_mode(self.mode)
        self._validate_legs(strategy_name, legs)
        results: list[dict[str, Any]] = []
        for leg in legs:
            symbol = str(leg["symbol"])
            side = str(leg["side"]).upper()
            qty = int(leg["qty"])
            try:
                self.db.insert_order_blotter_event(
                    strategy=strategy_name,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    status="SUBMITTED",
                    mode=self.mode,
                    message=f"Order submitted for {side} {symbol} x{qty}",
                    payload={"leg": leg},
                )
                if self._current_mode() != "live":
                    LOGGER.info("Paper mode: simulating order for %s %s x%s", side, symbol, qty)
                result = self.kite_client.place_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    product=self.product,
                )
                results.append(result)
                self.db.insert_trade(
                    strategy=strategy_name,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=float(result.get("average_price", 0.0)),
                    status="OPEN",
                    mode=self.mode,
                    metadata={"order_result": result},
                )
                self.db.insert_order_blotter_event(
                    strategy=strategy_name,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    status="FILLED",
                    mode=self.mode,
                    message=f"Order filled for {side} {symbol} x{qty}",
                    payload={"order_result": result},
                )
                self.db.insert_audit_event(
                    level="INFO",
                    event_type="order_placed",
                    message=f"Order placed {side} {symbol} x{qty} ({self.mode})",
                    payload={"strategy": strategy_name, "order_result": result},
                )
                self._notify(
                    "ORDER_PLACED",
                    f"{strategy_name}: {side} {symbol} x{qty} ({self.mode})",
                    payload={"order_result": result},
                )
            except Exception:
                self.db.insert_order_blotter_event(
                    strategy=strategy_name,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    status="REJECTED",
                    mode=self.mode,
                    message=f"Order placement failed for {side} {symbol} x{qty}",
                )
                self.db.insert_audit_event(
                    level="ERROR",
                    event_type="order_failed",
                    message=f"Order placement failed for {symbol}",
                )
                self._notify(
                    "ORDER_FAILED",
                    f"{strategy_name}: failed {side} {symbol} x{qty} ({self.mode})",
                )
                LOGGER.exception("Order placement failed for %s", symbol)
        return results

    def close_all_positions(self) -> None:
        self.refresh_mode(self.mode)
        self.close_positions(self.db.fetch_open_trades())

    def close_positions(self, trades: list[dict[str, Any]]) -> None:
        self.refresh_mode(self.mode)
        for trade in trades:
            symbol = str(trade["symbol"])
            side = "BUY" if trade["side"] == "SELL" else "SELL"
            qty = int(trade["qty"])
            try:
                self.db.insert_order_blotter_event(
                    strategy=str(trade["strategy"]),
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    status="SUBMITTED",
                    mode=self.mode,
                    message=f"Close submitted for {symbol}",
                    payload={"open_trade_id": trade["id"]},
                )
                quote = self.kite_client.quote([f"NFO:{symbol}"])
                close_price = float(quote.get(f"NFO:{symbol}", {}).get("last_price", 0.0))
                if close_price <= 0:
                    close_price = float(trade.get("price", 0.0))
                close_result = self.kite_client.close_position(symbol)
                close_status = str(close_result.get("status", "")).upper()
                if close_status not in {"CLOSED", "NO_POSITION"}:
                    raise RuntimeError(f"Unexpected close status for {symbol}: {close_status}")
                broker_price = float(close_result.get("average_price", 0.0) or 0.0)
                if broker_price > 0:
                    close_price = broker_price
                self.db.insert_trade(
                    strategy=str(trade["strategy"]),
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=close_price,
                    status="CLOSED",
                    mode=self.mode,
                    metadata={"closed_trade_id": trade["id"], "close_result": close_result},
                )
                self.db.mark_trade_closed(int(trade["id"]))
                self.db.insert_order_blotter_event(
                    strategy=str(trade["strategy"]),
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    status="CLOSED",
                    mode=self.mode,
                    message=f"Position closed for {symbol}",
                    payload={"close_result": close_result},
                )
                event_type = "position_closed" if close_status == "CLOSED" else "position_closed_no_position"
                message = (
                    f"Closed position for {symbol}"
                    if close_status == "CLOSED"
                    else f"No broker net position found for {symbol}; trade marked closed in DB"
                )
                self.db.insert_audit_event(
                    level="INFO",
                    event_type=event_type,
                    message=message,
                    payload={"trade_id": trade["id"], "close_price": close_price, "close_result": close_result},
                )
                self._notify(
                    "POSITION_CLOSED",
                    f"{trade['strategy']}: {symbol} x{qty} close={close_price:.2f} ({self.mode})",
                    payload={"close_result": close_result},
                )
            except Exception:
                self.db.insert_order_blotter_event(
                    strategy=str(trade["strategy"]),
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    status="REJECTED",
                    mode=self.mode,
                    message=f"Failed to close position for {symbol}",
                )
                self.db.insert_audit_event(
                    level="ERROR",
                    event_type="position_close_failed",
                    message=f"Failed to close position for {symbol}",
                )
                self._notify(
                    "POSITION_CLOSE_FAILED",
                    f"{trade['strategy']}: failed close {symbol} x{qty} ({self.mode})",
                )
                LOGGER.exception("Failed to close position for %s", symbol)
