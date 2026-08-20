from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from finance_crawler_poc.radar_manifest import (
    RadarManifest,
    RadarSource,
    load_radar_manifest,
    select_radar_sources,
)


MAX_LOOKBACK = timedelta(days=7)
OVERLAP = timedelta(minutes=5)


class RadarRunPlanError(ValueError):
    """Raised when the authenticated Worker run plan violates its boundary contract."""


@dataclass(frozen=True)
class CatchupWindow:
    source_id: str
    strategy: str
    request_url: str
    published_since: str | None


def build_run_plan_request(
    manifest: RadarManifest,
    *,
    workflow_run_id: str,
    commit_sha: str,
) -> dict[str, object]:
    if not workflow_run_id.isdigit():
        raise RadarRunPlanError("workflow_run_id must contain only digits")
    if not _valid_sha(commit_sha):
        raise RadarRunPlanError("commit_sha must be a lowercase 40-character Git SHA")
    return {
        "schema_version": 1,
        "workflow_run_id": workflow_run_id,
        "commit_sha": commit_sha,
        "source_ids": [source.source_id for source in manifest.sources],
    }


def parse_worker_run_plan(
    payload: object,
    manifest: RadarManifest,
) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise RadarRunPlanError("Worker run plan must be an object")
    required = {
        "schema_version",
        "as_of",
        "admitted",
        "reason",
        "retry_after_seconds",
        "policy",
        "checkpoints",
        "request_id",
    }
    if set(payload) != required or payload.get("schema_version") != 1:
        raise RadarRunPlanError("Worker run plan has invalid fields")
    try:
        _parse_checkpoint_time(payload.get("as_of"))
    except RadarRunPlanError as exc:
        raise RadarRunPlanError("Worker run plan has invalid as_of") from exc
    policy = payload.get("policy")
    if (
        not isinstance(policy, dict)
        or set(policy) != {
            "daily_run_limit",
            "minimum_interval_seconds",
            "admitted_runs_today",
        }
        or any(
            isinstance(policy[field], bool)
            or not isinstance(policy[field], int)
            or policy[field] < (0 if field == "admitted_runs_today" else 1)
            for field in policy
        )
        or not isinstance(payload.get("retry_after_seconds"), int)
        or payload["retry_after_seconds"] < 0
        or not isinstance(payload.get("reason"), str)
        or not isinstance(payload.get("request_id"), str)
        or not payload["request_id"]
    ):
        raise RadarRunPlanError("Worker run plan has invalid policy or request_id")
    if payload.get("admitted") is not True:
        raise RadarRunPlanError(f"run not admitted: {payload.get('reason', 'unknown')}")
    checkpoints = payload.get("checkpoints")
    if not isinstance(checkpoints, list):
        raise RadarRunPlanError("Worker run plan checkpoints must be a list")
    expected_ids = [source.source_id for source in manifest.sources]
    actual_ids = [
        checkpoint.get("source_id") if isinstance(checkpoint, dict) else None
        for checkpoint in checkpoints
    ]
    if actual_ids != expected_ids:
        raise RadarRunPlanError("Worker run plan source order does not match manifest")
    normalized: list[dict[str, object]] = []
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict) or set(checkpoint) != {
            "source_id",
            "status",
            "last_successful_crawl",
            "last_article_date",
            "cursor",
        }:
            raise RadarRunPlanError("Worker checkpoint has invalid fields")
        if checkpoint["status"] not in {None, "success", "partial", "failed"}:
            raise RadarRunPlanError("Worker checkpoint has invalid status")
        for field in ("last_successful_crawl", "last_article_date", "cursor"):
            if checkpoint[field] is not None and not isinstance(checkpoint[field], str):
                raise RadarRunPlanError(f"Worker checkpoint has invalid {field}")
        for field in ("last_successful_crawl", "last_article_date"):
            if checkpoint[field] is not None:
                _parse_checkpoint_time(checkpoint[field])
        normalized.append(dict(checkpoint))
    return normalized


def build_catchup_windows(
    manifest: RadarManifest,
    checkpoints: list[dict[str, object]],
    *,
    now: datetime,
) -> tuple[CatchupWindow, ...]:
    if now.tzinfo is None:
        raise RadarRunPlanError("run time must be timezone-aware")
    expected_ids = [source.source_id for source in manifest.sources]
    actual_ids = [str(checkpoint.get("source_id", "")) for checkpoint in checkpoints]
    if actual_ids != expected_ids:
        raise RadarRunPlanError("checkpoint source order does not match manifest")
    normalized_now = now.astimezone(timezone.utc)
    return tuple(
        _window_for(source, checkpoint, normalized_now)
        for source, checkpoint in zip(manifest.sources, checkpoints, strict=True)
    )


def _window_for(
    source: RadarSource,
    checkpoint: dict[str, object],
    now: datetime,
) -> CatchupWindow:
    if source.catchup_strategy == "latest_only":
        return CatchupWindow(source.source_id, "latest_only", source.canonical_url, None)
    earliest = now - MAX_LOOKBACK
    checkpoint_times = [
        parsed
        for parsed in (
            _parse_checkpoint_time(checkpoint.get("last_article_date")),
            _parse_checkpoint_time(checkpoint.get("last_successful_crawl")),
        )
        if parsed is not None
    ]
    checkpoint_time = max(checkpoint_times) if checkpoint_times else None
    since = min(now, max(earliest, checkpoint_time - OVERLAP if checkpoint_time else earliest))
    since_iso = _isoformat(since)
    request_url = source.canonical_url
    if source.catchup_strategy == "api_since":
        request_url = _api_since_url(source, since, since_iso)
    return CatchupWindow(source.source_id, source.catchup_strategy, request_url, since_iso)


def _api_since_url(source: RadarSource, since: datetime, since_iso: str) -> str:
    if source.extractor == "hn_algolia":
        return _replace_query(source.canonical_url, "numericFilters", f"created_at_i>{int(since.timestamp())}")
    if source.extractor == "stackexchange":
        return _replace_query(source.canonical_url, "fromdate", str(int(since.timestamp())))
    if source.extractor == "github_issues":
        return _replace_query(source.canonical_url, "since", since_iso)
    raise RadarRunPlanError(f"unsupported API catch-up extractor: {source.extractor}")


def _replace_query(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query = [(name, item) for name, item in parse_qsl(parts.query) if name != key]
    query.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _parse_checkpoint_time(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RadarRunPlanError("checkpoint time must be a string or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RadarRunPlanError("checkpoint time is not RFC 3339") from exc
    if parsed.tzinfo is None:
        raise RadarRunPlanError("checkpoint time must contain a timezone")
    return parsed.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_sha(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a bounded OIDC run-plan request")
    parser.add_argument("--manifest", type=Path, default=Path("radar-sources.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID", "0"))
    parser.add_argument("--commit-sha", default=os.environ.get("GITHUB_SHA", "0" * 40))
    parser.add_argument(
        "--source-ids",
        help="Comma-separated target-scoped source IDs; must contain 12–20 manifest sources",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = load_radar_manifest(args.manifest)
    if args.source_ids:
        manifest = select_radar_sources(
            manifest,
            [source_id.strip() for source_id in args.source_ids.split(",") if source_id.strip()],
        )
    payload = build_run_plan_request(
        manifest,
        workflow_run_id=args.workflow_run_id,
        commit_sha=args.commit_sha,
    )
    _write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
