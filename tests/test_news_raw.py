import json
from pathlib import Path

from finance_crawler_poc.news_probe import NewsBrandResult, NewsEndpointAttempt
from finance_crawler_poc.news_raw import write_news_raw_artifacts


def _attempt(*, endpoint_id: str, outcome: str, content: str) -> NewsEndpointAttempt:
    return NewsEndpointAttempt(
        endpoint_id=endpoint_id,
        transport="rss",
        url=f"https://example.com/{endpoint_id}",
        executor_id="github_actions_crawl4ai",
        outcome=outcome,
        status_code=200 if outcome == "success" else 403,
        elapsed_ms=12,
        content_chars=len(content),
        content_sha256="a" * 64 if content else "",
        preview=content[:20],
        error="" if outcome == "success" else "blocked",
        content=content,
    )


def test_raw_capture_writes_payloads_and_manifest_without_embedding_content(
    tmp_path: Path,
) -> None:
    results = [
        NewsBrandResult(
            brand_id="brand_one",
            name="Brand One",
            brand_class="finance_specialist",
            region="global",
            success=True,
            successful_endpoint_id="brand_one_rss",
            final_outcome="success",
            endpoint_attempts=(
                _attempt(
                    endpoint_id="brand_one_rss",
                    outcome="success",
                    content="<rss>finance payload</rss>",
                ),
                _attempt(
                    endpoint_id="brand_one_browser",
                    outcome="blocked",
                    content="challenge payload",
                ),
            ),
        )
    ]

    manifest_path = write_news_raw_artifacts(results, tmp_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["summary"] == {
        "brands": 1,
        "endpoint_attempts": 2,
        "payloads_written": 2,
        "payloads_missing": 0,
    }
    assert len(manifest["payloads"]) == 2
    assert (tmp_path / "news-raw" / "brand_one" / "brand_one_rss.raw").read_text(
        encoding="utf-8"
    ) == "<rss>finance payload</rss>"
    assert (tmp_path / "news-raw" / "brand_one" / "brand_one_browser.raw").read_text(
        encoding="utf-8"
    ) == "challenge payload"
    assert "finance payload" not in manifest_path.read_text(encoding="utf-8")


def test_raw_capture_records_empty_failed_attempt_without_fake_payload(
    tmp_path: Path,
) -> None:
    result = NewsBrandResult(
        brand_id="brand_one",
        name="Brand One",
        brand_class="finance_specialist",
        region="global",
        success=False,
        successful_endpoint_id="",
        final_outcome="timeout",
        endpoint_attempts=(
            _attempt(endpoint_id="brand_one_browser", outcome="timeout", content=""),
        ),
    )

    manifest_path = write_news_raw_artifacts([result], tmp_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["summary"]["payloads_written"] == 0
    assert manifest["summary"]["payloads_missing"] == 1
    assert manifest["payloads"][0]["payload_path"] is None
