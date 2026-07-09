# AI Infrastructure Daily Data Handoff - 2026-07-10

## Scope Completed

Expanded Phoenix Quant from a narrow large-cap/semiconductor watchlist into a 100-name AI infrastructure universe and added a repeatable daily OHLCV cache step for training/ranking.

The intent is to train against a broader sample covering the full AI infrastructure bottleneck, not just AI software or semiconductors.

Included themes:

- AI software / hyperscaler-adjacent names
- AI semiconductors
- semiconductor equipment / EDA / materials
- memory, storage, networking, and optical names
- data center, server, and infrastructure names
- power utilities and grid-exposed companies
- nuclear / SMR / uranium names
- electrical equipment, cooling, connectors, and contract manufacturing

## Commit Pushed

Pushed to `origin/fix/separate-toplive`:

```text
a8c4907 Expand AI infrastructure daily data universe
```

Files in that commit:

- `config/config.yaml`
- `scripts/fetch_daily_data.py`
- `README.md`

## Config Changes

`config/config.yaml` now has:

- `universe`: exactly 100 unique tickers
- `market_etfs`: 20 tickers including `URA`, `NLR`, and `^VIX`
- `sector_etf_map`: explicit mapping for all 100 universe tickers

Important YAML note:

- Ticker `ON` is quoted as `"ON"` because plain `ON` is parsed as boolean by YAML.

Ticker replacement made during validation:

- Removed `PSTG` because Yahoo Finance returned no data / quote not found.
- Added `CLS` as replacement AI infrastructure / contract manufacturing exposure.

## New Data Script

Added:

```text
scripts/fetch_daily_data.py
```

Purpose:

- Loads `config/config.yaml`.
- Downloads daily OHLCV through the existing `phoenix_core.data_loader.download_ohlcv` yfinance path.
- Uses `config.cache_dir` by default, currently `data/`.
- Includes both `config.universe` and `config.market_etfs` unless `--universe-only` is passed.
- Writes a quality manifest to `data/daily_data_manifest.csv` by default.
- Fails with exit code `1` if any ticker is missing, stale, or below minimum row count.

Primary command:

```bash
.venv/bin/python scripts/fetch_daily_data.py --period 5y --refresh
```

Useful non-refresh validation command:

```bash
.venv/bin/python scripts/fetch_daily_data.py --period 5y
```

Default quality thresholds:

- `--min-rows 300`
- `--max-age-days 7`

The row threshold is 300 because newer names such as `CRWV` and the post-spin/relisted `SNDK` do not have five full years of public daily bars.

## Data Pulled

Downloaded and validated:

```text
120 total series = 100 universe tickers + 20 market ETFs/VIX
```

Latest successful run:

```text
.venv/bin/python scripts/fetch_daily_data.py --period 5y
```

Result:

```text
success=120
failed_or_stale=0
manifest=data/daily_data_manifest.csv
latest_date_range=2026-07-09..2026-07-09
```

Cache/manifest check:

```text
data_csv_files=147
manifest_rows=120
bad_rows=[]
min_rows=321
latest_dates=['2026-07-09']
```

Generated data files live under `data/` and are intentionally git-ignored.

## Training / Ranking Validation

Ran a full retrain/ranking smoke test against the expanded universe and cached daily data:

```bash
.venv/bin/python main.py --top --top-n 5 --period 5y --retrain
```

Result completed successfully. Top 5 from that run:

```text
1 FN
2 CLS
3 ORCL
4 IREN
5 NOW
```

Generated artifacts:

```text
models/pattern_isolation_forest.joblib
models/similarity_cosine_knn.joblib
reports/ranking_2026-07-09.txt
```

These artifacts are git-ignored.

## Test Validation

Ran:

```bash
.venv/bin/python -m py_compile scripts/fetch_daily_data.py
.venv/bin/python tests/test_core_synthetic.py
```

Both passed.

Note: system `python3` in this shell did not have project dependencies such as `pandas` / `python-dotenv`; use `.venv/bin/python` for project commands.

## README Update

Added a short `일봉 데이터 캐시` section documenting:

```bash
.venv/bin/python scripts/fetch_daily_data.py --period 5y --refresh
```

and the generated manifest path:

```text
data/daily_data_manifest.csv
```

## Current Working Tree Notes

After the pushed commit, three pre-existing local modifications remain uncommitted and were intentionally not included in the daily-data commit:

```text
phoenix_core/engines/ranking_engine.py
phoenix_core/services/telegram_command_bot.py
tests/test_core_synthetic.py
```

Summary of those remaining changes:

- `ranking_engine.py`: display label now follows `final_rank_score` rather than raw `decision.label`, with a confidence/hit-rate based floor from `제외` to `보류`.
- `telegram_command_bot.py`: `/hot` logic allows a VWAP-missing fallback when price move is strong and intraday risk is low.
- `tests/test_core_synthetic.py`: tests for the two behavior changes above.

Those changes appear related to prior Top/Hot display behavior, not the AI infrastructure data universe work. Keep them separate unless the next task is to commit or revert that logic.

## Recommended Next Steps

1. Keep using `.venv/bin/python scripts/fetch_daily_data.py --period 5y --refresh` before major retraining runs.
2. Use `--retrain` after universe changes or after intentionally refreshing model artifacts.
3. Treat short-history names (`CRWV`, `SNDK`) carefully in any OOS interpretation.
4. If larger historical sample depth becomes more important than theme freshness, consider a separate `core_universe` excluding short-history names for validation.
5. Decide separately whether to commit or revert the remaining Top/Hot display logic changes.
