#!/usr/bin/env python3
"""Generate network-free, research-only kill-switch validation evidence.

This script imports only the in-memory paper engine. It has no broker client,
account client, network adapter, or order route.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.trade.paper_engine import (  # noqa: E402
    OrderSide,
    PaperEngineConfig,
    PaperSignal,
    PaperTradingEngine,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_signal(now: datetime, **overrides: object) -> PaperSignal:
    values = {
        "symbol": "TEST",
        "side": OrderSide.BUY,
        "price": 100.0,
        "quantity": 10.0,
        "confidence": 80.0,
        "rr_ratio": 2.0,
        "timestamp": now,
        "data_timestamp": now,
        "metadata": {"risk_fraction": 0.005, "validation_only": True},
    }
    values.update(overrides)
    return PaperSignal(**values)


def validate() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    config = PaperEngineConfig(
        min_confidence=70.0,
        min_rr_ratio=1.5,
        max_loss_per_trade=0.005,
        max_data_age_seconds=30,
        max_position_value=10_000.0,
    )
    engine = PaperTradingEngine(config, equity=100_000.0)

    engine.set_kill_switch(True)
    killed_signal = _valid_signal(now)
    killed_gate = engine.check_gates(killed_signal, now=now)
    killed_fill = engine.submit(killed_signal, now=now)

    engine.set_kill_switch(False)
    enabled_signal = _valid_signal(now)
    enabled_gate = engine.check_gates(enabled_signal, now=now)
    enabled_fill = engine.submit(enabled_signal, now=now)

    stale_signal = _valid_signal(
        now,
        data_timestamp=now - timedelta(seconds=config.max_data_age_seconds + 1),
    )
    stale_gate = engine.check_gates(stale_signal, now=now)
    stale_fill = engine.submit(stale_signal, now=now)

    missing_timestamp_signal = _valid_signal(now, data_timestamp=None)
    missing_timestamp_gate = engine.check_gates(missing_timestamp_signal, now=now)
    missing_timestamp_fill = engine.submit(missing_timestamp_signal, now=now)

    excessive_risk_signal = _valid_signal(
        now,
        metadata={"risk_fraction": config.max_loss_per_trade + 0.001},
    )
    excessive_risk_gate = engine.check_gates(excessive_risk_signal, now=now)
    excessive_risk_fill = engine.submit(excessive_risk_signal, now=now)

    checks = {
        "kill_switch_blocks_gate": (
            not killed_gate.allowed and "kill_switch" in killed_gate.reasons
        ),
        "kill_switch_blocks_fill": killed_fill is None,
        "disable_restores_paper_gate": enabled_gate.allowed,
        "disable_allows_only_simulated_fill": enabled_fill is not None,
        "stale_data_blocks_fill": (
            stale_fill is None and "stale_data" in stale_gate.reasons
        ),
        "missing_timestamp_blocks_fill": (
            missing_timestamp_fill is None
            and "missing_data_timestamp" in missing_timestamp_gate.reasons
        ),
        "risk_limit_blocks_fill": (
            excessive_risk_fill is None
            and "max_loss_exceeded" in excessive_risk_gate.reasons
        ),
    }
    passed = all(checks.values())
    return {
        "status": "PASS" if passed else "FAIL",
        "evidence_type": "PAPER_ENGINE_KILL_SWITCH_VALIDATION",
        "validated_at_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "checks": checks,
        "audit_event_count": len(engine.audit_log),
        "paper_fill_count": sum(
            fill is not None
            for fill in (
                killed_fill,
                enabled_fill,
                stale_fill,
                missing_timestamp_fill,
                excessive_risk_fill,
            )
        ),
        "expected_paper_fill_count": 1,
        "implementation": {
            "artifact": "phoenix_core/trade/paper_engine.py",
            "sha256": sha256(ROOT / "phoenix_core/trade/paper_engine.py"),
        },
        "network_called": False,
        "broker_routes_called": False,
        "account_endpoints_called": False,
        "live_enabled": False,
        "production_changed": False,
    }


def write_evidence(result: dict[str, object], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = str(result["validated_at_utc"]).replace("-", "").replace(":", "")
    timestamp = timestamp.replace("T", "_").replace("Z", "")
    artifact = output_dir / f"kill_switch_validation_{timestamp}.json"
    latest = output_dir / "kill_switch_validation_latest.json"
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    artifact.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    return artifact, latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="reports/paper_trading/kill_switch",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = validate()
    artifact, latest = write_evidence(result, ROOT / args.output_dir)
    output = {
        **result,
        "artifact": str(artifact.relative_to(ROOT)),
        "artifact_sha256": sha256(artifact),
        "latest": str(latest.relative_to(ROOT)),
    }
    print(
        json.dumps(output, ensure_ascii=False)
        if args.json
        else f"{result['status']} {output['artifact']}"
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
