#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


CONTEXT_TICKERS = ("SPY", "QQQ", "SMH", "SOXX", "IDX_VIX", "NVDA", "TSM", "AMD", "MU", "AVGO")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a deterministic Phoenix Quant research packet.")
    parser.add_argument("--root", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _latest_csv_date(path: Path) -> str | None:
    if not path.exists():
        return None
    latest: str | None = None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            value = str(row.get("Date") or "").strip()
            if value:
                latest = value[:10]
    return latest


def _candidate_summary(root: Path) -> dict[str, Any]:
    candidate_root = root / "models" / "candidates"
    counts: Counter[str] = Counter()
    latest: list[dict[str, Any]] = []
    if not candidate_root.exists():
        return {"counts": {}, "latest": []}
    paths = sorted((path for path in candidate_root.iterdir() if path.is_dir()), reverse=True)
    for path in paths:
        metrics = _load_json(path / "metrics.json")
        if metrics is None:
            status = "pending"
            gate_reasons: list[str] = []
        else:
            gate = metrics.get("gate", {}) if isinstance(metrics.get("gate"), dict) else {}
            promoted = bool(gate.get("promoted"))
            status = "promoted" if promoted else "rejected"
            gate_reasons = [str(item) for item in gate.get("reasons", [])]
        counts[status] += 1
        if len(latest) < 8:
            latest.append({"name": path.name, "status": status, "reasons": gate_reasons})
    return {"counts": dict(counts), "latest": latest}


def _feedback_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"rows": 0, "labels": {}, "reasons": {}}
    labels: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            labels[str(row.get("outcome_label") or "unknown")] += 1
            reasons[str(row.get("reason_category") or "unknown")] += 1
    return {"rows": rows, "labels": dict(labels), "reasons": dict(reasons)}


def build_packet(root: Path) -> dict[str, Any]:
    current = _load_json(root / "models" / "current" / "metrics.json") or {}
    metrics = current.get("metrics", {}) if isinstance(current.get("metrics"), dict) else {}
    data_latest = {
        ticker: _latest_csv_date(root / "data" / f"{ticker}.csv")
        for ticker in CONTEXT_TICKERS
    }
    log_path = root / "logs" / "phoenix_auto_validation.log"
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": str(root),
        "operations": {
            "auto_cycle_paused": (root / ".phoenix_auto_cycle.pause").exists(),
            "log_writable": os.access(log_path, os.W_OK) if log_path.exists() else os.access(log_path.parent, os.W_OK),
            "current_candidate": current.get("candidate_dir"),
            "current_generated_at": current.get("generated_at"),
        },
        "current_metrics": {
            key: metrics.get(key)
            for key in (
                "portfolio_return_by_date_mean",
                "alpha",
                "p_value",
                "mdd",
                "sample_size",
                "active_trades",
                "cash_weight_mean",
            )
        },
        "data_latest": data_latest,
        "candidates": _candidate_summary(root),
        "feedback": _feedback_summary(root / "data" / "operator_feedback.csv"),
    }


def _markdown(packet: dict[str, Any]) -> str:
    operations = packet["operations"]
    metrics = packet["current_metrics"]
    lines = [
        "# Phoenix Quant Research Packet",
        "",
        f"Generated: {packet['generated_at']}",
        "",
        "## Operations",
        "",
        f"- Auto cycle paused: {operations['auto_cycle_paused']}",
        f"- Auto log writable: {operations['log_writable']}",
        f"- Current candidate: {operations['current_candidate'] or 'none'}",
        "",
        "## Current metrics",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in metrics.items())
    lines.extend(["", "## Data latest dates", ""])
    lines.extend(f"- {key}: {value or 'missing'}" for key, value in packet["data_latest"].items())
    lines.extend(["", "## Candidate counts", ""])
    lines.extend(f"- {key}: {value}" for key, value in packet["candidates"]["counts"].items())
    lines.extend(["", "## Feedback", "", f"- Rows: {packet['feedback']['rows']}"])
    lines.append(f"- Labels: {packet['feedback']['labels']}")
    lines.append(f"- Reasons: {packet['feedback']['reasons']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else root / "reports" / "research_packets" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    packet = build_packet(root)
    json_path = output_dir / "research_packet.json"
    markdown_path = output_dir / "research_packet.md"
    json_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(packet), encoding="utf-8")
    if args.json:
        print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"RESEARCH_PACKET_JSON {json_path}")
        print(f"RESEARCH_PACKET_MARKDOWN {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
