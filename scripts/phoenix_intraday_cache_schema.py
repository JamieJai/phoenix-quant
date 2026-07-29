#!/usr/bin/env python3
"""Atomically validate or migrate the intraday research cache schema."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.intraday_feature_store import (  # noqa: E402
    ensure_intraday_feature_cache_schema,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="data/intraday_features.csv")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = ensure_intraday_feature_cache_schema(args.path)
    print(
        json.dumps(result, ensure_ascii=False)
        if args.json
        else f"{result['status']} rows={result.get('rows')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
