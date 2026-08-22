from __future__ import annotations

import hashlib

from finance_crawler_poc.contracts import build_item_id, validate_contract
from finance_crawler_poc.radar import build_topic_snapshot


def raw_item(
    *, source_id: str, layer: str, title: str, score: int | None = None
) -> dict[str, object]:
    content_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()
    item_id = build_item_id(source_id, f"https://example.com/{source_id}", content_hash)
    return {
        "schema_version": 1,
        "item_id": item_id,
        "source_id": source_id,
        "canonical_url": f"https://example.com/{source_id}",
        "title": title,
        "summary": "Synthetic for testing only",
        "content": title,
        "published_at": "2026-08-10T02:00:00Z",
        "collected_at": "2026-08-10T02:05:00Z",
        "transport": "rss" if layer == "news" else "json_api",
        "kind": "news" if layer == "news" else "community",
        "layer": layer,
        "content_sha256": content_hash,
        "rights": {
            "redistribution": "metadata_only",
            "retention_days": 1,
            "public_excerpt_chars": 0,
        },
        "engagement": {"score": score, "comments": None, "shares": None, "likes": None},
        "evidence": {
            "route": "synthetic_test",
            "status_code": 200,
            "final_url": f"https://example.com/{source_id}",
            "extraction_method": "synthetic_test",
        },
    }


def test_topic_radar_returns_only_ranked_top_three_with_traceable_evidence() -> None:
    items = [
        raw_item(source_id="news_a", layer="news", title="Fed interest rate and inflation"),
        raw_item(source_id="social_a", layer="social", title="Inflation and interest rate debate", score=50),
        raw_item(source_id="social_b", layer="social", title="Bitcoin crypto ETF rally", score=10),
        raw_item(source_id="news_b", layer="news", title="Bitcoin digital asset regulation"),
        raw_item(source_id="news_c", layer="news", title="Nvidia AI semiconductor earnings"),
        raw_item(source_id="social_c", layer="social", title="AI chip and Nvidia valuation", score=5),
        raw_item(source_id="news_d", layer="news", title="Tariff trade policy changes"),
    ]

    snapshot = build_topic_snapshot(
        items,
        run_id="run_20260810t020500z",
        snapshot_id="radar_20260810t020500z",
        as_of="2026-08-10T02:05:00Z",
        failed_sources=["failed_browser"],
    )

    assert len(snapshot["topics"]) == 3
    assert snapshot["partial"] is True
    topic_ids = [topic["topic_id"] for topic in snapshot["topics"]]
    assert "trade_policy" not in topic_ids
    assert set(topic_ids) == {"monetary_policy", "digital_assets", "ai_semiconductors"}
    assert set(snapshot["input_item_ids"]) == {item["item_id"] for item in items}
    for topic in snapshot["topics"]:
        assert set(topic["evidence_ids"]).issubset(snapshot["input_item_ids"])
    validate_contract("topic-snapshot", snapshot)


def test_divergence_requires_both_news_and_social_evidence() -> None:
    snapshot = build_topic_snapshot(
        [raw_item(source_id="social_only", layer="social", title="Bitcoin crypto rally")],
        run_id="run_20260810t020500z",
        snapshot_id="radar_20260810t020500z",
        as_of="2026-08-10T02:05:00Z",
        failed_sources=[],
    )

    assert snapshot["topics"][0]["divergence"] == {
        "direction": "insufficient_data",
        "magnitude": None,
    }
    assert snapshot["partial"] is True


def test_world_bank_name_does_not_create_a_false_banking_topic() -> None:
    snapshot = build_topic_snapshot(
        [raw_item(source_id="world_bank", layer="official", title="World Bank GDP growth outlook")],
        run_id="run_20260810t020500z",
        snapshot_id="radar_20260810t020500z",
        as_of="2026-08-10T02:05:00Z",
        failed_sources=[],
    )

    assert snapshot["topics"] == []


def test_central_bank_name_alone_does_not_create_a_policy_topic() -> None:
    snapshot = build_topic_snapshot(
        [
            raw_item(
                source_id="fed_admin",
                layer="official",
                title="Federal Reserve approves a bank holding company application",
            )
        ],
        run_id="run_20260810t020500z",
        snapshot_id="radar_20260810t020500z",
        as_of="2026-08-10T02:05:00Z",
        failed_sources=[],
    )

    assert snapshot["topics"] == []


