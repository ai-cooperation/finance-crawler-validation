from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from finance_crawler_poc.contracts import ContractValidationError, validate_contract


EXPECTED_REPOSITORY = "ai-cooperation/finance-crawler-validation"
EXPECTED_WORKFLOW = ".github/workflows/topic-radar.yml"
SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")


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
    github_repository: object,
    github_jobs: object,
    worker_groups: object,
    d1_groups: object,
    r2_groups: object,
    window_start: datetime,
    window_end: datetime,
    captured_at: datetime,
    cloudflare_account_id: str,
    worker_script: str,
    d1_database_id: str,
    r2_bucket: str,
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
    if not isinstance(commit_sha, str) or SHA_PATTERN.fullmatch(commit_sha) is None:
        raise UsageCollectionError("GitHub head_sha must be a 40-character lowercase hexadecimal SHA")
    run_attempt = github.get("run_attempt")
    if not isinstance(run_attempt, int) or isinstance(run_attempt, bool) or run_attempt < 1:
        raise UsageCollectionError("GitHub run_attempt must be positive")
    if github.get("path") != EXPECTED_WORKFLOW:
        raise UsageCollectionError(f"GitHub workflow path must be {EXPECTED_WORKFLOW}")
    if github.get("head_branch") != "main":
        raise UsageCollectionError("GitHub scheduled run must use the main branch")
    embedded_repository = _mapping(github.get("repository"), "GitHub run repository")
    repository = _mapping(github_repository, "GitHub repository")
    if embedded_repository.get("full_name") != EXPECTED_REPOSITORY:
        raise UsageCollectionError(f"GitHub run repository must be {EXPECTED_REPOSITORY}")
    if repository.get("full_name") != EXPECTED_REPOSITORY or repository.get("private") is not False:
        raise UsageCollectionError(f"GitHub repository must be public {EXPECTED_REPOSITORY}")
    runner_seconds = _runner_seconds(github_jobs, github["id"])

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

    if worker_requests < 1:
        raise UsageCollectionError("Worker requests must be positive")
    if d1_rows_read < 1 or d1_rows_written < 1:
        raise UsageCollectionError("D1 rows must be positive")
    if r2_class_a < 1 or r2_class_b < 1:
        raise UsageCollectionError("R2 Class A and Class B operations must be positive")

    result = {
        "schema_version": 1,
        "captured_at": _utc_text(captured_at),
        "window_started_at": _utc_text(window_start),
        "window_ended_at": _utc_text(window_end),
        "github_source": "github_api",
        "github_repository": EXPECTED_REPOSITORY,
        "github_repo_visibility": "public",
        "workflow_run_id": str(github["id"]),
        "run_attempt": run_attempt,
        "commit_sha": commit_sha,
        "github_actions_runner_seconds": runner_seconds,
        # Standard GitHub-hosted runners in public repositories are free. Bind
        # that assertion to both repository visibility and runner labels above.
        "github_actions_billable_seconds": 0,
        "cloudflare_source": "cloudflare_graphql",
        # Cloudflare explicitly says GraphQL analytics is observed usage, not
        # its billing ledger. Keep that limitation machine-readable.
        "cloudflare_analytics_scope": "observed_not_billing",
        "cloudflare_account_id": _non_empty_text(cloudflare_account_id, "account id"),
        "worker_script": _non_empty_text(worker_script, "worker script"),
        "d1_database_id": _non_empty_text(d1_database_id, "D1 database id"),
        "r2_bucket": _non_empty_text(r2_bucket, "R2 bucket"),
        "worker_requests": worker_requests,
        "d1_rows_read": d1_rows_read,
        "d1_rows_written": d1_rows_written,
        "r2_class_a_operations": r2_class_a,
        "r2_class_b_operations": r2_class_b,
    }
    try:
        validate_contract("soak-usage", result)
    except ContractValidationError as exc:
        raise UsageCollectionError(f"invalid soak usage contract: {exc}") from exc
    return result


def _runner_seconds(value: object, workflow_run_id: object) -> int:
    jobs_payload = _mapping(value, "GitHub jobs")
    jobs = _sequence(jobs_payload.get("jobs"), "GitHub jobs")
    if jobs_payload.get("total_count") != len(jobs) or not jobs:
        raise UsageCollectionError("GitHub jobs response must be complete and non-empty")
    total_seconds = 0
    job_ids: set[int] = set()
    for value in jobs:
        job = _mapping(value, "GitHub job")
        job_id = job.get("id")
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id in job_ids:
            raise UsageCollectionError("GitHub job ids must be unique integers")
        job_ids.add(job_id)
        if job.get("run_id") != workflow_run_id:
            raise UsageCollectionError("GitHub job run_id must match workflow run")
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            raise UsageCollectionError("GitHub jobs must be completed successfully")
        labels = _sequence(job.get("labels"), "GitHub job labels")
        runner_name = job.get("runner_name")
        if "ubuntu-latest" not in labels or not isinstance(runner_name, str) or not runner_name.startswith(
            "GitHub Actions "
        ):
            raise UsageCollectionError("GitHub job must use the standard GitHub-hosted Ubuntu runner")
        job_started = _timestamp(job.get("started_at"), "GitHub job started_at")
        job_completed = _timestamp(job.get("completed_at"), "GitHub job completed_at")
        seconds = int((job_completed - job_started).total_seconds())
        if seconds < 1:
            raise UsageCollectionError("GitHub job duration must be positive")
        total_seconds += seconds
    return total_seconds


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


def _non_empty_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UsageCollectionError(f"{name} must be a non-empty string")
    return value


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
