import json
from pathlib import Path

from finance_crawler_poc.models import Outcome, ProbeResult
from finance_crawler_poc.report import write_reports


def result(
    source_id: str,
    outcome: Outcome,
    *,
    transport: str = "browser",
    kind: str = "community",
    community_type: str = "retail_investing",
    region: str = "US",
    access_tier: str = "public_web",
    run_index: int = 1,
    route_group: str = "",
) -> ProbeResult:
    return ProbeResult(
        source_id=source_id,
        name=source_id,
        topic="finance",
        transport=transport,
        url=f"https://example.com/{source_id}",
        outcome=outcome,
        status_code=200 if outcome is Outcome.SUCCESS else None,
        attempts=1,
        elapsed_ms=123,
        content_chars=100 if outcome is Outcome.SUCCESS else 0,
        content_sha256="a" * 64 if outcome is Outcome.SUCCESS else "",
        preview="evidence" if outcome is Outcome.SUCCESS else "",
        error="" if outcome is Outcome.SUCCESS else "blocked",
        kind=kind,
        community_type=community_type,
        region=region,
        access_tier=access_tier,
        provenance="test",
        run_index=run_index,
        route_group=route_group or source_id,
    )


def test_write_reports_emits_machine_and_human_readable_contract(tmp_path: Path) -> None:
    paths = write_reports(
        [
            result("ok", Outcome.SUCCESS),
            result("ok", Outcome.SUCCESS, run_index=2),
            result(
                "no",
                Outcome.BLOCKED,
                transport="json_api",
                kind="market_data",
                community_type="quantitative",
                region="global",
                access_tier="public_api",
            ),
            result(
                "no",
                Outcome.AUTH_REQUIRED,
                transport="json_api",
                kind="market_data",
                community_type="quantitative",
                region="global",
                access_tier="public_api",
                run_index=2,
            ),
        ],
        tmp_path,
        generated_at="2026-08-07T00:00:00Z",
    )

    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    markdown = paths.markdown_path.read_text(encoding="utf-8")

    assert payload["schema_version"] == 4
    assert payload["summary"] == {"auth_required": 1, "blocked": 1, "success": 2}
    assert payload["breakdown"]["by_transport"] == {
        "browser": {"success": 2},
        "json_api": {"auth_required": 1, "blocked": 1},
    }
    assert payload["breakdown"]["by_kind"] == {
        "community": {"success": 2},
        "market_data": {"auth_required": 1, "blocked": 1},
    }
    assert payload["breakdown"]["by_community_type"] == {
        "quantitative": {"auth_required": 1, "blocked": 1},
        "retail_investing": {"success": 2},
    }
    assert payload["breakdown"]["by_region"] == {
        "US": {"success": 2},
        "global": {"auth_required": 1, "blocked": 1},
    }
    assert payload["breakdown"]["by_access_tier"] == {
        "public_api": {"auth_required": 1, "blocked": 1},
        "public_web": {"success": 2},
    }
    assert payload["path_repeatability"] == [
        {"source_id": "ok", "observations": 2, "successes": 2, "outcomes": {"success": 2}},
        {
            "source_id": "no",
            "observations": 2,
            "successes": 0,
            "outcomes": {"auth_required": 1, "blocked": 1},
        },
    ]
    assert [item["source_id"] for item in payload["results"]] == ["ok", "ok", "no", "no"]
    assert payload["measurement"]["repeat_semantics"] == "burst_repeatability"
    assert payload["acquisition"]["resolved_first_pass"]["by_access_tier"] == {
        "public_api": {"paths": 1, "successes": 0, "success_rate": 0.0},
        "public_web": {"paths": 1, "successes": 1, "success_rate": 1.0},
    }
    assert payload["community_resolution"] == [
        {
            "route_group": "ok",
            "paths": ["ok"],
            "successful_paths": ["ok"],
            "resolved": True,
            "transports": ["browser"],
        },
        {
            "route_group": "no",
            "paths": ["no"],
            "successful_paths": [],
            "resolved": False,
            "transports": ["json_api"],
        },
    ]
    assert "| ok | retail_investing | US | public_web | browser | 2/2 | success=2 |" in markdown
    assert "| 2 | no | quantitative | global | public_api | json_api | auth_required | - |" in markdown


def test_report_separates_direct_path_from_relay_resolved_result(tmp_path: Path) -> None:
    from finance_crawler_poc.models import DeliveryAttempt

    recovered = result("feed", Outcome.SUCCESS, transport="rss", access_tier="public_feed")
    recovered = ProbeResult(
        **{
            **recovered.to_dict(),
            "outcome": Outcome.SUCCESS,
            "delivery_attempts": (
                DeliveryAttempt(
                    route="direct",
                    outcome=Outcome.BLOCKED,
                    status_code=403,
                    content_chars=100,
                    content_sha256="b" * 64,
                    preview="challenge",
                    error="HTTP 403",
                ),
                DeliveryAttempt(
                    route="cloudflare_relay",
                    outcome=Outcome.SUCCESS,
                    status_code=200,
                    content_chars=1000,
                    content_sha256="c" * 64,
                    preview="feed",
                    error="",
                ),
            ),
        }
    )

    paths = write_reports([recovered], tmp_path, generated_at="2026-08-08T00:00:00Z")
    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))

    assert payload["acquisition"]["direct_first_pass"]["by_transport"]["rss"] == {
        "paths": 1,
        "successes": 0,
        "success_rate": 0.0,
    }
    assert payload["acquisition"]["resolved_first_pass"]["by_transport"]["rss"] == {
        "paths": 1,
        "successes": 1,
        "success_rate": 1.0,
    }
    assert payload["acquisition"]["fallback_recoveries"] == 1
