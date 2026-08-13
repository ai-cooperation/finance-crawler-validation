from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from finance_crawler_poc.radar_manifest import load_radar_manifest
from finance_crawler_poc.radar_run_plan import (
    RadarRunPlanError,
    build_catchup_windows,
    build_run_plan_request,
    main,
    parse_worker_run_plan,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 13, 8, tzinfo=timezone.utc)


def checkpoints() -> list[dict[str, object]]:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    return [
        {
            "source_id": source.source_id,
            "status": "success",
            "last_successful_crawl": "2026-08-12T02:05:00Z",
            "last_article_date": "2026-08-12T02:00:00Z",
            "cursor": None,
        }
        for source in manifest.sources
    ]


def test_catchup_windows_generate_transport_specific_request_urls() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")

    windows = build_catchup_windows(manifest, checkpoints(), now=NOW)
    by_id = {window.source_id: window for window in windows}

    assert by_id["federal_reserve_press_rss"].request_url.endswith("press_all.xml")
    assert by_id["federal_reserve_press_rss"].published_since == "2026-08-12T02:00:00Z"
    assert "numericFilters=created_at_i%3E" in by_id["hacker_news_finance_api"].request_url
    assert "fromdate=" in by_id["money_stackexchange_api"].request_url
    assert "since=2026-08-12T02%3A00%3A00Z" in by_id["openbb_github_issues_api"].request_url
    assert by_id["coingecko_markets_api"].published_since is None
    assert by_id["tradingview_ideas_browser"].published_since is None


def test_initial_or_stale_checkpoint_is_clamped_to_seven_days() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    empty = [
        {
            "source_id": source.source_id,
            "status": None,
            "last_successful_crawl": None,
            "last_article_date": None,
            "cursor": None,
        }
        for source in manifest.sources
    ]
    empty[0] = {
        **empty[0],
        "status": "success",
        "last_successful_crawl": "2025-01-01T00:00:00Z",
    }

    windows = build_catchup_windows(manifest, empty, now=NOW)

    bounded = "2026-08-06T08:00:00Z"
    assert windows[0].published_since == bounded
    assert windows[1].published_since == bounded


def test_future_checkpoint_is_clamped_to_run_time() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    future = checkpoints()
    future[0] = {
        **future[0],
        "last_article_date": "2026-08-14T08:00:00Z",
    }

    windows = build_catchup_windows(manifest, future, now=NOW)

    assert windows[0].published_since == "2026-08-13T08:00:00Z"


def test_successful_empty_run_advances_from_the_newer_crawl_checkpoint() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    advanced = checkpoints()
    advanced[0] = {
        **advanced[0],
        "last_article_date": "2026-08-10T02:00:00Z",
        "last_successful_crawl": "2026-08-13T07:00:00Z",
    }

    windows = build_catchup_windows(manifest, advanced, now=NOW)

    assert windows[0].published_since == "2026-08-13T06:55:00Z"


def test_worker_run_plan_is_fail_closed_and_identity_bound() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    response = {
        "schema_version": 1,
        "as_of": "2026-08-13T08:00:00.000Z",
        "admitted": True,
        "reason": "admitted",
        "retry_after_seconds": 0,
        "policy": {
            "daily_run_limit": 2,
            "minimum_interval_seconds": 21600,
            "admitted_runs_today": 0,
        },
        "checkpoints": checkpoints(),
        "request_id": "synthetic-request-id",
    }

    parsed = parse_worker_run_plan(response, manifest)
    assert parsed[0]["source_id"] == manifest.sources[0].source_id

    with pytest.raises(RadarRunPlanError, match="not admitted"):
        parse_worker_run_plan({**response, "admitted": False}, manifest)
    with pytest.raises(RadarRunPlanError, match="source order"):
        parse_worker_run_plan({**response, "checkpoints": list(reversed(checkpoints()))}, manifest)


def test_run_plan_request_and_cli_are_identity_bound(tmp_path: Path) -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    payload = build_run_plan_request(
        manifest,
        workflow_run_id="31309377786",
        commit_sha="d" * 40,
    )
    output = tmp_path / "nested" / "run-plan-request.json"

    exit_code = main([
        "--manifest", str(ROOT / "radar-sources.yaml"),
        "--output", str(output),
        "--workflow-run-id", "31309377786",
        "--commit-sha", "d" * 40,
    ])

    assert exit_code == 0
    assert output.read_text(encoding="utf-8").endswith("\n")
    assert payload["source_ids"] == [source.source_id for source in manifest.sources]
    with pytest.raises(RadarRunPlanError, match="digits"):
        build_run_plan_request(manifest, workflow_run_id="bad", commit_sha="d" * 40)
    with pytest.raises(RadarRunPlanError, match="Git SHA"):
        build_run_plan_request(manifest, workflow_run_id="1", commit_sha="D" * 40)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda response: [], "must be an object"),
        (lambda response: {**response, "extra": True}, "invalid fields"),
        (lambda response: {**response, "checkpoints": "bad"}, "must be a list"),
        (lambda response: {**response, "as_of": "not-a-time"}, "as_of"),
        (
            lambda response: {
                **response,
                "policy": {**response["policy"], "daily_run_limit": 0},
            },
            "policy",
        ),
        (lambda response: {**response, "request_id": ""}, "request_id"),
        (
            lambda response: {
                **response,
                "checkpoints": [{**response["checkpoints"][0], "extra": True}]
                + response["checkpoints"][1:],
            },
            "invalid fields",
        ),
        (
            lambda response: {
                **response,
                "checkpoints": [{**response["checkpoints"][0], "status": "unknown"}]
                + response["checkpoints"][1:],
            },
            "invalid status",
        ),
        (
            lambda response: {
                **response,
                "checkpoints": [{**response["checkpoints"][0], "cursor": 7}]
                + response["checkpoints"][1:],
            },
            "invalid cursor",
        ),
    ],
)
def test_worker_run_plan_rejects_malformed_boundaries(mutation, message: str) -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    response = {
        "schema_version": 1,
        "as_of": "2026-08-13T08:00:00.000Z",
        "admitted": True,
        "reason": "admitted",
        "retry_after_seconds": 0,
        "policy": {
            "daily_run_limit": 2,
            "minimum_interval_seconds": 21600,
            "admitted_runs_today": 1,
        },
        "checkpoints": checkpoints(),
        "request_id": "synthetic-request-id",
    }

    with pytest.raises(RadarRunPlanError, match=message):
        parse_worker_run_plan(mutation(response), manifest)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (7, "string or null"),
        ("not-a-time", "RFC 3339"),
        ("2026-08-13T08:00:00", "timezone"),
    ],
)
def test_catchup_rejects_invalid_checkpoint_times(value: object, message: str) -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    invalid = checkpoints()
    invalid[0] = {**invalid[0], "last_article_date": value}

    with pytest.raises(RadarRunPlanError, match=message):
        build_catchup_windows(manifest, invalid, now=NOW)

    with pytest.raises(RadarRunPlanError, match="timezone-aware"):
        build_catchup_windows(manifest, checkpoints(), now=NOW.replace(tzinfo=None))
