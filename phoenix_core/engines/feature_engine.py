from __future__ import annotations

from datetime import date
from typing import List

import numpy as np
import pandas as pd

from ..default_features import BASELINE_FEATURE_NAMES
from ..feature_catalog import default_catalog
from ..interfaces import FeatureEngine as FeatureEngineInterface
from ..models import FeatureEngineInput, FeatureVector
from ..registry import EngineRegistry


@EngineRegistry.register("feature_engine", "catalog_v1")
class CatalogFeatureEngine(FeatureEngineInterface):
    name = "catalog_v1"

    def configure(self, **kwargs):
        self.catalog = kwargs.get("catalog", default_catalog)
        self.feature_names: List[str] = kwargs.get("feature_names", BASELINE_FEATURE_NAMES)
        self.feature_set_version = kwargs.get("feature_set_version", "catalog_v1")
        return super().configure(**kwargs)

    def compute_frame(self, ohlcv: pd.DataFrame, feature_names: List[str] | None = None) -> pd.DataFrame:
        return self.catalog.compute(ohlcv.sort_index(), feature_names or self.feature_names)

    def latest_vector(self, ticker: str, ohlcv: pd.DataFrame, as_of: date | None = None,
                      feature_names: List[str] | None = None) -> FeatureVector:
        feats = self.compute_frame(ohlcv, feature_names)
        if as_of is not None:
            feats = feats[feats.index <= pd.Timestamp(as_of)]
        valid = feats.dropna(subset=feature_names or self.feature_names)
        if valid.empty:
            raise ValueError(f"{ticker}: 유효한 피처를 계산할 만큼 데이터가 충분하지 않습니다.")
        row = valid.iloc[-1]
        values = {k: float(row[k]) for k in (feature_names or self.feature_names)}
        if any(not np.isfinite(v) for v in values.values()):
            raise ValueError(f"{ticker}: 피처 벡터에 비정상 값이 있습니다.")
        return FeatureVector(ticker=ticker.upper(), as_of=row.name.date(), values=values,
                             feature_set_version=self.feature_set_version)

    def run(self, input_data: FeatureEngineInput) -> FeatureVector:
        return self.latest_vector(input_data.ticker, input_data.ohlcv, input_data.as_of, input_data.feature_names)