def test_target_terms_break_ties_toward_the_requested_asset() -> None:
    items = [
        raw_item(source_id="generic_news", layer="news", title="Stock portfolio allocation outlook"),
        raw_item(source_id="asset_news", layer="news", title="Bitcoin ETF flows and crypto market risk"),
    ]

    snapshot = build_topic_snapshot(
        items,
        run_id="run_20260810t020500z",
        snapshot_id="radar_20260810t020500z",
        as_of="2026-08-10T02:05:00Z",
        failed_sources=[],
        target={"kind": "crypto", "symbol": "BTC", "name": "Bitcoin"},
        question="What are the current drivers and risks for BTC?",
    )

    assert snapshot["topics"][0]["topic_id"] == "digital_assets"


def test_target_scope_excludes_generic_finance_headlines_from_btc_radar() -> None:
    items = [
        raw_item(source_id="btc_news", layer="news", title="Bitcoin ETF flows and crypto rally"),
        raw_item(source_id="generic_news", layer="news", title="Stock earnings beat estimates"),
        raw_item(source_id="rates_news", layer="news", title="Interest rate outlook changes"),
    ]
    snapshot = build_topic_snapshot(
        items,
        run_id="run_20260810t020500z",
        snapshot_id="radar_20260810t020500z",
        as_of="2026-08-10T02:05:00Z",
        failed_sources=[],
        target={"kind": "crypto", "symbol": "BTC", "name": "Bitcoin"},
        question="What are the recent drivers and risks?",
    )
    assert snapshot["target_scope"]["input_item_count"] == 3
    assert snapshot["target_scope"]["relevant_item_count"] == 1
    assert snapshot["input_item_ids"] == [items[0]["item_id"]]


def test_target_scope_ignores_btc_in_raw_channel_payload() -> None:
    item = raw_item(
        source_id="generic_news",
        layer="news",
        title="Markets homepage capture",
    )
    item["content"] = "Navigation links: Bitcoin, crypto, Ethereum and digital assets"
    snapshot = build_topic_snapshot(
        [item],
        run_id="run_20260810t020500z",
        snapshot_id="radar_20260810t020500z",
        as_of="2026-08-10T02:05:00Z",
        failed_sources=[],
        target={"kind": "crypto", "symbol": "BTC", "name": "Bitcoin"},
        question="What are the recent drivers and risks?",
    )
    assert snapshot["target_scope"]["relevant_item_count"] == 0
    assert snapshot["topics"] == []


def test_target_scope_excludes_unpublished_browser_capture_pages() -> None:
    item = raw_item(
        source_id="generic_news",
        layer="news",
        title="generic_news_browser capture",
    )
    item["summary"] = "Markets navigation: Bitcoin crypto Ethereum digital assets"
    item["published_at"] = None
    snapshot = build_topic_snapshot(
        [item],
        run_id="run_20260810t020500z",
        snapshot_id="radar_20260810t020500z",
        as_of="2026-08-10T02:05:00Z",
        failed_sources=[],
        target={"kind": "crypto", "symbol": "BTC", "name": "Bitcoin"},
        question="What are the recent drivers and risks?",
    )
    assert snapshot["target_scope"]["relevant_item_count"] == 0


def test_target_scope_uses_news_headline_not_multi_story_feed_summary() -> None:
    item = raw_item(
        source_id="finance_feed",
        layer="news",
        title="Bond market outlook",
    )
    item["summary"] = "Bond market outlook. Bitcoin roars back in a separate feed story."
    item["published_at"] = "2026-08-10T02:05:00Z"
    snapshot = build_topic_snapshot(
        [item],
        run_id="run_20260810t020500z",
        snapshot_id="radar_20260810t020500z",
        as_of="2026-08-10T02:05:00Z",
        failed_sources=[],
        target={"kind": "crypto", "symbol": "BTC", "name": "Bitcoin"},
        question="What are the recent drivers and risks?",
    )
    assert snapshot["target_scope"]["relevant_item_count"] == 0
