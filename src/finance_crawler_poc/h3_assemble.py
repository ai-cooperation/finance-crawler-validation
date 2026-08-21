from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.radar import build_topic_snapshot


def assemble_h3_artifacts(
    news_envelope_path: Path,
    radar_directory: Path,
    output_directory: Path,
    *,
    target: dict[str, Any] | None = None,
    question: str | None = None,
) -> dict[str, Any]:
    """Join the full news catalog and 15-source radar into one frozen run."""

    news = json.loads(news_envelope_path.read_text(encoding="utf-8"))
    radar = json.loads((radar_directory / "ingest-envelope.json").read_text(encoding="utf-8"))
    run_id = str(radar["run_id"])
    snapshot_id = str(radar["snapshot_id"])
    collected_at = str(radar.get("collected_at") or news.get("collected_at"))
    items = _dedupe_items([*news.get("items", []), *radar.get("items", [])])
    checkpoints = _merge_checkpoints([*news.get("checkpoints", []), *radar.get("checkpoints", [])])
    failed_sources = [str(row["source_id"]) for row in checkpoints if row.get("status") != "success"]
    topic_snapshot = build_topic_snapshot(
        items,
        run_id=run_id,
        snapshot_id=snapshot_id,
        as_of=collected_at,
        failed_sources=failed_sources,
        target=target,
        question=question,
    )
    source_manifest_hash = hashlib.sha256(
        json.dumps({"news": news.get("source_manifest_hash"), "radar": radar.get("source_manifest_hash")}, sort_keys=True).encode()
    ).hexdigest()
    target_relevant = [item for item in items if _is_relevant(item, target, question)]
    result = {
        "schema_version": 1,
        "operation": "upsert_items",
        "collection_scope": "full_catalog",
        "run_id": run_id,
        "workflow_run_id": str(radar.get("workflow_run_id", news.get("workflow_run_id", "0"))),
        "commit_sha": str(radar.get("commit_sha", news.get("commit_sha", "0" * 40))),
        "snapshot_id": snapshot_id,
        "source_manifest_hash": source_manifest_hash,
        "collected_at": collected_at,
        "items": items,
        "checkpoints": checkpoints,
        "collection_source_group_count": len(checkpoints),
        "endpoint_attempt_count": int(news.get("endpoint_attempt_count", 0)) + len(radar.get("checkpoints", [])),
        "normalized_item_count": len(items),
        "normalization_error_count": int(news.get("normalization_error_count", 0)),
        "target_relevant_item_count": len(target_relevant),
        "model_context_item_count": len(target_relevant),
        "evidence_appendix_item_count": len(items),
        "failed_sources": failed_sources,
    }
    ingest_envelope = {key: result[key] for key in (
        "schema_version", "operation", "run_id", "workflow_run_id", "commit_sha", "snapshot_id",
        "source_manifest_hash", "collected_at", "items", "checkpoints",
    )}
    validate_contract("ingest-envelope", ingest_envelope)
    validate_contract("topic-snapshot", topic_snapshot)
    output_directory.mkdir(parents=True, exist_ok=True)
    _write_json(output_directory / "ingest-envelope.json", ingest_envelope)
    _write_json(output_directory / "topic-snapshot.json", topic_snapshot)
    _write_json(output_directory / "raw-items.json", items)
    _write_json(output_directory / "full-catalog-report.json", {
        key: result[key] for key in (
            "collection_scope", "collection_source_group_count", "endpoint_attempt_count",
            "normalized_item_count", "target_relevant_item_count", "model_context_item_count",
            "evidence_appendix_item_count", "normalization_error_count", "failed_sources", "run_id", "snapshot_id",
        )
    })
    return result


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("item_id", ""))
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def _merge_checkpoints(checkpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for checkpoint in checkpoints:
        source_id = str(checkpoint["source_id"])
        existing = by_source.get(source_id)
        if existing is None or checkpoint.get("status") == "success":
            by_source[source_id] = checkpoint
    return list(by_source.values())


def _is_relevant(item: dict[str, Any], target: dict[str, Any] | None, question: str | None) -> bool:
    terms: list[str] = []
    if target:
        terms.extend(str(target.get(key, "")) for key in ("symbol", "name", "market"))
    if question:
        terms.extend(re.findall(r"[A-Za-z0-9]{3,}", question))
    if not terms:
        return True
    haystack = f"{item.get('title', '')} {item.get('summary', '')}".casefold()
    return any(term.casefold() in haystack for term in terms if term)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble full news catalog and radar artifacts")
    parser.add_argument("--news-envelope", type=Path, required=True)
    parser.add_argument("--radar-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-json")
    parser.add_argument("--question")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = assemble_h3_artifacts(
        args.news_envelope,
        args.radar_directory,
        args.output,
        target=json.loads(args.target_json) if args.target_json else None,
        question=args.question,
    )
    print(json.dumps({key: result[key] for key in (
        "run_id", "collection_source_group_count", "endpoint_attempt_count", "normalized_item_count",
        "target_relevant_item_count",
    )}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
