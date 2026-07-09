# Phoenix Toplive Separation Handoff - 2026-07-09

## Scope Completed

Separated the experimental intraday reranking flow from the stable `/top` Telegram command.

Important boundary:

- No automatic trading was added.
- `/top` is again the stable daily-ranking path.
- `/toplive` and `/top live` are explicit experimental intraday rerank commands.
- `/hot` remains a separate intraday strength filter.
- Automatic model/rule promotion still restarts the Telegram bot only after a successful promotion to `models/current/`; it does not validate service command semantics.

## Git State

Current checkout at handoff time:

```text
branch: fix/separate-toplive
remote tracking: origin/fix/separate-toplive
commit: 78ec6df Separate experimental toplive flow
working tree: clean
```

Previous relevant commits:

```text
f68cc62 Improve intraday top ranking
dc9c9df Add guarded Phoenix auto learning pipeline
```

The branch has been pushed to GitHub:

```text
origin/fix/separate-toplive
PR URL suggested by GitHub:
https://github.com/JamieJai/phoenix-quant/pull/new/fix/separate-toplive
```

## Behavior Changes

### `/top`

Restored to conservative behavior:

- Executes `main.py --top --top-n top_n`.
- Keeps daily ranking order in the main Telegram summary.
- Uses `compact_ranking_output(out, max_rows=top_n)` as the main summary.
- Adds Intraday Overlay only as a separate block below the daily summary.
- Does not replace the summary with `_format_adjusted_top()`.

### `/toplive` and `/top live`

New explicit experimental flow:

- Executes daily candidate pool with at least `PHOENIX_TOP_CANDIDATE_N`, minimum 50.
- Applies intraday `adjusted_score` reranking.
- Output title/body clearly marks the feature as experimental:
  - `실험: 장중 재정렬`
  - `Experimental Intraday Rerank`
- Must not replace `/top` until enough forward evidence exists.

### `/hot`

Kept as a separate intraday strength filter:

- Requires current price.
- Requires positive move versus previous close.
- Requires above VWAP.
- Requires positive 10m or 30m move.
- Requires intraday score above `PHOENIX_HOT_INTRADAY_MIN_SCORE`.
- Output is a reference-only `장중 관심 후보`, not a buy/sell recommendation.

## Environment Variable Policy

Kept existing meaning:

```text
PHOENIX_INTRADAY_OVERLAY_RERANK
```

This controls only the separate Intraday Overlay block ordering under `/top`.

Added/used clear variables:

```text
PHOENIX_TOP_CANDIDATE_N=50
PHOENIX_HOT_INTRADAY_MIN_SCORE=55
PHOENIX_TOP_SHADOW_LOG_DIR=results/top_shadow_compare
```

Did not add:

```text
PHOENIX_TOP_INTRADAY_RERANK
```

## Label Semantics

Documented in README, Telegram README, help message, and `decision_engine.py` comment:

```text
관심: 우선 관찰 후보
관찰: 일부 조건 양호, 추가 확인 필요
보류: 조건 부족, 매매 후보로 해석 금지
제외: 제외 대상
```

## Files Changed

Code:

- `phoenix_core/services/telegram_command_bot.py`
  - Restores `/top` default behavior.
  - Adds `/toplive` and `/top live` routing.
  - Keeps `/hot` separate.
  - Adds shadow snapshot writing.

- `phoenix_core/services/intraday_message_formatter.py`
  - Keeps strict ticker extraction from ranking rows only.
  - Logs excluded no-data intraday contexts with `[intraday overlay] excluded_no_data ...`.

- `phoenix_core/services/telegram_message_formatter.py`
  - Updates `/help` text.
  - Adds status field for shadow log dir.

- `phoenix_core/engines/decision_engine.py`
  - Adds concise label meaning comment.

Script:

- `scripts/top_shadow_compare.py`
  - Generates/evaluates forward shadow comparison artifacts.
  - Uses artifact date as snapshot date and evaluates only future bars after that date.

Docs:

