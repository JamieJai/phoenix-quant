from __future__ import annotations

import os
from datetime import timedelta
from typing import Iterable, List

import joblib
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from ..default_features import BASELINE_FEATURE_NAMES
from ..interfaces import SimilarityEngine as SimilarityEngineInterface
from ..models import PatternRecord, SimilarityQuery, SimilarityResult, SimilarNeighbor
from ..registry import EngineRegistry

LABEL_KEYS = ["hit_5pct_5d", "hit_10pct_10d", "fwd_max_ret_5d", "fwd_max_ret_10d"]


@EngineRegistry.register("similarity_engine", "cosine_knn")
class CosineKnnSimilarityEngine(SimilarityEngineInterface):
    name = "cosine_knn"

    def configure(self, **kwargs):
        self.feature_names: List[str] = kwargs.get("feature_names", BASELINE_FEATURE_NAMES)
        self.k = kwargs.get("k", 50)
        self.scaler = StandardScaler()
        self._fitted = False
        return super().configure(**kwargs)

    def build(self, records: Iterable[PatternRecord]) -> "CosineKnnSimilarityEngine":
        rows = []
        for r in records:
            if not all(k in r.forward_labels for k in LABEL_KEYS):
                continue
            row = {
                "ticker": r.ticker,
                "date": pd.Timestamp(r.date),
                **{f: r.feature_vector.values.get(f) for f in self.feature_names},
                **r.forward_labels,
            }
            rows.append(row)
        df = pd.DataFrame(rows)
        if df.empty:
            raise ValueError("SimilarityEngine 구축용 레코드가 비어 있습니다.")
        df[self.feature_names + LABEL_KEYS] = df[self.feature_names + LABEL_KEYS].apply(pd.to_numeric, errors="coerce")
        clean = df.dropna(subset=self.feature_names + LABEL_KEYS).reset_index(drop=True)
        if len(clean) < 20:
            raise ValueError(f"SimilarityEngine 구축 데이터가 너무 적습니다 (n={len(clean)}).")
        self._meta = clean[["ticker", "date"]].copy()
        self._labels = clean[LABEL_KEYS].copy()
        X = clean[self.feature_names].values.astype(float)
        self._X = self.scaler.fit_transform(X)
        self._nn = NearestNeighbors(n_neighbors=min(self.k, len(clean)), metric="cosine")
        self._nn.fit(self._X)
        self._fitted = True
        return self

    def _dedupe_by_date(self, neighbors: list[SimilarNeighbor]) -> list[SimilarNeighbor]:
        """Keep the strongest neighbor per date so one shock day does not dominate evidence."""
        best_by_date: dict[pd.Timestamp, SimilarNeighbor] = {}
        for neighbor in neighbors:
            key = pd.Timestamp(neighbor.date).normalize()
            current = best_by_date.get(key)
            if current is None or neighbor.similarity > current.similarity:
                best_by_date[key] = neighbor
        return sorted(best_by_date.values(), key=lambda n: n.similarity, reverse=True)

    def run(self, input_data: SimilarityQuery) -> SimilarityResult:
        if not self._fitted:
            raise RuntimeError("SimilarityEngine이 아직 build/load 되지 않았습니다.")
        fv = input_data.feature_vector
        k = input_data.k or self.k
        x = np.array([float(fv.values[f]) for f in self.feature_names], dtype=float).reshape(1, -1)
        if not np.isfinite(x).all():
            raise ValueError("입력 피처 벡터에 결측/비정상 값이 있습니다.")
        search_k = min(len(self._meta), max(k * 5, k + 50))
        distances, indices = self._nn.kneighbors(self.scaler.transform(x), n_neighbors=search_k)
        distances, indices = distances[0], indices[0]
        sims = 1.0 - distances
        result = self._meta.iloc[indices].copy().reset_index(drop=True)
        result["similarity"] = sims
        result = pd.concat([result, self._labels.iloc[indices].reset_index(drop=True)], axis=1)
        if input_data.exclude_ticker:
            query_date = pd.Timestamp(fv.as_of)
            too_close = (result["ticker"] == input_data.exclude_ticker.upper()) & (
                (query_date - result["date"]).abs() <= pd.Timedelta(days=input_data.exclude_recent_days)
            )
            result = result[~too_close]
        result = result.sort_values("similarity", ascending=False).head(k).reset_index(drop=True)
        raw_neighbors: list[SimilarNeighbor] = []
        for _, row in result.iterrows():
            labels = {key: float(row[key]) for key in LABEL_KEYS if key in row and pd.notna(row[key])}
            raw_neighbors.append(SimilarNeighbor(
                ticker=str(row["ticker"]),
                date=pd.Timestamp(row["date"]).date(),
                similarity=float(row["similarity"]),
                labels=labels,
            ))
        if not raw_neighbors:
            return SimilarityResult(fv.ticker, fv.as_of, [], 0, 0.0, 0.0, 0, 0.0)
        neighbors = self._dedupe_by_date(raw_neighbors)
        n_similar = sum(1 for n in neighbors if n.similarity >= input_data.similarity_threshold)
        hit_rate_5d = float(np.mean([n.labels.get("hit_5pct_5d", 0.0) for n in neighbors]))
        hit_rate_10d = float(np.mean([n.labels.get("hit_10pct_10d", 0.0) for n in neighbors]))
        avg_similarity = float(np.mean([n.similarity for n in neighbors]))
        return SimilarityResult(fv.ticker, fv.as_of, neighbors, n_similar, hit_rate_5d, hit_rate_10d, len(neighbors), avg_similarity)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump({
            "feature_names": self.feature_names,
            "k": self.k,
            "scaler": self.scaler,
            "meta": self._meta,
            "labels": self._labels,
            "X": self._X,
            "nn": self._nn,
        }, path)

    def load(self, path: str) -> "CosineKnnSimilarityEngine":
        payload = joblib.load(path)
        self.feature_names = payload["feature_names"]
        self.k = payload["k"]
        self.scaler = payload["scaler"]
        self._meta = payload["meta"]
        self._labels = payload["labels"]
        self._X = payload["X"]
        self._nn = payload["nn"]
        self._fitted = True
        return self
