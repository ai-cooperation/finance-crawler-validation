from __future__ import annotations

import json
import html

import httpx

from finance_crawler_poc.target_retrieval import fetch_hackernews_target_community, fetch_yahoo_target_news


TARGET = {
    "kind": "equity",
    "symbol": "2330.TW",
    "name": "Taiwan Semiconductor Manufacturing Company Limited",
    "aliases": ["TSMC", "2330", "Taiwan Semiconductor"],
}


def test_target_retrieval_merges_rss_and_search_routes_and_filters_exact_identity(monkeypatch) -> None:
    rss = """<?xml version="1.0"?><rss><channel>
      <item><title>TSMC sees AI chip demand rise</title><link>https://news.example/tsmc-1</link><pubDate>Fri, 21 Aug 2026 10:00:00 +0000</pubDate><description>TSMC update</description></item>
      <item><title>Generic stock market update</title><link>https://news.example/generic</link><pubDate>Fri, 21 Aug 2026 09:00:00 +0000</pubDate></item>
    </channel></rss>"""
    search = {
        "news": [
            {"title": "Taiwan Semiconductor reports strong demand", "link": "https://news.example/tsmc-2", "publisher": "Example Wire", "providerPublishTime": 1787302800},
            {"title": "Stock earnings calendar", "link": "https://news.example/generic-2", "publisher": "Example Wire", "providerPublishTime": 1787302800},
        ]
    }

    def fake_get(url: str, **kwargs):
        payload = (
            rss.replace("tsmc-1", "tsmc-google")
            if "news.google.com" in url
            else rss
            if "feeds.finance.yahoo.com" in url
            else json.dumps(search)
        )
        return httpx.Response(
            200,
            content=payload.encode("utf-8"),
            request=httpx.Request("GET", url),
            headers={"content-type": "application/rss+xml" if "feeds." in url or "news.google.com" in url else "application/json"},
        )

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_yahoo_target_news(TARGET, max_items=10)

    assert result["status"] == "available"
    assert len(result["items"]) == 3
    assert {item["source_id"] for item in result["items"]} == {
        "yahoo_finance_target_rss",
        "yahoo_finance_target_search",
        "google_news_target_rss",
    }
    assert all("tsmc" in item["title"].casefold() or "taiwan semiconductor" in item["title"].casefold() for item in result["items"])
    assert result["target_scope"]["relevant_item_count"] == 3


