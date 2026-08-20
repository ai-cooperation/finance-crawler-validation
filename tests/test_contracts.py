from __future__ import annotations

from copy import deepcopy
from typing import Callable

import pytest

from finance_crawler_poc.contracts import (
    CONTRACT_NAMES,
    ContractValidationError,
    build_item_id,
    load_contract,
    validate_contract,
)


def source_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_id": "federal_reserve_press_rss",
        "name": "Federal Reserve Press Releases",
        "kind": "official_news",
        "layer": "official",
        "transport": "rss",
        "canonical_url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "freshness_sla_minutes": 360,
        "rights": {
            "redistribution": "metadata_only",
            "retention_days": 30,
            "public_excerpt_chars": 0,
        },
    }


def raw_item() -> dict[str, object]:
    return {
        "schema_version": 1,
        "item_id": "a" * 64,
        "source_id": "federal_reserve_press_rss",
        "canonical_url": "https://www.federalreserve.gov/newsevents/example.htm",
        "title": "Federal Reserve issues a policy statement",
        "summary": "Synthetic for testing only",
        "content": "Synthetic for testing only",
        "published_at": "2026-08-10T02:00:00Z",
        "collected_at": "2026-08-10T02:05:00Z",
        "transport": "rss",
        "kind": "official_news",
        "layer": "official",
        "content_sha256": "b" * 64,
        "rights": {
            "redistribution": "metadata_only",
            "retention_days": 30,
            "public_excerpt_chars": 0,
        },
        "engagement": {
            "score": None,
            "comments": None,
            "shares": None,
            "likes": None,
        },
        "evidence": {
            "route": "direct",
            "status_code": 200,
            "final_url": "https://www.federalreserve.gov/newsevents/example.htm",
            "extraction_method": "rss",
        },
    }


def topic_snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_id": "radar_20260810t020500z",
        "run_id": "run_20260810t020500z",
        "as_of": "2026-08-10T02:05:00Z",
        "partial": False,
        "failed_sources": [],
        "input_item_ids": ["a" * 64],
        "topics": [
            {
                "topic_id": "monetary_policy",
                "label": "Monetary policy",
                "score": 1,
                "item_count": 1,
                "source_count": 1,
                "news_count": 0,
                "social_count": 0,
                "evidence_ids": ["a" * 64],
                "divergence": {
                    "direction": "insufficient_data",
                    "magnitude": None,
                },
            }
        ],
    }


def research_report() -> dict[str, object]:
    claim = {
        "text": "Synthetic for testing only",
        "confidence": 0.5,
        "evidence_ids": ["a" * 64],
    }
    return {
        "schema_version": 1,
        "report_id": "report_20260820t040000z",
        "topic_snapshot_id": "radar_20260810t020500z",
        "plan_id": "plan_20260820t040000z",
        "alignment_id": "align_20260820t035900z",
        "market_snapshot_id": "market_20260820t035900z",
        "topic_id": "digital_assets",
        "generated_at": "2026-08-20T04:00:00Z",
        "expires_at": "2026-08-21T04:00:00Z",
        "model": "synthetic-test-model",
        "agent_version": "tradingagents-deferred-v1",
        "second_opinion": True,
        "evidence_ids": ["a" * 64],
        "bull_case": [claim],
        "bear_case": [claim],
        "risk_view": [claim],
    }


def research_report_envelope() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "upsert_research_report",
        "run_id": "run_20260820t035848z",
        "workflow_run_id": "32330093877",
        "commit_sha": "d" * 40,
        "report": research_report(),
    }


def status_response() -> dict[str, object]:
    return {
        "schema_version": 1,
        "service": "finance-crawler-ingest",
        "as_of": "2026-08-10T02:10:00Z",
        "state": "warning",
        "reasons": ["partial_snapshot"],
        "freshness": {
            "state": "healthy",
            "age_seconds": 300,
            "warning_after_seconds": 21600,
            "stale_after_seconds": 86400,
        },
        "current_snapshot": {
            "snapshot_id": "radar_20260810t020500z",
            "run_id": "run_20260810t020500z",
            "as_of": "2026-08-10T02:05:00Z",
            "partial": True,
            "failed_source_count": 0,
            "topic_count": 1,
            "content_sha256": "c" * 64,
        },
        "source_counts": {"total": 1, "success": 1, "partial": 0, "failed": 0},
    }


