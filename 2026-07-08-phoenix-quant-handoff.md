# Phoenix Quant Handoff — 2026-07-08

## 1. Version 확인 및 표기 정리

현재 레포 문서와 코드의 버전 표기가 서로 불일치한다.

확인된 상태:

- `README.md` 현재 버전 요약은 다음을 포함한다.
  - `Phoenix Quant Core: v1.2 report format 유지`
  - `Benchmark / Validation: v2.0.1`
  - `Telegram Bot: v0.4`
  - `Intraday Context Layer: v2.1`
  - `Compact Analyze Hotfix: v2.1.1`
- `main.py` argparse 설명은 아직 `Phoenix Quant v1.2 - 설명 가능한 퀀트 리서치 플랫폼`으로 표시된다.
- `phoenix_core/pipeline.py` 리포트 헤더는 아직 `Phoenix Quant v1.2`, 랭킹 헤더는 `Phoenix Quant v1.2 Ranking`으로 표시된다.

판단:

- 사용자가 보는 Telegram/CLI 메시지의 제품 표기는 `v1.2`가 아니라 현재 통합 운영 버전 기준 `v2.1.1`로 맞추는 것이 타당하다.
- 단, `Core: v1.2 report format 유지`는 “엔진 전체 버전”이 아니라 오래된 리포트 포맷 명칭처럼 보이므로 README에서는 `Report Format: legacy v1.2` 또는 `Core Report Format: legacy v1.2`로 분리 표기한다.

권장 표기:

```text
Phoenix Quant Platform: v2.1.1
Benchmark / Validation: v2.0.1
Intraday Context Layer: v2.1
Report Format: legacy v1.2-compatible
```

## 2. 즉시 수정할 파일

### 2.1 `main.py`

현재:

```python
parser = argparse.ArgumentParser(description="Phoenix Quant v1.2 - 설명 가능한 퀀트 리서치 플랫폼")
```

변경:

```python
PHOENIX_QUANT_VERSION = "v2.1.1"
parser = argparse.ArgumentParser(description=f"Phoenix Quant {PHOENIX_QUANT_VERSION} - 설명 가능한 퀀트 리서치 플랫폼")
```

### 2.2 `phoenix_core/pipeline.py`

상단 import 아래에 추가:

```python
PHOENIX_QUANT_VERSION = "v2.1.1"
```

현재:

```python
"Phoenix Quant v1.2"
"Phoenix Quant v1.2 Ranking"
```

변경:

```python
f"Phoenix Quant {PHOENIX_QUANT_VERSION}"
f"Phoenix Quant {PHOENIX_QUANT_VERSION} Ranking"
```

### 2.3 `README.md`

현재:

```text
Phoenix Quant Core: v1.2 report format 유지
Benchmark / Validation: v2.0.1
Telegram Bot: v0.4
Intraday Context Layer: v2.1
Compact Analyze Hotfix: v2.1.1
```

변경:

```text
Phoenix Quant Platform: v2.1.1
Benchmark / Validation: v2.0.1
Telegram Bot: v0.4
Intraday Context Layer: v2.1
Report Format: legacy v1.2-compatible
```

## 3. v1.3/v2.x 고도화 작업 우선순위

현재 최신 검증상 OOS 알파는 존재하지만 p-value가 강하게 유의하지 않다.
따라서 기능을 한꺼번에 넣지 말고, 기능별 ON/OFF가 가능하도록 구현하고 각각 OOS 재검증한다.

우선순위:

1. 유사 사례 날짜 클러스터 제거
2. Daily / Intraday 4분면 시나리오 라벨
3. Earnings Window가 아니라 Event Shock Proxy 우선 도입
4. Sector 5D / 20D / 60D 분리
5. VWAP ATR/Z-score 정규화

## 4. Codex 작업 지시

```text
JamieJai/phoenix-quant 레포에서 Phoenix Quant 버전 표기와 v1.3 고도화 handoff를 정리해줘.

먼저 버전 표기부터 수정해줘.

1. main.py의 argparse description에서 Phoenix Quant v1.2를 v2.1.1로 바꿔줘.
2. phoenix_core/pipeline.py의 리포트 헤더 `Phoenix Quant v1.2`, `Phoenix Quant v1.2 Ranking`을 v2.1.1 기준으로 바꿔줘.
3. README.md의 현재 버전 요약을 다음처럼 정리해줘.
   - Phoenix Quant Platform: v2.1.1
   - Benchmark / Validation: v2.0.1
   - Telegram Bot: v0.4
   - Intraday Context Layer: v2.1
   - Report Format: legacy v1.2-compatible

그 다음 docs/handoff/2026-07-08-phoenix-quant-handoff.md 문서를 추가해줘.
문서 내용은 다음 내용을 포함해야 해.

- 현재 버전 표기 불일치 요약
- v1.2는 현재 플랫폼 버전이 아니라 legacy report format에 가까움
- 사용자 노출 메시지는 v2.1.1로 통일
- v1.3/v2.x 고도화 우선순위
  1. similarity_engine.py 유사 사례 날짜 클러스터 제거
  2. intraday_overlay_ranker.py Daily/Intraday 4분면 시나리오 라벨
  3. Event Shock Proxy
  4. Sector 5D/20D/60D 분리
  5. VWAP ATR/Z-score 정규화

중요:
- 기능 고도화는 한 번에 다 넣지 말고 기능 플래그로 ON/OFF 가능하게 설계
- OOS p-value가 약하므로 과최적화 방지
- 먼저 버전 표기 수정과 handoff 문서 커밋부터 진행
```

