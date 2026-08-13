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


def test_all_versioned_contracts_are_loadable() -> None:
    assert CONTRACT_NAMES == frozenset(
        {
            "audit-event",
            "ingest-envelope",
            "market-snapshot",
            "raw-item",
            "research-report",
            "source-record",
            "status-response",
            "topic-snapshot",
        }
    )
    for name in CONTRACT_NAMES:
        schema = load_contract(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/{name}.schema.json")


@pytest.mark.parametrize(
    ("contract", "payload"),
    [
        ("source-record", source_record()),
        ("raw-item", raw_item()),
        ("topic-snapshot", topic_snapshot()),
        ("status-response", status_response()),
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
