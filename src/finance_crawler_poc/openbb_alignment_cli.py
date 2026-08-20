from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.openbb_alignment import build_market_snapshot, build_topic_market_alignment


def build_alignment_artifacts(
    raw_items_path: Path,
    topic_snapshot_path: Path,
    *,
    provider: str,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        raw_items = json.loads(raw_items_path.read_text(encoding="utf-8"))
        topic_snapshot = json.loads(topic_snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read alignment inputs: {exc}") from exc
    if not isinstance(raw_items, list) or not isinstance(topic_snapshot, dict):
        raise ValueError("alignment inputs must be a raw-item array and topic snapshot object")
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stamp = _timestamp_stamp(timestamp)
    market_snapshot = build_market_snapshot(
        raw_items,
        snapshot_id=f"market_{stamp}",
        as_of=timestamp,
        provider=provider,
    )
    alignment = build_topic_market_alignment(
        topic_snapshot,
        market_snapshot,
        alignment_id=f"align_{stamp}",
        generated_at=timestamp,
    )
    return market_snapshot, alignment, topic_snapshot


def write_alignment_artifacts(
    raw_items_path: Path,
    topic_snapshot_path: Path,
    output_directory: Path,
    *,
    provider: str,
    generated_at: str | None = None,
    workflow_run_id: str | None = None,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    market_snapshot, alignment, topic_snapshot = build_alignment_artifacts(
        raw_items_path,
        topic_snapshot_path,
        provider=provider,
        generated_at=generated_at,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    _write_json(output_directory / "market-snapshot.json", market_snapshot)
    _write_json(output_directory / "market-topic-alignment.json", alignment)
    envelope = {
        "schema_version": 1,
        "operation": "upsert_market_alignment",
        "run_id": str(topic_snapshot["run_id"]),
        "workflow_run_id": workflow_run_id or os.environ.get("GITHUB_RUN_ID", "0"),
        "commit_sha": commit_sha or os.environ.get("GITHUB_SHA", "0" * 40),
        "market_snapshot": market_snapshot,
        "alignment": alignment,
    }
    validate_contract("market-alignment-envelope", envelope)
    _write_json(output_directory / "market-alignment-envelope.json", envelope)
    report = {
        "schema_version": 1,
        "market_snapshot_id": market_snapshot["snapshot_id"],
        "alignment_id": alignment["alignment_id"],
        "provider": provider,
        "instruments": len(market_snapshot["instruments"]),
        "topics": len(alignment["topics"]),
        "coverage_ratio": alignment["coverage_ratio"],
        "partial": alignment["partial"],
    }
    _write_json(output_directory / "openbb-alignment-report.json", report)
    return report


def _timestamp_stamp(value: str) -> str:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError("generated_at must be an ISO-8601 datetime") from exc
    return parsed.strftime("%Y%m%dt%H%M%Sz")


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an OpenBB-compatible market/topic alignment artifact")
    parser.add_argument("--raw-items", type=Path, required=True)
    parser.add_argument("--topic-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", default="coingecko")
    parser.add_argument("--generated-at")
    parser.add_argument("--workflow-run-id")
    parser.add_argument("--commit-sha")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = write_alignment_artifacts(
        args.raw_items,
        args.topic_snapshot,
        args.output,
        provider=args.provider,
        generated_at=args.generated_at,
        workflow_run_id=args.workflow_run_id,
        commit_sha=args.commit_sha,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
