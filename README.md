# Phoenix Quant

**Phoenix Quant**는 미국 주식 단기 모멘텀 후보를 선별하기 위한 **설명 가능한 Quant Research / Decision Support Platform**입니다.

이 프로젝트의 목적은 자동매매가 아니라, 사용자가 직접 판단하기 전에 참고할 수 있는 후보 종목, 시장 국면, 섹터 상태, 유사 과거 패턴, 리스크 정보를 구조화해서 제공하는 것입니다.

> **This is not financial advice.**  
> Phoenix Quant는 매수/매도 추천, 수익 보장, 자동매매 시스템이 아닙니다. 모든 결과는 연구 및 참고용입니다.

---

## 1. Project Goal

Phoenix Quant의 핵심 질문은 단순합니다.

> “지금 이 종목의 단기 패턴이 과거의 성공 사례들과 얼마나 비슷한가?”

이를 위해 Phoenix Quant는 다음 흐름으로 동작합니다.

```text
Market / Sector / OHLCV Data
        ↓
Feature Engineering
        ↓
Pattern & Similarity Analysis
        ↓
Market Regime / Sector Rotation / Correlation Context
        ↓
Decision Score & Risk Score
        ↓
Ranking / Explanation / Backtest / Telegram Alert
```

최종 목표는 다음과 같습니다.

```text
매일 한국시간 21:00
→ 미국장 후보군 분석
→ Top 후보 + 판단 근거 + 리스크 + 참고 시나리오 생성
→ Telegram으로 전송
→ 최종 매매 판단은 사용자가 직접 수행
```

---

## 2. What Phoenix Quant Is / Is Not

### Phoenix Quant Is

- 미국 주식 단기 후보 선별 도구
- 설명 가능한 의사결정 보조 시스템
- 과거 유사 패턴 기반 참고 분석기
- 시장 국면, 섹터, 상관관계, 랭킹, 리스크를 함께 보는 연구 플랫폼
- Telegram 기반 알림/질의 응답 봇으로 확장 가능한 분석 엔진

### Phoenix Quant Is Not

- 자동매매 봇
- 매수/매도 추천 시스템
- 급등 확정 예측기
- 수익 보장 전략
- 투자자문 서비스

---

## 3. Current Version

```text
Phoenix Quant v2.0.1
Purged Train/Test Validation
Telegram Multi-user Bot v0.2
```

Key improvements:

```text
v1.8   realistic next-open entry, fee/slippage
v1.9   statistical validation, random baseline
v1.9.1 block bootstrap by as-of date
v1.9.2 trade random baseline, portfolio-by-date validation
v2.0   purged train/test split, embargo, train-only grid search
v2.0.1 execution filter cash-slot handling and date-type hotfix
```

---

## 4. Core Features

### Analysis Engine

- Feature catalog
- Pattern rarity
- Similarity engine
- Decision score
- Confidence score
- Risk score
- Top similar historical cases
- Explanation-oriented output

### Market Context

- Market regime analysis
- Sector rotation analysis
- Correlation analysis
- Ranking engine

### Backtesting & Validation

- As-of-date backtesting
- Random baseline comparison
- Block bootstrap validation
- Trade simulation
- Train/Test split
- Trading-day embargo
- Cash-slot execution filter
- Portfolio-by-date performance analysis

### Telegram Integration

- `/top` command
- `/analyze TICKER` command
- `/regime` command
- `/whoami` command
- Multi-user chat_id authorization
- Daily 21:00 KST alert support

---

## 5. Project Structure

```text
phoenix_ai_core_mvp/
├── main.py
├── benchmark.py
├── telegram_bot_run.py
├── telegram_daily_2100.py
├── phoenix_core/
│   ├── config.py
│   ├── models.py
│   ├── interfaces.py
│   ├── registry.py
│   ├── engines/
│   │   ├── backtest_engine.py
│   │   ├── feature_importance_engine.py
│   │   ├── experiment_engine.py
│   │   └── statistical_validation_engine.py
│   ├── trade/
│   │   ├── trade_models.py
│   │   ├── trade_rules.py
│   │   └── trade_sim_engine.py
│   └── services/
│       ├── telegram_sender.py
│       ├── telegram_message_formatter.py
│       └── telegram_command_bot.py
├── reports/
└── config/
    └── config.yaml
```

---

## 6. Quick Start

### 6.1 Install Dependencies

```powershell
python -m pip install -U pandas numpy scikit-learn yfinance pyyaml
```

### 6.2 Run Single Ticker Analysis

```powershell
python main.py --ticker NVDA
```

Refresh data:

```powershell
python main.py --ticker NVDA --refresh
```