- `docs/operations/top-shadow-compare.md`
- `README.md`
- `README_TELEGRAM_BOT.md`
- `.env.example`
- `.gitignore`

Tests:

- `tests/test_core_synthetic.py`
  - Verifies `/top` keeps daily order.
  - Verifies `/toplive` can rerank by adjusted score.
  - Verifies `/hot` is separate from `/top` and `/toplive`.
  - Verifies `XGB`, `TP`, `SL` are not extracted as tickers.
  - Verifies no conflicting env var is introduced.

## Validation Performed

Commands passed:

```bash
./.venv/bin/python -m py_compile   phoenix_core/services/telegram_command_bot.py   phoenix_core/services/telegram_message_formatter.py   phoenix_core/services/intraday_message_formatter.py   phoenix_core/engines/decision_engine.py   scripts/top_shadow_compare.py   tests/test_core_synthetic.py

git diff --check
./.venv/bin/python scripts/top_shadow_compare.py --help
./.venv/bin/python tests/test_core_synthetic.py
./.venv/bin/python main.py --top --top-n 10
```

`main.py --top --top-n 10` succeeded and produced the daily ranking report with `Label | Reason` columns.

## Runtime State At Handoff

Telegram bot:

```text
phoenix-telegram-bot.service: active running
Main PID: 269485
Started: 2026-07-09 16:57:19 KST
Checkout used by service: /home/sysadmin/phoenix_ai_core_mvp
Current branch: fix/separate-toplive
```

Auto cycle:

```text
phoenix-auto-cycle.timer: active
phoenix-auto-cycle.service: activating/running at handoff
Main PID: 269997
Running benchmark PID: 270393
Command includes: benchmark.py --train-test --rank-mode decision --xgb-blend-weight 0.0
```

Important: the auto-cycle was restarted after the branch was pushed and is currently running from the working tree checkout. The service runs as root and may create root-owned files under generated artifact directories.

## Operational Notes

Automatic learning already includes bot restart after successful promotion:

```bash
scripts/phoenix_auto_cycle.sh
```

Relevant behavior:

- If promotion gate returns `0`, it runs `systemctl restart "$BOT_SERVICE"`.
- If promotion is rejected or errors, bot restart is skipped.
- This restart covers model/rule promotion, not ordinary service-code deployment.

Manual Telegram bot restart was performed for this code branch because command/formatter code changed.

## Next Recommended Tasks

1. Monitor the currently running auto-cycle until it finishes:

```bash
systemctl status phoenix-auto-cycle.service --no-pager --lines=30
journalctl -u phoenix-auto-cycle.service -f
```

2. After the cycle completes, inspect status and gate result:

```bash
cd /home/sysadmin/phoenix_ai_core_mvp
./scripts/phoenix_auto_status.py
./scripts/phoenix_failure_analysis.py --limit 50
```

3. Test Telegram commands manually:

```text
/top 10
/toplive 10
/top live 10
/hot 10
/status
/help
```

Expected behavior:

- `/top` daily summary order is unchanged and overlay appears below it.
- `/toplive` clearly says experimental and can reorder candidates.
- `/hot` only shows intraday condition matches.

4. Open PR from `fix/separate-toplive` and review before merging into `main`.

5. If merged into `main`, decide deployment flow explicitly:

```bash
git switch main
git pull --ff-only
sudo systemctl restart phoenix-telegram-bot.service
```

If systemd interactive auth is unavailable in the agent session, use an interactive admin terminal.

6. Keep `/toplive` experimental until `results/top_shadow_compare/YYYYMMDD/summary.json` accumulates enough forward observations.

## Cautions

- Do not let `/toplive` replace `/top` by default without forward validation.
- Do not add a similarly named `PHOENIX_TOP_INTRADAY_RERANK`; use `PHOENIX_TOP_CANDIDATE_N` for candidate pool sizing.
- Do not interpret `보류` as a trade candidate.
- Do not add automatic trading behavior.
- Systemd auth limitations mean the agent may not be able to start/stop timers directly without user-side sudo.
