from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance

from ..interfaces import FeatureImportanceEngine as FeatureImportanceEngineInterface
from ..models import FeatureImportanceResult
from ..registry import EngineRegistry


@EngineRegistry.register("feature_importance_engine", "random_forest")
class RandomForestFeatureImportanceEngine(FeatureImportanceEngineInterface):
    name = "random_forest"

    def configure(self, **kwargs):
        self.n_estimators = kwargs.get("n_estimators", 300)
        self.random_state = kwargs.get("random_state", 42)
        self.use_permutation = kwargs.get("use_permutation", False)
        return super().configure(**kwargs)

    def run(self, input_data):
        X, y, feature_names = input_data
        return self.calculate(X, y, feature_names)

    def calculate(self, X: pd.DataFrame, y: pd.Series, feature_names: Optional[List[str]] = None) -> FeatureImportanceResult:
        feature_names = feature_names or list(X.columns)
        X_use = X[feature_names].apply(pd.to_numeric, errors="coerce").dropna()
        y_use = y.loc[X_use.index]
        if len(X_use) < 20:
            raise ValueError(f"feature importance 계산에 데이터가 너무 적습니다 (n={len(X_use)})")
        if y_use.nunique() < 2:
            raise ValueError("라벨(y)에 클래스가 하나뿐입니다.")
        clf = RandomForestClassifier(n_estimators=self.n_estimators, random_state=self.random_state, n_jobs=-1)
        clf.fit(X_use.values, y_use.values)
        if self.use_permutation:
            result = permutation_importance(clf, X_use.values, y_use.values, n_repeats=10,
                                            random_state=self.random_state, n_jobs=-1)
            importances = result.importances_mean
            method = "permutation_importance"
        else:
            importances = clf.feature_importances_
            method = "random_forest_gini"
        order = np.argsort(importances)[::-1]
        return FeatureImportanceResult(method, [{"feature": feature_names[i], "importance": float(importances[i])} for i in order])
