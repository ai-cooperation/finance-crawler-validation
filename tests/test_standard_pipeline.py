from __future__ import annotations

import pytest

from finance_crawler_poc.canonical_evidence import build_canonical_evidence_pack
from finance_crawler_poc.quality_gate import evaluate_quality_gate
from finance_crawler_poc.source_registry import (
    SourceRegistryError,
    build_registry_for_items,
    build_source_registry,
)


def _item(item_id: str, source_id: str, title: str, url: str, published_at: str = "2026-08-21T10:00:00Z") -> dict[str, object]:
    return {
        "item_id": item_id,
        "source_id": source_id,
        "canonical_url": url,
        "content_sha256": (item_id[0] * 64),
        "title": title,
        "summary": title,
        "published_at": published_at,
    }


def _registry() -> dict[str, object]:
    return build_source_registry([
        {
            "source_id": "publisher_rss",
            "publisher_id": "publisher_a",
            "source_tier": "direct_secondary",
            "independence_group": "publisher_a",
            "transport": "rss",
            "canonical_url": "https://publisher.example/rss",
        },
        {
            "source_id": "publisher_search",
            "publisher_id": "publisher_a",
            "source_tier": "direct_secondary",
            "independence_group": "publisher_a",
            "transport": "json_api",
            "canonical_url": "https://publisher.example/search",
        },
        {
            "source_id": "official_ir",
            "publisher_id": "issuer_ir",
            "source_tier": "official",
            "independence_group": "issuer_ir",
            "transport": "rss",
            "canonical_url": "https://issuer.example/ir",
        },
    ])


def test_registry_rejects_duplicate_source_id() -> None:
    entry = {
        "source_id": "dup",
        "publisher_id": "publisher",
        "source_tier": "direct_secondary",
        "independence_group": "publisher",
        "transport": "rss",
        "canonical_url": "https://example.com/rss",
    }
    with pytest.raises(SourceRegistryError, match="duplicate source_id"):
        build_source_registry([entry, dict(entry)])


def test_legacy_registry_fallback_handles_non_mapping_evidence_metadata() -> None:
    registry = build_registry_for_items([
        {"source_id": "legacy", "evidence": "not-an-object"},
    ])

    assert registry["sources"][0]["transport"] == "file"


def test_canonical_pack_rejects_evidence_without_source_registry() -> None:
    with pytest.raises(ValueError, match="non-empty evidence requires"):
        build_canonical_evidence_pack(
            [_item("a" * 64, "publisher_rss", "TSMC expands capacity", "https://publisher.example/a")],
            registry={"schema_version": 1, "registry_id": "empty_sources_v1", "sources": []},
        )


def test_canonical_pack_collapses_syndicated_routes_but_preserves_raw_items() -> None:
    registry = _registry()
    items = [
        _item("a" * 64, "publisher_rss", "TSMC expands capacity", "https://publisher.example/a"),
        _item("b" * 64, "publisher_search", "TSMC expands capacity", "https://publisher.example/a?utm_source=search"),
        _item("c" * 64, "official_ir", "TSMC expands capacity", "https://issuer.example/release"),
    ]

    pack = build_canonical_evidence_pack(items, registry=registry)

    assert pack["item_count"] == 3
    assert pack["canonical_story_count"] == 1
    assert pack["duplicate_item_count"] == 2
    assert pack["independent_publisher_count"] == 2
    assert len(pack["items"]) == 3
    assert len(pack["canonical_items"]) == 1
    assert pack["canonical_items"][0]["item_id"] == "c" * 64


def test_verified_aggregator_publisher_is_resolved_without_counting_aggregator_route() -> None:
    registry = build_source_registry([
        {
            "source_id": "google_news_target_rss",
            "publisher_id": "google_news",
            "source_tier": "aggregator",
            "independence_group": "google_news",
            "transport": "rss",
            "canonical_url": "https://news.google.com/rss/search",
        },
    ])
    item = _item("a" * 64, "google_news_target_rss", "TSMC expands capacity", "https://news.google.com/rss/articles/a")
    item["evidence"] = {
        "publisher_verified": True,
        "publisher_id": "example_wire",
        "publisher_url": "https://example.com",
    }

    pack = build_canonical_evidence_pack([item], registry=registry)

    resolved = pack["items"][0]
    assert resolved["publisher_id"] == "example_wire"
    assert resolved["independence_group"] == "example_wire"
    assert resolved["source_tier"] == "direct_secondary"


def test_equity_gate_blocks_without_official_source_period_alignment_and_event_study() -> None:
    pack = build_canonical_evidence_pack(
        [_item("a" * 64, "publisher_rss", "TSMC expands capacity", "https://publisher.example/a")],
        registry=_registry(),
    )

    result = evaluate_quality_gate(
        target={"kind": "equity", "symbol": "2330.TW"},
        evidence_pack=pack,
        time_series={"status": "available"},
        fundamentals={"status": "available"},
        valuation={"status": "available"},
        market_drivers={"status": "available"},
        event_alignment={"status": "available"},
    )

    assert result["status"] == "professional_partial"
    assert "official_source_required" in result["blocking_reasons"]
    assert "valuation_period_alignment_required" in result["blocking_reasons"]
    assert "event_study_required" in result["blocking_reasons"]


