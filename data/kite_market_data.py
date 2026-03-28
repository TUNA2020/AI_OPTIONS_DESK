from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.market_hours import market_session_status
from data.market_features import compute_atr, compute_trend_strength, compute_vwap, to_dataframe
from execution.kite_client import KiteClient


LOGGER = logging.getLogger(__name__)


def _parse_hhmm(value: str) -> time:
    hour_str, minute_str = value.split(":")
    return time(hour=int(hour_str), minute=int(minute_str))


@dataclass(slots=True)
class KiteMarketData:
    kite_client: KiteClient
    symbol: str = "NIFTY 50"
    timezone: str = "Asia/Kolkata"
    market_open_time: str = "09:15"
    market_close_time: str = "15:30"
    _spot_token_cache: int | None = field(init=False, default=None)
    _last_snapshot: dict[str, Any] = field(init=False, default_factory=dict)

    def fetch_market_snapshot(self) -> dict[str, Any]:
        session = self.market_session_status()
        if not bool(session["is_open"]) and self._last_snapshot:
            snapshot = dict(self._last_snapshot)
            snapshot["market_open"] = bool(session["is_open"])
            snapshot["market_status"] = str(session["status"])
            snapshot["market_status_reason"] = str(session["reason"])
            snapshot["market_local_time"] = str(session["local_time"])
            snapshot["market_open_time"] = str(session["open_time"])
            snapshot["market_close_time"] = str(session["close_time"])
            return snapshot

        spot_symbol = self._normalize_spot_symbol(self.symbol)
        symbols = [f"NSE:{spot_symbol}", "NSE:INDIA VIX"]
        try:
            quotes = self.kite_client.quote(symbols)
            nifty_quote = quotes.get(f"NSE:{spot_symbol}", {})
            vix_quote = quotes.get("NSE:INDIA VIX", {})
            nifty_price = float(nifty_quote.get("last_price", 0.0))
            vix = float(vix_quote.get("last_price", 0.0))
            volume = int(nifty_quote.get("volume", 0))
            if vix <= 0:
                raise RuntimeError("Missing/invalid INDIA VIX quote from Kite")

            candles = self.fetch_5m_candles()
            df = to_dataframe(candles)
            if df.empty or len(df) < 2:
                raise RuntimeError(
                    f"No usable 5m candles from Kite for {self.symbol} (received {len(df)})"
                )
            if not bool(session["is_open"]):
                nifty_price = float(df["close"].iloc[-1])
                volume = int(df["volume"].iloc[-1])
            if nifty_price <= 0:
                raise RuntimeError(f"Missing/invalid spot price from Kite for NSE:{spot_symbol}")

            vwap = compute_vwap(df)
            atr = compute_atr(df)
            trend, trend_strength = compute_trend_strength(df)
            snapshot = {
                "nifty_price": nifty_price,
                "vix": vix,
                "volume": volume,
                "vwap": vwap,
                "atr": atr,
                "trend": trend,
                "trend_strength": trend_strength,
                "candles_5m": candles,
                "market_open": bool(session["is_open"]),
                "market_status": str(session["status"]),
                "market_status_reason": str(session["reason"]),
                "market_local_time": str(session["local_time"]),
                "market_open_time": str(session["open_time"]),
                "market_close_time": str(session["close_time"]),
            }
            self._last_snapshot = snapshot
            LOGGER.debug("Fetched market snapshot for %s", self.symbol)
            return snapshot
        except Exception:
            if self._last_snapshot:
                LOGGER.warning("Market snapshot fetch failed; using cached snapshot.")
                snapshot = dict(self._last_snapshot)
                snapshot["market_open"] = bool(session["is_open"])
                snapshot["market_status"] = str(session["status"])
                snapshot["market_status_reason"] = str(session["reason"])
                snapshot["market_local_time"] = str(session["local_time"])
                snapshot["market_open_time"] = str(session["open_time"])
                snapshot["market_close_time"] = str(session["close_time"])
                return snapshot
            raise

    def fetch_5m_candles(self, lookback_minutes: int = 300, min_candles: int = 20) -> list[dict[str, Any]]:
        instrument_token = self._resolve_spot_instrument_token()
        now_local = datetime.now(ZoneInfo(self.timezone))
        open_at = _parse_hhmm(self.market_open_time)
        close_at = _parse_hhmm(self.market_close_time)
        session = self.market_session_status()

        # Try most relevant windows first, then backtrack day-by-day for weekends/holidays.
        windows: list[tuple[datetime, datetime]] = []
        if bool(session["is_open"]):
            end_live = now_local.replace(tzinfo=None)
            start_live = end_live - timedelta(minutes=lookback_minutes)
            windows.append((start_live, end_live))

        today = now_local.date()
        if today.weekday() < 5 and now_local.time() >= open_at:
            day_end = (
                close_at
                if now_local.time() > close_at
                else now_local.time().replace(second=0, microsecond=0, tzinfo=None)
            )
            windows.append(
                (datetime.combine(today, open_at), datetime.combine(today, day_end))
            )

        backtrack_day = today
        for _ in range(14):
            backtrack_day -= timedelta(days=1)
            if backtrack_day.weekday() >= 5:
                continue
            windows.append(
                (
                    datetime.combine(backtrack_day, open_at),
                    datetime.combine(backtrack_day, close_at),
                )
            )

        best: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for from_dt, to_dt in windows:
            key = (from_dt.isoformat(), to_dt.isoformat())
            if key in seen:
                continue
            seen.add(key)
            candles = self.kite_client.historical_data(
                instrument_token=instrument_token,
                from_dt=from_dt,
                to_dt=to_dt,
                interval="5minute",
            )
            if len(candles) > len(best):
                best = candles
            if len(candles) >= min_candles:
                return candles

        if best:
            return best
        raise RuntimeError(f"No usable 5m candles from Kite for {self.symbol}")

    def fetch_5m_candle_context(self, days_back: int = 7) -> dict[str, Any]:
        today_candles = self.fetch_5m_candles()
        today_summary = self._summarize_candles(today_candles, label="today")
        history: list[dict[str, Any]] = []

        symbol_date = date.today()
        open_at = _parse_hhmm(self.market_open_time)
        close_at = _parse_hhmm(self.market_close_time)
        instrument_token = self._resolve_spot_instrument_token()
        seen_days: set[str] = set()

        for day in self._previous_trading_days(symbol_date, days_back):
            day_key = day.isoformat()
            if day_key in seen_days:
                continue
            seen_days.add(day_key)
            from_dt = datetime.combine(day, open_at)
            to_dt = datetime.combine(day, close_at)
            try:
                candles = self.kite_client.historical_data(
                    instrument_token=instrument_token,
                    from_dt=from_dt,
                    to_dt=to_dt,
                    interval="5minute",
                )
            except Exception:
                continue
            if not candles:
                continue
            history.append(self._summarize_candles(candles, label=day_key))

        history.sort(key=lambda row: str(row.get("date", "")), reverse=True)
        return {
            "today": today_summary,
            "today_candles_5m": today_candles,
            "history_5m_7d": history[:days_back],
        }

    def market_session_status(self) -> dict[str, str | bool]:
        return market_session_status(
            timezone_name=self.timezone,
            open_time=self.market_open_time,
            close_time=self.market_close_time,
        )

    def _resolve_spot_instrument_token(self) -> int:
        if self._spot_token_cache is not None:
            return self._spot_token_cache

        symbol_upper = self._normalize_spot_symbol(self.symbol).upper()
        static_tokens = {
            "NIFTY 50": 256265,
            "NIFTY BANK": 260105,
            "BANKNIFTY": 260105,
        }
        if symbol_upper in static_tokens:
            self._spot_token_cache = static_tokens[symbol_upper]
            return self._spot_token_cache

        instruments = self.kite_client.instruments("NSE")
        for instrument in instruments:
            trading_symbol = str(instrument.get("tradingsymbol", "")).upper()
            name = str(instrument.get("name", "")).upper()
            if trading_symbol == symbol_upper or name == symbol_upper:
                token = int(instrument.get("instrument_token", 0))
                if token > 0:
                    self._spot_token_cache = token
                    return token
        raise RuntimeError(f"Unable to resolve NSE instrument token for symbol '{symbol_upper}'")

    @staticmethod
    def _normalize_spot_symbol(symbol: str) -> str:
        value = str(symbol).strip().upper()
        if value in {"BANKNIFTY", "NIFTY BANK"}:
            return "NIFTY BANK"
        if value in {"NIFTY", "NIFTY50", "NIFTY 50"}:
            return "NIFTY 50"
        return str(symbol).strip()

    def _summarize_candles(self, candles: list[dict[str, Any]], label: str) -> dict[str, Any]:
        df = to_dataframe(candles)
        if df.empty:
            return {"date": label, "candle_count": 0}
        first = df.iloc[0]
        last = df.iloc[-1]
        trend, trend_strength = compute_trend_strength(df)
        summary = {
            "date": label,
            "candle_count": int(len(df)),
            "open": float(first["open"]),
            "high": float(df["high"].max()),
            "low": float(df["low"].min()),
            "close": float(last["close"]),
            "volume": int(df["volume"].sum()),
            "vwap": compute_vwap(df),
            "atr": compute_atr(df),
            "trend": trend,
            "trend_strength": trend_strength,
            "change_pct": float(((last["close"] - first["open"]) / first["open"]) * 100.0) if float(first["open"]) else 0.0,
            "range_pct": float(((df["high"].max() - df["low"].min()) / first["open"]) * 100.0) if float(first["open"]) else 0.0,
        }
        return summary

    def _previous_trading_days(self, start_day: date, count: int) -> list[date]:
        days: list[date] = []
        cursor = start_day
        while len(days) < count:
            cursor -= timedelta(days=1)
            if cursor.weekday() >= 5:
                continue
            days.append(cursor)
        return days
