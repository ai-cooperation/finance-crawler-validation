from __future__ import annotations

import json

import pytest

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.models import FetchResponse
from finance_crawler_poc.radar_collect import _source_failure_result, extract_source_items
from finance_crawler_poc.radar_manifest import RadarSource


COLLECTED_AT = "2026-08-10T02:05:00Z"


def source_for(profile: str) -> RadarSource:
    transport = "browser" if profile == "browser_document" else (
        "rss" if profile == "rss" else "json_api"
    )
    return RadarSource(
        schema_version=1,
        source_id=f"synthetic_{profile}",
        name=f"Synthetic for testing only {profile}",
        kind="community" if profile in {"browser_document", "stackexchange", "hn_algolia"} else "official_data",
        layer="social" if profile in {"browser_document", "stackexchange", "hn_algolia"} else "official",
        transport=transport,
        canonical_url="https://example.com/feed",
        freshness_sla_minutes=60,
        rights={
            "redistribution": "metadata_only",
            "retention_days": 1,
            "public_excerpt_chars": 0,
        },
        extractor=profile,
        max_items=2,
        timeout_seconds=5,
    )


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Synthetic for testing only</title>
<item><title>Fed inflation outlook — Synthetic for testing only</title>
<link>https://example.com/articles/fed-outlook</link>
<description>Synthetic for testing only: interest rate discussion.</description>
<pubDate>Sun, 10 Aug 2026 02:00:00 GMT</pubDate></item>
</channel></rss>"""


PROFILE_PAYLOADS = {
    "rss": RSS,
    "hn_algolia": json.dumps({
        "hits": [{
            "objectID": "1",
            "title": "Synthetic for testing only: finance agents",
            "url": "https://example.com/hn-finance",
            "created_at": "2026-08-10T02:00:00Z",
            "points": 7,
            "num_comments": 3,
        }]
    }),
    "stackexchange": json.dumps({
        "items": [{
            "question_id": 2,
            "title": "Synthetic for testing only: portfolio allocation",
            "link": "https://example.com/questions/2",
            "creation_date": 1786327200,
            "score": 5,
            "answer_count": 2,
        }]
    }),
    "github_issues": json.dumps([{
        "number": 3,
        "title": "Synthetic for testing only: market data connector",
        "html_url": "https://example.com/issues/3",
        "body": "Synthetic for testing only: connector details",
        "created_at": "2026-08-10T02:00:00Z",
        "comments": 4,
    }]),
    "coingecko_markets": json.dumps([{
        "id": "bitcoin",
        "symbol": "btc",
        "name": "Bitcoin",
        "current_price": 1,
        "market_cap": 2,
        "price_change_percentage_24h": 3,
        "last_updated": "2026-08-10T02:00:00Z",
    }]),
    "world_bank": json.dumps([
        {"page": 1},
        [{
            "indicator": {"id": "NY.GDP", "value": "GDP growth"},
            "country": {"id": "1W", "value": "World"},
            "countryiso3code": "WLD",
            "date": "2025",
            "value": 2.5,
        }],
    ]),
    "browser_document": "Synthetic for testing only: markets inflation and portfolio",
}


@pytest.mark.parametrize("profile", sorted(PROFILE_PAYLOADS))
def test_extractors_normalize_synthetic_payloads(profile: str) -> None:
    source = source_for(profile)
    response = FetchResponse(
        status_code=200,
        content=PROFILE_PAYLOADS[profile],
        route="synthetic_test",
        final_url=source.canonical_url,
    )

    items = extract_source_items(source, response, COLLECTED_AT)

    assert len(items) == 1
    assert "Synthetic for testing only" in (items[0]["title"] + items[0]["content"])
    assert items[0]["source_id"] == source.source_id
    assert items[0]["evidence"]["extraction_method"] == profile
    validate_contract("raw-item", items[0])


def test_extractor_rejects_unsuccessful_delivery() -> None:
    source = source_for("rss")

    with pytest.raises(ValueError, match="HTTP 403"):
        extract_source_items(
            source,
            FetchResponse(status_code=403, content="blocked", route="direct"),
            COLLECTED_AT,
        )


def test_failure_result_preserves_delivery_evidence() -> None:
    source = source_for("browser_document")
    response = FetchResponse(
        status_code=403,
        content="challenge",
        error="anti-bot challenge",
        route="crawl4ai",
        final_url=source.canonical_url,
    )

    result = _source_failure_result(source, response, ValueError(response.error))

    assert result["status_code"] == 403
    assert result["route"] == "crawl4ai"
    assert "anti-bot challenge" in result["error"]
