from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .trade_models import EntryMode, ExitReason, TradeCandidate, TradeConfig, TradeResult, TradeSummary
from .trade_rules import apply_costs, decide_intraday_exit, normalize_config


class TradeSimulationEngine:
    """Phoenix Quant Trade Simulation Engine v1.6.

    역할:
    - 일봉 OHLCV를 기준으로 실제 매매 규칙을 시뮬레이션한다.
    - TP/SL/Trailing/Time Exit를 처리한다.
    - 결과를 TradeResult/TradeSummary로 반환한다.

    설계 원칙:
    - 예측/스코어 계산은 하지 않는다.
    - 이미 선택된 후보를 실제 거래했을 때의 결과만 계산한다.
    - daily OHLCV에서는 장중 순서를 알 수 없으므로 same_day_rule로 보수성 수준을 명시한다.
    """

    def __init__(self, config: TradeConfig | None = None):
        self.config = normalize_config(config)

    def simulate_trade(
        self,
        *,
        ticker: str,
        ohlcv: pd.DataFrame,
        as_of: date | str | pd.Timestamp,
        score: float = 0.0,
        rank: int = 0,
        config: TradeConfig | None = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TradeResult:
        cfg = normalize_config(config or self.config)
        df = self._normalize_ohlcv(ohlcv)

        if df.empty:
            return self._empty_result(ticker, as_of, score, rank, metadata)

        as_of_ts = pd.Timestamp(as_of).normalize()
        idx = df.index

        # as_of 이전/당일 마지막 거래일을 기준일로 삼는다.
        pos = idx.searchsorted(as_of_ts, side="right") - 1
        if pos < 0 or pos >= len(df):
            return self._empty_result(ticker, as_of, score, rank, metadata)

        if cfg.entry_mode == EntryMode.NEXT_OPEN:
            entry_pos = pos + 1
            if entry_pos >= len(df):
                return self._empty_result(ticker, as_of, score, rank, metadata)
            entry_date = idx[entry_pos].date()
            entry_price = float(df.iloc[entry_pos]["open"])
            first_future_pos = entry_pos
        else:
            entry_pos = pos
            entry_date = idx[entry_pos].date()
            entry_price = float(df.iloc[entry_pos]["close"])
            first_future_pos = entry_pos + 1

        if not np.isfinite(entry_price) or entry_price <= 0:
            return self._empty_result(ticker, as_of, score, rank, metadata)

        end_pos = min(first_future_pos + int(cfg.max_hold_days), len(df))
        future = df.iloc[first_future_pos:end_pos]

        if future.empty:
            return self._empty_result(ticker, as_of, score, rank, metadata, entry_date, entry_price)

        highest_price = entry_price
        lowest_price = entry_price
        exit_price = float(future.iloc[-1]["close"])
        exit_date = future.index[-1].date()
        exit_reason = ExitReason.TIME_EXIT
        hit_tp = False
        hit_sl = False
        hit_tr = False
        exit_hold_days = 0

        for hold_i, (dt, row) in enumerate(future.iterrows(), start=1):
            day_open = float(row["open"])
            day_high = float(row["high"])
            day_low = float(row["low"])
            day_close = float(row["close"])

            if not all(np.isfinite(x) for x in [day_open, day_high, day_low, day_close]):
                continue

            highest_price = max(highest_price, day_high)
            lowest_price = min(lowest_price, day_low)

            reason, px = decide_intraday_exit(
                entry_price=entry_price,
                day_open=day_open,
                day_high=day_high,
                day_low=day_low,
                highest_price=highest_price,
                config=cfg,
            )

            if reason is not None and px is not None:
                exit_reason = reason
                exit_price = float(px)
                exit_date = dt.date()
                exit_hold_days = hold_i
                hit_tp = reason == ExitReason.TAKE_PROFIT
                hit_sl = reason == ExitReason.STOP_LOSS
                hit_tr = reason == ExitReason.TRAILING_STOP
                break

            # 마지막 보유일은 종가 청산
            if hold_i >= int(cfg.max_hold_days):
                exit_reason = ExitReason.TIME_EXIT
                exit_price = day_close
                exit_date = dt.date()
                exit_hold_days = hold_i
                break
        if exit_hold_days == 0:
            exit_hold_days = int(len(future))

        gross_return = (exit_price / entry_price) - 1.0
        net_return = apply_costs(gross_return, cfg)
        hold_days = max(0, exit_hold_days)
        max_high_return = (highest_price / entry_price) - 1.0
        max_low_return = (lowest_price / entry_price) - 1.0

        return TradeResult(
            ticker=ticker,
            as_of=pd.Timestamp(as_of_ts).date(),
            entry_date=entry_date,
            exit_date=exit_date,
            entry_price=entry_price,
            exit_price=exit_price,
            gross_return=float(gross_return),
            net_return=float(net_return),
            hold_days=int(hold_days),
            exit_reason=exit_reason,
            max_high_return=float(max_high_return),
            max_low_return=float(max_low_return),
            hit_take_profit=hit_tp,
            hit_stop_loss=hit_sl,
            hit_trailing_stop=hit_tr,
            score=float(score),
            rank=int(rank),
            metadata=metadata or {},
        )

    def simulate_candidates(
        self,
        *,
        candidates: Iterable[TradeCandidate],
        raw_data: Dict[str, pd.DataFrame],
        config: TradeConfig | None = None,
    ) -> List[TradeResult]:
        results: List[TradeResult] = []
        for c in candidates:
            df = raw_data.get(c.ticker)
            if df is None:
                results.append(
                    self._empty_result(c.ticker, c.as_of, c.score, c.rank, {**c.metadata, "error": "missing_ohlcv"})
                )
                continue

            results.append(
                self.simulate_trade(
                    ticker=c.ticker,
                    ohlcv=df,
                    as_of=c.as_of,
                    score=c.score,
                    rank=c.rank,
                    config=config,
                    metadata=c.metadata,
                )
            )
        return results

    def summarize(self, results: Iterable[TradeResult]) -> TradeSummary:
        rows = list(results)
        valid = [r for r in rows if r.exit_reason != ExitReason.NO_DATA and np.isfinite(r.net_return)]

        if not valid:
            return TradeSummary(
                n_trades=0,
                win_rate=0.0,
                avg_return=0.0,
                median_return=0.0,
                cumulative_return=0.0,
                mdd=0.0,
                profit_factor=0.0,
                tp_rate=0.0,
                sl_rate=0.0,
                trailing_rate=0.0,
                time_exit_rate=0.0,
            )

        rets = np.array([r.net_return for r in valid], dtype=float)
        wins = rets[rets > 0]
        losses = rets[rets < 0]

        equity = np.cumprod(1.0 + rets)
        peak = np.maximum.accumulate(equity)
        drawdown = equity / peak - 1.0

        gross_profit = float(wins.sum()) if len(wins) else 0.0
        gross_loss = abs(float(losses.sum())) if len(losses) else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        n = len(valid)
        return TradeSummary(
            n_trades=n,
            win_rate=float((rets > 0).mean()),
            avg_return=float(np.mean(rets)),
            median_return=float(np.median(rets)),
            cumulative_return=float(equity[-1] - 1.0),
            mdd=float(abs(drawdown.min())),
            profit_factor=float(profit_factor),
            tp_rate=float(np.mean([r.exit_reason == ExitReason.TAKE_PROFIT for r in valid])),
            sl_rate=float(np.mean([r.exit_reason == ExitReason.STOP_LOSS for r in valid])),
            trailing_rate=float(np.mean([r.exit_reason == ExitReason.TRAILING_STOP for r in valid])),
            time_exit_rate=float(np.mean([r.exit_reason == ExitReason.TIME_EXIT for r in valid])),
        )

    def results_to_frame(self, results: Iterable[TradeResult]) -> pd.DataFrame:
        return pd.DataFrame([r.to_dict() for r in results])

    def summary_to_frame(self, summary: TradeSummary) -> pd.DataFrame:
        return pd.DataFrame([summary.to_dict()])

    def _normalize_ohlcv(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        if ohlcv is None or len(ohlcv) == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close"])

        df = ohlcv.copy()

        # yfinance MultiIndex 컬럼 방어
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]

        rename_map = {
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "adj close": "close",
        }
        df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

        required = ["open", "high", "low", "close"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"OHLCV missing columns: {missing}")

        df = df[required].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        df = df.sort_index()
        df = df.dropna(subset=required)
        return df

    def _empty_result(
        self,
        ticker: str,
        as_of: date | str | pd.Timestamp,
        score: float = 0.0,
        rank: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        entry_date: Optional[date] = None,
        entry_price: float = 0.0,
    ) -> TradeResult:
        return TradeResult(
            ticker=ticker,
            as_of=pd.Timestamp(as_of).date(),
            entry_date=entry_date,
            exit_date=None,
            entry_price=float(entry_price),
            exit_price=0.0,
            gross_return=0.0,
            net_return=0.0,
            hold_days=0,
            exit_reason=ExitReason.NO_DATA,
            max_high_return=0.0,
            max_low_return=0.0,
            score=float(score),
            rank=int(rank),
            metadata=metadata or {},
        )
