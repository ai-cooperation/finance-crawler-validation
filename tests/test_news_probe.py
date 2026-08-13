import asyncio

from finance_crawler_poc.models import Outcome, ProbeResult
from pathlib import Path

from finance_crawler_poc.news_catalog import NewsBrand, NewsEndpoint, load_news_catalog
from finance_crawler_poc.news_probe import probe_news_brand, resource_demand_for_endpoint
from finance_crawler_poc.resource_router import (
    Executor,
    ExecutorState,
    load_executors,
    select_executor,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class FakeAdapter:
    pass


def endpoint(endpoint_id: str, transport: str, *capabilities: str) -> NewsEndpoint:
    return NewsEndpoint(
        id=endpoint_id,
        transport=transport,
        url=f"https://finance.example/{endpoint_id}",
        required_capabilities=frozenset(capabilities),
    )


def result_for(source, outcome: Outcome) -> ProbeResult:
    return ProbeResult(
        source_id=source.id,
        name=source.name,
        topic=source.topic,
        transport=source.transport,
        url=source.url,
        outcome=outcome,
        status_code=200 if outcome is Outcome.SUCCESS else 403,
        attempts=1,
        elapsed_ms=10,
        content_chars=500 if outcome is Outcome.SUCCESS else 0,
        content_sha256="a" * 64 if outcome is Outcome.SUCCESS else "",
        preview="finance news" if outcome is Outcome.SUCCESS else "",
        error="" if outcome is Outcome.SUCCESS else "blocked",
        route_group=source.route_group,
        final_url="https://finance.example/final",
        content_type="text/html",
    )


def test_brand_probe_falls_back_between_endpoints_but_counts_one_brand() -> None:
    brand = NewsBrand(
        id="finance_brand",
        name="Finance Brand",
        canonical_domain="finance.example",
        brand_class="finance_specialist",
        region="global",
        languages=("en",),
        endpoints=(
            endpoint("finance_rss", "rss", "http", "rss"),
            endpoint("finance_browser", "browser", "http", "javascript"),
        ),
    )
    executor = Executor(
        id="github_actions_crawl4ai",
        platform="github_actions",
        capabilities=frozenset(
            {"http", "rss", "javascript", "chromium", "python", "crawl4ai"}
        ),
        max_duration_seconds=1200,
        max_response_bytes=20_000_000,
        cost_rank=3,
    )
    states = {
        executor.id: ExecutorState(
            available=True, credential_available=True, remaining_jobs=10
        )
    }
    outcomes = iter((Outcome.BLOCKED, Outcome.SUCCESS))

    async def fake_probe(source, adapter, *, run_index):
        return result_for(source, next(outcomes))

    result = asyncio.run(
        probe_news_brand(
            brand,
            executors=(executor,),
            states=states,
            adapters={"rss": FakeAdapter(), "browser": FakeAdapter()},
            probe=fake_probe,
        )
    )

    assert result.success is True
    assert result.successful_endpoint_id == "finance_browser"
    assert result.final_outcome == "success"
    assert len(result.endpoint_attempts) == 2
    assert {item.executor_id for item in result.endpoint_attempts} == {
        "github_actions_crawl4ai"
    }
    assert result.endpoint_attempts[0].outcome == "blocked"
    assert result.endpoint_attempts[1].final_url == "https://finance.example/final"
    assert result.endpoint_attempts[1].content_type == "text/html"


def test_brand_probe_records_routing_failure_instead_of_dropping_brand() -> None:
    brand = NewsBrand(
        id="finance_brand",
        name="Finance Brand",
        canonical_domain="finance.example",
        brand_class="finance_specialist",
        region="global",
        languages=("en",),
        endpoints=(endpoint("finance_rss", "rss", "http", "rss"),),
    )
    result = asyncio.run(
        probe_news_brand(
            brand,
            executors=(),
            states={},
            adapters={"rss": FakeAdapter()},
        )
    )

    assert result.success is False
    assert result.final_outcome == "routing_blocked"
    assert len(result.endpoint_attempts) == 1
    assert result.endpoint_attempts[0].executor_id == ""


def test_all_120_brand_endpoints_fit_the_github_actions_executor_policy() -> None:
    catalog = load_news_catalog(REPOSITORY_ROOT / "news-sources.yaml")
    executors = load_executors(REPOSITORY_ROOT / "resource-executors.yaml")
    github = next(
        executor
        for executor in executors
        if executor.id == "github_actions_crawl4ai"
    )
    states = {
        github.id: ExecutorState(
            available=True,
            credential_available=True,
            remaining_jobs=catalog.endpoint_count,
        )
    }

    selected = [
        select_executor(
            resource_demand_for_endpoint(endpoint), executors, states
        ).id
        for brand in catalog.brands
        for endpoint in brand.endpoints
    ]

    assert len(selected) == catalog.endpoint_count
    assert catalog.endpoint_count >= 148
    assert set(selected) == {github.id}
