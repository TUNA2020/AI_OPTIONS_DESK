from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def build_iv_surface(option_chain: list[dict[str, Any]]) -> pd.DataFrame:
    if not option_chain:
        return pd.DataFrame(columns=["strike", "ce_iv", "pe_iv", "mean_iv", "skew"])

    df = pd.DataFrame(option_chain)
    df["mean_iv"] = (df["ce_iv"].astype(float) + df["pe_iv"].astype(float)) / 2
    df["skew"] = df["pe_iv"].astype(float) - df["ce_iv"].astype(float)
    return df[["strike", "ce_iv", "pe_iv", "mean_iv", "skew"]].sort_values("strike")


def estimate_iv_skew(option_chain: list[dict[str, Any]]) -> float:
    surface = build_iv_surface(option_chain)
    if surface.empty:
        return 0.0
    return float(np.nanmean(surface["skew"].astype(float)))
