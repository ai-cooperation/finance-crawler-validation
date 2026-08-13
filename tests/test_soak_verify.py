from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from finance_crawler_poc.soak_verify import SoakVerificationError, verify_soak_window


SOURCE_IDS = tuple(f"source_{index:02d}" for index in range(15))


def _iso(day: date, hour: int, minute: int = 0) -> str:
    return datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=timezone.utc,
    ).isoformat().replace("+00:00", "Z")


def _observation(day: date, run_number: int, counters: int) -> dict[str, object]:
    workflow_run_id = str(40_000_000_000 + run_number)
    run_id = f"run_202608{day.day:02d}t031700z"
    snapshot_id = f"radar_202608{day.day:02d}t031700z"
    commit_sha = f"{run_number + 1:040x}"
    content_sha = f"{run_number + 101:064x}"
    return {
        "schema_version": 1,
        "workflow_run_id": workflow_run_id,
        "run_attempt": 1,
        "commit_sha": commit_sha,
        "observed_at": _iso(day, 3, 20),
        "replayed": False,
        "admission": {
            "decision": "admitted",
            "reason": "admitted",
            "requested_at": _iso(day, 3, 17),
        },
        "scheduled_run": {
            "state": "published",
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "item_count": 30,
            "published_at": _iso(day, 3, 19),
            "current_snapshot_matches": True,
        },
        "status": {
            "schema_version": 1,
            "service": "finance-crawler-ingest",
            "as_of": _iso(day, 3, 20),
            "state": "warning",
            "reasons": ["partial_snapshot", "source_failures"],
            "freshness": {
                "state": "healthy",
                "age_seconds": 60,
                "warning_after_seconds": 21600,
                "stale_after_seconds": 86400,
            },
            "current_snapshot": {
                "snapshot_id": snapshot_id,
                "run_id": run_id,
                "as_of": _iso(day, 3, 19),
                "partial": True,
                "failed_source_count": 1,
                "topic_count": 3,
                "content_sha256": content_sha,
            },
            "source_counts": {
                "total": 15,
                "success": 14,
                "partial": 0,
                "failed": 1,
            },
        },
        "d1_counts": {
            "runs": counters,
            "published_runs": counters,
            "raw_items": counters * 30,
            "topic_snapshots": counters,
            "audit_events": counters * 2,
            "run_admissions": counters,
            "operational_alerts": 0,
            "open_alerts": 0,
        },
        "r2_integrity": {
            "checked_objects": 2,
            "max_checked_objects": 4,
            "all_metadata_match": True,
            "samples": [
                {
                    "kind": "topic",
                    "object_key": f"topics/{snapshot_id}.json",
                    "size": 512,
                    "content_sha256": content_sha,
                },
                {
                    "kind": "raw",
                    "object_key": f"raw/source_00/{run_number:064x}.json",
                    "size": 256,
                    "content_sha256": f"{run_number + 201:064x}",
                },
            ],
        },
    }


def _day(day: date, run_number: int, counters: int) -> dict[str, object]:
    observation = _observation(day, run_number, counters)
    scheduled_run = observation["scheduled_run"]
    assert isinstance(scheduled_run, dict)
    return {
        "date": day.isoformat(),
        "github": {
            "workflow_run_id": observation["workflow_run_id"],
            "commit_sha": observation["commit_sha"],
            "event_name": "schedule",
            "conclusion": "success",
            "run_attempt": 1,
            "started_at": _iso(day, 3, 17),
            "completed_at": _iso(day, 3, 21),
            "duration_seconds": 240,
            "source": "github_api",
        },
        "observation": observation,
        "source_report": {
            "schema_version": 1,
            "run_id": scheduled_run["run_id"],
            "snapshot_id": scheduled_run["snapshot_id"],
            "as_of": _iso(day, 3, 19),
            "accepted": True,
            "minimum_successful_sources": 12,
            "total_sources": 15,
            "successful_sources": 14,
            "failed_sources": ["source_14"],
            "items": 30,
            "topics": 3,
            "source_results": [
                {
                    "source_id": source_id,
                    "transport": "browser" if index < 3 else "json_api" if index < 8 else "rss",
                    "status": "failed" if source_id == "source_14" else "success",
                    "route": "synthetic_test",
                    "request_url": f"https://example.com/{source_id}",
                    "item_count": 0 if source_id == "source_14" else 2,
                    "catchup_strategy": "bounded_window",
                    "published_since": _iso(day - timedelta(days=1), 3, 17),
                }
                for index, source_id in enumerate(SOURCE_IDS)
            ],
            "checkpoints": [
                {
                    "source_id": source_id,
                    "status": "failed" if source_id == "source_14" else "success",
                    "last_successful_crawl": None
                    if source_id == "source_14"
                    else _iso(day, 3, 19),
                    "last_article_date": None,
                    "cursor": None,
                }
                for source_id in SOURCE_IDS
            ],
        },
        "usage": {
            "schema_version": 1,
            "captured_at": _iso(day + timedelta(days=1), 1),
            "window_started_at": _iso(day, 0),
            "window_ended_at": _iso(day + timedelta(days=1), 0),
            "github_source": "github_api",
            "github_repository": "ai-cooperation/finance-crawler-validation",
            "github_repo_visibility": "public",
            "workflow_run_id": str(40000000000 + run_number),
            "run_attempt": 1,
            "commit_sha": observation["commit_sha"],
            "github_actions_runner_seconds": 240,
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
        },
    }


