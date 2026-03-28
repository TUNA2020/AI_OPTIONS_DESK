from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from math import erf, exp, log, sqrt
from typing import Any

from execution.kite_client import KiteClient


LOGGER = logging.getLogger(__name__)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _bs_d1(spot: float, strike: float, t: float, iv: float, risk_free_rate: float) -> float:
    return (log(spot / strike) + (risk_free_rate + 0.5 * iv * iv) * t) / (iv * sqrt(t))


def _bs_price(
    spot: float,
    strike: float,
    t: float,
    iv: float,
    is_call: bool,
    risk_free_rate: float,
) -> float:
    if spot <= 0 or strike <= 0 or t <= 0 or iv <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, t, iv, risk_free_rate)
    d2 = d1 - iv * sqrt(t)
    discount = exp(-risk_free_rate * t)
    if is_call:
        return (spot * _normal_cdf(d1)) - (strike * discount * _normal_cdf(d2))
    return (strike * discount * _normal_cdf(-d2)) - (spot * _normal_cdf(-d1))


def _implied_vol_from_price(
    option_price: float,
    spot: float,
    strike: float,
    t: float,
    is_call: bool,
    risk_free_rate: float,
) -> float:
    if option_price <= 0 or spot <= 0 or strike <= 0 or t <= 0:
        return 0.0
    intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
    if option_price < intrinsic:
        return 0.0
    low, high = 0.01, 5.0
    for _ in range(70):
        mid = (low + high) / 2.0
        mid_price = _bs_price(spot, strike, t, mid, is_call, risk_free_rate)
        if mid_price > option_price:
            high = mid
        else:
            low = mid
    return round((low + high) / 2.0, 6)


def _bs_delta(spot: float, strike: float, t: float, iv: float, is_call: bool, risk_free_rate: float) -> float:
    if spot <= 0 or strike <= 0 or t <= 0 or iv <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, t, iv, risk_free_rate)
    call_delta = _normal_cdf(d1)
    return call_delta if is_call else call_delta - 1


def _parse_expiry(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@dataclass(slots=True)
class OptionChainFetcher:
    kite_client: KiteClient
    strike_step: int = 50
    _last_chain: list[dict[str, Any]] = field(init=False, default_factory=list)

    def fetch_option_chain(
        self,
        spot_price: float,
        strikes_each_side: int = 20,
        risk_free_rate: float = 0.07,
        base_iv: float = 0.18,
        underlying: str = "NIFTY",
    ) -> list[dict[str, Any]]:
        del base_iv  # IV comes from market prices now, not synthetic defaults.
        if spot_price <= 0:
            raise RuntimeError("Invalid spot price for option chain build.")

        expiry, strike_map = self._near_expiry_strike_map(underlying)
        all_strikes = sorted(strike_map.keys())
        if not all_strikes:
            raise RuntimeError(f"No strikes available for underlying {underlying}")

        atm_idx = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - spot_price))
        start = max(0, atm_idx - strikes_each_side)
        end = min(len(all_strikes), atm_idx + strikes_each_side + 1)
        selected_strikes = all_strikes[start:end]
        if not selected_strikes:
            raise RuntimeError(f"No strikes selected near ATM for {underlying}")

        symbols: list[str] = []
        for strike in selected_strikes:
            ce_symbol, pe_symbol = strike_map[strike]
            symbols.extend([f"NFO:{ce_symbol}", f"NFO:{pe_symbol}"])
        try:
            quotes = self.kite_client.quote(symbols)
        except Exception:
            if self._last_chain:
                LOGGER.warning("Option chain quote fetch failed; using cached chain.")
                return list(self._last_chain)
            raise

        days_to_expiry = max((expiry - date.today()).days, 1)
        t = days_to_expiry / 365.0
        rows: list[dict[str, Any]] = []
        for strike in selected_strikes:
            ce_symbol, pe_symbol = strike_map[strike]
            ce_quote = quotes.get(f"NFO:{ce_symbol}", {})
            pe_quote = quotes.get(f"NFO:{pe_symbol}", {})
            ce_ltp = float(ce_quote.get("last_price", 0.0))
            pe_ltp = float(pe_quote.get("last_price", 0.0))
            ce_iv = _implied_vol_from_price(
                option_price=ce_ltp,
                spot=spot_price,
                strike=float(strike),
                t=t,
                is_call=True,
                risk_free_rate=risk_free_rate,
            )
            pe_iv = _implied_vol_from_price(
                option_price=pe_ltp,
                spot=spot_price,
                strike=float(strike),
                t=t,
                is_call=False,
                risk_free_rate=risk_free_rate,
            )
            ce_delta = _bs_delta(spot_price, float(strike), t, ce_iv, True, risk_free_rate)
            pe_delta = _bs_delta(spot_price, float(strike), t, pe_iv, False, risk_free_rate)
            rows.append(
                {
                    "strike": strike,
                    "ce_symbol": ce_symbol,
                    "pe_symbol": pe_symbol,
                    "ce_oi": int(ce_quote.get("oi", 0) or 0),
                    "pe_oi": int(pe_quote.get("oi", 0) or 0),
                    "ce_iv": round(ce_iv, 6),
                    "pe_iv": round(pe_iv, 6),
                    "ce_delta": round(ce_delta, 6),
                    "pe_delta": round(pe_delta, 6),
                    "volume": int(ce_quote.get("volume", 0) or 0) + int(pe_quote.get("volume", 0) or 0),
                    "ce_ltp": ce_ltp,
                    "pe_ltp": pe_ltp,
                    "expiry": expiry.isoformat(),
                    "days_to_expiry": days_to_expiry,
                }
            )

        LOGGER.debug("Option chain fetched from Kite with %d rows", len(rows))
        self._last_chain = rows
        return rows

    def _near_expiry_strike_map(self, underlying: str) -> tuple[date, dict[int, tuple[str, str]]]:
        instruments = self.kite_client.instruments("NFO")
        today = date.today()
        candidates: list[dict[str, Any]] = []
        for instrument in instruments:
            if str(instrument.get("name", "")).upper() != underlying.upper():
                continue
            option_type = str(instrument.get("instrument_type", "")).upper()
            if option_type not in {"CE", "PE"}:
                continue
            expiry = _parse_expiry(instrument.get("expiry"))
            if not expiry or expiry < today:
                continue
            candidates.append(instrument)

        if not candidates:
            raise RuntimeError(f"No valid NFO option instruments found for {underlying}")

        expiries = sorted({_parse_expiry(i.get("expiry")) for i in candidates if _parse_expiry(i.get("expiry"))})
        if not expiries:
            raise RuntimeError(f"No valid expiries found for {underlying}")
        nearest_expiry = expiries[0]

        strike_index: dict[int, dict[str, str]] = {}
        for instrument in candidates:
            expiry = _parse_expiry(instrument.get("expiry"))
            if expiry != nearest_expiry:
                continue
            strike = int(float(instrument.get("strike", 0)))
            option_type = str(instrument.get("instrument_type", "")).upper()
            trading_symbol = str(instrument.get("tradingsymbol", ""))
            if strike <= 0 or not trading_symbol:
                continue
            strike_index.setdefault(strike, {})
            strike_index[strike][option_type] = trading_symbol

        strike_map = {
            strike: (types["CE"], types["PE"])
            for strike, types in strike_index.items()
            if "CE" in types and "PE" in types
        }
        if not strike_map:
            raise RuntimeError(f"No complete CE/PE strike pairs found for {underlying}")
        return nearest_expiry, strike_map
