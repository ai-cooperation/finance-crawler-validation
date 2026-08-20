from __future__ import annotations

import hashlib

import pytest

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.openbb_alignment import (
    build_market_snapshot,
    build_topic_market_alignment,
)


def market_item(
    item_id: str,
    symbol: str,
    name: str,
    price: float,
    change: float,
) -> dict[str, object]:
    content = (
        '{"current_price": '
        f"{price}, \"id\": \"{name.lower()}\", \"last_updated\": "
        '"2026-08-20T03:55:20Z", '
        f'"market_cap": 1000000, "name": "{name}", '
        f'"price_change_percentage_24h": {change}, "symbol": "{symbol.lower()}"}}'
    )
    return {
        "item_id": item_id,
        "source_id": "coingecko_markets_api",
        "kind": "market_data",
        "content": content,
        "published_at": "2026-08-20T03:55:20Z",
        "collected_at": "2026-08-20T03:58:48Z",
    }


def topic_snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_id": "radar_20260820t035848z",
        "run_id": "run_20260820t035848z",
        "as_of": "2026-08-20T03:58:48Z",
        "partial": True,
        "failed_sources": ["bogleheads_investing_browser"],
        "input_item_ids": ["a" * 64, "b" * 64],
        "topics": [
            {
                "topic_id": "digital_assets",
                "label": "Digital assets",
                "score": 6,
                "item_count": 2,
                "source_count": 2,
                "news_count": 1,
                "social_count": 1,
                "evidence_ids": ["a" * 64],
                "divergence": {"direction": "insufficient_data", "magnitude": None},
            },
            {
                "topic_id": "ai_semiconductors",
                "label": "AI and semiconductors",
                "score": 5,
                "item_count": 1,
                "source_count": 1,
                "news_count": 1,
                "social_count": 0,
                "evidence_ids": ["b" * 64],
                "divergence": {"direction": "insufficient_data", "magnitude": None},
            },
        ],
    }


def test_market_snapshot_is_openbb_compatible_and_traceable() -> None:
    items = [
        market_item("b" * 64, "eth", "Ethereum", 1900.0, -2.0),
        market_item("a" * 64, "btc", "Bitcoin", 64000.0, 4.0),
    ]

    snapshot = build_market_snapshot(
        items,
        snapshot_id="market_20260820t035900z",
        as_of="2026-08-20T03:59:00Z",
        provider="coingecko",
    )

    validate_contract("market-snapshot", snapshot)
    assert [instrument["symbol"] for instrument in snapshot["instruments"]] == ["BTC", "ETH"]
    assert snapshot["instruments"][0]["source_item_ids"] == ["a" * 64]
    assert snapshot["instruments"][1]["change_24h_pct"] == -2.0


def test_market_snapshot_filters_to_requested_target_symbol() -> None:
    snapshot = build_market_snapshot(
        [
            market_item("b" * 64, "eth", "Ethereum", 1900.0, -2.0),
            market_item("a" * 64, "btc", "Bitcoin", 64000.0, 4.0),
        ],
        snapshot_id="market_20260820t035900z",
        as_of="2026-08-20T03:59:00Z",
        provider="coingecko",
        target={"kind": "crypto", "symbol": "BTC"},
    )

    assert [instrument["symbol"] for instrument in snapshot["instruments"]] == ["BTC"]


def test_market_snapshot_fails_closed_when_requested_target_is_not_observed() -> None:
    with pytest.raises(ValueError, match="target market instrument not found"):
        build_market_snapshot(
            [market_item("a" * 64, "btc", "Bitcoin", 64000.0, 4.0)],
            snapshot_id="market_20260820t035900z",
            as_of="2026-08-20T03:59:00Z",
            provider="coingecko",
            target={"kind": "crypto", "symbol": "SOL"},
        )


def test_market_snapshot_rejects_malformed_market_payload() -> None:
    malformed = market_item("a" * 64, "btc", "Bitcoin", 64000.0, 4.0)
    malformed["content"] = "not-json"

    with pytest.raises(ValueError, match="invalid market item"):
        build_market_snapshot(
            [malformed],
            snapshot_id="market_20260820t035900z",
            as_of="2026-08-20T03:59:00Z",
            provider="coingecko",
        )


def test_topic_market_alignment_preserves_evidence_and_exposes_coverage() -> None:
    market = build_market_snapshot(
        [
            market_item("a" * 64, "btc", "Bitcoin", 64000.0, 4.0),
            market_item("b" * 64, "eth", "Ethereum", 1900.0, -2.0),
        ],
        snapshot_id="market_20260820t035900z",
        as_of="2026-08-20T03:59:00Z",
        provider="coingecko",
    )
    alignment = build_topic_market_alignment(
        topic_snapshot(),
        market,
        alignment_id="align_20260820t035900z",
        generated_at="2026-08-20T03:59:00Z",
    )

    validate_contract("market-topic-alignment", alignment)
    assert alignment["partial"] is True
    assert alignment["coverage_ratio"] == 0.5
    by_topic = {topic["topic_id"]: topic for topic in alignment["topics"]}
    assert by_topic["digital_assets"]["market_direction"] == "mixed"
    assert by_topic["digital_assets"]["symbols"] == ["BTC", "ETH"]
    assert by_topic["ai_semiconductors"]["market_direction"] == "not_covered"
    assert set(by_topic["digital_assets"]["evidence_ids"]) == {"a" * 64, "b" * 64}


def test_market_snapshot_requires_at_least_one_valid_item() -> None:
    with pytest.raises(ValueError, match="no market data items"):
        build_market_snapshot(
            [],
            snapshot_id="market_20260820t035900z",
            as_of="2026-08-20T03:59:00Z",
            provider="coingecko",
        )
