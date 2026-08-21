from __future__ import annotations

import json
from pathlib import Path

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
