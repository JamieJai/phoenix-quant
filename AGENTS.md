# Phoenix Quant repository guidance

## Safety and scope

- Treat all outputs as research support, never as automatic trading instructions.
- Keep model promotion and deployed scoring changes separate from exploratory analysis.
- Do not resume `.phoenix_auto_cycle.pause` or change host-local deployment settings without an explicit user request.
- Preserve unrelated working-tree changes and root-owned runtime artifacts.

## Validation integrity

- Use only information available at prediction time; reject lookahead and post-outcome features.
- Store timestamps in UTC and align features by the relevant market session.
- Require data coverage, freshness, leakage audit, and rolling OOS checks before promotion.
- Prefer walk-forward or purged time-series validation over random splits.
- Compare every feature or overlay with a frozen base-only ablation on identical OOS windows.
- Report sample size, active trades, cash weight, MDD, alpha, p-value, and calibration where applicable.
- Treat an LLM conclusion as a hypothesis or risk flag until converted to an explicit feature and validated OOS.

## Research workflow

- Interpret `일일 점검 명령 실행` as an explicit invocation of the repo skill `phoenix-quant-research` and run its complete daily audit without asking for the longer prompt.
- Start operational audits with `scripts/phoenix_research_packet.py`.
- Use `scripts/phoenix_data_coverage_audit.py` for split coverage and freshness evidence.
- Use `scripts/phoenix_auto_status.py` and `scripts/phoenix_failure_analysis.py` for champion/challenger evidence.
- Record each experiment's hypothesis, feature definition, target, windows, embargo, baseline, and acceptance criteria.
- Keep cross-market adjustments as a bounded overlay until ablation proves stable contribution.

## Verification

- Run focused tests first, then the relevant synthetic suite.
- Run `bash -n` for changed shell scripts and `py_compile` for changed Python scripts.
- Never declare a model improved solely from in-sample metrics or a single OOS split.
