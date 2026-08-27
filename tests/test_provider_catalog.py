from __future__ import annotations

from collections import Counter

import pytest

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.provider_catalog import (
    DEFAULT_CATALOG_PATH,
    ProviderCatalogError,
    load_provider_catalog,
    providers_for_gaps,
    query_providers,
    render_provider_url,
)
from finance_crawler_poc.research_planner import build_research_plan
from finance_crawler_poc.target_profiles import get_target_profile


EXPECTED_DISCOVERY_SOURCES = {
    "public_apis",
    "openbb",
    "finmind",
    "akshare",
    "awesome_realtime",
    "finance_database",
}

CRITICAL_PROVIDER_IDS = {
    "twse_openapi",
    "mops_xbrl",
    "finmind",
    "moea_industrial_statistics",
    "taiwan_customs_trade",
    "openbb",
    "akshare",
    "finance_database",
    "sec_edgar",
    "fred",
    "bls",
    "eia",
    "world_bank",
    "imf",
    "oecd",
    "gdelt",
    "coingecko",
    "yfinance",
    "wsts",
    "worldsteel",
    "usgs",
}

PIPELINE_DERIVED_METRICS = {
    "currency",
    "period",
    "segment_period",
    "target_id",
    "unit",
}


def _catalog() -> dict[str, object]:
    return load_provider_catalog(DEFAULT_CATALOG_PATH)


def test_catalog_is_versioned_valid_and_contains_all_discovery_projects() -> None:
    catalog = _catalog()
    validate_contract("provider-catalog", catalog)

    discovery = {item["discovery_id"]: item for item in catalog["discovery_sources"]}
    assert EXPECTED_DISCOVERY_SOURCES == discovery.keys()
    for item in discovery.values():
        assert item["repository_url"].startswith("https://github.com/")
        assert len(item["commit_sha"]) == 40
        assert item["repository_path"]

    providers = catalog["providers"]
    assert len(providers) >= 100
    assert CRITICAL_PROVIDER_IDS <= {item["provider_id"] for item in providers}


def test_provider_ids_and_discovery_references_are_not_ambiguous() -> None:
    catalog = _catalog()
    discovery_ids = {item["discovery_id"] for item in catalog["discovery_sources"]}
    provider_ids = [item["provider_id"] for item in catalog["providers"]]

    assert len(provider_ids) == len(set(provider_ids))
    assert all(set(item["discovered_via"]) <= discovery_ids for item in catalog["providers"])
    counts = Counter(source for item in catalog["providers"] for source in item["discovered_via"])
    assert counts["openbb"] >= 32
    assert counts["public_apis"] >= 80


def test_callable_invariants_prevent_unverified_or_commercial_execution() -> None:
    catalog = _catalog()
    callable_statuses = {"verified_public", "verified_requires_key"}
    for provider in catalog["providers"]:
        integration = provider["integration"]
        access = provider["access"]
        if integration["callable"]:
            assert integration["status"] in callable_statuses
            assert integration["adapter"] != "none"
            assert integration["endpoint_template"].startswith("https://")
            assert integration["verification_method"] in {"official_documentation", "live_payload"}
        if integration["status"] == "verified_public":
            assert access["auth"] == "none"
        if integration["status"] == "verified_requires_key":
            assert integration["credential_env"]
        if integration["status"] in {
            "catalogued_unverified",
            "adapter_required",
            "commercial_only",
            "blocked",
            "deprecated",
        }:
            assert integration["callable"] is False


def test_catalog_has_candidates_for_every_research_metric() -> None:
    required_metrics: set[str] = set()
    for target_id in ("tcc", "csc", "formosa", "nanya", "yageo", "asus", "wistron"):
        plan = build_research_plan(get_target_profile(target_id), as_of="2026-08-26T00:00:00Z")
        required_metrics.update(
            str(metric)
            for requirement in plan["requirements"]
            for metric in requirement.get("required_metrics", [])
        )

    uncovered = {
        metric
        for metric in required_metrics - PIPELINE_DERIVED_METRICS
        if not query_providers(_catalog(), metric_ids=[metric], support_levels={"exact", "derived"})
    }
    assert uncovered == set()


def test_query_prefers_taiwan_exact_sources_and_keeps_noncallable_candidates() -> None:
    rows = query_providers(
        _catalog(),
        metric_ids=["segment_revenue"],
        geographies=["TW"],
        support_levels={"exact", "derived", "proxy"},
    )

    assert rows
    assert rows[0]["provider_id"] in {"mops_xbrl", "twse_openapi", "finmind"}
    assert any(not row["integration"]["callable"] for row in rows)
    assert [row["rank_score"] for row in rows] == sorted(
        (row["rank_score"] for row in rows), reverse=True
    )

    callable_rows = query_providers(
        _catalog(), metric_ids=["valuation"], geographies=["TW"], callable_only=True
    )
    assert callable_rows
    assert all(row["integration"]["callable"] for row in callable_rows)


def test_gap_handoff_reports_credentials_and_adapter_blocks() -> None:
    candidates = providers_for_gaps(
        _catalog(),
        gaps=[{
            "requirement_id": "peer.comparison",
            "missing_metrics": ["valuation"],
            "missing_geographies": ["TW", "Asia"],
        }],
        configured_credentials=set(),
    )

    rows = candidates["peer.comparison"]
    assert rows
    assert all(
        "callable_now" in row and "blocked_reasons" in row and "missing_parameters" in row
        for row in rows
    )
    assert any("credential_not_configured" in row["blocked_reasons"] for row in rows)
    assert any("provider_not_callable" in row["blocked_reasons"] for row in rows)
    assert any("route_parameters_missing" in row["blocked_reasons"] for row in rows)
    assert all(
        "auth_injection" not in row or row["auth_injection"]
        for row in rows
    )
    assert all("auth_field" not in row or row["auth_field"] for row in rows)


def test_render_provider_url_encodes_values_and_rejects_unknown_or_missing_parameters() -> None:
    catalog = _catalog()
    provider = next(item for item in catalog["providers"] if item["provider_id"] == "finmind")

    url = render_provider_url(
        provider,
        {
            "dataset": "TaiwanStockFinancialStatements",
            "data_id": "2330 TW",
            "start_date": "2025-01-01",
        },
    )
    assert "data_id=2330+TW" in url

    with pytest.raises(ProviderCatalogError, match="missing template parameters"):
        render_provider_url(provider, {"dataset": "TaiwanStockFinancialStatements"})
    with pytest.raises(ProviderCatalogError, match="unknown template parameters"):
        render_provider_url(
            provider,
            {
                "dataset": "TaiwanStockFinancialStatements",
                "data_id": "2330",
                "start_date": "2025-01-01",
                "redirect_url": "https://evil.example",
            },
        )
