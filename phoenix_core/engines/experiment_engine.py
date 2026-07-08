from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit, cross_val_score

try:
    from xgboost import XGBClassifier
except Exception:  # pragma: no cover
    XGBClassifier = None

from ..interfaces import ExperimentEngine as ExperimentEngineInterface
from ..models import ExperimentResult
from ..registry import EngineRegistry


@EngineRegistry.register("experiment_engine", "cv_compare")
class CvCompareExperimentEngine(ExperimentEngineInterface):
    name = "cv_compare"

    def configure(self, **kwargs):
        self.cv = kwargs.get("cv", 5)
        self.metric = kwargs.get("metric", "roc_auc")
        self.n_estimators = kwargs.get("n_estimators", 200)
        self.random_state = kwargs.get("random_state", 42)
        self.min_improvement = kwargs.get("min_improvement", 0.0)
        return super().configure(**kwargs)

    def run(self, input_data):
        X, y, baseline_features, candidate_features = input_data[:4]
        experiment_name = input_data[4] if len(input_data) > 4 else None
        return self.compare(X, y, baseline_features, candidate_features, experiment_name)

    def _cv_score(self, X: pd.DataFrame, y: pd.Series) -> dict:
        if len(X) < 20:
            raise ValueError(f"experiment cv data too small (n={len(X)})")
        clf = RandomForestClassifier(n_estimators=self.n_estimators, random_state=self.random_state, n_jobs=-1)
        n_splits = min(int(self.cv), max(2, len(X) // 5))
        n_splits = min(n_splits, len(X) - 1)
        if n_splits < 2:
            n_splits = 2
        cv = TimeSeriesSplit(n_splits=n_splits)
        scores = cross_val_score(clf, X.values, y.values, cv=cv, scoring=self.metric)
        return {"mean": float(np.mean(scores)), "std": float(np.std(scores)), "scores": scores.tolist()}

    def compare(self, X: pd.DataFrame, y: pd.Series, baseline_features: List[str],
                candidate_features: List[str], experiment_name: Optional[str] = None) -> ExperimentResult:
        combined = X[baseline_features + candidate_features].apply(pd.to_numeric, errors="coerce").dropna()
        y_use = y.loc[combined.index]
        if y_use.nunique() < 2:
            raise ValueError("라벨(y)에 클래스가 하나뿐입니다.")
        baseline = self._cv_score(combined[baseline_features], y_use)
        candidate = self._cv_score(combined[baseline_features + candidate_features], y_use)
        delta = candidate["mean"] - baseline["mean"]
        return ExperimentResult(experiment_name or f"add_{'_'.join(candidate_features)}", self.metric,
                                list(baseline_features), list(candidate_features), baseline["mean"],
                                candidate["mean"], delta, delta > self.min_improvement,
                                {"baseline": baseline, "candidate": candidate})


@EngineRegistry.register("experiment_engine", "xgb_compare")
class XgbCompareExperimentEngine(CvCompareExperimentEngine):
    name = "xgb_compare"

    def configure(self, **kwargs):
        self.max_depth = kwargs.get("max_depth", 4)
        self.learning_rate = kwargs.get("learning_rate", 0.05)
        self.n_estimators = kwargs.get("n_estimators", 300)
        self.subsample = kwargs.get("subsample", 0.8)
        self.colsample_bytree = kwargs.get("colsample_bytree", 0.8)
        self.reg_lambda = kwargs.get("reg_lambda", 1.0)
        self.random_state = kwargs.get("random_state", 42)
        self.cv = kwargs.get("cv", 5)
        self.metric = kwargs.get("metric", "roc_auc")
        self.min_improvement = kwargs.get("min_improvement", 0.0)
        return super().configure(**kwargs)

    def _cv_score(self, X: pd.DataFrame, y: pd.Series) -> dict:
        if len(X) < 20:
            raise ValueError(f"experiment cv data too small (n={len(X)})")
        if XGBClassifier is None:
            clf = RandomForestClassifier(n_estimators=self.n_estimators, random_state=self.random_state, n_jobs=-1)
        else:
            clf = XGBClassifier(
                n_estimators=self.n_estimators,
                max_depth=self.max_depth,
                learning_rate=self.learning_rate,
                subsample=self.subsample,
                colsample_bytree=self.colsample_bytree,
                reg_lambda=self.reg_lambda,
                objective="binary:logistic",
                eval_metric="logloss",
                random_state=self.random_state,
                n_jobs=-1,
                tree_method="hist",
            )
        n_splits = min(int(self.cv), max(2, len(X) // 5))
        n_splits = min(n_splits, len(X) - 1)
        if n_splits < 2:
            n_splits = 2
        cv = TimeSeriesSplit(n_splits=n_splits)
        scores = cross_val_score(clf, X.values, y.values, cv=cv, scoring=self.metric)
        return {"mean": float(np.mean(scores)), "std": float(np.std(scores)), "scores": scores.tolist()}
