from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.market_hours import market_session_status
from execution.kite_client import KiteClient

try:
    from kiteconnect import KiteTicker
except Exception:  # pragma: no cover
    KiteTicker = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)


TickHandler = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class WebSocketStream:
    settings: dict[str, Any]
    kite_client: KiteClient | None = None
    on_tick: TickHandler | None = None
    _running: bool = field(init=False, default=False)
    _thread: threading.Thread | None = field(init=False, default=None)
    _ticker: Any = field(init=False, default=None)
    _token_symbol_map: dict[int, str] = field(init=False, default_factory=dict)
    _latest_spot_price: float = field(init=False, default=0.0)
    _latest_spot_volume: int = field(init=False, default=0)
    _latest_vix: float = field(init=False, default=0.0)
    _after_hours_seeded: bool = field(init=False, default=False)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        mode = self.settings["app"]["mode"].lower()
        if mode == "live" and KiteTicker is not None:
            self._start_live()
            return
        self._thread = threading.Thread(target=self._start_paper_loop, daemon=True)
        self._thread.start()
        LOGGER.info("Paper WebSocket stream started")

    def _start_live(self) -> None:
        api_key = self.settings["kite"]["api_key"]
        access_token = self.settings["kite"]["access_token"]
        self._ticker = KiteTicker(api_key, access_token)
        spot_symbol = self._normalize_spot_symbol(str(self.settings["app"]["symbol"]))
        spot_token = self._resolve_nse_token(spot_symbol)
        vix_token = self._resolve_nse_token("INDIA VIX")
        tokens = [token for token in [spot_token, vix_token] if token > 0]
        self._token_symbol_map = {}
        if spot_token > 0:
            self._token_symbol_map[spot_token] = "SPOT"
        if vix_token > 0:
            self._token_symbol_map[vix_token] = "VIX"

        def on_ticks(_: Any, ticks: list[dict[str, Any]]) -> None:
            for tick in ticks:
                token = int(tick.get("instrument_token", 0) or 0)
                price = float(tick.get("last_price", 0.0) or 0.0)
                if token in self._token_symbol_map:
                    kind = self._token_symbol_map[token]
                    if kind == "SPOT" and price > 0:
                        self._latest_spot_price = price
                        self._latest_spot_volume = int(tick.get("volume", 0) or 0)
                    elif kind == "VIX" and price > 0:
                        self._latest_vix = price
                if self.on_tick and self._latest_spot_price > 0:
                    self.on_tick(
                        {
                            "nifty_price": self._latest_spot_price,
                            "vix": self._latest_vix,
                            "volume": self._latest_spot_volume,
                            "instrument_token": token,
                            "last_price": price,
                        }
                    )

        def on_connect(ws: Any, _: Any) -> None:
            LOGGER.info("KiteTicker connected")
            if tokens:
                ws.subscribe(tokens)
                try:
                    ws.set_mode(ws.MODE_FULL, tokens)
                except Exception:
                    LOGGER.exception("Failed setting KiteTicker mode to FULL")
                LOGGER.info("KiteTicker subscribed tokens=%s", tokens)
            else:
                LOGGER.warning("No NSE tokens resolved for live websocket stream")

        self._ticker.on_ticks = on_ticks
        self._ticker.on_connect = on_connect
        self._thread = threading.Thread(target=self._ticker.connect, kwargs={"threaded": True}, daemon=True)
        self._thread.start()
        LOGGER.info("Live WebSocket stream initialized")

    def _start_paper_loop(self) -> None:
        spot_symbol = f"NSE:{self._normalize_spot_symbol(str(self.settings['app']['symbol']))}"
        vix_symbol = "NSE:INDIA VIX"
        timezone_name = str(self.settings["app"].get("timezone", "Asia/Kolkata"))
        open_time = str(self.settings["app"].get("market_open_time", "09:15"))
        close_time = str(self.settings["app"].get("market_close_time", "15:30"))
        while self._running:
            session = market_session_status(
                timezone_name=timezone_name,
                open_time=open_time,
                close_time=close_time,
            )
            tick: dict[str, Any]
            sleep_seconds = 1.0
            try:
                if bool(session["is_open"]):
                    self._after_hours_seeded = False
                    if self.kite_client is None:
                        raise RuntimeError("kite_client is required for paper tick streaming")
                    quotes = self.kite_client.quote([spot_symbol, vix_symbol])
                    quote = quotes.get(spot_symbol, {})
                    vix_quote = quotes.get(vix_symbol, {})
                    tick = {
                        "instrument_token": int(quote.get("instrument_token", 0) or 0),
                        "last_price": float(quote.get("last_price", 0.0) or 0.0),
                        "nifty_price": float(quote.get("last_price", 0.0) or 0.0),
                        "vix": float(vix_quote.get("last_price", 0.0) or 0.0),
                        "volume": int(quote.get("volume", 0) or 0),
                    }
                    if tick["nifty_price"] > 0:
                        self._latest_spot_price = tick["nifty_price"]
                        self._latest_spot_volume = tick["volume"]
                    if tick["vix"] > 0:
                        self._latest_vix = tick["vix"]
                else:
                    if not self._after_hours_seeded:
                        self._after_hours_seeded = True
                        sleep_seconds = 60.0
                        if self.kite_client is not None:
                            try:
                                quotes = self.kite_client.quote([spot_symbol, vix_symbol])
                                quote = quotes.get(spot_symbol, {})
                                vix_quote = quotes.get(vix_symbol, {})
                                spot_price = float(quote.get("last_price", 0.0) or 0.0)
                                vix_price = float(vix_quote.get("last_price", 0.0) or 0.0)
                                if spot_price > 0:
                                    self._latest_spot_price = spot_price
                                    self._latest_spot_volume = int(quote.get("volume", 0) or 0)
                                if vix_price > 0:
                                    self._latest_vix = vix_price
                            except Exception:
                                LOGGER.exception("Paper after-hours seed fetch failed from Kite quote API")
                    else:
                        sleep_seconds = 60.0
                    tick = {
                        "instrument_token": 0,
                        "last_price": self._latest_spot_price,
                        "nifty_price": self._latest_spot_price,
                        "vix": self._latest_vix,
                        "volume": self._latest_spot_volume,
                    }
            except Exception:
                LOGGER.exception("Paper tick fetch failed from Kite quote API")
                time.sleep(2)
                continue
            if self.on_tick:
                self.on_tick(tick)
            time.sleep(sleep_seconds)

    def stop(self) -> None:
        self._running = False
        if self._ticker is not None:
            try:
                self._ticker.close()
            except Exception:
                LOGGER.exception("Error while closing kite ticker")
        LOGGER.info("WebSocket stream stopped")

    def _resolve_nse_token(self, symbol: str) -> int:
        if self.kite_client is None:
            return 0
        static_tokens = {
            "NIFTY 50": 256265,
            "NIFTY BANK": 260105,
            "BANKNIFTY": 260105,
        }
        symbol_upper = self._normalize_spot_symbol(symbol).upper()
        if symbol_upper in static_tokens:
            return static_tokens[symbol_upper]
        try:
            instruments = self.kite_client.instruments("NSE")
        except Exception:
            LOGGER.exception("Failed loading NSE instruments to resolve token for %s", symbol)
            return 0
        for instrument in instruments:
            trading_symbol = str(instrument.get("tradingsymbol", "")).upper()
            name = str(instrument.get("name", "")).upper()
            if symbol_upper in {trading_symbol, name}:
                token = int(instrument.get("instrument_token", 0) or 0)
                if token > 0:
                    return token
        LOGGER.warning("Unable to resolve NSE token for symbol=%s", symbol)
        return 0

    @staticmethod
    def _normalize_spot_symbol(symbol: str) -> str:
        value = str(symbol).strip().upper()
        if value in {"BANKNIFTY", "NIFTY BANK"}:
            return "NIFTY BANK"
        if value in {"NIFTY", "NIFTY50", "NIFTY 50"}:
            return "NIFTY 50"
        return str(symbol).strip()
