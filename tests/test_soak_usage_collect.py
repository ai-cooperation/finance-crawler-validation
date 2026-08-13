from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from finance_crawler_poc.soak_usage import UsageCollectionError
from finance_crawler_poc.soak_usage_collect import collect_daily_usage, write_private_json


def github_run() -> dict[str, object]:
    return {
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
    }


def github_jobs() -> dict[str, object]:
    return {
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
    }


def cloudflare_response() -> dict[str, object]:
    return {
        "data": {
            "viewer": {
                "accounts": [
                    {
                        "workers": [{"sum": {"requests": 8}}],
                        "d1": [{"sum": {"rowsRead": 200, "rowsWritten": 100}}],
                        "r2": [
                            {
                                "dimensions": {"actionType": "PutObject"},
                                "sum": {"requests": 40},
                            },
                            {
                                "dimensions": {"actionType": "HeadObject"},
                                "sum": {"requests": 10},
                            },
                        ],
                    }
                ]
            }
        },
        "errors": None,
    }


def test_collects_bound_usage_from_official_endpoints_without_leaking_tokens() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url == httpx.URL(
            "https://api.github.com/repos/ai-cooperation/finance-crawler-validation"
        ):
            return httpx.Response(
                200,
                json={"full_name": "ai-cooperation/finance-crawler-validation", "private": False},
            )
        if request.url == httpx.URL(
            "https://api.github.com/repos/ai-cooperation/finance-crawler-validation/actions/runs/40000000000"
        ):
            return httpx.Response(200, json=github_run())
        if request.url == httpx.URL(
            "https://api.github.com/repos/ai-cooperation/finance-crawler-validation/actions/runs/40000000000/attempts/1/jobs?per_page=100"
        ):
            return httpx.Response(200, json=github_jobs())
        if request.url == httpx.URL("https://api.cloudflare.com/client/v4/graphql"):
            body = json.loads(request.content)
            assert body["variables"] == {
                "accountTag": "ca985c195ab218488fc0744692dbde21",
                "bucketName": "finance-crawler-validation-raw",
                "databaseId": "476bd84f-e924-4b9b-a9d9-dfca9ea29a1a",
                "date": "2026-08-14",
                "datetimeEnd": "2026-08-15T00:00:00Z",
                "datetimeStart": "2026-08-14T00:00:00Z",
                "scriptName": "finance-crawler-validation-ingest",
            }
            assert "workersInvocationsAdaptive" in body["query"]
            assert "d1AnalyticsAdaptiveGroups" in body["query"]
            assert "r2OperationsAdaptiveGroups" in body["query"]
            return httpx.Response(200, json=cloudflare_response())
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect_daily_usage(
            workflow_run_id=40000000000,
            day=date(2026, 8, 14),
            cloudflare_token="cf-private-token",
            github_token="gh-private-token",
            captured_at=datetime(2026, 8, 15, 1, tzinfo=timezone.utc),
            client=client,
        )

    assert result["workflow_run_id"] == "40000000000"
    assert result["github_actions_runner_seconds"] == 181
    assert result["github_actions_billable_seconds"] == 0
    assert result["worker_requests"] == 8
    assert requests[0].headers["authorization"] == "Bearer gh-private-token"
    assert requests[-1].headers["authorization"] == "Bearer cf-private-token"
    assert "gh-private-token" not in json.dumps(result)
    assert "cf-private-token" not in json.dumps(result)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"errors": [{"message": "forbidden"}], "data": None}, "GraphQL errors"),
        (
            {"errors": None, "data": {"viewer": {"accounts": []}}},
            "exactly one Cloudflare account",
        ),
    ],
)
def test_cloudflare_failures_are_explicit_and_do_not_echo_the_token(
    response: dict[str, object], message: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/ai-cooperation/finance-crawler-validation":
            return httpx.Response(
                200,
                json={"full_name": "ai-cooperation/finance-crawler-validation", "private": False},
            )
        if request.url.path.endswith("/attempts/1/jobs"):
            return httpx.Response(200, json=github_jobs())
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=github_run())
        return httpx.Response(200, json=response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UsageCollectionError, match=message) as raised:
            collect_daily_usage(
                workflow_run_id=40000000000,
                day=date(2026, 8, 14),
                cloudflare_token="do-not-echo-this-token",
                captured_at=datetime(2026, 8, 15, 1, tzinfo=timezone.utc),
                client=client,
            )
    assert "do-not-echo-this-token" not in str(raised.value)


def test_private_json_is_atomic_and_owner_read_write_only(tmp_path: Path) -> None:
    output = tmp_path / "private" / "usage.json"
    write_private_json(output, {"schema_version": 1, "value": 2})

    assert json.loads(output.read_text(encoding="utf-8")) == {"schema_version": 1, "value": 2}
    assert output.stat().st_mode & 0o777 == 0o600
    assert not output.with_suffix(".json.tmp").exists()


def test_private_json_refuses_an_existing_shared_directory(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)

    with pytest.raises(UsageCollectionError, match="private output directory"):
        write_private_json(shared / "usage.json", {"schema_version": 1})

    assert not (shared / "usage.json").exists()


def test_private_json_never_overwrites_existing_evidence(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    output = private / "usage.json"
    output.write_text('{"original":true}\n', encoding="utf-8")

    with pytest.raises(UsageCollectionError, match="already exists"):
        write_private_json(output, {"replacement": True})

    assert output.read_text(encoding="utf-8") == '{"original":true}\n'


def test_cli_fails_closed_without_cloudflare_token(tmp_path: Path, monkeypatch, capsys) -> None:
    from finance_crawler_poc.soak_usage_collect import main

    output = tmp_path / "usage.json"
    monkeypatch.delenv("CF_ANALYTICS_API_TOKEN", raising=False)

    assert main(
        [
            "--workflow-run-id",
            "40000000000",
            "--day",
            "2026-08-14",
            "--output",
            str(output),
        ]
    ) == 2
    assert "CF_ANALYTICS_API_TOKEN is required" in capsys.readouterr().out
    assert not output.exists()
