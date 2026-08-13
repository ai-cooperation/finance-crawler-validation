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
            "run_attempt": 1,
            "run_started_at": "2026-08-14T03:17:00Z",
            "updated_at": "2026-08-14T03:21:00Z",
        },
        github_timing={
            "run_duration_ms": 240000,
            "billable": {"UBUNTU": {"total_ms": 180000, "jobs": 1, "job_runs": []}},
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
    )

    assert usage == {
        "captured_at": "2026-08-15T01:00:00Z",
        "window_started_at": "2026-08-14T00:00:00Z",
        "window_ended_at": "2026-08-15T00:00:00Z",
        "github_source": "github_api",
        "cloudflare_source": "cloudflare_graphql",
        "github_actions_seconds": 180,
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
                "run_attempt": 1,
                "run_started_at": "2026-08-14T03:17:00Z",
                "updated_at": "2026-08-14T03:21:00Z",
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
            "run_attempt": 1,
            "run_started_at": "2026-08-14T03:17:00Z",
            "updated_at": "2026-08-14T03:21:00Z",
        },
        "github_timing": {
            "run_duration_ms": 240000,
            "billable": {"UBUNTU": {"total_ms": 180000, "jobs": 1, "job_runs": []}},
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
        )