def research_requirement() -> dict[str, object]:
    return {
        "schema_version": 1,
        "requirement_id": "req_request-1",
        "target": {"kind": "crypto", "symbol": "BTC", "name": "Bitcoin"},
        "question": "What are the current drivers and risks for BTC?",
        "objective": "research",
        "as_of": "latest",
        "horizon": "months",
        "constraints": {},
        "requested_outputs": ["detailed_report", "evidence_appendix"],
        "include_market_data": True,
        "include_topic_radar": True,
        "max_sources": 12,
        "source_strategy": "actions",
    }


def source_bundle_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "manifest_id": "bundle_req_request-1",
        "requirement_id": "req_request-1",
        "strategy": "refresh",
        "source_ids": ["coingecko_markets_api", "bbc_business_rss"],
        "source_count": 2,
        "layers": ["market", "news"],
        "reused_snapshot_id": None,
        "sufficiency": {
            "status": "refresh_required",
            "coverage_ratio": 0.5,
            "reasons": ["snapshot_partial"],
        },
        "missing_data": ["snapshot_partial"],
        "planner_version": "research-requirement-planner-v1",
        "generated_at": "2026-08-20T04:30:00Z",
        "reason": "explicit_refresh",
    }


def research_job_status() -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": "request-1",
        "job_id": "research_20260820041000_abc12345",
        "status": "queued",
        "target": {"kind": "crypto", "symbol": "BTC"},
        "requirements": {"question": "What are the current drivers and risks for BTC?"},
        "run_id": None,
        "pack_id": None,
        "report_count": 0,
        "error_code": None,
        "created_at": "2026-08-20T04:10:00Z",
        "updated_at": "2026-08-20T04:10:00Z",
        "completed_at": None,
        "stage": "queued",
        "progress": 0,
        "retryable": True,
        "next_action": "poll_job_status",
        "planner": None,
    }


def research_job_complete() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "complete_research_job",
        "job_id": "research_20260820041000_abc12345",
        "run_id": "run_20260810t020500z",
        "plan_id": "plan_20260820t040000z",
        "alignment_id": "align_20260820t035900z",
        "research_target": {"kind": "crypto", "symbol": "BTC", "name": "Bitcoin"},
        "research_requirement_id": "req_request-1",
        "research_source_ids": [
            "coingecko_markets_api",
            "bbc_business_rss",
            "hacker_news_finance_api",
            "cnbc_top_news_rss",
            "marketwatch_topstories_rss",
            "money_stackexchange_api",
            "quant_stackexchange_api",
            "openbb_github_issues_api",
            "tradingagents_github_issues_api",
            "openbb_github_discussions_browser",
            "tradingview_ideas_browser",
            "bogleheads_investing_browser",
        ],
        "workflow_run_id": "32330093877",
        "commit_sha": "d" * 40,
    }


def research_job_failure() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "fail_research_job",
        "job_id": "research_20260820041000_abc12345",
        "research_target": {"kind": "crypto", "symbol": "BTC", "name": "Bitcoin"},
        "research_requirement_id": "req_request-1",
        "error_code": "actions_workflow_failed",
        "workflow_run_id": "32330093877",
        "commit_sha": "d" * 40,
    }


def soak_observation() -> dict[str, object]:
    return {
        "schema_version": 1,
        "workflow_run_id": "31309377786",
        "run_attempt": 1,
        "commit_sha": "d" * 40,
        "observed_at": "2026-08-13T12:00:00Z",
        "replayed": False,
        "admission": {
            "decision": "admitted",
            "reason": "admitted",
            "requested_at": "2026-08-13T11:59:00Z",
        },
        "scheduled_run": {
            "state": "published",
            "run_id": "run_20260810t020500z",
            "snapshot_id": "radar_20260810t020500z",
            "item_count": 1,
            "published_at": "2026-08-13T12:00:00Z",
            "current_snapshot_matches": True,
        },
        "status": status_response(),
        "d1_counts": {
            "runs": 1,
            "published_runs": 1,
            "raw_items": 1,
            "topic_snapshots": 1,
            "audit_events": 2,
            "run_admissions": 1,
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
                    "object_key": "topics/radar_20260810t020500z.json",
                    "size": 512,
                    "content_sha256": "b" * 64,
                },
                {
                    "kind": "raw",
                    "object_key": "raw/source/item.json",
                    "size": 256,
                    "content_sha256": "c" * 64,
                },
            ],
        },
    }


