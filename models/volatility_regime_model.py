from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


LOGGER = logging.getLogger(__name__)

REGIMES = ["range", "trend", "volatile", "breakout"]


@dataclass(slots=True)
class VolatilityRegimeModel:
    model_path: Path = Path("models/volatility_regime.joblib")
    pipeline: Pipeline = field(init=False)

    def __post_init__(self) -> None:
        self.pipeline = Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                ("rf", RandomForestClassifier(n_estimators=200, random_state=42)),
            ]
        )

    def train(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        if len(rows) < 50:
            rows = self._bootstrap_training_data(200)
        x = np.array(
            [
                [
                    float(r["atr"]),
                    float(r["vix"]),
                    float(r["iv_skew"]),
                    float(r["volume"]),
                    float(r["trend_strength"]),
                ]
                for r in rows
            ]
        )
        y = np.array([str(r["label"]) for r in rows])
        x_train, x_test, y_train, y_test = train_test_split(
            x, y, test_size=0.2, random_state=42, stratify=y
        )
        self.pipeline.fit(x_train, y_train)
        acc = float(self.pipeline.score(x_test, y_test))
        self.save()
        LOGGER.info("Volatility regime model trained with accuracy %.3f", acc)
        return {"accuracy": acc}

    def predict(self, features: dict[str, float]) -> dict[str, Any]:
        x = np.array(
            [
                [
                    float(features["atr"]),
                    float(features["vix"]),
                    float(features["iv_skew"]),
                    float(features["volume"]),
                    float(features["trend_strength"]),
                ]
            ]
        )
        proba = self.pipeline.predict_proba(x)[0]
        labels = self.pipeline.classes_.tolist()
        idx = int(np.argmax(proba))
        return {"regime": labels[idx], "confidence": float(proba[idx])}

    def save(self) -> None:
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.pipeline, self.model_path)

    def load(self) -> None:
        self.pipeline = joblib.load(self.model_path)

    def ensure_ready(self) -> None:
        if self.model_path.exists():
            self.load()
        else:
            self.train([])

    def _bootstrap_training_data(self, n: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        rng = np.random.default_rng(42)
        for _ in range(n):
            atr = float(rng.uniform(30, 260))
            vix = float(rng.uniform(10, 32))
            iv_skew = float(rng.uniform(-0.12, 0.16))
            volume = float(rng.uniform(80000, 500000))
            trend_strength = float(rng.uniform(0, 7))
            label = "range"
            if vix > 22 and trend_strength > 4:
                label = "breakout"
            elif vix > 20:
                label = "volatile"
            elif trend_strength > 3.5:
                label = "trend"
            rows.append(
                {
                    "atr": atr,
                    "vix": vix,
                    "iv_skew": iv_skew,
                    "volume": volume,
                    "trend_strength": trend_strength,
                    "label": label,
                }
            )
        return rows
