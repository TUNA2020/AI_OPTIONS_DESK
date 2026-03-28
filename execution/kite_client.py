from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta


def _get_ist_time() -> datetime:
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=5, minutes=30), "IST")
    )


from typing import Any

from core.retry import retry
from execution.token_manager import KiteTokenManager
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from kiteconnect import KiteConnect
except Exception:  # pragma: no cover - optional during local setup
    KiteConnect = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class KiteClient:
    settings: dict[str, Any]
    mode: str = field(init=False)
    _kite: Any = field(init=False, default=None)
    _paper_order_counter: itertools.count = field(
        init=False, default_factory=lambda: itertools.count(1)
    )
    _paper_positions: list[dict[str, Any]] = field(init=False, default_factory=list)
    _token_manager: KiteTokenManager | None = field(init=False, default=None)
    _instrument_cache_by_exchange: dict[str, list[dict[str, Any]]] = field(
        init=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        self.mode = self.settings["app"]["mode"].lower()
        if KiteConnect is None:
            raise RuntimeError(
                "kiteconnect package is required for both paper/live mode market data."
            )
        base_dir = self.settings.get("__base_dir__")
        if not base_dir:
            raise RuntimeError("Internal error: missing __base_dir__ in settings.")
        self._token_manager = KiteTokenManager(self.settings, base_dir=base_dir)
        access_token = self._token_manager.ensure_access_token(force_refresh=False)
        self._kite = self._build_kite_client(access_token)
        LOGGER.info("Kite client initialized in %s mode", self.mode)

    def set_mode(self, mode: str) -> None:
        self.mode = str(mode).lower().strip() or self.mode

    def _build_kite_client(self, access_token: str | None = None) -> Any:
        kite = KiteConnect(api_key=self.settings["kite"]["api_key"])
        if access_token:
            kite.set_access_token(access_token)
        retry_cfg = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST", "DELETE"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_cfg, pool_connections=5, pool_maxsize=10)
        if hasattr(kite, "reqsession"):
            kite.reqsession.mount("https://", adapter)
            kite.reqsession.mount("http://", adapter)
        return kite

    def _refresh_kite_client(self) -> None:
        if self._token_manager is None:
            return
        token = self._token_manager.ensure_access_token(force_refresh=False)
        self._kite = self._build_kite_client(token)

    def _call_with_recovery(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, 3):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                text = str(exc).lower()
                recoverable = (
                    "sslv3_alert_bad_record_mac" in text
                    or "sslerror" in text
                    or "connectionerror" in text
                    or "max retries exceeded" in text
                    or "read timed out" in text
                    or "tokenexception" in text
                    or "invalid" in text
                    or "expired" in text
                )
                if attempt == 1 and recoverable:
                    LOGGER.warning("Kite API call failed; refreshing client and retrying: %s", exc)
                    self._refresh_kite_client()
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Kite API call failed without an exception.")

    @retry(attempts=3, delay_seconds=1)
    def quote(self, symbols: list[str]) -> dict[str, Any]:
        return self._call_with_recovery(self._kite.quote, symbols)

    @retry(attempts=3, delay_seconds=1)
    def historical_data(
        self,
        instrument_token: int,
        from_dt: datetime,
        to_dt: datetime,
        interval: str = "5minute",
    ) -> list[dict[str, Any]]:
        return self._call_with_recovery(
            self._kite.historical_data,
            instrument_token=instrument_token,
            from_date=from_dt,
            to_date=to_dt,
            interval=interval,
        )

    @retry(attempts=3, delay_seconds=1)
    def positions(self) -> list[dict[str, Any]]:
        if self.mode == "live":
            pos = self._call_with_recovery(self._kite.positions)
            return pos.get("net", [])
        return list(self._paper_positions)

    @retry(attempts=3, delay_seconds=1)
    def instruments(self, exchange: str = "NFO") -> list[dict[str, Any]]:
        exchange_upper = exchange.upper()
        if exchange_upper not in self._instrument_cache_by_exchange:
            self._instrument_cache_by_exchange[exchange_upper] = self._call_with_recovery(
                self._kite.instruments,
                exchange_upper,
            )
        return list(self._instrument_cache_by_exchange[exchange_upper])

    @retry(attempts=3, delay_seconds=1)
    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str = "MARKET",
        product: str = "NRML",
    ) -> dict[str, Any]:
        side = side.upper()
        if self.mode == "live":
            transaction_type = "BUY" if side == "BUY" else "SELL"
            order_id = self._call_with_recovery(
                self._kite.place_order,
                variety="regular",
                exchange="NFO",
                tradingsymbol=symbol,
                transaction_type=transaction_type,
                quantity=qty,
                order_type=order_type,
                product=product,
            )
            quote = self.quote([f"NFO:{symbol}"])
            market_price = float(quote.get(f"NFO:{symbol}", {}).get("last_price", 0.0))
            return {
                "order_id": order_id,
                "status": "OPEN",
                "average_price": market_price,
            }

        order_id = f"PAPER-{next(self._paper_order_counter)}"
        quote_key = f"NFO:{symbol}"
        quote = self.quote([quote_key])
        quote_row = quote.get(quote_key, {})
        price = float(quote_row.get("last_price", 0.0) or 0.0)
        if price <= 0:
            raise RuntimeError(
                f"Invalid quote for paper order {quote_key}: {quote_row}"
            )
        self._paper_positions.append(
            {
                "order_id": order_id,
                "tradingsymbol": symbol,
                "transaction_type": side,
                "quantity": qty,
                "average_price": price,
                "timestamp": _get_ist_time().isoformat(),
            }
        )
        return {"order_id": order_id, "status": "OPEN", "average_price": price}

    @retry(attempts=3, delay_seconds=1)
    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if self.mode == "live":
            self._call_with_recovery(
                self._kite.cancel_order,
                variety="regular",
                order_id=order_id,
            )
            return {"order_id": order_id, "status": "CANCELLED"}
        return {"order_id": order_id, "status": "CANCELLED"}

    @retry(attempts=3, delay_seconds=1)
    def close_position(self, symbol: str) -> dict[str, Any]:
        if self.mode == "live":
            net_qty = 0
            product = "NRML"
            for position in self.positions():
                if str(position.get("tradingsymbol", "")) != symbol:
                    continue
                qty = int(position.get("quantity", 0) or 0)
                net_qty += qty
                if str(position.get("product", "")).strip():
                    product = str(position.get("product"))

            if net_qty == 0:
                return {"symbol": symbol, "status": "NO_POSITION"}

            transaction_type = "SELL" if net_qty > 0 else "BUY"
            qty_to_close = abs(net_qty)
            order_id = self._call_with_recovery(
                self._kite.place_order,
                variety="regular",
                exchange="NFO",
                tradingsymbol=symbol,
                transaction_type=transaction_type,
                quantity=qty_to_close,
                order_type="MARKET",
                product=product,
            )
            quote = self.quote([f"NFO:{symbol}"])
            market_price = float(quote.get(f"NFO:{symbol}", {}).get("last_price", 0.0))
            return {
                "symbol": symbol,
                "status": "CLOSED",
                "order_id": order_id,
                "side": transaction_type,
                "qty": qty_to_close,
                "average_price": market_price,
            }

        self._paper_positions = [
            p for p in self._paper_positions if p["tradingsymbol"] != symbol
        ]
        return {"symbol": symbol, "status": "CLOSED"}

    def refresh_session(self, force: bool = True) -> None:
        if self._token_manager is None:
            return
        token = self._token_manager.ensure_access_token(force_refresh=force)
        self._kite = self._build_kite_client(token)
        LOGGER.info("Kite session refreshed.")
