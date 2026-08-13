from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence


R2_CLASS_A_ACTIONS = frozenset(
    {
        "ListBuckets",
        "PutBucket",
        "ListObjects",
        "PutObject",
        "CopyObject",
        "CompleteMultipartUpload",
        "CreateMultipartUpload",
        "LifecycleStorageTierTransition",
        "ListMultipartUploads",
        "UploadPart",
        "UploadPartCopy",
        "ListParts",
        "PutBucketEncryption",
        "PutBucketCors",
        "PutBucketLifecycleConfiguration",
    }
)
R2_CLASS_B_ACTIONS = frozenset(
    {
        "HeadBucket",
        "HeadObject",
        "GetObject",
        "UsageSummary",
        "GetBucketEncryption",
        "GetBucketLocation",
        "GetBucketCors",
        "GetBucketLifecycleConfiguration",
    }
)
R2_FREE_ACTIONS = frozenset({"DeleteObject", "DeleteBucket", "AbortMultipartUpload"})


class UsageCollectionError(ValueError):
    """Raised when official telemetry cannot be normalized without guessing."""


def build_daily_usage(
    *,
    github_run: object,
    github_timing: object,
    worker_groups: object,
    d1_groups: object,
    r2_groups: object,
    window_start: datetime,
    window_end: datetime,
    captured_at: datetime,
) -> dict[str, object]:
    _verify_window(window_start, window_end, captured_at)
    github = _mapping(github_run, "GitHub run")
    if github.get("event") != "schedule" or github.get("conclusion") != "success":
        raise UsageCollectionError("usage requires one successful scheduled GitHub run")
    started = _timestamp(github.get("run_started_at"), "GitHub run_started_at")
    completed = _timestamp(github.get("updated_at"), "GitHub updated_at")
    if completed < started or started < window_start or started >= window_end:
        raise UsageCollectionError("GitHub run timestamps are outside the UTC usage window")
    if not isinstance(github.get("id"), int) or isinstance(github.get("id"), bool):
        raise UsageCollectionError("GitHub run id must be an integer")
    commit_sha = github.get("head_sha")
    if not isinstance(commit_sha, str) or len(commit_sha) != 40:
        raise UsageCollectionError("GitHub head_sha must be a 40-character SHA")
    run_attempt = github.get("run_attempt")
    if not isinstance(run_attempt, int) or isinstance(run_attempt, bool) or run_attempt < 1:
        raise UsageCollectionError("GitHub run_attempt must be positive")
    timing = _mapping(github_timing, "GitHub timing")
    billable = _mapping(timing.get("billable"), "GitHub billable")
    billable_ms = 0
    for operating_system, value in billable.items():
        platform = _mapping(value, f"GitHub billable {operating_system}")
        billable_ms += _metric(platform.get("total_ms"), "GitHub billable total_ms")
    _metric(timing.get("run_duration_ms"), "GitHub run_duration_ms")

    worker_requests = sum(
        _metric(_mapping(_mapping(group, "Worker group").get("sum"), "Worker sum").get("requests"),
                "Worker requests")
        for group in _sequence(worker_groups, "Worker groups")
    )
    d1_rows_read = 0
    d1_rows_written = 0
    for group in _sequence(d1_groups, "D1 groups"):
        sums = _mapping(_mapping(group, "D1 group").get("sum"), "D1 sum")
        d1_rows_read += _metric(sums.get("rowsRead"), "D1 rowsRead")
        d1_rows_written += _metric(sums.get("rowsWritten"), "D1 rowsWritten")

    r2_class_a = 0
    r2_class_b = 0
    known_actions = R2_CLASS_A_ACTIONS | R2_CLASS_B_ACTIONS | R2_FREE_ACTIONS
    for group in _sequence(r2_groups, "R2 groups"):
        row = _mapping(group, "R2 group")
        dimensions = _mapping(row.get("dimensions"), "R2 dimensions")
        action = dimensions.get("actionType")
        if not isinstance(action, str) or action not in known_actions:
            raise UsageCollectionError(f"unknown R2 action type: {action}")
        requests = _metric(
            _mapping(row.get("sum"), "R2 sum").get("requests"),
            "R2 requests",
        )
        if action in R2_CLASS_A_ACTIONS:
            r2_class_a += requests
        elif action in R2_CLASS_B_ACTIONS:
            r2_class_b += requests

    return {
        "captured_at": _utc_text(captured_at),
        "window_started_at": _utc_text(window_start),
        "window_ended_at": _utc_text(window_end),
        "github_source": "github_api",
        "cloudflare_source": "cloudflare_graphql",
        "github_actions_seconds": (billable_ms + 999) // 1000,
        "worker_requests": worker_requests,
        "d1_rows_read": d1_rows_read,
        "d1_rows_written": d1_rows_written,
        "r2_class_a_operations": r2_class_a,
        "r2_class_b_operations": r2_class_b,
    }


def _verify_window(start: datetime, end: datetime, captured: datetime) -> None:
    for value, name in ((start, "window_start"), (end, "window_end"), (captured, "captured_at")):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise UsageCollectionError(f"{name} must be UTC")
    if end - start != timedelta(days=1) or start.time() != datetime.min.time():
        raise UsageCollectionError("usage window must be one exact UTC day")
    if captured < end:
        raise UsageCollectionError("usage must be captured after the UTC day")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise UsageCollectionError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise UsageCollectionError(f"{name} must be a list")
    return value


def _metric(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise UsageCollectionError(f"{name} must be a non-negative number")
    if int(value) != value:
        raise UsageCollectionError(f"{name} must be a whole number")
    return int(value)


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise UsageCollectionError(f"{name} must be RFC 3339")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsageCollectionError(f"{name} must be RFC 3339") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise UsageCollectionError(f"{name} must be UTC")
    return result.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
