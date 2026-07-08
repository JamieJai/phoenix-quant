from __future__ import annotations

import argparse
import logging

from phoenix_core.config import load_config
from phoenix_core.pipeline import analyze_ticker, rank_universe


PHOENIX_QUANT_VERSION = "v2.1.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"Phoenix Quant {PHOENIX_QUANT_VERSION} - 설명 가능한 퀀트 리서치 플랫폼")
    parser.add_argument("--ticker", help="분석할 티커 예: MRVL")
    parser.add_argument("--top", action="store_true", help="Universe 전체 랭킹 출력")
    parser.add_argument("--top-n", type=int, default=20, help="--top 사용 시 출력할 상위 개수")
    parser.add_argument("--config", default="config/config.yaml", help="config.yaml 경로")
    parser.add_argument("--period", default="3y", help="yfinance 다운로드 기간")
    parser.add_argument("--refresh", action="store_true", help="OHLCV 캐시 무시하고 재다운로드")
    parser.add_argument("--retrain", action="store_true", help="모델/인덱스 강제 재학습")
    parser.add_argument("--k", type=int, default=None, help="유사도 검색 이웃 수")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    args = build_parser().parse_args()
    config = load_config(args.config)
    if args.top:
        report, meta = rank_universe(config, period=args.period, refresh=args.refresh, retrain=args.retrain, top_n=args.top_n, k=args.k)
    else:
        if not args.ticker:
            raise SystemExit("--ticker TICKER 또는 --top 중 하나를 지정하세요.")
        report, meta = analyze_ticker(config, args.ticker, period=args.period, refresh=args.refresh, retrain=args.retrain, k=args.k)
    print()
    print(report)
    print(f"\n(리포트 저장됨: {meta['report_path']})")


if __name__ == "__main__":
    main()
