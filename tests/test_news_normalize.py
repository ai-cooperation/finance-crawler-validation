from __future__ import annotations

import json
from pathlib import Path

from finance_crawler_poc.contracts import build_item_id
from finance_crawler_poc.news_normalize import normalize_news_capture


def _write_capture(root: Path, *, transport: str, content: str) -> Path:
    (root / "news-raw" / "acme").mkdir(parents=True)
    payload = root / "news-raw" / "acme" / "acme_endpoint.raw"
    payload.write_text(content, encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "capture_type": "news_endpoint_raw_payload",
        "generated_at": "2026-08-21T00:00:00Z",
        "summary": {"brands": 1, "endpoint_attempts": 1, "payloads_written": 1, "payloads_missing": 0},
        "payloads": [{
            "brand_id": "acme",
            "brand_class": "finance_specialist",
            "region": "US",
            "endpoint_id": "acme_endpoint",
            "transport": transport,
            "url": "https://example.com/feed",
            "final_url": "https://example.com/feed",
            "executor_id": "github_actions_crawl4ai",
            "outcome": "success",
            "status_code": 200,
            "content_chars": len(content),
            "content_sha256": "",
            "reported_content_sha256": "",
            "content_type": "application/rss+xml",
            "payload_path": "news-raw/acme/acme_endpoint.raw",
            "error": "",
        }],
    }
    manifest["payloads"][0]["content_sha256"] = __import__("hashlib").sha256(content.encode()).hexdigest()
    manifest["payloads"][0]["reported_content_sha256"] = manifest["payloads"][0]["content_sha256"]
    path = root / "news-raw-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_normalize_rss_payload_produces_auditable_item_and_full_catalog_counts(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="rss",
        content="""<rss><channel><item><title>Markets rise on earnings</title><link>https://example.com/story</link><description>Profit outlook improves</description><pubDate>Wed, 21 Aug 2026 00:00:00 GMT</pubDate></item></channel></rss>""",
    )

    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )

    assert result["collection_scope"] == "full_catalog"
    assert result["collection_source_group_count"] == 1
    assert result["endpoint_attempt_count"] == 1
    assert result["normalized_item_count"] == 1
    assert result["checkpoints"][0]["source_id"] == "acme"
    item = result["items"][0]
    assert item["source_id"] == "acme"
    assert item["canonical_url"] == "https://example.com/story"
    assert item["title"] == "Markets rise on earnings"
    assert item["evidence"]["route"] == "acme_endpoint"


def test_normalize_rss_expands_every_feed_item_and_uses_contract_item_id(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="rss",
        content="""<rss><channel>
          <item><title>First story</title><link>https://example.com/first</link><description>One</description><pubDate>Wed, 21 Aug 2026 00:00:00 GMT</pubDate></item>
          <item><title>Second story</title><link>https://example.com/second</link><description>Two</description><pubDate>Wed, 21 Aug 2026 01:00:00 GMT</pubDate></item>
        </channel></rss>""",
    )

    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )

    assert result["normalized_item_count"] == 2
    assert result["raw_entry_count"] == 2
    assert [item["title"] for item in result["items"]] == ["First story", "Second story"]
    for item in result["items"]:
        assert item["item_id"] == build_item_id(
            item["source_id"], item["canonical_url"], item["content_sha256"]
        )
    assert result["items"][0]["evidence"]["route"] == "acme_endpoint#item_0"
    assert result["items"][1]["evidence"]["route"] == "acme_endpoint#item_1"


def test_normalize_json_expands_article_array(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="json_api",
        content=json.dumps({
            "articles": [
                {"title": "API first", "url": "https://example.com/api-first", "published_at": "2026-08-21T00:00:00Z"},
                {"title": "API second", "url": "https://example.com/api-second", "published_at": "2026-08-21T01:00:00Z"},
            ]
        }),
    )

    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )

    assert result["normalized_item_count"] == 2
    assert result["raw_entry_count"] == 2
    assert {item["canonical_url"] for item in result["items"]} == {
        "https://example.com/api-first",
        "https://example.com/api-second",
    }


def test_normalize_json_expands_nested_article_array(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="json_api",
        content=json.dumps({
            "data": {
                "articles": [
                    {"headline": "Nested first", "url": "https://example.com/nested-first"},
                    {"headline": "Nested second", "url": "https://example.com/nested-second"},
                ]
            }
        }),
    )

    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )

    assert result["normalized_item_count"] == 2
    assert [item["title"] for item in result["items"]] == ["Nested first", "Nested second"]