## 5. 서버에서 바로 실행할 패치 명령

```bash
cd ~/phoenix_ai_core_mvp

python3 - <<'PY'
from pathlib import Path

# 1) main.py 버전 표기 수정
p = Path('main.py')
s = p.read_text(encoding='utf-8')
if 'PHOENIX_QUANT_VERSION' not in s:
    s = s.replace(
        'from phoenix_core.pipeline import analyze_ticker, rank_universe\n',
        'from phoenix_core.pipeline import analyze_ticker, rank_universe\n\n\nPHOENIX_QUANT_VERSION = "v2.1.1"\n'
    )
s = s.replace(
    'parser = argparse.ArgumentParser(description="Phoenix Quant v1.2 - 설명 가능한 퀀트 리서치 플랫폼")',
    'parser = argparse.ArgumentParser(description=f"Phoenix Quant {PHOENIX_QUANT_VERSION} - 설명 가능한 퀀트 리서치 플랫폼")'
)
p.write_text(s, encoding='utf-8')

# 2) pipeline.py 리포트 헤더 수정
p = Path('phoenix_core/pipeline.py')
s = p.read_text(encoding='utf-8')
if 'PHOENIX_QUANT_VERSION' not in s:
    s = s.replace(
        'from .registry import EngineRegistry\n',
        'from .registry import EngineRegistry\n\n\nPHOENIX_QUANT_VERSION = "v2.1.1"\n'
    )
s = s.replace('"Phoenix Quant v1.2",', 'f"Phoenix Quant {PHOENIX_QUANT_VERSION}",')
s = s.replace('"Phoenix Quant v1.2 Ranking",', 'f"Phoenix Quant {PHOENIX_QUANT_VERSION} Ranking",')
p.write_text(s, encoding='utf-8')

# 3) README 현재 버전 요약 수정
p = Path('README.md')
s = p.read_text(encoding='utf-8')
s = s.replace(
    'Phoenix Quant Core: v1.2 report format 유지\nBenchmark / Validation: v2.0.1\nTelegram Bot: v0.4\nIntraday Context Layer: v2.1\nCompact Analyze Hotfix: v2.1.1',
    'Phoenix Quant Platform: v2.1.1\nBenchmark / Validation: v2.0.1\nTelegram Bot: v0.4\nIntraday Context Layer: v2.1\nReport Format: legacy v1.2-compatible'
)
p.write_text(s, encoding='utf-8')

# 4) handoff 문서 생성
handoff = Path('docs/handoff/2026-07-08-phoenix-quant-handoff.md')
handoff.parent.mkdir(parents=True, exist_ok=True)
handoff.write_text('''# Phoenix Quant Handoff — 2026-07-08

## Version Cleanup

- User-facing CLI/report headers were still showing `Phoenix Quant v1.2`.
- Current integrated platform should be exposed as `Phoenix Quant Platform: v2.1.1`.
- `v1.2` should be treated as legacy report format compatibility, not the current platform version.

## Files Updated

- `main.py`
  - argparse description now uses `PHOENIX_QUANT_VERSION = "v2.1.1"`.
- `phoenix_core/pipeline.py`
  - analyze report header now uses `Phoenix Quant v2.1.1`.
  - ranking report header now uses `Phoenix Quant v2.1.1 Ranking`.
- `README.md`
  - version summary now separates platform version from legacy report format.

## v1.3 / v2.x Handoff Priorities

1. Similarity date-cluster dedupe
   - Avoid counting multiple tickers from the same market shock date as independent evidence.
   - Implement in `similarity_engine.py` with feature flag.

2. Daily / Intraday scenario labels
   - Add four-quadrant interpretation in `intraday_overlay_ranker.py`.
   - Display friendly Korean scenario messages in Telegram overlay.

3. Event Shock Proxy
   - Do not call it Earnings Window until an actual earnings calendar exists.
   - Start with gap + volume shock + post-gap selloff proxy.

4. Sector 5D / 20D / 60D split
   - Separate short/mid/long sector context.
   - Prefer penalty relaxation over contrarian bonus in early versions.

5. VWAP normalization
   - Normalize VWAP distance by ATR or z-score.
   - Keep raw `vwap_position_pct` for display compatibility.

## Validation Rule

Do not claim improvement from one combined change.
Each feature must be independently toggled and re-tested with purged train/test OOS validation.
''', encoding='utf-8')
PY

git diff -- main.py phoenix_core/pipeline.py README.md docs/handoff/2026-07-08-phoenix-quant-handoff.md
git add main.py phoenix_core/pipeline.py README.md docs/handoff/2026-07-08-phoenix-quant-handoff.md
git commit -m "docs: add 2026-07-08 Phoenix Quant handoff"
git push origin main
```