Retrain pattern/similarity models:

```powershell
python main.py --ticker NVDA --refresh --retrain
```

### 6.3 Run Top Ranking

```powershell
python main.py --top --top-n 10
```

---

## 7. Benchmark

### 7.1 Basic Monthly Benchmark

```powershell
python benchmark.py --start 2025-01-01 --end 2026-07-06 --top-list 5,10,20 --frequency monthly --random-baseline 1000 --bootstrap 1000 --trade-sim
```

### 7.2 Purged Train/Test Validation

```powershell
python benchmark.py --train-test `
  --train-start 2023-01-01 `
  --train-end 2024-12-20 `
  --test-start 2025-01-16 `
  --test-end 2026-07-06 `
  --embargo-trading-days 10 `
  --top-n 10 `
  --top-list 5,10,20 `
  --frequency monthly `
  --random-baseline 1000 `
  --bootstrap 1000 `
  --grid-search `
  --trade-sim `
  --min-price 5 `
  --min-dollar-volume 10000000 `
  --max-gap-open 0.08 `
  --entry-penalty-bps 20
```

---

## 8. Validation Methodology

Phoenix Quant v2.0.1 uses the following validation rules.

### 8.1 No Future Leakage

- Features are calculated only from data available up to each `as_of` date.
- Future returns are used only in label/evaluation paths.
- Train/Test split is separated by trading-day embargo.

### 8.2 Purged Train/Test Split

Latest run:

```text
Train Start: 2023-01-01
Train End:   2024-12-20
Test Start:  2025-01-16
Test End:    2026-07-06

Train last trading day: 2024-12-20
Minimum test start after embargo: 2025-01-08
Embargo trading days: 10
```

### 8.3 Execution Assumptions

```text
Entry Mode: next_open
Fee: 1.5 bps
Slippage: 5.0 bps
Entry Penalty: 20.0 bps
Min Price: $5.0
Min Dollar Volume: $10,000,000
Max Gap Open: 8.0%
```

### 8.4 Cash Slot Logic

Phoenix Quant does not replace filtered stocks with the next candidate.

If a Top-N candidate fails execution filters, that slot becomes cash.

```text
Top 10 slots fixed
Filtered candidate → cash return 0
Portfolio return = sum(all slot returns including cash) / Top N
```

This avoids overstating performance by silently replacing untradable candidates.

---

## 9. Latest v2.0.1 OOS Result Snapshot

Test setting:

```text
Test Period: 2025-01-16 ~ 2026-07-06
Frequency: monthly
Top N: 10
Test Dates: 18
Slots: 180
Active Trades: 176
Cash Slots: 4
```

### 9.1 OOS Fixed Rule Results

| OOS Rank | Rule | Portfolio Mean | Random Mean | Alpha | p-value | MDD | Positive Date Rate |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | TP 6 / SL 4 / Hold 7 | 0.61% | 0.34% | 0.27% | 0.140 | 3.95% | 55.56% |
| 2 | Default 5/3/5 | 0.59% | 0.38% | 0.21% | 0.163 | 2.94% | 55.56% |
| 3 | TP 8 / SL 4 / Hold 10 | 0.61% | 0.38% | 0.23% | 0.214 | 6.37% | 61.11% |
| 4 | TP 8 / SL 4 / Hold 7 | 0.56% | 0.47% | 0.09% | 0.361 | 6.96% | 66.67% |
| 5 | TP 8 / SL 2 / Hold 7 | 0.55% | 0.54% | 0.01% | 0.479 | 4.22% | 55.56% |
| 6 | TP 8 / SL 4 / Hold 5 | 0.44% | 0.47% | -0.03% | 0.548 | 7.77% | 66.67% |

### 9.2 Interpretation

The best OOS rule was:

```text
TP 6% / SL 4% / Hold 7D
Portfolio mean: 0.61%
Random mean:    0.34%
Alpha:          0.27%
p-value:        0.140
MDD:            3.95%
```

The default rule remains competitive:

```text
Default TP 5% / SL 3% / Hold 5D
Portfolio mean: 0.59%
Random mean:    0.38%
Alpha:          0.21%
p-value:        0.163
MDD:            2.94%
```

Current conclusion:

```text
Phoenix Quant did not collapse out-of-sample.
However, the OOS edge is not yet statistically strong enough to claim a validated trading alpha.
```

More precisely:

```text
Phoenix appears to select candidates with better-than-random tendencies,
but the current sample size and p-values are not sufficient for a strong claim.
```

---

## 10. Train Grid Search Snapshot

Train grid search is **exploratory** and should not be interpreted as production-ready optimization.

