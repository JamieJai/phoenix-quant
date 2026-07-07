from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score

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
        clf = RandomForestClassifier(n_estimators=self.n_estimators, random_state=self.random_state, n_jobs=-1)
        scores = cross_val_score(clf, X.values, y.values, cv=self.cv, scoring=self.metric)
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
