from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def to_dataframe(candles: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    return df[["date", "open", "high", "low", "close", "volume"]].copy()


def compute_vwap(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    return float((typical_price * df["volume"]).sum() / max(df["volume"].sum(), 1))


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr if not np.isnan(atr) else 0.0)


def compute_trend_strength(df: pd.DataFrame, lookback: int = 20) -> tuple[str, float]:
    if df.empty:
        return "sideways", 0.0
    sample = df.tail(lookback)
    x = np.arange(len(sample))
    y = sample["close"].to_numpy(dtype=float)
    if len(y) < 2:
        return "sideways", 0.0
    slope = float(np.polyfit(x, y, 1)[0])
    trend = "uptrend" if slope > 0 else "downtrend"
    if abs(slope) < 1:
        trend = "sideways"
    return trend, abs(slope)


def compute_rsi(df: pd.DataFrame, period: int = 14) -> float:
    if df.empty or len(df) < period + 1:
        return 50.0
    close = df["close"].astype(float)
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50.0


def compute_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, float]:
    if df.empty or len(df) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    close = df["close"].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd": float(macd_line.iloc[-1]),
        "signal": float(signal_line.iloc[-1]),
        "histogram": float(histogram.iloc[-1]),
    }


def compute_bollinger_bands(
    df: pd.DataFrame, period: int = 20, std_dev: float = 2.0
) -> dict[str, float]:
    if df.empty or len(df) < period:
        return {"upper": 0.0, "middle": 0.0, "lower": 0.0, "width": 0.0}
    close = df["close"].astype(float)
    rolling_mean = close.rolling(window=period).mean()
    rolling_std = close.rolling(window=period).std()
    upper = rolling_mean + (rolling_std * std_dev)
    lower = rolling_mean - (rolling_std * std_dev)
    width_pct = ((upper - lower) / rolling_mean * 100).iloc[-1]
    return {
        "upper": float(upper.iloc[-1]),
        "middle": float(rolling_mean.iloc[-1]),
        "lower": float(lower.iloc[-1]),
        "width_pct": float(width_pct) if not np.isnan(width_pct) else 0.0,
    }


def compute_adr(df: pd.DataFrame, period: int = 10) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    high_low = df["high"] - df["low"]
    adr = high_low.rolling(window=period).mean()
    return float(adr.iloc[-1]) if not np.isnan(adr.iloc[-1]) else 0.0


def compute_support_resistance(
    df: pd.DataFrame, lookback: int = 20, num_levels: int = 3
) -> list[dict[str, Any]]:
    if df.empty or len(df) < lookback:
        return []
    recent = df.tail(lookback)
    highs = recent["high"].astype(float)
    lows = recent["low"].astype(float)
    current_price = float(recent["close"].iloc[-1])

    # Simple pivot point detection
    resistance_levels = []
    support_levels = []

    for i in range(2, len(recent) - 2):
        if highs.iloc[i] >= highs.iloc[i - 1] and highs.iloc[i] >= highs.iloc[i + 1]:
            resistance_levels.append(highs.iloc[i])
        if lows.iloc[i] <= lows.iloc[i - 1] and lows.iloc[i] <= lows.iloc[i + 1]:
            support_levels.append(lows.iloc[i])

    # Sort and get top levels
    resistance_levels = sorted(set(resistance_levels), reverse=True)[:num_levels]
    support_levels = sorted(set(support_levels))[:num_levels]

    result = []
    for level in resistance_levels:
        result.append(
            {
                "level": round(level, 2),
                "type": "resistance",
                "strength": 0.5,  # Could be based on touch count
                "distance_pct": abs(level - current_price) / current_price * 100,
            }
        )
    for level in support_levels:
        result.append(
            {
                "level": round(level, 2),
                "type": "support",
                "strength": 0.5,
                "distance_pct": abs(level - current_price) / current_price * 100,
            }
        )

    return sorted(result, key=lambda x: x["distance_pct"])[: num_levels * 2]


def compute_volume_percentile(df: pd.DataFrame, lookback: int = 20) -> float:
    if df.empty or len(df) < lookback:
        return 0.5
    volume = df["volume"].astype(float)
    current = volume.iloc[-1]
    historical = volume.iloc[-lookback:-1] if len(volume) > lookback else volume[:-1]
    if len(historical) == 0:
        return 0.5
    percentile = (historical < current).sum() / len(historical)
    return float(percentile)


def compute_pcr(option_chain: list[dict[str, Any]]) -> float:
    if not option_chain:
        return 0.0
    total_ce_oi = sum(int(row.get("ce_oi", 0)) for row in option_chain)
    total_pe_oi = sum(int(row.get("pe_oi", 0)) for row in option_chain)
    if total_ce_oi == 0:
        return 0.0
    return round(total_pe_oi / total_ce_oi, 3)