| Train Rank | Rule | Train Portfolio Mean | 2023 Return | 2024 Return | Min Subperiod Return | Stability Score |
|---:|---|---:|---:|---:|---:|---:|
| 1 | TP 8 / SL 4 / Hold 7 | 1.06% | 0.20% | 1.92% | 0.20% | 1.914 |
| 2 | TP 8 / SL 4 / Hold 10 | 1.18% | 0.51% | 1.85% | 0.51% | 1.933 |
| 3 | TP 8 / SL 4 / Hold 5 | 0.73% | -0.13% | 1.59% | -0.13% | 0.788 |
| 4 | TP 6 / SL 4 / Hold 7 | 0.83% | -0.07% | 1.74% | -0.07% | 0.837 |
| 5 | TP 8 / SL 2 / Hold 7 | 0.65% | 0.02% | 1.29% | 0.02% | 1.936 |

Observation:

```text
Top train rules performed well in-sample,
but several rules relied heavily on the 2024 market regime.
Train rank did not perfectly transfer to OOS rank.
```

This is why Phoenix v2.0.1 treats grid search as candidate discovery, not proof.

---

## 11. Telegram Bot

### 11.1 Environment

Create `.env` in the project root.

```env
TELEGRAM_BOT_TOKEN=your_bot_token

TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_ALLOWED_CHAT_IDS=A_chat_id,B_chat_id,C_chat_id
TELEGRAM_DAILY_CHAT_IDS=A_chat_id,B_chat_id,C_chat_id

TELEGRAM_ALLOW_ALL=0

PHOENIX_PROJECT_DIR=.
PHOENIX_PYTHON=python
PHOENIX_COMMAND_TIMEOUT=240
PHOENIX_TOP_N=10
PHOENIX_REFRESH_ON_ANALYZE=0
PHOENIX_REFRESH_ON_TOP=0

PHOENIX_DAILY_TOP_N=10
PHOENIX_DAILY_REFRESH=1
PHOENIX_DAILY_TIMEOUT=420
```

Never commit `.env`.

```gitignore
.env
*.env
```

### 11.2 Run Telegram Bot

```powershell
python telegram_bot_run.py
```

Supported commands:

```text
/ping
/whoami
/status
/top
/top 5
/top 10 refresh
/analyze NVDA
/analyze NVDA refresh
/regime
/help
```

### 11.3 Multi-user Logic

```text
A -> /analyze NVDA -> response only to A
B -> /analyze TSLA -> response only to B
C -> /top 5        -> response only to C
```

Daily alerts can be broadcast to all users listed in `TELEGRAM_DAILY_CHAT_IDS`.

### 11.4 Daily 21:00 KST Alert

One-time test:

```powershell
python telegram_daily_2100.py --once
```

Production recommendation:

```text
Use Windows Task Scheduler
Trigger: every day at 21:00 KST
Action: python telegram_daily_2100.py --once
```

---

## 12. Responsible Output Policy

Phoenix Quant should use language like:

```text
관심 후보
단기 모멘텀 후보
과거 유사 패턴 기준
참고용 시나리오
리스크 높음/낮음
```

Phoenix Quant should not use language like:

```text
급등 확정
매수 추천
수익 보장
검증된 필승 전략
```

---

## 13. Current Limitations

- Current universe is not fully survivorship-bias-free.
- Data quality depends on public market data sources.
- Current sample size is still limited.
- Monthly test frequency gives relatively few OOS dates.
- Grid search remains in-sample exploratory.
- Portfolio constraints are still simple.
- No broker execution or order management is implemented.
- No automatic trading is implemented.

---

## 14. Roadmap

### v2.1

- Rule ensemble reporting
- Daily / weekly benchmark frequency
- Better OOS rule selection criteria
- More robust Telegram message formatter
- Separate ranking alpha vs trade-rule alpha reporting

### v2.2

- Walk-forward validation
- ATR-based exits
- Volatility-adjusted position sizing
- Portfolio risk constraints

### v2.3+

- Optimizer after enough sample size
- Broader historical universe
- Survivorship-bias mitigation
- Sector-aware portfolio construction
- Fractional Kelly only, never full Kelly

---

## 15. Disclaimer

This project is for research and decision-support purposes only. It is not investment advice, not a recommendation to buy or sell securities, and not a guarantee of performance. Users are responsible for their own decisions.

---

## 16. Status Summary

```text
Current status:
- Research engine: working
- Backtest engine: working
- Statistical validation: working
- Purged Train/Test validation: working
- Telegram command bot: working
- Daily alert flow: ready for testing
- Production trading: not implemented and not intended
```