def test_all_versioned_contracts_are_loadable() -> None:
    assert CONTRACT_NAMES == frozenset(
        {
            "audit-event",
            "ingest-envelope",
            "market-alignment-envelope",
            "market-snapshot",
            "market-topic-alignment",
            "raw-item",
            "research-requirement",
            "research-report",
            "research-report-envelope",
            "research-job-complete",
            "research-job-failure",
            "research-job-status",
            "source-record",
            "soak-observation",
            "soak-usage",
            "status-response",
            "source-bundle-manifest",
            "topic-snapshot",
            "tradingagents-plan-envelope",
            "tradingagents-run-plan",
        }
    )
    for name in CONTRACT_NAMES:
        schema = load_contract(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/{name}.schema.json")


def test_research_report_requires_plan_market_and_root_evidence_links() -> None:
    validate_contract("research-report", research_report())
    invalid = deepcopy(research_report())
    del invalid["plan_id"]
    with pytest.raises(ContractValidationError):
        validate_contract("research-report", invalid)


def test_research_report_envelope_accepts_private_ingest_shape() -> None:
    validate_contract("research-report-envelope", research_report_envelope())


@pytest.mark.parametrize(
    ("contract", "payload"),
    [
        ("source-record", source_record()),
        ("raw-item", raw_item()),
        ("research-requirement", research_requirement()),
        ("topic-snapshot", topic_snapshot()),
        ("status-response", status_response()),
        ("source-bundle-manifest", source_bundle_manifest()),
        ("research-job-status", research_job_status()),
        ("research-job-complete", research_job_complete()),
        ("research-job-failure", research_job_failure()),
        ("soak-observation", soak_observation()),
    ],
)
def test_core_contracts_accept_valid_payloads(
    contract: str, payload: dict[str, object]
) -> None:
    validate_contract(contract, payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda item: item.pop("title"), "title"),
        (lambda item: item.__setitem__("canonical_url", "file:///tmp/private"), "canonical_url"),
        (lambda item: item.__setitem__("content_sha256", "short"), "content_sha256"),
        (lambda item: item.__setitem__("unexpected", True), "Additional properties"),
    ],
)
def test_raw_item_rejects_boundary_violations(
    mutate: Callable[[dict[str, object]], object], message: str
) -> None:
    item = deepcopy(raw_item())
    mutate(item)

    with pytest.raises(ContractValidationError, match=message):
        validate_contract("raw-item", item)


def test_source_rights_contract_rejects_public_excerpt_beyond_policy() -> None:
    source = source_record()
    source["rights"] = {
        "redistribution": "metadata_only",
        "retention_days": 30,
        "public_excerpt_chars": 500,
    }

    with pytest.raises(ContractValidationError, match="public_excerpt_chars"):
        validate_contract("source-record", source)


def test_topic_snapshot_never_accepts_more_than_three_topics() -> None:
    snapshot = topic_snapshot()
    first = snapshot["topics"][0]  # type: ignore[index]
    snapshot["topics"] = [deepcopy(first) for _ in range(4)]

    with pytest.raises(ContractValidationError, match="too long"):
        validate_contract("topic-snapshot", snapshot)


def test_item_id_is_deterministic_and_source_scoped() -> None:
    first = build_item_id(
        "source_a",
        "https://example.com/article?utm_source=newsletter",
        "a" * 64,
    )
    replay = build_item_id(
        "source_a",
        "https://example.com/article?utm_source=newsletter",
        "a" * 64,
    )
    other_source = build_item_id(
        "source_b",
        "https://example.com/article?utm_source=newsletter",
        "a" * 64,
    )
    revised_content = build_item_id(
        "source_a",
        "https://example.com/article?utm_source=newsletter",
        "b" * 64,
    )

    assert first == replay
    assert first != other_source
    assert first != revised_content
    assert len(first) == 64