def valid_bundle() -> dict[str, object]:
    start = date(2026, 8, 14)
    return {
        "schema_version": 1,
        "repository": "ai-cooperation/finance-crawler-validation",
        "workflow": ".github/workflows/topic-radar.yml",
        "window_started_at": _iso(start, 0),
        "window_ended_at": _iso(start + timedelta(days=7), 0),
        "expected_source_ids": list(SOURCE_IDS),
        "resource_ceilings": {
            "github_actions_runner_seconds": 2700,
            "worker_requests": 100,
            "d1_rows_read": 5000,
            "d1_rows_written": 1000,
            "r2_class_a_operations": 500,
            "r2_class_b_operations": 500,
        },
        "human_alert_validation": {
            "primary_provider": "provider_a",
            "fallback_provider": "provider_b",
            "different_failure_domain": True,
            "primary_received_at": _iso(start - timedelta(days=1), 1),
            "fallback_received_at": _iso(start - timedelta(days=1), 2),
            "confirmation_reference": "private-operator-record-1",
        },
        "days": [
            _day(start + timedelta(days=index), index, index + 10)
            for index in range(7)
        ],
    }


def test_accepts_seven_consecutive_machine_and_human_verified_days() -> None:
    result = verify_soak_window(valid_bundle())

    assert result == {
        "accepted": True,
        "days_verified": 7,
        "scheduled_runs": 7,
        "published_runs": 7,
        "source_observations": 105,
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda bundle: bundle["days"].pop(), "exactly 7 daily records"),
        (
            lambda bundle: bundle["days"][3].__setitem__("date", "2026-08-18"),
            "consecutive UTC dates",
        ),
        (
            lambda bundle: bundle["days"][1]["github"].__setitem__(
                "workflow_run_id", bundle["days"][0]["github"]["workflow_run_id"]
            ),
            "unique GitHub workflow run IDs",
        ),
        (
            lambda bundle: bundle["days"][2]["github"].__setitem__("conclusion", "failure"),
            "GitHub conclusion must be success",
        ),
        (
            lambda bundle: bundle["days"][2]["observation"].__setitem__("run_attempt", 2),
            "observation run_attempt must match GitHub",
        ),
        (
            lambda bundle: bundle["days"][2]["observation"]["admission"].__setitem__(
                "decision", "denied"
            ),
            "scheduled admission must be admitted",
        ),
        (
            lambda bundle: bundle["days"][2]["observation"]["scheduled_run"].__setitem__(
                "state", "incomplete"
            ),
            "scheduled run must be published",
        ),
        (
            lambda bundle: bundle["days"][2]["observation"]["status"]["freshness"].__setitem__(
                "state", "stale"
            ),
            "snapshot freshness must be healthy",
        ),
        (
            lambda bundle: bundle["days"][2]["observation"]["d1_counts"].__setitem__(
                "open_alerts", 1
            ),
            "open alert count must be zero",
        ),
        (
            lambda bundle: bundle["days"][2]["source_report"].__setitem__("accepted", False),
            "source report must be accepted",
        ),
        (
            lambda bundle: bundle["days"][2]["source_report"]["source_results"].pop(),
            "source report must cover the exact manifest source set",
        ),
        (
            lambda bundle: bundle["days"][3]["observation"]["d1_counts"].__setitem__(
                "raw_items", 1
            ),
            "D1 counters must be monotonic",
        ),
        (
            lambda bundle: bundle["days"][2]["usage"].__setitem__("worker_requests", 101),
            "resource ceiling exceeded: worker_requests",
        ),
        (
            lambda bundle: bundle["days"][2]["usage"].__setitem__(
                "workflow_run_id", "49999999999"
            ),
            "usage workflow_run_id must match GitHub",
        ),
        (
            lambda bundle: bundle["days"][2]["usage"].__setitem__(
                "github_actions_billable_seconds", 1
            ),
            "GitHub Actions billable seconds must be zero",
        ),
        (
            lambda bundle: bundle["human_alert_validation"].__setitem__(
                "fallback_provider", "provider_a"
            ),
            "alert providers must use different failure domains",
        ),
    ],
)
def test_rejects_incomplete_or_inconsistent_soak_evidence(mutate, message: str) -> None:
    bundle = deepcopy(valid_bundle())
    mutate(bundle)

    with pytest.raises(SoakVerificationError, match=message):
        verify_soak_window(bundle)


def test_cli_writes_a_separate_verdict_file(tmp_path, capsys) -> None:
    from finance_crawler_poc.soak_verify import main

    evidence = tmp_path / "evidence.json"
    verdict = tmp_path / "verdict.json"
    evidence.write_text(__import__("json").dumps(valid_bundle()), encoding="utf-8")

    assert main([str(evidence), "--output", str(verdict)]) == 0
    assert __import__("json").loads(verdict.read_text(encoding="utf-8"))["accepted"] is True
    assert __import__("json").loads(capsys.readouterr().out)["accepted"] is True
