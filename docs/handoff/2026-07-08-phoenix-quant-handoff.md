# Phoenix Quant Handoff — 2026-07-08

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
