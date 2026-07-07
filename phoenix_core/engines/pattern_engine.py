from __future__ import annotations

import os
from typing import Iterable, List

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from ..default_features import BASELINE_FEATURE_NAMES
from ..interfaces import PatternEngine as PatternEngineInterface
from ..models import PatternEngineInput, PatternRecord, PatternScanResult
from ..registry import EngineRegistry


@EngineRegistry.register("pattern_engine", "isolation_forest")
class IsolationForestPatternEngine(PatternEngineInterface):
    name = "isolation_forest"

    def configure(self, **kwargs):
        self.feature_names: List[str] = kwargs.get("feature_names", BASELINE_FEATURE_NAMES)
        self.n_estimators = kwargs.get("n_estimators", 300)
        self.contamination = kwargs.get("contamination", 0.05)
        self.random_state = kwargs.get("random_state", 42)
        self.scaler = StandardScaler()
        self.model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state,
            n_jobs=-1,
        )
        self._fitted = False
        self._train_scores: np.ndarray | None = None
        return super().configure(**kwargs)

    def _records_to_frame(self, records: Iterable[PatternRecord]) -> pd.DataFrame:
        rows = []
        for r in records:
            row = {f: r.feature_vector.values.get(f) for f in self.feature_names}
            rows.append(row)
        X = pd.DataFrame(rows).apply(pd.to_numeric, errors="coerce")
        return X.dropna(subset=self.feature_names)

    def fit(self, records: Iterable[PatternRecord]) -> "IsolationForestPatternEngine":
        X = self._records_to_frame(records)
        if len(X) < 50:
            raise ValueError(f"IsolationForest 학습 데이터가 너무 적습니다 (n={len(X)}).")
        Xs = self.scaler.fit_transform(X[self.feature_names].values.astype(float))
        self.model.fit(Xs)
        self._train_scores = self.model.score_samples(Xs)
        self._fitted = True
        return self

    def anomaly_percentile(self, values: dict[str, float]) -> float:
        if not self._fitted or self._train_scores is None:
            raise RuntimeError("PatternEngine이 아직 fit/load 되지 않았습니다.")
        x = np.array([float(values[f]) for f in self.feature_names], dtype=float).reshape(1, -1)
        if not np.isfinite(x).all():
            raise ValueError("입력 피처 벡터에 결측/비정상 값이 있습니다.")
        raw_score = self.model.score_samples(self.scaler.transform(x))[0]
        percentile_from_top = 100.0 * (self._train_scores < raw_score).mean()
        return float(np.clip(100.0 - percentile_from_top, 0.0, 100.0))

    def run(self, input_data: PatternEngineInput) -> PatternScanResult:
        if input_data.reference_records is not None and not self._fitted:
            self.fit(input_data.reference_records)
        fv = input_data.feature_vector
        return PatternScanResult(
            ticker=fv.ticker,
            as_of=fv.as_of,
            anomaly_percentile=self.anomaly_percentile(fv.values),
            model_version="isolation_forest_v1",
        )

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump({
            "feature_names": self.feature_names,
            "n_estimators": self.n_estimators,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "scaler": self.scaler,
            "model": self.model,
            "train_scores": self._train_scores,
        }, path)

    def load(self, path: str) -> "IsolationForestPatternEngine":
        payload = joblib.load(path)
        self.feature_names = payload["feature_names"]
        self.n_estimators = payload["n_estimators"]
        self.contamination = payload["contamination"]
        self.random_state = payload["random_state"]
        self.scaler = payload["scaler"]
        self.model = payload["model"]
        self._train_scores = payload["train_scores"]
        self._fitted = True
        return self
