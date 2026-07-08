from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml
from dotenv import load_dotenv


@dataclass
class AppConfig:
    engines: Dict[str, str]
    universe: List[str]
    market_etfs: List[str]
    sector_etf_map: Dict[str, str]
    default_sector_etf: str = "QQQ"
    cache_dir: str = "data"
    models_dir: str = "models"
    reports_dir: str = "reports"
    similarity_k: int = 50
    similarity_threshold: float = 0.80
    backtest: Dict[str, Any] = field(default_factory=dict)
    trade: Dict[str, Any] = field(default_factory=dict)

    def sector_etf_for(self, ticker: str) -> str:
        return self.sector_etf_map.get(ticker.upper(), self.default_sector_etf)


def load_config(path: str | Path = "config/config.yaml") -> AppConfig:
    load_dotenv("config/.env")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # PHX_* 환경변수는 배포 환경 오버라이드용. 필요한 최소값만 지원.
    for key in ("cache_dir", "models_dir", "reports_dir"):
        env_key = f"PHX_{key.upper()}"
        if os.getenv(env_key):
            raw[key] = os.getenv(env_key)
    if os.getenv("PHX_SIMILARITY_K"):
        raw["similarity_k"] = int(os.getenv("PHX_SIMILARITY_K", "50"))

    return AppConfig(
        engines=raw.get("engines", {}),
        universe=raw.get("universe", []),
        market_etfs=raw.get("market_etfs", []),
        sector_etf_map=raw.get("sector_etf_map", {}),
        default_sector_etf=raw.get("default_sector_etf", "QQQ"),
        cache_dir=raw.get("cache_dir", "data"),
        models_dir=raw.get("models_dir", "models"),
        reports_dir=raw.get("reports_dir", "reports"),
        similarity_k=int(raw.get("similarity_k", 50)),
        similarity_threshold=float(raw.get("similarity_threshold", 0.80)),
        backtest=raw.get("backtest", {}),
        trade=raw.get("trade", {}),
    )
