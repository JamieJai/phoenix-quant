#!/usr/bin/env python3
"""One-command, read-first Phoenix daily research audit."""
from __future__ import annotations
import argparse, json, subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = str(ROOT / ".venv/bin/python")

def run(command: list[str]) -> tuple[int, str]:
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    return proc.returncode, proc.stdout + proc.stderr

def main() -> int:
    parser = argparse.ArgumentParser(description="Run the complete Phoenix daily check")
    parser.add_argument("--allow-candidate-training", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    checks = {
        "packet": [PYTHON, "scripts/phoenix_research_packet.py"],
        "auto_status": [PYTHON, "scripts/phoenix_auto_status.py", "--models-root", "models", "--json"],
        "failure_analysis": [PYTHON, "scripts/phoenix_failure_analysis.py", "--models-root", "models", "--json"],
        "coverage": [PYTHON, "scripts/phoenix_data_coverage_audit.py", "--config", "config/config.yaml", "--cache-dir", "data", "--include-etfs", "--max-age-days", "4", "--min-split-coverage", "0.90", "--min-universe-usable-ratio", "0.90", "--json"],
        "cross_market": [PYTHON, "scripts/phoenix_cross_market_report.py", "--ticker", "NVDA", "--ticker", "AMD", "--ticker", "AVGO", "--ticker", "TSM", "--json"],
        "intraday_labels": [PYTHON, "scripts/phoenix_intraday_label_cache.py", "--path", "data/intraday_features.csv"],
        "intraday_ablation": [PYTHON, "scripts/phoenix_intraday_oos_ablation.py", "--path", "data/intraday_features.csv", "--json"],
        "paper_pnl": [PYTHON, "scripts/phoenix_paper_pnl_report.py", "--path", "data/intraday_features.csv", "--json"],
    }
    results = {}
    for name, command in checks.items():
        code, output = run(command)
        results[name] = {"returncode": code, "tail": output[-4000:]}
    coverage_failed = results["coverage"]["returncode"] != 0
    check_failed = any(result["returncode"] != 0 for result in results.values())
    auto_tail = results["auto_status"]["tail"].lower()
    auto_log_error = any(marker in auto_tail for marker in ("permission denied", "traceback", "code=1"))
    degraded = coverage_failed or check_failed or auto_log_error
    pause_exists = (ROOT / ".phoenix_auto_cycle.pause").exists()
    training = {"requested": args.allow_candidate_training, "started": False, "reason": "not requested"}
    if args.allow_candidate_training and not coverage_failed and not pause_exists:
        code, output = run(["bash", "scripts/phoenix_auto_cycle.sh"])
        training = {"requested": True, "started": True, "returncode": code, "tail": output[-4000:]}
    elif args.allow_candidate_training and pause_exists:
        training = {"requested": True, "started": False, "reason": "pause file exists; no resume performed"}
    results["training"] = training
    payload = {"status": "DEGRADED" if degraded else "HEALTHY", "as_of": datetime.now(timezone.utc).date().isoformat(), "pause_file": pause_exists, "results": results}
    print(json.dumps(payload, ensure_ascii=False, indent=None if args.json else 2))
    return 2 if degraded else 0

if __name__ == "__main__":
    raise SystemExit(main())
