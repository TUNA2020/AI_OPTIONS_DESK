from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(slots=True)
class HistoricalLoader:
    data_path: Path = Path("data/historical_nifty_5m.csv")

    def load(self) -> pd.DataFrame:
        if self.data_path.exists():
            df = pd.read_csv(self.data_path)
            return df
        # Empty frame for graceful behavior.
        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "vix",
                "atr",
                "iv_skew",
                "trend_strength",
            ]
        )
