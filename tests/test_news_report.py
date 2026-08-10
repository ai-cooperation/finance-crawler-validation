import json
from pathlib import Path

from finance_crawler_poc.news_probe import NewsBrandResult, NewsEndpointAttempt
from finance_crawler_poc.news_report import write_news_reports


def attempt(endpoint_id: str, transport: str, outcome: str) -> NewsEndpointAttempt:
    return NewsEndpointAttempt(
        endpoint_id=endpoint_id,
        transport=transport,
        url=f"https://example.com/{endpoint_id}",
        executor_id="github_actions_crawl4ai",
        outcome=outcome,
        status_code=200 if outcome == "success" else 403,
        elapsed_ms=10,
        content_chars=500 if outcome == "success" else 0,
        content_sha256="a" * 64 if outcome == "success" else "",
        preview="news" if outcome == "success" else "",
        error="" if outcome == "success" else "blocked",
    )


def test_news_report_uses_brand_denominator_and_keeps_endpoint_attempts(tmp_path: Path) -> None:
    results = [
        NewsBrandResult(
            brand_id="brand_one",
            name="Brand One",
            brand_class="finance_specialist",
            region="global",
            success=True,
            successful_endpoint_id="one_browser",
            final_outcome="success",
            endpoint_attempts=(
                attempt("one_rss", "rss", "blocked"),
                attempt("one_browser", "browser", "success"),
            ),
        ),
        NewsBrandResult(
            brand_id="brand_two",
            name="Brand Two",
            brand_class="general_finance_desk",
            region="US",
            success=False,
            successful_endpoint_id="",
            final_outcome="blocked",
            endpoint_attempts=(attempt("two_html", "static_html", "blocked"),),
        ),
    ]

    paths = write_news_reports(
        results,
        tmp_path,
        generated_at="2026-08-09T00:00:00Z",
        target_total=120,
    )
    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["observation_unit"] == "unique_news_brand"
    assert payload["summary"] == {
        "catalog_brands": 2,
        "target_brands": 120,
        "successful_brands": 1,
        "failed_brands": 1,
        "brand_success_rate": 0.5,
        "endpoint_attempts": 3,
    }
    assert len(payload["results"]) == 2
    assert len(payload["results"][0]["endpoint_attempts"]) == 2
    assert "1/2" in paths.markdown_path.read_text(encoding="utf-8")
