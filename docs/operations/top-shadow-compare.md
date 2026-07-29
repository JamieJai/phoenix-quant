# Top Shadow Compare Operations

Phoenix Quant의 `/top` 기본 동작은 검증된 일봉 daily ranking을 유지합니다. `/toplive`와 `/hot`은 장중 데이터를 쓰는 별도 참고 기능이며, 충분한 전향적 기록이 쌓이기 전까지 `/top`을 대체하지 않습니다.

## Command Roles

```text
/top      일봉 기반 후보. Telegram summary는 daily ranking 순서를 유지한다. Intraday Overlay는 별도 참고 블록이다.
/toplive  실험: Daily 후보 50개 이상을 intraday adjusted_score로 재정렬한다. OOS 검증 전 기능이다.
/hot      장중 강세 조건 충족 후보만 필터링한다.
```

`PHOENIX_INTRADAY_OVERLAY_RERANK`는 `/top` 아래 Intraday Overlay 블록 내부 정렬에만 사용합니다. `/toplive`의 실험 후보 풀 크기는 `PHOENIX_TOP_CANDIDATE_N`을 사용합니다. `PHOENIX_TOP_INTRADAY_RERANK` 같은 유사 변수는 사용하지 않습니다.

## Forward Shadow Logging

`/top` 실행 시 bot은 사용자에게 보이는 daily ranking 결과를 바꾸지 않고 아래 snapshot을 남깁니다.

```text
results/top_shadow_compare/YYYYMMDD/legacy_candidates.csv
results/top_shadow_compare/YYYYMMDD/toplive_candidates.csv
results/top_shadow_compare/YYYYMMDD/hot_candidates.csv
results/top_shadow_compare/YYYYMMDD/summary.json
```

운영 로그에는 저장 경로와 각 후보 수가 `[top shadow]` prefix로 남습니다. yfinance 데이터가 없어 overlay에서 제외된 ticker는 `[intraday overlay] excluded_no_data` prefix로 확인할 수 있습니다.

## Manual Compare Script

후보 생성과 성과 요약을 명시적으로 실행합니다.

```bash
cd /home/sysadmin/phoenix_ai_core_mvp
./.venv/bin/python scripts/top_shadow_compare.py --candidate-n 50 --top-n 10
```

기존 CSV를 다시 평가하려면:

```bash
./.venv/bin/python scripts/top_shadow_compare.py --date YYYYMMDD --evaluate-only
```

## Metrics

`summary.json`에는 다음 지표를 기록합니다. 미래 데이터가 아직 충분하지 않으면 evaluated count가 낮거나 일부 값이 null일 수 있습니다.

```text
1일 후 수익률
3일 후 수익률
5일 후 수익률
5일 내 +5% 도달률
5일 내 -3% 먼저 도달률
평균 최대낙폭
legacy/toplive/hot 간 중복 ticker 수
라벨별 평균 성과
```

## Intraday Similarity Checklist

장중 1분봉 유사도는 `/toplive`와 `/hot`의 참고 관찰 항목으로만 기록합니다. 일봉 기반 `/top` 순위를 대체하거나 promotion gate를 완화하는 근거로 쓰지 않습니다.

체크할 항목:

- 관측 시각, 비교 ticker pair, 사용한 intraday window를 함께 기록한다.
- 같은 pair의 일봉 유사도와 1분봉 유사도를 분리해서 해석한다.
- 이후 1D/3D/5D 성과와 장중 follow-through를 shadow 결과에 남긴다.
- 유사도가 높았지만 성과가 나빴으면 operator feedback reason에 `false_similarity`를 사용한다.

2026-07-10 관찰 메모:

- INTC와 MRVL이 1분봉 기준으로 높은 장중 유사도를 보임.
- 다음 shadow/feedback 점검 때 daily similarity가 아닌 intraday similarity pair로 따로 확인한다.

## Label Semantics

```text
관심: 우선 관찰 후보
관찰: 일부 조건 양호, 추가 확인 필요
보류: 조건 부족, 매매 후보로 해석 금지
제외: 제외 대상
```

이 문서와 산출물은 참고용 분석을 위한 운영 자료입니다. 자동매매, 매수/매도 추천, 투자 자문으로 사용하지 않습니다.
