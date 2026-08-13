from __future__ import annotations

from datetime import datetime, timezone

import pytest

from finance_crawler_poc.soak_usage import UsageCollectionError, build_daily_usage


START = datetime(2026, 8, 14, tzinfo=timezone.utc)
END = datetime(2026, 8, 15, tzinfo=timezone.utc)


def test_builds_usage_only_from_official_github_and_cloudflare_responses() -> None:
    usage = build_daily_usage(
        github_run={
            "id": 40000000000,
            "event": "schedule",
            "conclusion": "success",
            "head_sha": "d" * 40,
            "head_branch": "main",
            "run_attempt": 1,
            "run_started_at": "2026-08-14T03:17:00Z",
            "updated_at": "2026-08-14T03:21:00Z",
            "path": ".github/workflows/topic-radar.yml",
            "repository": {"full_name": "ai-cooperation/finance-crawler-validation"},
        },
        github_repository={"full_name": "ai-cooperation/finance-crawler-validation", "private": False},
        github_jobs={
            "total_count": 1,
            "jobs": [
                {
                    "id": 9001,
                    "run_id": 40000000000,
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-08-14T03:17:10Z",
                    "completed_at": "2026-08-14T03:20:11Z",
                    "labels": ["ubuntu-latest"],
                    "runner_name": "GitHub Actions 1000000000",
                }
            ],
        },
        worker_groups=[{"sum": {"requests": 8}}],
        d1_groups=[{"sum": {"rowsRead": 200, "rowsWritten": 100}}],
        r2_groups=[
            {"dimensions": {"actionType": "PutObject"}, "sum": {"requests": 40}},
            {"dimensions": {"actionType": "HeadObject"}, "sum": {"requests": 10}},
            {"dimensions": {"actionType": "DeleteObject"}, "sum": {"requests": 2}},
        ],
        window_start=START,
        window_end=END,
        captured_at=datetime(2026, 8, 15, 1, tzinfo=timezone.utc),
        cloudflare_account_id="ca985c195ab218488fc0744692dbde21",
        worker_script="finance-crawler-validation-ingest",
        d1_database_id="476bd84f-e924-4b9b-a9d9-dfca9ea29a1a",
        r2_bucket="finance-crawler-validation-raw",
    )

    assert usage == {
        "schema_version": 1,
        "captured_at": "2026-08-15T01:00:00Z",
        "window_started_at": "2026-08-14T00:00:00Z",
        "window_ended_at": "2026-08-15T00:00:00Z",
        "github_source": "github_api",
        "github_repository": "ai-cooperation/finance-crawler-validation",
        "github_repo_visibility": "public",
        "workflow_run_id": "40000000000",
        "run_attempt": 1,
        "commit_sha": "d" * 40,
        "github_actions_runner_seconds": 181,
        "github_actions_billable_seconds": 0,
        "cloudflare_source": "cloudflare_graphql",
        "cloudflare_analytics_scope": "observed_not_billing",
        "cloudflare_account_id": "ca985c195ab218488fc0744692dbde21",
        "worker_script": "finance-crawler-validation-ingest",
        "d1_database_id": "476bd84f-e924-4b9b-a9d9-dfca9ea29a1a",
        "r2_bucket": "finance-crawler-validation-raw",
        "worker_requests": 8,
        "d1_rows_read": 200,
        "d1_rows_written": 100,
        "r2_class_a_operations": 40,
        "r2_class_b_operations": 10,
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("worker_groups", [{"sum": {"requests": -1}}], "non-negative"),
        (
            "r2_groups",
            [{"dimensions": {"actionType": "FutureOperation"}, "sum": {"requests": 1}}],
            "unknown R2 action type",
        ),
        (
            "github_run",
            {
                "id": 40000000000,
                "event": "workflow_dispatch",
                "conclusion": "success",
                "head_sha": "d" * 40,
                "head_branch": "main",
                "run_attempt": 1,
                "run_started_at": "2026-08-14T03:17:00Z",
                "updated_at": "2026-08-14T03:21:00Z",
                "path": ".github/workflows/topic-radar.yml",
                "repository": {"full_name": "ai-cooperation/finance-crawler-validation"},
            },
            "scheduled GitHub run",
        ),
    ],
)
def test_usage_collection_fails_closed_on_unverifiable_metrics(field, value, message: str) -> None:
    values = {
        "github_run": {
            "id": 40000000000,
            "event": "schedule",
            "conclusion": "success",
            "head_sha": "d" * 40,
            "head_branch": "main",
            "run_attempt": 1,
            "run_started_at": "2026-08-14T03:17:00Z",
            "updated_at": "2026-08-14T03:21:00Z",
            "path": ".github/workflows/topic-radar.yml",
            "repository": {"full_name": "ai-cooperation/finance-crawler-validation"},
        },
        "github_repository": {
            "full_name": "ai-cooperation/finance-crawler-validation",
            "private": False,
        },
        "github_jobs": {
            "total_count": 1,
            "jobs": [
                {
                    "id": 9001,
                    "run_id": 40000000000,
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-08-14T03:17:10Z",
                    "completed_at": "2026-08-14T03:20:11Z",
                    "labels": ["ubuntu-latest"],
                    "runner_name": "GitHub Actions 1000000000",
                }
            ],
        },
        "worker_groups": [{"sum": {"requests": 8}}],
        "d1_groups": [{"sum": {"rowsRead": 200, "rowsWritten": 100}}],
        "r2_groups": [],
    }
    values[field] = value

    with pytest.raises(UsageCollectionError, match=message):
        build_daily_usage(
            **values,
            window_start=START,
            window_end=END,
            captured_at=datetime(2026, 8, 15, 1, tzinfo=timezone.utc),
            cloudflare_account_id="ca985c195ab218488fc0744692dbde21",
            worker_script="finance-crawler-validation-ingest",
            d1_database_id="476bd84f-e924-4b9b-a9d9-dfca9ea29a1a",
            r2_bucket="finance-crawler-validation-raw",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda values: values["github_repository"].__setitem__("private", True), "public"),
        (
            lambda values: values["github_jobs"]["jobs"][0].__setitem__(
                "labels", ["self-hosted"]
            ),
            "standard GitHub-hosted Ubuntu",
        ),
        (
            lambda values: values.__setitem__("worker_groups", []),
            "Worker requests must be positive",
        ),
        (
            lambda values: values.__setitem__("d1_groups", []),
            "D1 rows must be positive",
        ),
        (
            lambda values: values.__setitem__("r2_groups", []),
            "R2 Class A and Class B operations must be positive",
        ),
        (
            lambda values: values["github_run"].__setitem__("head_sha", "z" * 40),
            "lowercase hexadecimal",
        ),
        (
            lambda values: values["github_jobs"].__setitem__("total_count", 101),
            "complete and non-empty",
        ),
        (
            lambda values: values["github_run"].__setitem__("head_branch", "experiment"),
            "main branch",
        ),
    ],
)
def test_usage_collection_rejects_false_free_quota_evidence(mutation, message: str) -> None:
    values = {
        "github_run": {
            "id": 40000000000,
            "event": "schedule",
            "conclusion": "success",
            "head_sha": "d" * 40,
            "head_branch": "main",
            "run_attempt": 1,
            "run_started_at": "2026-08-14T03:17:00Z",
            "updated_at": "2026-08-14T03:21:00Z",
            "path": ".github/workflows/topic-radar.yml",
            "repository": {"full_name": "ai-cooperation/finance-crawler-validation"},
        },
        "github_repository": {
            "full_name": "ai-cooperation/finance-crawler-validation",
            "private": False,
        },
        "github_jobs": {
            "total_count": 1,
            "jobs": [
                {
                    "id": 9001,
                    "run_id": 40000000000,
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-08-14T03:17:10Z",
                    "completed_at": "2026-08-14T03:20:11Z",
                    "labels": ["ubuntu-latest"],
                    "runner_name": "GitHub Actions 1000000000",
                }
            ],
        },
        "worker_groups": [{"sum": {"requests": 8}}],
        "d1_groups": [{"sum": {"rowsRead": 200, "rowsWritten": 100}}],
        "r2_groups": [
            {"dimensions": {"actionType": "PutObject"}, "sum": {"requests": 40}},
            {"dimensions": {"actionType": "HeadObject"}, "sum": {"requests": 10}},
        ],
    }
    mutation(values)

    with pytest.raises(UsageCollectionError, match=message):
        build_daily_usage(
            **values,
            window_start=START,
            window_end=END,
            captured_at=datetime(2026, 8, 15, 1, tzinfo=timezone.utc),
            cloudflare_account_id="ca985c195ab218488fc0744692dbde21",
            worker_script="finance-crawler-validation-ingest",
            d1_database_id="476bd84f-e924-4b9b-a9d9-dfca9ea29a1a",
            r2_bucket="finance-crawler-validation-raw",
        )
