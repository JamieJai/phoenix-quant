from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

import pandas as pd

FeatureFn = Callable[[pd.DataFrame], pd.Series]


@dataclass
class FeatureSpec:
    name: str
    category: str
    version: str
    description: str
    compute: FeatureFn
    requires: List[str] = field(default_factory=lambda: ["Open", "High", "Low", "Close", "Volume"])


class DuplicateFeatureError(ValueError):
    pass


class FeatureCatalog:
    def __init__(self) -> None:
        self._specs: dict[str, FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> None:
        if spec.name in self._specs:
            raise DuplicateFeatureError(f"이미 등록된 feature입니다: {spec.name}")
        self._specs[spec.name] = spec

    def register_fn(self, name: str, category: str, description: str,
                    requires: Optional[List[str]] = None, version: str = "v1"):
        def decorator(fn: FeatureFn) -> FeatureFn:
            self.register(FeatureSpec(
                name=name, category=category, version=version,
                description=description, compute=fn,
                requires=requires or ["Open", "High", "Low", "Close", "Volume"],
            ))
            return fn
        return decorator

    def get_spec(self, name: str) -> FeatureSpec:
        return self._specs[name]

    def list_features(self, category: Optional[str] = None) -> List[str]:
        return [n for n, s in self._specs.items() if category is None or s.category == category]

    def list_categories(self) -> List[str]:
        return sorted({s.category for s in self._specs.values()})

    def compute(self, df: pd.DataFrame, feature_names: Optional[List[str]] = None) -> pd.DataFrame:
        names = feature_names if feature_names is not None else list(self._specs.keys())
        out = pd.DataFrame(index=df.index)
        for name in names:
            if name not in self._specs:
                raise KeyError(f"카탈로그에 등록되지 않은 feature: {name}")
            spec = self._specs[name]
            missing = [c for c in spec.requires if c not in df.columns]
            if missing:
                raise ValueError(f"'{name}' 계산에 필요한 컬럼이 없습니다: {missing}")
            out[name] = pd.to_numeric(spec.compute(df), errors="coerce")
        return out

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, name: str) -> bool:
        return name in self._specs


default_catalog = FeatureCatalog()
