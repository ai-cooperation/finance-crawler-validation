from __future__ import annotations

from datetime import datetime, timezone

from finance_crawler_poc.context_requirements import (
    build_context_coverage,
    build_context_gap_report,
    build_research_requirements,
    build_context_packs,
)
from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.target_profiles import get_target_profile


def _source(url: str, group: str, *, roles: list[str], success: bool = True, **extra: object) -> dict[str, object]:
    return {
        "url": url,
        "group": group,
        "publisher": group,
        "requirement_ids": roles,
        "fetch_status": "success" if success else "failed",
        "response_sha256": "a" * 64 if success else None,
        "content_type": "text/html",
        **extra,
    }


def test_requirements_are_industry_specific_and_contract_valid() -> None:
    profile = get_target_profile("csc")
    requirements = build_research_requirements(profile)

    assert requirements["industry_family"] == "steel"
    ids = {item["requirement_id"] for item in requirements["requirements"]}
    assert {"industry.price_capacity_cycle", "peer.comparison", "segment.disclosure"} <= ids
    validate_contract("research-context-requirement", requirements)


def test_coverage_does_not_count_duplicate_or_failed_sources() -> None:
    profile = get_target_profile("csc")
    context = {
        "company": {"sources": [_source("https://issuer.example/annual", "issuer", roles=["company.business_model", "segment.disclosure"])]},
        "industry": {
            "sources": [
                _source("https://steel.example/stat", "steel_stats", roles=["industry.market_demand", "industry.price_capacity_cycle"]),
                _source("https://steel.example/stat-rss", "steel_stats", roles=["industry.market_demand"]),
                _source("https://peer.example/report", "peer_a", roles=["peer.comparison"]),
                _source("https://peer.example/report2", "peer_b", roles=["peer.comparison"]),
            ]
        },
        "governance": {"sources": [_source("https://issuer.example/governance", "issuer_governance", roles=["governance.board_and_ownership"])]},
        "esg": {"sources": [_source("https://issuer.example/esg", "issuer_esg", roles=["esg.materiality_kpi"] , success=False)]},
    }
    packs = build_context_packs(profile, context=context, history={}, valuation={})
    coverage = build_context_coverage(profile, context=context, packs=packs, generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    price = next(item for item in coverage["requirements"] if item["requirement_id"] == "industry.price_capacity_cycle")
    assert price["independent_source_count"] == 1
    assert price["status"] == "partial"
    esg = next(item for item in coverage["requirements"] if item["requirement_id"] == "esg.materiality_kpi")
    assert esg["status"] == "partial"
    assert coverage["summary"]["l3_ready"] is False
    validate_contract("research-context-coverage", coverage)


def test_gap_report_preserves_missing_requirement_and_routes() -> None:
    profile = get_target_profile("tcc")
    requirements = build_research_requirements(profile)
    coverage = {
        "requirements": [
            {
                "requirement_id": requirements["requirements"][0]["requirement_id"],
                "status": "complete",
                "missing_reasons": [],
                "evidence_ids": [],
                "source_groups": [],
                "independent_source_count": 0,
            }
        ]
    }
    gap = build_context_gap_report(profile, requirements=requirements, coverage=coverage)

    assert gap["status"] == "refresh_required"
    assert gap["missing_requirements"]
    assert all(item["recommended_routes"] for item in gap["missing_requirements"])
    validate_contract("research-context-gap", gap)


def test_pack_status_is_not_promoted_by_source_count() -> None:
    profile = get_target_profile("nanya")
    context = {"industry": {"sources": [_source("https://one.example", "one", roles=["industry.market_demand"])]}}
    packs = build_context_packs(profile, context=context, history={}, valuation={})

    assert packs["industry"]["status"] == "partial"
    assert packs["industry"]["evidence_count"] == 1
    validate_contract("context-pack", packs["industry"])


def test_required_metrics_are_not_satisfied_by_successful_source_count() -> None:
    profile = get_target_profile("tcc")
    requirement_id = "industry.market_demand"
    context = {
        "industry": {
            "sources": [
                _source("https://issuer.test", "issuer", roles=[requirement_id], evidence_role="company_disclosure", geography_scope=["TW"]),
                _source("https://stats.test", "stats", roles=[requirement_id], evidence_role="industry_statistic", geography_scope=["Asia"]),
                _source("https://peer.test", "peer", roles=[requirement_id], evidence_role="peer_filing", geography_scope=["Asia"]),
            ]
        }
    }

    coverage = build_context_coverage(profile, context=context, packs={"industry": {"status": "available"}})
    market = next(item for item in coverage["requirements"] if item["requirement_id"] == requirement_id)

    assert market["status"] == "partial"
    assert set(market["missing_metrics"]) == {"market_size_or_demand", "period", "unit"}
    assert "required_metrics_missing" in market["missing_reasons"]


def test_wrong_geography_and_missing_required_role_fail_closed() -> None:
    profile = get_target_profile("tcc")
    requirement_id = "industry.market_demand"
    metrics = [
        {"metric": "market_size_or_demand", "value": 100, "unit": "million tonnes", "period": "2025", "geography_scope": "US"},
        {"metric": "period", "value": "2025", "unit": "year", "period": "2025", "geography_scope": "US"},
        {"metric": "unit", "value": "million tonnes", "unit": "text", "period": "2025", "geography_scope": "US"},
    ]
    context = {
        "industry": {
            "sources": [
                _source(f"https://source{index}.test", f"group{index}", roles=[requirement_id], evidence_role=role, geography_scope=["US"], metric_attestations=metrics)
                for index, role in enumerate(("company_disclosure", "industry_statistic", "industry_statistic"), start=1)
            ]
        }
    }

    coverage = build_context_coverage(profile, context=context, packs={"industry": {"status": "available"}})
    market = next(item for item in coverage["requirements"] if item["requirement_id"] == requirement_id)

    assert market["status"] == "partial"
    assert "required_evidence_roles_missing" in market["missing_reasons"]
    assert "target_geography_evidence_missing" in market["missing_reasons"]


def test_metric_attestation_with_wrong_response_hash_cannot_raise_coverage() -> None:
    profile = get_target_profile("tcc")
    requirement_id = "industry.market_demand"
    facts = [{
        "metric": "market_size_or_demand",
        "value": "123",
        "unit": "million tonnes",
        "period": "2025",
        "geography_scope": "Asia",
        "source_response_sha256": "b" * 64,
        "target_id": "tcc",
        "requirement_ids": [requirement_id],
    }]
    context = {"industry": {"sources": [
        _source(
            "https://stats.test",
            "stats",
            roles=[requirement_id],
            evidence_role="industry_statistic",
            geography_scope=["Asia"],
            metric_attestations=facts,
        )
    ]}}

    coverage = build_context_coverage(profile, context=context, packs={"industry": {"status": "available"}})
    market = next(item for item in coverage["requirements"] if item["requirement_id"] == requirement_id)

    assert "market_size_or_demand" in market["missing_metrics"]
