from __future__ import annotations

import numpy as np
import pandas as pd

from .feature_catalog import FeatureCatalog, default_catalog


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50.0)


def register_default_features(catalog: FeatureCatalog = default_catalog) -> FeatureCatalog:
    if "ret_5d" in catalog:
        return catalog

    @catalog.register_fn("ret_5d", "price", "5일 수익률")
    def ret_5d(df: pd.DataFrame) -> pd.Series:
        return df["Close"].pct_change(5)

    @catalog.register_fn("ret_10d", "price", "10일 수익률")
    def ret_10d(df: pd.DataFrame) -> pd.Series:
        return df["Close"].pct_change(10)

    @catalog.register_fn("ret_20d", "price", "20일 수익률")
    def ret_20d(df: pd.DataFrame) -> pd.Series:
        return df["Close"].pct_change(20)

    @catalog.register_fn("vol_ratio_5d", "volume", "5일/20일 평균거래량 비율")
    def vol_ratio_5d(df: pd.DataFrame) -> pd.Series:
        return df["Volume"].rolling(5).mean() / df["Volume"].rolling(20).mean().replace(0, np.nan)

    @catalog.register_fn("vol_ratio_20d", "volume", "20일/60일 평균거래량 비율")
    def vol_ratio_20d(df: pd.DataFrame) -> pd.Series:
        return df["Volume"].rolling(20).mean() / df["Volume"].rolling(60).mean().replace(0, np.nan)

    @catalog.register_fn("dollar_volume_z", "volume", "거래대금의 60일 z-score")
    def dollar_volume_z(df: pd.DataFrame) -> pd.Series:
        dollar_volume = df["Close"] * df["Volume"]
        return (dollar_volume - dollar_volume.rolling(60).mean()) / dollar_volume.rolling(60).std().replace(0, np.nan)

    @catalog.register_fn("volatility_20d", "price", "20일 일간수익률 표준편차")
    def volatility_20d(df: pd.DataFrame) -> pd.Series:
        return df["Close"].pct_change().rolling(20).std()

    @catalog.register_fn("high_breakout_20d", "price", "20일 고가 대비 종가 위치")
    def high_breakout_20d(df: pd.DataFrame) -> pd.Series:
        return df["Close"] / df["High"].rolling(20).max().replace(0, np.nan)

    @catalog.register_fn("high_breakout_60d", "price", "60일 고가 대비 종가 위치")
    def high_breakout_60d(df: pd.DataFrame) -> pd.Series:
        return df["Close"] / df["High"].rolling(60).max().replace(0, np.nan)

    @catalog.register_fn("ma_dev_20", "price", "20일 이동평균 이격도")
    def ma_dev_20(df: pd.DataFrame) -> pd.Series:
        ma = df["Close"].rolling(20).mean()
        return (df["Close"] - ma) / ma.replace(0, np.nan)

    @catalog.register_fn("ma_dev_60", "price", "60일 이동평균 이격도")
    def ma_dev_60(df: pd.DataFrame) -> pd.Series:
        ma = df["Close"].rolling(60).mean()
        return (df["Close"] - ma) / ma.replace(0, np.nan)

    @catalog.register_fn("rsi_14", "price", "RSI 14일")
    def rsi_14(df: pd.DataFrame) -> pd.Series:
        return _rsi(df["Close"], 14)

    @catalog.register_fn("close_position_20d", "price", "20일 레인지 내 종가 위치")
    def close_position_20d(df: pd.DataFrame) -> pd.Series:
        hi = df["High"].rolling(20).max()
        lo = df["Low"].rolling(20).min()
        return (df["Close"] - lo) / (hi - lo).replace(0, np.nan)

    return catalog


BASELINE_FEATURE_NAMES = [
    "ret_5d", "ret_10d", "ret_20d", "vol_ratio_5d", "vol_ratio_20d",
    "dollar_volume_z", "volatility_20d", "high_breakout_20d", "high_breakout_60d",
    "ma_dev_20", "ma_dev_60", "rsi_14", "close_position_20d",
]

register_default_features(default_catalog)
