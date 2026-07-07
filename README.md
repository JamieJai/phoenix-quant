# Phoenix Quant v1.2

Explainable Quant Research Platform MVP.

v1.2는 기존 Phoenix AI Core MVP(v1.1)에 다음 기능을 추가한 버전입니다.

## 추가 기능

- Market Regime Engine (`regime_v1`)
  - QQQ/SPY/IWM/SMH/SOXX/VIX 기반 시장 국면 분류
  - 예: `AI Growth Rotation`, `Broad Bull`, `Risk Off`, `Bear Trend`, `Neutral / Mixed`
- Sector Rotation Engine (`rotation_v1`)
  - XLK/XLY/XLC/XLF/XLV/XLI/XLE/XLP/XLU/XLB/XLRE/SMH/SOXX/QQQ ETF 강도 계산
- Correlation Engine (`correlation_v1`)
  - 티커와 QQQ/SPY/섹터 ETF/주요 동종주 간 90/180/360일 상관계수 계산
- Ranking Engine (`ranking_v1`)
  - Universe 전체를 분석해 Top-N 랭킹 출력
- 출력 개선
  - `Pattern`을 `Pattern Rarity`로 변경
  - Pattern Rarity는 “상승확률”이 아니라 “이례도”임을 명시
  - 동일 유사사례 표시 중복을 종목당 최대 1개로 완화

## 실행

```bash
pip install -r requirements.txt
python main.py --ticker MRVL --refresh --retrain
```

랭킹:

```bash
python main.py --top --top-n 20 --refresh --retrain
```

두 번째부터는 캐시/모델을 재사용합니다.

```bash
python main.py --ticker MRVL
python main.py --top --top-n 20
```

## 테스트

```bash
python tests/test_core_synthetic.py
```

## 주의

본 프로젝트는 과거 패턴 기반 통계적 참고 자료를 제공하는 연구용 도구이며, 투자 자문이 아닙니다.
