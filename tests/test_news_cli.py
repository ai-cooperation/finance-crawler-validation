import asyncio
from pathlib import Path

import pytest

from finance_crawler_poc import news_cli
from finance_crawler_poc.news_catalog import (
    NewsBrand,
    NewsCatalog,
    NewsEndpoint,
    NewsTarget,
)
from finance_crawler_poc.news_probe import NewsBrandResult
from finance_crawler_poc.resource_router import Executor


class FakeAdapter:
    instances: list["FakeAdapter"] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.closed = False
        self.instances.append(self)

    async def close(self) -> None:
        self.closed = True


def catalog(*, complete: bool = True) -> NewsCatalog:
    brand = NewsBrand(
        id="brand_one",
        name="Brand One",
        canonical_domain="brand.example",
        brand_class="finance_specialist",
        region="global",
        languages=("en",),
        endpoints=(
            NewsEndpoint(
                id="brand_one_rss",
                transport="rss",
                url="https://brand.example/rss",
                required_capabilities=frozenset({"http", "rss"}),
            ),
        ),
    )
    return NewsCatalog(
        version=1,
        status="complete" if complete else "draft",
        target=NewsTarget(
            total_brands=1, finance_specialist=1, general_finance_desk=0
        ),
        brands=(brand,),
    )


def two_brand_catalog() -> NewsCatalog:
    first = catalog().brands[0]
    second = NewsBrand(
        id="brand_two",
        name="Brand Two",
        canonical_domain="brand-two.example",
        brand_class="general_finance_desk",
        region="US",
        languages=("en",),
        endpoints=(
            NewsEndpoint(
                id="brand_two_rss",
                transport="rss",
                url="https://brand-two.example/rss",
                required_capabilities=frozenset({"http", "rss"}),
            ),
        ),
    )
    return NewsCatalog(
        version=1,
        status="complete",
        target=NewsTarget(
            total_brands=2, finance_specialist=1, general_finance_desk=1
        ),
        brands=(first, second),
    )


def executor() -> Executor:
    return Executor(
        id="github_actions_crawl4ai",
        platform="github_actions",
        capabilities=frozenset({"http", "rss"}),
        max_duration_seconds=1200,
        max_response_bytes=20_000_000,
        cost_rank=3,
    )


def test_news_run_exposes_only_the_actual_runtime_executor(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_probe(brand, *, executors, states, adapters):
        captured["states"] = states
        return NewsBrandResult(
            brand_id=brand.id,
            name=brand.name,
            brand_class=brand.brand_class,
            region=brand.region,
            success=True,
            successful_endpoint_id="brand_one_rss",
            final_outcome="success",
            endpoint_attempts=(),
        )

    def fake_write(results, output, *, generated_at, target_total):
        captured["results"] = results
        captured["target_total"] = target_total
        captured["generated_at"] = generated_at

    FakeAdapter.instances = []
    monkeypatch.setattr(news_cli, "load_news_catalog", lambda _: catalog())
    monkeypatch.setattr(news_cli, "load_executors", lambda _: (executor(),))
    monkeypatch.setattr(news_cli, "HttpAdapter", FakeAdapter)
    monkeypatch.setattr(news_cli, "Crawl4AIAdapter", FakeAdapter)
    monkeypatch.setattr(news_cli, "probe_news_brand", fake_probe)
    monkeypatch.setattr(news_cli, "write_news_reports", fake_write)

    results = asyncio.run(
        news_cli.run(
            Path("news-sources.yaml"),
            Path("resource-executors.yaml"),
            tmp_path,
            current_executor_id="github_actions_crawl4ai",
        )
    )

    states = captured["states"]
    assert states["github_actions_crawl4ai"].available is True
    assert captured["target_total"] == 1
    assert results[0].brand_id == "brand_one"
    assert all(adapter.closed for adapter in FakeAdapter.instances)


def test_news_run_rejects_draft_catalog(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(news_cli, "load_news_catalog", lambda _: catalog(complete=False))

    with pytest.raises(ValueError, match="must be complete"):
        asyncio.run(
            news_cli.run(
                Path("news-sources.yaml"),
                Path("resource-executors.yaml"),
                tmp_path,
                current_executor_id="github_actions_crawl4ai",
            )
        )


def test_news_run_selects_a_bounded_unique_brand_batch(monkeypatch, tmp_path: Path) -> None:
    probed: list[str] = []

    async def fake_probe(brand, *, executors, states, adapters):
        probed.append(brand.id)
        return NewsBrandResult(
            brand_id=brand.id,
            name=brand.name,
            brand_class=brand.brand_class,
            region=brand.region,
            success=True,
            successful_endpoint_id=f"{brand.id}_rss",
            final_outcome="success",
            endpoint_attempts=(),
        )

    FakeAdapter.instances = []
    monkeypatch.setattr(news_cli, "load_news_catalog", lambda _: two_brand_catalog())
    monkeypatch.setattr(news_cli, "load_executors", lambda _: (executor(),))
    monkeypatch.setattr(news_cli, "HttpAdapter", FakeAdapter)
    monkeypatch.setattr(news_cli, "Crawl4AIAdapter", FakeAdapter)
    monkeypatch.setattr(news_cli, "probe_news_brand", fake_probe)

    results = asyncio.run(
        news_cli.run(
            Path("news-sources.yaml"),
            Path("resource-executors.yaml"),
            tmp_path,
            current_executor_id="github_actions_crawl4ai",
            brand_ids=("brand_two",),
            max_brands=1,
        )
    )

    report = __import__("json").loads((tmp_path / "news-report.json").read_text())
    assert probed == ["brand_two"]
    assert [result.brand_id for result in results] == ["brand_two"]
    assert report["selection"] == {
        "mode": "explicit_brand_ids",
        "selected_brands": 1,
        "target_brands": 2,
        "brand_ids": ["brand_two"],
    }
    assert report["summary"]["catalog_brands"] == 1
    assert report["summary"]["target_brands"] == 2


@pytest.mark.parametrize(
    ("brand_ids", "max_brands", "message"),
    [
        (("missing",), 1, "unknown brand ids: missing"),
        (("brand_one", "brand_one"), 2, "brand ids must be unique"),
        (("brand_one", "brand_two"), 1, "brand batch exceeds max_brands: 2 > 1"),
    ],
)
def test_news_run_rejects_invalid_brand_batches(
    monkeypatch,
    tmp_path: Path,
    brand_ids: tuple[str, ...],
    max_brands: int,
    message: str,
) -> None:
    monkeypatch.setattr(news_cli, "load_news_catalog", lambda _: two_brand_catalog())
    monkeypatch.setattr(news_cli, "load_executors", lambda _: (executor(),))

    with pytest.raises(ValueError, match=message):
        asyncio.run(
            news_cli.run(
                Path("news-sources.yaml"),
                Path("resource-executors.yaml"),
                tmp_path,
                current_executor_id="github_actions_crawl4ai",
                brand_ids=brand_ids,
                max_brands=max_brands,
            )
        )