def test_equity_gate_reports_non_positive_eps_as_valuation_blocker() -> None:
    pack = build_canonical_evidence_pack(
        [_item("a" * 64, "publisher_rss", "Tatung update", "https://publisher.example/a")],
        registry=_registry(),
    )

    result = evaluate_quality_gate(
        target={"kind": "equity", "symbol": "2371.TW"},
        evidence_pack=pack,
        time_series={"status": "available"},
        fundamentals={"status": "available", "eps": -5.2},
        valuation={"status": "insufficient_data", "missing_fields": ["positive_eps_for_pe_valuation"]},
        market_drivers={"status": "available"},
        event_alignment={"event_study_status": "available"},
    )

    assert "valuation_positive_eps_required" in result["blocking_reasons"]
    assert "valuation_period_alignment_required" not in result["blocking_reasons"]


def test_equity_gate_allows_explicit_dcf_only_fallback_when_peer_set_is_unavailable() -> None:
    pack = build_canonical_evidence_pack(
        [_item("a" * 64, "publisher_rss", "Issuer update", "https://publisher.example/a")],
        registry=_registry(),
    )
    result = evaluate_quality_gate(
        target={"kind": "equity", "symbol": "1303.TW"},
        evidence_pack=pack,
        time_series={"status": "available"},
        fundamentals={"status": "available"},
        valuation={
            "status": "insufficient_data",
            "period_alignment_status": "not_applicable",
            "dcf_only_fallback_eligible": True,
        },
        market_drivers={"status": "available"},
        event_alignment={"event_study_status": "available", "event_study_quality_status": "complete", "event_study_significance_status": "computed"},
    )
    assert "valuation_period_alignment_required" not in result["blocking_reasons"]


def test_equity_gate_requires_financial_filing_scope_not_identity_profile_only() -> None:
    items = [_item("a" * 64, "publisher_rss", "Issuer profile", "https://publisher.example/a")]
    items[0]["official_scope"] = "identity_profile"
    pack = build_canonical_evidence_pack(items, registry=_registry())
    result = evaluate_quality_gate(
        target={"kind": "equity", "symbol": "2308.TW"},
        evidence_pack=pack,
        time_series={"status": "available"},
        fundamentals={"status": "available"},
        valuation={"status": "available", "period_alignment_status": "aligned"},
        market_drivers={"status": "available"},
        event_alignment={"event_study_status": "available"},
    )
    assert "official_financial_source_required" in result["blocking_reasons"]


def test_crypto_gate_can_reach_ready_with_complete_provider_bundle() -> None:
    items = [
        _item("a" * 64, "source_a", "Bitcoin rises", "https://a.example/btc"),
        _item("b" * 64, "source_b", "Bitcoin falls", "https://b.example/btc"),
    ]
    registry = build_source_registry([
        {
            "source_id": "source_a",
            "publisher_id": "publisher_a",
            "source_tier": "direct_secondary",
            "independence_group": "publisher_a",
            "transport": "rss",
            "canonical_url": "https://a.example/rss",
        },
        {
            "source_id": "source_b",
            "publisher_id": "publisher_b",
            "source_tier": "direct_secondary",
            "independence_group": "publisher_b",
            "transport": "rss",
            "canonical_url": "https://b.example/rss",
        },
    ])
    pack = build_canonical_evidence_pack(items, registry=registry)

    result = evaluate_quality_gate(
        target={"kind": "crypto", "symbol": "BTC"},
        evidence_pack=pack,
        time_series={"status": "available"},
        fundamentals={"status": "unavailable"},
        valuation={"status": "not_applicable"},
        market_drivers={"status": "available"},
        event_alignment={"status": "insufficient_data"},
        provider_data={
            "volume": {"status": "available"},
            "etf_flows": {"status": "available"},
            "derivatives": {"status": "available"},
            "on_chain": {"status": "available"},
        },
    )

    assert result["status"] == "professional_ready"
    assert result["blocking_reasons"] == []


def test_requirement_coverage_blocks_l3_even_when_source_count_is_high() -> None:
    result = evaluate_quality_gate(
        target={"kind": "equity", "symbol": "2330.TW"},
        evidence_pack={
            "canonical_story_count": 12,
            "items": [
                {"source_tier": "regulatory", "official_scope": "financial_statement", "independence_group": "sec"},
                {"source_tier": "direct_primary", "independence_group": "issuer"},
                {"source_tier": "direct_secondary", "independence_group": "wire"},
            ],
        },
        time_series={"status": "available"},
        fundamentals={"status": "available"},
        valuation={"status": "available", "period_alignment_status": "aligned"},
        market_drivers={"status": "available"},
        event_alignment={
            "event_study_status": "available",
            "event_study_quality_status": "complete",
            "event_study_significance_status": "computed",
            "event_study_unique_event_date_count": 8,
            "unresolved_event_count": 0,
        },
        requirement_coverage={
            "summary": {"required_count": 9, "complete_count": 8, "coverage_ratio": 0.888889, "l3_ready": False},
            "requirements": [{"requirement_id": "esg.materiality_kpi", "status": "partial", "required": True}],
        },
    )

    assert result["status"] == "professional_partial"
    assert "requirement_coverage_required" in result["blocking_reasons"]
    assert next(check for check in result["checks"] if check["check_id"] == "requirement_coverage")["status"] == "fail"
