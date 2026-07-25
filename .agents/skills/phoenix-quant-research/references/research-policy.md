# Phoenix Quant research policy

## Evidence hierarchy

1. Point-in-time cached market data and deterministic reports
2. Frozen OOS and rolling OOS results
3. Operator feedback with completed forward returns
4. Current external information with timestamped sources
5. LLM inference, clearly labeled as a hypothesis

## Required experiment record

Record:

- hypothesis and economic mechanism
- exact feature formula and timestamp availability
- target and horizon
- universe and exclusions
- train, validation, test, purge, and embargo windows
- frozen baseline and changed component
- missing-value policy
- sample size, active trades, and cash weight
- alpha, p-value, MDD, rank IC, or calibration metrics as applicable
- acceptance and rollback criteria decided before viewing OOS results

## Promotion rules

- Reject missing, stale, or insufficient split coverage.
- Reject lookahead, target leakage, and holiday/timezone misalignment.
- Require base-only versus overlay ablation on identical windows.
- Require stability across at least two meaningful rolling OOS regimes.
- Keep heuristic and LLM-derived adjustments bounded until validated.
- Never promote from qualitative plausibility, in-sample lift, or one favorable period.

## LLM boundary

Use Codex for synthesis, anomaly investigation, hypothesis generation, feature specification, experiment implementation, and review. Do not use prose sentiment or an uncalibrated LLM score as a direct production trading signal. Convert durable insights into explicit, point-in-time numeric features or auditable risk flags.
