from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.radar import build_topic_snapshot
from finance_crawler_poc.target_scope import select_target_items


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
    successful_source_groups = sum(row.get("status") in {"success", "partial"} for row in checkpoints)
    fully_successful_source_groups = sum(row.get("status") == "success" for row in checkpoints)
    partial_source_groups = sum(row.get("status") == "partial" for row in checkpoints)
    failed_source_groups = sum(row.get("status") == "failed" for row in checkpoints)
    target_items, target_scope = select_target_items(items, target=target, question=question)
    topic_snapshot = build_topic_snapshot(
        target_items,
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
        "successful_source_group_count": successful_source_groups,
        "fully_successful_source_group_count": fully_successful_source_groups,
        "partial_source_group_count": partial_source_groups,
        "failed_source_group_count": failed_source_groups,
        "incomplete_source_group_count": partial_source_groups + failed_source_groups,
        "endpoint_attempt_count": int(news.get("endpoint_attempt_count", 0)) + int(
            radar.get("endpoint_attempt_count", len(radar.get("checkpoints", [])))
        ),
        "normalized_item_count": len(items),
        "normalization_error_count": int(news.get("normalization_error_count", 0)),
        "target_relevant_item_count": len(target_items),
        "model_context_item_count": len(target_items),
        "evidence_appendix_item_count": len(target_items),
        "target_scope": target_scope,
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
            "successful_source_group_count", "fully_successful_source_group_count",
            "partial_source_group_count", "failed_source_group_count", "incomplete_source_group_count",
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
    grouped: dict[str, list[dict[str, Any]]] = {}
    for checkpoint in checkpoints:
        source_id = str(checkpoint["source_id"])
        grouped.setdefault(source_id, []).append(checkpoint)
    merged: list[dict[str, Any]] = []
    for source_id, rows in grouped.items():
        statuses = {str(row.get("status")) for row in rows}
        if statuses == {"success"}:
            status = "success"
        elif statuses == {"failed"}:
            status = "failed"
        else:
            status = "partial"
        successful_rows = [row for row in rows if row.get("last_successful_crawl")]
        last_successful = max(
            (str(row["last_successful_crawl"]) for row in successful_rows),
            default=None,
        )
        article_dates = [row for row in rows if row.get("last_article_date")]
        last_article_date = max(
            (str(row["last_article_date"]) for row in article_dates),
            default=None,
        )
        cursors = [row for row in rows if row.get("cursor")]
        merged.append({
            "source_id": source_id,
            "status": status,
            "last_successful_crawl": last_successful,
            "last_article_date": last_article_date,
            "cursor": cursors[-1].get("cursor") if cursors else None,
        })
    return merged


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
