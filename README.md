# Phoenix Quant v1.8

**Explainable Quant Research Platform**

Phoenix Quant는 **설명 가능한(Explainable) 퀀트 리서치 플랫폼**입니다.
단순히 종목을 추천하는 것이 아니라, **왜 그런 결과가 나왔는지**, **과거 어떤 사례와 유사한지**, **백테스트에서 실제로 어떤 성과를 보였는지**를 함께 제공합니다.

> 연구용 프로젝트이며 투자 자문이 아닙니다.

---

# 주요 기능

## Market Intelligence
- Market Regime Engine
- Sector Rotation Engine
- Correlation Engine

## Pattern Analysis
- Feature Engine (13개 Baseline Feature)
- Isolation Forest 기반 Pattern Engine
- Cosine Similarity Engine

## Decision Engine
- Suitability Score
- Confidence Score
- Risk Score
- Explainable Score Breakdown

## Ranking Engine
- Universe Top-N Ranking
- Sector / Market Context 반영

## Trade Simulation Engine (v1.6~)
- TP / SL / Hold 기반 매매 시뮬레이션
- Win Rate
- Profit Factor
- MDD
- Sharpe

## Benchmark Engine (v1.8)
- Walk-forward(as-of) 방식 검증
- Random Baseline 비교
- Alpha 측정
- Trade Simulation
- Grid Search
- 현실적인 체결 가정
  - 기본 진입: Next Open
  - Fee: 1.5 bps
  - Slippage: 5 bps

---

# 실행

```bash
pip install -r requirements.txt

python main.py --ticker MRVL --refresh --retrain
python main.py --top --top-n 20
```

백테스트

```bash
python benchmark.py --start 2025-01-01 --end 2026-07-06 --top-list 5,10,20 --frequency monthly --random-baseline 1000
```

Trade Simulation + Grid Search

```bash
python benchmark.py ^
  --start 2025-01-01 ^
  --end 2026-07-06 ^
  --trade-sim ^
  --grid-search
```

---

# 현재 구현 완료

- Engine Registry Architecture
- Feature Catalog
- Pattern Engine
- Similarity Engine
- Market Regime Engine
- Sector Rotation Engine
- Correlation Engine
- Decision Engine
- Explain Engine
- Ranking Engine
- Trade Simulation Engine
- Benchmark Engine
- Random Baseline
- Grid Search

---

# Roadmap

## v1.9
- Train/Test Split
- Out-of-Sample Validation

## v2.0
- Walk-Forward Optimization

## v2.1
- Bootstrap
- Monte Carlo
- p-value / Confidence Interval

## v2.2
- Decision Weight Optimization

## v2.3
- Portfolio Optimizer
- Kelly Position Sizing
- Sector Diversification

---

# Disclaimer

본 프로젝트는 과거 데이터를 이용한 통계적 연구 도구입니다.
투자 판단과 투자 손실에 대한 책임은 사용자에게 있습니다.
