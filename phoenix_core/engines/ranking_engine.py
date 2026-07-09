from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

try:
    from xgboost import XGBClassifier
except Exception:  # pragma: no cover
    XGBClassifier = None

from ..default_features import BASELINE_FEATURE_NAMES
from ..interfaces import Engine
from ..models import FeatureEngineInput, RankingInput, RankingItem, RankingResult
from ..pipeline import analyze_ticker_quiet, build_trade_plan
from ..registry import EngineRegistry


@EngineRegistry.register("ranking_engine", "ranking_v1")
class RankingEngine(Engine[RankingInput, RankingResult]):
    """Universe 전체를 분석해 상위 종목을 반환한다.

    v1은 기존 단일 티커 파이프라인을 재사용하는 안전한 구현이다. 속도 최적화는 향후 배치형으로 개선한다.
    """

    slot = "ranking_engine"
    name = "ranking_v1"

    def _fit_xgb_model(self, records):
        rows = []
        for record in records:
            label = record.forward_labels.get("hit_5pct_5d")
            if label is None or pd.isna(label):
                continue
            row = {name: record.feature_vector.values.get(name) for name in BASELINE_FEATURE_NAMES}
            row["label"] = int(float(label) > 0.0)
            rows.append(row)
        df = pd.DataFrame(rows)
        if df.empty or len(df) < 500 or df["label"].nunique() < 2:
            return None
        df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=BASELINE_FEATURE_NAMES + ["label"])
        if len(df) < 500 or df["label"].nunique() < 2:
            return None
        if XGBClassifier is None:
            model = RandomForestClassifier(n_estimators=300, random_state=42, n_jobs=-1)
        else:
            model = XGBClassifier(
                n_estimators=350,
                max_depth=4,
                learning_rate=0.04,
                subsample=0.85,
                colsample_bytree=0.85,
                reg_lambda=1.5,
                objective="binary:logistic",
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1,
                tree_method="hist",
            )
        model.fit(df[BASELINE_FEATURE_NAMES].values.astype(float), df["label"].astype(int).values)
        return model

    def _xgb_score(self, model, values: dict[str, float]) -> float:
        if model is None:
            return float("nan")
        x = np.array([[float(values[name]) for name in BASELINE_FEATURE_NAMES]], dtype=float)
        if not np.isfinite(x).all():
            return float("nan")
        if hasattr(model, "predict_proba"):
            return float(model.predict_proba(x)[0, 1])
        return float(model.predict(x)[0])

    def _label_reason(self, decision, xgb_score: float, final_rank_score: float) -> str:
        reasons = []
        if np.isfinite(xgb_score):
            if xgb_score >= 0.65:
                reasons.append("XGB 높음")
            elif xgb_score < 0.45:
                reasons.append("XGB 낮음")
        if decision.risk_score >= 70:
            reasons.append("risk 과다")
        elif decision.risk_score <= 45:
            reasons.append("risk 양호")
        if decision.confidence_score >= 85:
            reasons.append("conf 높음")
        elif decision.confidence_score < 60:
            reasons.append("conf 낮음")
        market_score = float(decision.sub_scores.get("market_score", 0.0))
        if market_score >= 60:
            reasons.append("market 양호")
        elif market_score < 45:
            reasons.append("market 약함")
        if decision.success_rate_5d >= 0.55:
            reasons.append("5D hit 양호")
        elif decision.success_rate_5d < 0.40:
            reasons.append("5D hit 낮음")
        if final_rank_score < 35:
            reasons.append("final 낮음")
        return " / ".join(reasons[:5]) if reasons else "중립"

    def run(self, input_data: RankingInput) -> RankingResult:
        items: list[RankingItem] = []
        xgb_model = None
        try:
            xgb_model = self._fit_xgb_model((input_data.prebuilt or {}).get("records", []))
        except Exception as exc:  # noqa: BLE001
            if input_data.verbose:
                print(f"[rank warn] xgb disabled: {exc}")
        for ticker in input_data.universe:
            try:
                decision, meta = analyze_ticker_quiet(
                    input_data.config,
                    ticker,
                    period=input_data.period,
                    refresh=False,
                    retrain=False,
                    k=input_data.k,
                    raw_data=input_data.raw_data,
                    prebuilt=input_data.prebuilt,
                )
                trade_plan = build_trade_plan(meta["latest_close"], input_data.config)
                feature_engine = (input_data.prebuilt or {}).get("feature_engine")
                feature_vector = feature_engine.run(FeatureEngineInput(ticker=ticker, ohlcv=input_data.raw_data[ticker], as_of=decision.as_of))
                xgb_score = self._xgb_score(xgb_model, feature_vector.values)
                xgb_blend_weight = float(np.clip(getattr(input_data, "xgb_blend_weight", 0.30), 0.0, 1.0))
                final_rank_score = float(decision.suitability_score)
                if np.isfinite(xgb_score):
                    final_rank_score = (1.0 - xgb_blend_weight) * final_rank_score + xgb_blend_weight * (xgb_score * 100.0)
                items.append(RankingItem(
                    ticker=ticker,
                    as_of=decision.as_of,
                    suitability_score=decision.suitability_score,
                    confidence_score=decision.confidence_score,
                    risk_score=decision.risk_score,
                    label=decision.label,
                    market_score=decision.sub_scores.get("market_score", 0.0),
                    sector_score=decision.sub_scores.get("sector_rotation_score", decision.sub_scores.get("market_score", 0.0)),
                    pattern_rarity=decision.sub_scores.get("anomaly_percentile", 0.0),
                    hit_rate_5d=decision.success_rate_5d,
                    entry_price=float(trade_plan["entry_price"]),
                    take_profit_price=float(trade_plan["take_profit_price"]),
                    stop_loss_price=float(trade_plan["stop_loss_price"]),
                    max_hold_days=int(trade_plan["max_hold_days"]),
                    xgb_score=0.0 if not np.isfinite(xgb_score) else xgb_score,
                    final_rank_score=final_rank_score,
                    label_reason=self._label_reason(decision, xgb_score, final_rank_score),
                ))
            except Exception as exc:  # noqa: BLE001
                if input_data.verbose:
                    print(f"[rank warn] {ticker}: {exc}")
        items.sort(key=lambda x: (x.final_rank_score or x.suitability_score, x.suitability_score, x.confidence_score), reverse=True)
        return RankingResult(as_of=items[0].as_of if items else input_data.as_of, items=items[:input_data.top_n])
