from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.tradingagents_plan import build_tradingagents_run_plan


def write_tradingagents_plan(
    topic_snapshot_path: Path,
    alignment_path: Path | None,
    output_directory: Path,
    *,
    plan_id: str,
    created_at: str,
    max_topics: int,
    max_claims_per_topic: int,
    max_tokens: int,
    max_usd: float,
    model: str,
    requested_topic_ids: Sequence[str] = (),
) -> dict[str, Any]:
    topic_snapshot = _read_object(topic_snapshot_path, "topic snapshot")
    alignment = _read_object(alignment_path, "market alignment") if alignment_path else None
    plan = build_tradingagents_run_plan(
        topic_snapshot,
        alignment,
        plan_id=plan_id,
        created_at=created_at,
        max_topics=max_topics,
        max_claims_per_topic=max_claims_per_topic,
        max_tokens=max_tokens,
        max_usd=max_usd,
        model=model,
        requested_topic_ids=requested_topic_ids,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    _write_json(output_directory / "tradingagents-run-plan.json", plan)
    envelope = {
        "schema_version": 1,
        "operation": "upsert_tradingagents_plan",
        "run_id": topic_snapshot["run_id"],
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", "0"),
        "commit_sha": os.environ.get("GITHUB_SHA", "0" * 40),
        "plan": plan,
    }
    validate_contract("tradingagents-plan-envelope", envelope)
    _write_json(output_directory / "tradingagents-plan-envelope.json", envelope)
    report = {
        "schema_version": 1,
        "plan_id": plan["plan_id"],
        "decision": plan["decision"],
        "skip_reason": plan["skip_reason"],
        "topics_to_run": sum(topic["decision"] == "run" for topic in plan["topics"]),
        "topics_considered": len(plan["topics"]),
        "model": plan["budget"]["model"],
    }
    _write_json(output_directory / "tradingagents-plan-report.json", report)
    return report


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _timestamp_stamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError("created_at must be an ISO-8601 datetime") from exc
    return parsed.strftime("%Y%m%dt%H%M%Sz")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a bounded TradingAgents second-opinion run plan")
    parser.add_argument("--topic-snapshot", type=Path, required=True)
    parser.add_argument("--alignment", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-id")
    parser.add_argument("--created-at", default=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    parser.add_argument("--max-topics", type=int, default=3)
    parser.add_argument("--max-claims-per-topic", type=int, default=6)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--max-usd", type=float, default=0.0)
    parser.add_argument("--model", default="tradingagents-deferred")
    parser.add_argument("--requested-topic-id", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    created_at = args.created_at
    report = write_tradingagents_plan(
        args.topic_snapshot,
        args.alignment,
        args.output,
        plan_id=args.plan_id or f"plan_{_timestamp_stamp(created_at)}",
        created_at=created_at,
        max_topics=args.max_topics,
        max_claims_per_topic=args.max_claims_per_topic,
        max_tokens=args.max_tokens,
        max_usd=args.max_usd,
        model=args.model,
        requested_topic_ids=args.requested_topic_id,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