def test_normalize_html_expands_article_cards_in_browser_capture(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="browser",
        content="""<html><body>
          <article><h2><a href="/one">First headline</a></h2><p>First summary</p><time datetime="2026-08-21T00:00:00Z">Today</time></article>
          <article><h2><a href="/two">Second headline</a></h2><p>Second summary</p><time datetime="2026-08-21T01:00:00Z">Today</time></article>
        </body></html>""",
    )

    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )

    assert result["normalized_item_count"] == 2
    assert [item["title"] for item in result["items"]] == ["First headline", "Second headline"]
    assert [item["canonical_url"] for item in result["items"]] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert [item["published_at"] for item in result["items"]] == [
        "2026-08-21T00:00:00Z",
        "2026-08-21T01:00:00Z",
    ]


def test_normalize_reports_explicit_item_cap_without_losing_raw_count(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="rss",
        content="""<rss><channel>
          <item><title>First story</title><link>https://example.com/first</link></item>
          <item><title>Second story</title><link>https://example.com/second</link></item>
        </channel></rss>""",
    )

    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
        max_items=1,
    )

    assert result["raw_entry_count"] == 2
    assert result["normalized_item_count"] == 1
    assert result["truncated_item_count"] == 1
    assert result["items_truncated"] is True


def test_normalize_skips_missing_payload_but_keeps_failed_endpoint_observation(tmp_path: Path) -> None:
    manifest = tmp_path / "news-raw-manifest.json"
    manifest.write_text(json.dumps({
        "payloads": [{
            "brand_id": "broken",
            "brand_class": "general_finance_desk",
            "region": "global",
            "endpoint_id": "broken_browser",
            "transport": "browser",
            "url": "https://example.com",
            "final_url": "",
            "executor_id": "github_actions_crawl4ai",
            "outcome": "timeout",
            "status_code": None,
            "payload_path": None,
            "error": "timeout",
        }],
    }), encoding="utf-8")

    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )

    assert result["items"] == []
    assert result["checkpoints"] == [{
        "source_id": "broken",
        "status": "failed",
        "last_successful_crawl": None,
        "last_article_date": None,
        "cursor": None,
    }]
    assert result["failed_endpoint_count"] == 1


def test_normalize_marks_brand_partial_when_one_endpoint_succeeds_and_one_fails(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="rss",
        content="<rss><channel><item><title>One story</title><link>https://example.com/one</link></item></channel></rss>",
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    failed = dict(payload["payloads"][0])
    failed.update({"endpoint_id": "acme_browser", "transport": "browser", "outcome": "timeout", "payload_path": None, "status_code": None})
    payload["payloads"].append(failed)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )

    assert result["checkpoints"][0]["status"] == "partial"
    assert result["successful_source_group_count"] == 1
    assert result["fully_successful_source_group_count"] == 0
    assert result["failed_endpoint_count"] == 1


def test_normalize_strips_markup_punctuation_from_html_url(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="static_html",
        content='<html><title>Morningstar</title><body>[logo](https://www.morningstar.com/assets/img/logo.svg)]</body></html>',
    )
    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )
    assert result["items"][0]["canonical_url"] == "https://www.morningstar.com/assets/img/logo.svg"


def test_normalize_strips_markdown_link_suffix_from_html_url(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="static_html",
        content='<html><body>![logo](https://www.morningstar.com/assets/img/morningstar.svg)](https://www.morningstar.com/)</body></html>',
    )
    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )
    assert result["items"][0]["canonical_url"] == "https://www.morningstar.com/assets/img/morningstar.svg"


def test_normalize_strips_bracket_suffix_from_extracted_url(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="static_html",
        content='<html><body>https://www.thinkadvisor.com/tax-facts/)[</body></html>',
    )
    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )
    assert result["items"][0]["canonical_url"] == "https://www.thinkadvisor.com/tax-facts/"


def test_normalize_strips_adjacent_markdown_image_suffix(tmp_path: Path) -> None:
    manifest = _write_capture(
        tmp_path,
        transport="static_html",
        content='<html><body>https://tr-cdn.tipranks.com/static/v2/static/images/logo.svg)![tipranks</body></html>',
    )
    result = normalize_news_capture(
        manifest,
        workflow_run_id="123",
        commit_sha="a" * 40,
        collected_at="2026-08-21T01:00:00Z",
    )
    assert result["items"][0]["canonical_url"] == "https://tr-cdn.tipranks.com/static/v2/static/images/logo.svg"
