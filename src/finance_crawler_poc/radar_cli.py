from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.radar import build_topic_snapshot
from finance_crawler_poc.radar_collect import RadarCollection, collect_radar_sources
from finance_crawler_poc.radar_manifest import RadarManifest, load_radar_manifest, select_radar_sources
from finance_crawler_poc.radar_run_plan import build_catchup_windows, parse_worker_run_plan


MIN_CONTENT_SOURCES = 3


def build_radar_artifacts(
    manifest: RadarManifest,
    collection: RadarCollection,
    *,
    workflow_run_id: str,
    commit_sha: str,
    now: datetime,
    manifest_sha256: str,
    target: dict[str, Any] | None = None,
    question: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not collection.items:
        raise ValueError("cannot build ingest artifacts without normalized items")
    if not workflow_run_id.isdigit():
        raise ValueError("workflow_run_id must contain only digits")
    if len(commit_sha) != 40 or any(character not in "0123456789abcdef" for character in commit_sha):
        raise ValueError("commit_sha must be a lowercase 40-character Git SHA")
    normalized_now = now.astimezone(timezone.utc)
    as_of = normalized_now.isoformat().replace("+00:00", "Z")
    stamp = normalized_now.strftime("%Y%m%dt%H%M%Sz")
    run_id = f"run_{stamp}"
    snapshot_id = f"radar_{stamp}"
    failed_sources = collection.failed_source_ids
    snapshot = build_topic_snapshot(
        collection.items,
        run_id=run_id,
        snapshot_id=snapshot_id,
        as_of=as_of,
        failed_sources=failed_sources,
        target=target,
        question=question,
    )
    envelope = {
        "schema_version": 1,
        "operation": "upsert_items",
        "run_id": run_id,
        "workflow_run_id": workflow_run_id,
        "commit_sha": commit_sha,
        "snapshot_id": snapshot_id,
        "source_manifest_hash": manifest_sha256,
        "collected_at": as_of,
        "items": list(collection.items),
        "checkpoints": list(collection.checkpoints),
    }
    validate_contract("ingest-envelope", envelope)
    validate_contract("topic-snapshot", snapshot)
    minimum_topics = 1 if target is not None else 3
    accepted = (
        collection.successful_source_count >= manifest.minimum_successful_sources
        and collection.content_source_count >= MIN_CONTENT_SOURCES
        and len(snapshot["topics"]) >= minimum_topics
    )
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "as_of": as_of,
        "accepted": accepted,
        "minimum_successful_sources": manifest.minimum_successful_sources,
        "minimum_content_sources": MIN_CONTENT_SOURCES,
        "minimum_topics": minimum_topics,
        "total_sources": len(manifest.sources),
        "successful_sources": collection.successful_source_count,
        "content_sources": collection.content_source_count,
        "empty_sources": list(collection.empty_source_ids),
        "failed_sources": list(failed_sources),
        "items": len(collection.items),
        "topics": len(snapshot["topics"]),
        "by_transport": {
            transport: {
                "total": sum(result["transport"] == transport for result in collection.source_results),
                "successful": sum(
                    result["transport"] == transport and result["status"] == "success"
                    for result in collection.source_results
                ),
            }
            for transport in ("rss", "json_api", "browser")
        },
        "source_results": list(collection.source_results),
        "checkpoints": list(collection.checkpoints),
    }
    return envelope, snapshot, report


async def run_radar(
    manifest_path: Path,
    output_directory: Path,
    *,
    workflow_run_id: str,
    commit_sha: str,
    now: datetime | None = None,
    run_plan_path: Path | None = None,
    source_ids: Sequence[str] | None = None,
    target: dict[str, Any] | None = None,
    question: str | None = None,
) -> dict[str, Any]:
    manifest_bytes = manifest_path.read_bytes()
    manifest = load_radar_manifest(manifest_path)
    if source_ids is not None:
        manifest = select_radar_sources(manifest, list(source_ids))
        manifest_bytes += ("\nselected:" + ",".join(source_ids)).encode("utf-8")
    run_time = now or datetime.now(timezone.utc)
    collected_at = run_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    catchup_windows = None
    if run_plan_path is not None:
        try:
            run_plan_payload = json.loads(run_plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read Worker run plan: {exc}") from exc
        checkpoints = parse_worker_run_plan(run_plan_payload, manifest)
        catchup_windows = build_catchup_windows(manifest, checkpoints, now=run_time)
    collection = await collect_radar_sources(
        manifest,
        collected_at=collected_at,
        catchup_windows=catchup_windows,
    )
    if not collection.items:
        minimum_topics = 1 if target is not None else 3
        report = {
            "schema_version": 1,
            "accepted": False,
            "as_of": collected_at,
            "minimum_successful_sources": manifest.minimum_successful_sources,
            "minimum_content_sources": MIN_CONTENT_SOURCES,
            "minimum_topics": minimum_topics,
            "total_sources": len(manifest.sources),
            "successful_sources": collection.successful_source_count,
            "content_sources": collection.content_source_count,
            "empty_sources": list(collection.empty_source_ids),
            "failed_sources": list(collection.failed_source_ids),
            "items": 0,
            "topics": 0,
            "source_results": list(collection.source_results),
            "checkpoints": list(collection.checkpoints),
        }
        _write_json(output_directory / "run-report.json", report)
        return report

    envelope, snapshot, report = build_radar_artifacts(
        manifest,
        collection,
        workflow_run_id=workflow_run_id,
        commit_sha=commit_sha,
        now=run_time,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        target=target,
        question=question,
    )
    _write_json(output_directory / "raw-items.json", list(collection.items))
    _write_json(output_directory / "ingest-envelope.json", envelope)
    _write_json(output_directory / "topic-snapshot.json", snapshot)
    _write_json(output_directory / "run-report.json", report)
    return report


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect a 12–20 source finance topic radar slice")
    parser.add_argument("--manifest", type=Path, default=Path("radar-sources.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--workflow-run-id",
        default=os.environ.get("GITHUB_RUN_ID", "0"),
        help="GitHub run ID; use 0 for a local-only validation run",
    )
    parser.add_argument(
        "--run-plan",
        type=Path,
        help="Authenticated Worker run-plan response used for checkpoint catch-up",
    )
    parser.add_argument(
        "--commit-sha",
        default=os.environ.get("GITHUB_SHA", "0" * 40),
        help="GitHub commit SHA; use 40 zeros for a local-only validation run",
    )
    parser.add_argument(
        "--source-ids",
        help="Comma-separated target-scoped source IDs; must contain 12–20 manifest sources",
    )
    parser.add_argument(
        "--target-json",
        help="JSON target object used to prioritize topic evidence",
    )
    parser.add_argument(
        "--question",
        help="Frozen research question used to prioritize topic evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = asyncio.run(
        run_radar(
            args.manifest,
            args.output,
            workflow_run_id=args.workflow_run_id,
            commit_sha=args.commit_sha,
            run_plan_path=args.run_plan,
            source_ids=(
                [source_id.strip() for source_id in args.source_ids.split(",") if source_id.strip()]
                if args.source_ids
                else None
            ),
            target=(json.loads(args.target_json) if args.target_json else None),
            question=args.question,
        )
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