def test_google_news_rss_preserves_declared_publisher_for_independence_resolution(monkeypatch) -> None:
    rss = """<rss><channel>
      <item><title>TSMC expands advanced packaging capacity</title>
        <link>https://news.google.com/rss/articles/abc</link>
        <pubDate>Fri, 21 Aug 2026 10:00:00 +0000</pubDate>
        <description>TSMC update</description>
        <source url="https://example.com">Example Wire</source>
      </item>
    </channel></rss>"""
    search = {"news": []}

    def fake_get(url: str, **kwargs):
        payload = rss if "news.google.com" in url else json.dumps(search)
        return httpx.Response(200, content=payload.encode(), request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_yahoo_target_news(TARGET)

    google_item = next(item for item in result["items"] if item["source_id"] == "google_news_target_rss")
    assert google_item["evidence"]["publisher_verified"] is True
    assert google_item["evidence"]["publisher_id"] == "example_wire"
    assert google_item["evidence"]["publisher_url"] == "https://example.com"


def test_target_retrieval_returns_explicit_insufficient_data_when_routes_have_no_target_headline(monkeypatch) -> None:
    rss = "<rss><channel><item><title>Generic stock market update</title><link>https://news.example/generic</link></item></channel></rss>"
    search = {"news": [{"title": "Generic earnings calendar", "link": "https://news.example/generic-2"}]}

    def fake_get(url: str, **kwargs):
        payload = rss if "feeds.finance.yahoo.com" in url or "news.google.com" in url else json.dumps(search)
        return httpx.Response(200, content=payload.encode(), request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_yahoo_target_news(TARGET)

    assert result["status"] == "insufficient_data"
    assert result["items"] == []
    assert result["missing_reason"] == "no_researchable_target_headlines_from_public_routes"


def test_taiwan_target_news_routes_local_first_then_asia_and_global(monkeypatch) -> None:
    target = {
        "kind": "equity",
        "symbol": "2357.TW",
        "name": "ASUSTeK Computer Inc.",
        "aliases": ["華碩", "ASUS", "ASUSTeK", "2357"],
        "market": "TW",
        "primary_region": "TW",
        "languages": ["zh-TW", "en"],
        "region_priority": ["TW", "Asia", "global"],
        "local_names": ["華碩"],
        "international_names": ["ASUSTeK Computer Inc.", "ASUS"],
    }

    def fake_get(url: str, **kwargs):
        if "query1.finance.yahoo.com" in url:
            payload = {"news": []}
            content_type = "application/json"
        else:
            title = "華碩公布最新營運展望" if "ceid=TW" in url or "region=TW" in url else "ASUS publishes operating outlook"
            payload = f"<rss><channel><item><title>{title}</title><link>https://news.example/{abs(hash(url))}</link><pubDate>Fri, 21 Aug 2026 10:00:00 +0000</pubDate><source url=\"https://publisher.example\">Publisher</source></item></channel></rss>"
            content_type = "application/rss+xml"
        body = json.dumps(payload).encode() if isinstance(payload, dict) else payload.encode()
        return httpx.Response(200, content=body, request=httpx.Request("GET", url), headers={"content-type": content_type})

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_yahoo_target_news(target, source_id_prefix="asus", max_items=20)

    regional_attempts = [item for item in result["attempts"] if item.get("region_scope")]
    assert [item["region_scope"] for item in regional_attempts[:3]] == ["TW", "TW", "Asia"]
    assert {item["region_scope"] for item in regional_attempts} >= {"TW", "Asia", "global"}
    assert all(item["route_status"] == "success" for item in regional_attempts)
    assert all(item["query_status"] in {"success_with_hits", "success_no_hits"} for item in regional_attempts)
    assert result["geo_coverage"]["status"] == "complete"
    assert result["geo_coverage"]["local_relevant_item_count"] >= 1
    assert result["items"][0]["evidence"]["region_scope"] == "TW"


def test_target_retrieval_filters_ownership_filler_but_keeps_raw_item_count(monkeypatch) -> None:
    rss = """<rss><channel>
      <item><title>Foster Acquires New Position in Taiwan Semiconductor</title><link>https://news.example/ownership</link><pubDate>Fri, 21 Aug 2026 10:00:00 +0000</pubDate></item>
      <item><title>TSMC expands advanced packaging capacity</title><link>https://news.example/research</link><pubDate>Fri, 21 Aug 2026 09:00:00 +0000</pubDate></item>
    </channel></rss>"""
    search = {"news": []}

    def fake_get(url: str, **kwargs):
        payload = rss if "feeds.finance.yahoo.com" in url or "news.google.com" in url else json.dumps(search)
        return httpx.Response(200, content=payload.encode(), request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_yahoo_target_news(TARGET)

    assert result["noise_item_count"] == 1
    assert len(result["all_items"]) == 2
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "TSMC expands advanced packaging capacity"


def test_target_retrieval_filters_opinion_and_insider_headlines_but_keeps_raw_items(monkeypatch) -> None:
    titles = [
        "174K Reasons To Be Bullish On Taiwan Semiconductor Stock",
        "Is It Worth Investing in TSMC Based on Wall Street's Bullish Views?",
        "Ninepoint Partners LP Invests $6.25 Million in Taiwan Semiconductor",
        "TSMC VP and spouse buy shares, report ESPP stake",
        "Lipen Yuan Purchases 1,000 Shares of Taiwan Semiconductor Stock",
        "Taiwan Semiconductor vs. ASML: Which Titan Is the Better Buy Today?",
        "Taiwan Semiconductor Manufacturing Looks Built for Long-Term Growth",
        "Prediction: Taiwan Semiconductor Stock Will Reach a Fresh High Before 2026 Ends",
        "History of TSMC & its stock: Company timeline, facts & milestones",
        "TSMC expands advanced packaging capacity",
    ]
    rss = "<rss><channel>" + "".join(
        f"<item><title>{html.escape(title)}</title><link>https://news.example/{index}</link><pubDate>Fri, 21 Aug 2026 10:00:00 +0000</pubDate></item>"
        for index, title in enumerate(titles)
    ) + "</channel></rss>"

    def fake_get(url: str, **kwargs):
        return httpx.Response(200, content=rss.encode(), request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_yahoo_target_news(TARGET)

    assert result["noise_item_count"] == 9
    assert len(result["all_items"]) == 10
    assert [item["title"] for item in result["items"]] == ["TSMC expands advanced packaging capacity"]


def test_hackernews_target_community_preserves_original_discussion_and_engagement(monkeypatch) -> None:
    payload = {
        "hits": [
            {
                "objectID": "49386030",
                "title": "TSMC Completes New Advanced Packaging Facility",
                "story_text": "Discussion: readers compare capacity, demand and execution risks.",
                "url": "https://example.com/tsmc-story",
                "created_at": "2026-08-21T10:12:56Z",
                "points": 42,
                "num_comments": 7,
                "author": "reader",
            },
            {
                "objectID": "49386031",
                "title": "Generic market headline",
                "story_text": "No target match",
                "url": "https://example.com/generic",
                "created_at": "2026-08-21T10:00:00Z",
            },
        ]
    }
    detail_payload = {
        "id": "49386030",
        "title": "TSMC Completes New Advanced Packaging Facility",
        "children": [{"id": "9001", "author": "commenter", "text": "The capacity ramp depends on yield and customer demand."}],
    }

    def fake_get(url: str, **kwargs):
        body = detail_payload if "/items/49386030" in url else payload
        return httpx.Response(200, content=json.dumps(body).encode(), request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_hackernews_target_community(TARGET, max_items=10)

    assert result["status"] == "available"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["kind"] == "social"
    assert item["layer"] == "social"
    assert item["canonical_url"] == "https://news.ycombinator.com/item?id=49386030"
    assert item["evidence"]["outbound_url"] == "https://example.com/tsmc-story"
    assert item["evidence"]["author"] == "reader"
    assert item["engagement"]["score"] == 42
    assert item["engagement"]["comments"] == 7
    assert "capacity" in item["content"]
    assert item["evidence"]["comments"][0]["author"] == "commenter"
    assert "yield" in item["evidence"]["comments"][0]["text"]


def test_hackernews_target_community_tries_non_local_identity_terms(monkeypatch) -> None:
    payload = {
        "hits": [
            {
                "objectID": "49386040",
                "title": "Taiwan Semiconductor expands advanced packaging capacity",
                "story_text": "Discussion: readers compare capacity and demand.",
                "url": "https://example.com/tsmc-story",
                "created_at": "2026-08-21T10:12:56Z",
                "points": 10,
                "num_comments": 2,
                "author": "reader",
            }
        ]
    }

    def fake_get(url: str, **kwargs):
        if "query=%E5%8F%B0%E7%A9%8D%E9%9B%BB" in url:
            body = {"hits": []}
        elif "query=TSMC" in url:
            body = payload
        elif "/items/49386040" in url:
            body = {"children": []}
        else:
            body = {"hits": []}
        return httpx.Response(200, content=json.dumps(body).encode(), request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_hackernews_target_community(
        {"symbol": "2330.TW", "name": "Taiwan Semiconductor", "aliases": ["台積電", "TSMC", "2330"]},
        max_items=10,
    )

    assert result["status"] == "available"
    assert result["items"][0]["title"].startswith("Taiwan Semiconductor")
    assert result["target_scope"]["query_count"] >= 2
    assert any(attempt.get("query") == "TSMC" and attempt.get("item_count") == 1 for attempt in result["attempts"])


def test_hackernews_target_community_ignores_numeric_ticker_collisions(monkeypatch) -> None:
    payloads = {
        "Taiwan Cement": {
            "hits": [{
                "objectID": "49386050",
                "title": "Taiwan Cement promotes garbage treatment project",
                "story_text": "Discussion of the company's waste treatment project.",
                "created_at": "2026-08-21T10:12:56Z",
            }]
        },
        "1101": {
            "hits": [{
                "objectID": "49386051",
                "title": "IC 1101 is the largest galaxy identified, so far, in the universe",
                "story_text": "Astronomy discussion unrelated to Taiwan Cement.",
                "created_at": "2026-08-21T10:12:56Z",
            }]
        },
    }

    def fake_get(url: str, **kwargs):
        if "/items/49386050" in url:
            body = {"children": []}
        else:
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(url).query).get("query", [""])[0]
            body = payloads.get(query, {"hits": []})
        return httpx.Response(200, content=json.dumps(body).encode(), request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_hackernews_target_community(
        {"symbol": "1101.TW", "name": "Taiwan Cement", "aliases": ["台泥", "Taiwan Cement", "1101"]},
        max_items=10,
    )

    assert [item["title"] for item in result["items"]] == ["Taiwan Cement promotes garbage treatment project"]
    assert "1101" in result["target_scope"]["skipped_ambiguous_terms"]
    assert "1101" not in result["target_scope"]["queries"]


def test_hackernews_target_community_records_blocked_public_boundaries(monkeypatch) -> None:
    def fake_get(url: str, **kwargs):
        if "reddit.com" in url or "stocktwits.com" in url:
            return httpx.Response(403, content=b"forbidden", request=httpx.Request("GET", url))
        if "/items/" in url:
            return httpx.Response(200, content=b'{"children": []}', request=httpx.Request("GET", url))
        return httpx.Response(200, content=b'{"hits": []}', request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_hackernews_target_community(TARGET, max_items=10)

    assert result["status"] == "insufficient_data"
    assert result["missing_reason"] == "community_routes_blocked_or_no_target_discussions"
    blocked = {item["source_id"]: item for item in result["attempts"] if item.get("status") == "blocked"}
    assert blocked["reddit_public_search"]["status_code"] == 403
    assert blocked["stocktwits_public_symbol_stream"]["status_code"] == 403


def test_hackernews_target_community_rejects_incidental_body_mention(monkeypatch) -> None:
    payload = {
        "hits": [{
            "objectID": "49386060",
            "title": "Ask HN: Is this Dell laptop good for beginner programming?",
            "story_text": "The author compares Dell and Lenovo; ASUS is mentioned once as another option.",
            "created_at": "2026-08-21T10:12:56Z",
        }]
    }

    def fake_get(url: str, **kwargs):
        if "/items/" in url:
            return httpx.Response(200, content=b'{"children": []}', request=httpx.Request("GET", url))
        if "reddit.com" in url or "stocktwits.com" in url:
            return httpx.Response(403, content=b"forbidden", request=httpx.Request("GET", url))
        return httpx.Response(200, content=json.dumps(payload).encode(), request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.target_retrieval.httpx.get", fake_get)

    result = fetch_hackernews_target_community(
        {"symbol": "2357.TW", "name": "ASUSTeK Computer Inc.", "aliases": ["ASUS"]},
        max_items=10,
    )

    assert result["items"] == []
    assert result["status"] == "insufficient_data"
