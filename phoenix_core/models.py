from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass
class ContextEngineInput:
    as_of: date
    market_ohlcv: Dict[str, Any]
    sector_etf: Optional[str] = None


@dataclass
class MarketContext:
    as_of: date
    regime: str
    risk_level: str
    market_score: float
    trend_scores: Dict[str, float] = field(default_factory=dict)
    vix_level: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketRegimeInput:
    as_of: date
    market_ohlcv: Dict[str, Any]


@dataclass
class MarketRegimeResult:
    as_of: date
    regime: str
    confidence_score: float
    risk_score: float
    momentum_score: float
    breadth_score: float
    volatility_score: float
    components: Dict[str, float] = field(default_factory=dict)


@dataclass
class SectorRotationInput:
    as_of: date
    market_ohlcv: Dict[str, Any]
    target_sector_etf: Optional[str] = None
    sector_etfs: Optional[List[str]] = None


@dataclass
class SectorStrength:
    etf: str
    score: float
    return_5d: float
    return_20d: float
    rank: int = 0


@dataclass
class SectorRotationResult:
    as_of: date
    target_etf: Optional[str]
    target_strength: Optional[SectorStrength]
    top_sectors: List[SectorStrength] = field(default_factory=list)
    all_strengths: List[SectorStrength] = field(default_factory=list)


@dataclass
class CorrelationInput:
    ticker: str
    as_of: date
    ohlcv: Dict[str, Any]
    compare_tickers: List[str]
    windows: List[int] = field(default_factory=lambda: [90, 180, 360])


@dataclass
class CorrelationResult:
    ticker: str
    as_of: date
    correlations: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def strongest(self, window_key: str = "corr_90d", limit: int = 5) -> List[tuple[str, float]]:
        rows = []
        for ticker, vals in self.correlations.items():
            if window_key in vals:
                rows.append((ticker, vals[window_key]))
        rows.sort(key=lambda x: abs(x[1]), reverse=True)
        return rows[:limit]


@dataclass
class RankingItem:
    ticker: str
    as_of: date
    suitability_score: float
    confidence_score: float
    risk_score: float
    label: str
    market_score: float
    sector_score: float
    pattern_rarity: float
    hit_rate_5d: float
    entry_price: float = 0.0
    take_profit_price: float = 0.0
    stop_loss_price: float = 0.0
    max_hold_days: int = 0


@dataclass
class RankingResult:
    as_of: date
    items: List[RankingItem] = field(default_factory=list)


@dataclass
class RankingInput:
    config: Any
    universe: List[str]
    as_of: date
    raw_data: Optional[Dict[str, Any]] = None
    prebuilt: Optional[Dict[str, Any]] = None
    period: str = "3y"
    top_n: int = 20
    k: int = 50
    verbose: bool = False


@dataclass
class FeatureEngineInput:
    ticker: str
    ohlcv: Any
    feature_names: Optional[List[str]] = None
    as_of: Optional[date] = None


@dataclass
class FeatureVector:
    ticker: str
    as_of: date
    values: Dict[str, float]
    feature_set_version: str = "v1"

    def as_ordered_list(self, names: List[str]) -> List[float]:
        return [float(self.values[n]) for n in names]


@dataclass
class PatternEngineInput:
    feature_vector: FeatureVector
    reference_records: Optional[List["PatternRecord"]] = None


@dataclass
class PatternScanResult:
    ticker: str
    as_of: date
    anomaly_percentile: float
    model_version: str = "v1"


@dataclass
class SimilarityQuery:
    feature_vector: FeatureVector
    k: int = 50
    exclude_ticker: Optional[str] = None
    exclude_recent_days: int = 30
    similarity_threshold: float = 0.80


@dataclass
class SimilarNeighbor:
    ticker: str
    date: date
    similarity: float
    labels: Dict[str, float] = field(default_factory=dict)


@dataclass
class SimilarityResult:
    query_ticker: str
    query_date: date
    neighbors: List[SimilarNeighbor]
    n_similar: int
    hit_rate_5d: float
    hit_rate_10d: float
    avg_similarity: float = 0.0


@dataclass
class DecisionInput:
    ticker: str
    as_of: date
    pattern: PatternScanResult
    similarity: SimilarityResult
    market_context: MarketContext
    feature_vector: FeatureVector
    regime_result: Optional[MarketRegimeResult] = None
    sector_rotation: Optional[SectorRotationResult] = None
    correlation_result: Optional[CorrelationResult] = None


@dataclass
class DecisionResult:
    ticker: str
    as_of: date
    suitability_score: float
    confidence_score: float
    risk_score: float
    success_rate_5d: float
    success_rate_10d: float
    sub_scores: Dict[str, float] = field(default_factory=dict)
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    confidence_breakdown: Dict[str, float] = field(default_factory=dict)
    label: str = ""
    explanation: Optional[str] = None


@dataclass
class PatternRecord:
    ticker: str
    date: date
    feature_vector: FeatureVector
    forward_labels: Dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestTrade:
    ticker: str
    as_of: date
    predicted_positive: bool
    actual_positive: bool
    forward_return: float


@dataclass
class BacktestResult:
    n_records: int
    n_trades: int
    hit_rate: float
    avg_return: float
    max_return: float
    min_return: float
    sharpe: float
    mdd: float
    profit_factor: float
    precision: float
    recall: float
    f1: float
    trades: List[BacktestTrade] = field(default_factory=list)


@dataclass
class FeatureImportanceResult:
    method: str
    ranking: List[Dict[str, float]]


@dataclass
class ExperimentResult:
    experiment_name: str
    metric_name: str
    baseline_features: List[str]
    candidate_features: List[str]
    baseline_metric: float
    candidate_metric: float
    delta: float
    improved: bool
    detail: Dict[str, Any] = field(default_factory=dict)
