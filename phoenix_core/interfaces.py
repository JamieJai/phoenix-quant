from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

TIn = TypeVar("TIn")
TOut = TypeVar("TOut")


class Engine(ABC, Generic[TIn, TOut]):
    slot: str = "engine"
    name: str = "base"
    version: str = "0.1.0"

    def configure(self, **kwargs: Any) -> "Engine":
        for key, value in kwargs.items():
            setattr(self, key, value)
        return self

    @abstractmethod
    def run(self, input_data: TIn) -> TOut:
        raise NotImplementedError


class ContextEngine(Engine):
    slot = "context_engine"


class FeatureEngine(Engine):
    slot = "feature_engine"


class PatternEngine(Engine):
    slot = "pattern_engine"


class SimilarityEngine(Engine):
    slot = "similarity_engine"


class DecisionEngine(Engine):
    slot = "decision_engine"


class ExplainEngine(Engine):
    slot = "explain_engine"


class BacktestEngine(Engine):
    slot = "backtest_engine"


class FeatureImportanceEngine(Engine):
    slot = "feature_importance_engine"


class ExperimentEngine(Engine):
    slot = "experiment_engine"
