from __future__ import annotations

import logging
import os
import time
from typing import Dict, Iterable, Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

logger = logging.getLogger("phoenix.data_loader")


def _cache_path(ticker: str, cache_dir: str) -> str:
    safe = ticker.replace("^", "IDX_").replace("/", "_")
    return os.path.join(cache_dir, f"{safe}.csv")


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("빈 데이터프레임")
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] for c in out.columns]
    out = out.rename(columns={str(c): str(c).title() for c in out.columns})
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}; columns={list(out.columns)}")
    out = out[required].copy()
    out.index = pd.to_datetime(out.index)
    out.index.name = "Date"
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=required)
    return out.sort_index()


def download_single(ticker: str, period: str = "3y", interval: str = "1d",
                    retries: int = 2, pause: float = 1.0) -> Optional[pd.DataFrame]:
    if yf is None:
        raise ImportError("yfinance가 설치되어 있지 않습니다. pip install -r requirements.txt")
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            raw = yf.download(
                ticker, period=period, interval=interval,
                auto_adjust=True, progress=False, threads=False,
            )
            return normalize_ohlcv(raw)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("다운로드 실패 %s (%s/%s): %s", ticker, attempt, retries, exc)
            time.sleep(pause)
    logger.error("최종 다운로드 실패: %s (%s)", ticker, last_err)
    return None


def download_ohlcv(tickers: Iterable[str], cache_dir: str = "data", period: str = "3y",
                   interval: str = "1d", force_refresh: bool = False) -> Dict[str, pd.DataFrame]:
    os.makedirs(cache_dir, exist_ok=True)
    result: Dict[str, pd.DataFrame] = {}
    failed: list[str] = []
    for ticker in list(dict.fromkeys([t.upper() if not t.startswith("^") else t for t in tickers])):
        path = _cache_path(ticker, cache_dir)
        if not force_refresh and os.path.exists(path):
            try:
                result[ticker] = normalize_ohlcv(pd.read_csv(path, index_col="Date", parse_dates=True))
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("캐시 로드 실패, 재다운로드: %s (%s)", ticker, exc)
        df = download_single(ticker, period=period, interval=interval)
        if df is None or df.empty:
            failed.append(ticker)
            continue
        df.to_csv(path)
        result[ticker] = df
    logger.info("다운로드 완료: 성공 %d / 실패 %d", len(result), len(failed))
    if failed:
        logger.info("실패 티커: %s", ", ".join(failed))
    return result
