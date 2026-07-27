# Scheduler ownership audit — 2026-07-27

Status before remediation: `DEGRADED`

This audit records the scheduler state before the approved ownership migration.
Original unit files are preserved under
`reports/scheduler_migration/20260727_scheduler_ownership_fix/before/`.

## Before-state inventory

| Unit | Scope / execution user | Schedule | Working directory | Command | Lock | Last result |
|---|---|---|---|---|---|---|
| `phoenix-auto-cycle.service/.timer` | system / root | hourly, randomized 5m | `/home/sysadmin/phoenix_ai_core_mvp` | `scripts/phoenix_auto_cycle.sh` | `/tmp/stock_ai_auto_learning.lock` from host env | failed hourly, status 1, permission denied |
| `phoenix-auto-cycle.service/.timer` | user / sysadmin | Sunday 02:00 KST | `/home/sysadmin/phoenix_ai_core_mvp` | `scripts/phoenix_auto_cycle.sh` | `/tmp/phoenix_auto_cycle.lock` from runtime env | success, 2026-07-26, 4h28m |
| `python-stock-auto-train.service/.timer` | user / sysadmin | Saturday 02:00 KST | `/home/sysadmin/python-stock` | `scripts/auto_train_cycle.sh` | `/tmp/stock_ai_auto_learning.lock` | success, 2026-07-25, dry-run promotion policy |
| `phoenix-feedback-return-cycle.service/.timer` | user / sysadmin | daily 08:30 KST | Phoenix repo | feedback refresh/update script | `/tmp/phoenix_feedback_return_cycle.lock` | success, 2026-07-27 |
| `python-stock-pre-breakout-shadow.service/.timer` | user / sysadmin | daily 22:30 KST | python-stock | shadow report script | no explicit lock | success, 2026-07-26 |
| `python-stock-research-packet.service/.timer` | user / sysadmin | daily 22:40 KST | python-stock | research packet command | no explicit lock | success, 2026-07-26 |
| `python-stock-toss-pre-breakout-paper.service/.timer` | user / sysadmin | weekdays 19:05 KST | python-stock | live-readiness daily script | no explicit lock | service success; live gate fail-closed |

The root failure is an ownership conflict, not evidence of a model or data
failure. A sysadmin-owned regular file in the sticky `/tmp` directory is reused
by a root system service; the host's protected-regular policy rejects the
redirection used to open file descriptor 9.

## Approved target state

- One ownership domain: `systemd --user` for `sysadmin`.
- Canonical hourly operational job:
  data refresh, coverage/freshness validation, feedback maturation, paper replay,
  calibration, and fail-closed health reporting.
- Canonical weekly governance job:
  python-stock dry-run candidate training followed by Phoenix research-only
  challenger, rolling OOS, and gate evaluation.
- Runtime locks live in `/home/sysadmin/python-stock/run/locks/`.
- Lock existence is not treated as liveness; every job uses `flock -n`.
- The root `phoenix-auto-cycle.timer` is disabled, not deleted.
- Existing market-window shadow/paper jobs remain user-scoped and retain their
  production behavior.
- Champion artifacts, Telegram behavior, broker routes, manual approval, and
  kill switches are unchanged.

## Rollback

1. Disable the new user timers.
2. Restore user unit files from the before-state artifact.
3. Run `systemctl --user daemon-reload` and re-enable the prior user timers.
4. Re-enable the root timer only if root ownership is explicitly chosen later.
5. Environment files are verified by SHA256 before and after migration; secret
   values are not copied into this report.

## After-state verification

The canonical `sysadmin` user timers are:

| Unit | Role | Schedule | Lock |
|---|---|---|---|
| `python-stock-hourly-ops.timer` | refresh, freshness/coverage, paper/shadow and health | hourly | `/home/sysadmin/python-stock/run/locks/scheduler-domain.lock` |
| `python-stock-weekly-governance.timer` | dry-run candidate training, rolling OOS and governance | Sunday 02:00 KST | `/home/sysadmin/python-stock/run/locks/scheduler-domain.lock` |

The superseded user timers `phoenix-auto-cycle.timer`,
`python-stock-auto-train.timer`, and `phoenix-feedback-return-cycle.timer`
were disabled. Market-window paper/shadow timers were not changed.

Verification evidence:

- Manual hourly run `20260727T114714Z`: exit 0; 120 tickers checked,
  stale 0, missing 0, train coverage 95.00%, test coverage 98.33%.
- Weekly preflight: exit 0; python-stock is dry-run, Phoenix output is
  research-only, and bot restart is disabled.
- Concurrent hourly acquisition: second process exited 75 with `LOCK_BUSY`.
- Simulated failure: exited 70; the following acquisition succeeded.
- Permission-denied matches in the regression artifacts: 0.
- All runtime directories and lock files are owned by `sysadmin`.

The root `phoenix-auto-cycle.timer` could not be disabled non-interactively
because this host requires an interactive sudo authentication. Its retained
environment is therefore set to `PHOENIX_AUTO_CYCLE_DISABLED=1`, so a scheduled
invocation exits before lock acquisition or training. The final ownership
migration remains operationally `DEGRADED_PENDING_ROOT_DISABLE` until an
administrator runs:

```bash
sudo systemctl disable --now phoenix-auto-cycle.timer
sudo systemctl daemon-reload
```

No champion artifact, broker route, Telegram production behavior, kill switch,
or manual-approval setting was changed.

## Model and research disposition

- Frozen champion: `20260714_000251`; unchanged.
- Challenger `20260726_020035`: `CHALLENGER_REJECTED`.
- `SEMI_DAMAGE_REBOUND_OVERLAY_V1` is research-only and not imported by the
  production pipeline.
- Its preregistration SHA256 is
  `eae8cd59967b159b2ff467dd6b6bbe559fabb51b36ad921a3c3071149a1f15a4`.
- Observations through 2026-07-24 are hypothesis-generation data.
- First eligible feature close: 2026-07-27 20:00 UTC.
- First eligible next-session prediction: 2026-07-28.
- Intraday overlay status remains `INTRADAY_OVERLAY_NOT_PROMOTED`.
- Live trading status remains `LIVE_BLOCKED`.

Latest paper evidence after applying the configured 2 bps commission and 5 bps
one-way slippage assumption:

- Mature 5-minute outcomes: 54 across 19.13 observation days.
- Net hit rate: 37.04%; mean net return: +0.0226%.
- Regime count: not yet evidenced.
- Predicted-return and actual/paper fill-slippage columns are absent, so the
  calibration audit is `CALIBRATION_INCOMPLETE`; it does not authorize tuning.
- Live gates still require 500 trades, 60 days, at least two regimes, validated
  kill switch, and explicit manual approval.
