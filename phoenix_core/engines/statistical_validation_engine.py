
"""
statistical_validation_engine.py
--------------------------------
Phoenix Quant v1.9.2

Cluster-aware statistical validation for benchmark results.

핵심 원칙:
- 같은 as_of 날짜의 Top-N 종목은 독립 표본으로 보지 않는다.
- CI는 row bootstrap이 아니라 날짜 단위 block bootstrap으로 계산한다.
- random_z_score는 Cohen's d가 아니다.
  random baseline distribution의 표준편차 대비 Phoenix가 몇 표준편차 위인지 보는 진단 지표다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pandas as pd


ENGINE_VERSION = "v2.0"


@dataclass
class ValidationConfig:
    bootstrap_iterations: int = 1000
    confidence_level: float = 0.95
    random_seed: int = 42


@dataclass
class ValidationResult:
    metric: str
    observed: float
    baseline_mean: Optional[float]
    baseline_std: Optional[float]
    alpha: Optional[float]
    ci_low: float
    ci_high: float
    p_value: Optional[float]
    random_z_score: Optional[float]
    n: int
    n_groups: int
    iterations: int
    bootstrap_method: str = "block_by_as_of"
    random_z_score_method: str = "(observed - random_mean) / random_distribution_std"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StatisticalValidationEngine:
    """Bootstrap CI / empirical p-value / random z-score calculator."""

    def __init__(self, config: Optional[ValidationConfig] = None):
        self.config = config or ValidationConfig()
        self.rng = np.random.default_rng(self.config.random_seed)

    def validate_grouped_mean(
        self,
        df: pd.DataFrame,
        *,
        value_col: str,
        group_col: str = "as_of",
        baseline_values: Optional[Iterable[float]] = None,
        metric: Optional[str] = None,
        higher_is_better: bool = True,
    ) -> ValidationResult:
        clean_df = self._clean_frame(df, value_col=value_col, group_col=group_col)
        values = clean_df[value_col].to_numpy(dtype=float) if not clean_df.empty else np.array([], dtype=float)
        observed = float(np.mean(values)) if len(values) else 0.0
        ci_low, ci_high = self.block_bootstrap_ci(clean_df, value_col=value_col, group_col=group_col)

        baseline_mean = None
        baseline_std = None
        alpha = None
        p_value = None
        random_z_score = None

        if baseline_values is not None:
            base = self._clean_array(baseline_values)
            if len(base):
                baseline_mean = float(np.mean(base))
                baseline_std = float(np.std(base, ddof=1)) if len(base) >= 2 else 0.0
                alpha = observed - baseline_mean
                p_value = self.empirical_p_value(observed, base, higher_is_better=higher_is_better)
                random_z_score = self.random_z_score(observed, base)

        return ValidationResult(
            metric=metric or value_col,
            observed=observed,
            baseline_mean=baseline_mean,
            baseline_std=baseline_std,
            alpha=alpha,
            ci_low=ci_low,
            ci_high=ci_high,
            p_value=p_value,
            random_z_score=random_z_score,
            n=int(len(values)),
            n_groups=int(clean_df[group_col].nunique()) if not clean_df.empty else 0,
            iterations=int(self.config.bootstrap_iterations),
        )

    def validate_mean(
        self,
        values: Iterable[float],
        *,
        baseline_values: Optional[Iterable[float]] = None,
        metric: str = "metric",
        higher_is_better: bool = True,
    ) -> ValidationResult:
        arr = self._clean_array(values)
        df = pd.DataFrame({"_group": range(len(arr)), "_value": arr})
        return self.validate_grouped_mean(
            df,
            value_col="_value",
            group_col="_group",
            baseline_values=baseline_values,
            metric=metric,
            higher_is_better=higher_is_better,
        )

    def validate_binary_rate(
        self,
        values: Iterable[float],
        *,
        baseline_values: Optional[Iterable[float]] = None,
        metric: str = "hit_rate",
        higher_is_better: bool = True,
    ) -> ValidationResult:
        return self.validate_mean(values, baseline_values=baseline_values, metric=metric, higher_is_better=higher_is_better)

    def block_bootstrap_ci(self, df: pd.DataFrame, *, value_col: str, group_col: str = "as_of") -> tuple[float, float]:
        clean_df = self._clean_frame(df, value_col=value_col, group_col=group_col)
        if clean_df.empty:
            return 0.0, 0.0

        groups = []
        for _, g in clean_df.groupby(group_col):
            vals = g[value_col].to_numpy(dtype=float)
            vals = vals[np.isfinite(vals)]
            if len(vals):
                groups.append(vals)

        if not groups:
            return 0.0, 0.0

        iterations = int(self.config.bootstrap_iterations)
        if iterations <= 0:
            observed_values = np.concatenate(groups)
            observed_mean = float(np.mean(observed_values)) if len(observed_values) else 0.0
            return observed_mean, observed_mean

        boot_means = []
        n_groups = len(groups)
        for _ in range(iterations):
            sampled_idx = self.rng.choice(n_groups, size=n_groups, replace=True)
            sampled = np.concatenate([groups[int(i)] for i in sampled_idx])
            boot_means.append(float(np.mean(sampled)))

        alpha = 1.0 - float(self.config.confidence_level)
        return (
            float(np.quantile(boot_means, alpha / 2.0)),
            float(np.quantile(boot_means, 1.0 - alpha / 2.0)),
        )

    def empirical_p_value(self, observed: float, baseline: Iterable[float], *, higher_is_better: bool = True) -> float:
        base = self._clean_array(baseline)
        if len(base) == 0:
            return 1.0
        if higher_is_better:
            return float((np.sum(base >= observed) + 1) / (len(base) + 1))
        return float((np.sum(base <= observed) + 1) / (len(base) + 1))

    def random_z_score(self, observed: float, baseline: Iterable[float]) -> float:
        base = self._clean_array(baseline)
        if len(base) < 2:
            return 0.0
        std = float(np.std(base, ddof=1))
        if std <= 1e-12:
            return 0.0
        return float((observed - float(np.mean(base))) / std)

    def _clean_frame(self, df: pd.DataFrame, *, value_col: str, group_col: str) -> pd.DataFrame:
        if df is None or df.empty or value_col not in df.columns or group_col not in df.columns:
            return pd.DataFrame(columns=[group_col, value_col])
        out = df[[group_col, value_col]].copy()
        out[value_col] = pd.to_numeric(out[value_col], errors="coerce")
        out = out.dropna(subset=[group_col, value_col])
        out = out[np.isfinite(out[value_col].astype(float))]
        return out

    def _clean_array(self, values: Iterable[float]) -> np.ndarray:
        arr = pd.to_numeric(pd.Series(list(values)), errors="coerce").dropna().astype(float).values
        arr = arr[np.isfinite(arr)]
        return arr
