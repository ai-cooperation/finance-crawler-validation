import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from finance_crawler_poc.adapters import Crawl4AIAdapter, HttpAdapter, _markdown_text
from finance_crawler_poc.models import Source


def source(transport: str) -> Source:
    return Source(
        id="source",
        name="Source",
        topic="finance",
        transport=transport,
        url="https://example.com/data",
        required_terms=(),
    )


def test_http_adapter_normalizes_json_for_contract_validation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"].startswith("FinanceCrawlerCapabilityProbe/")
        assert request.headers["Accept"].startswith("application/json")
        return httpx.Response(200, json={"price": 42})

    adapter = HttpAdapter(transport=httpx.MockTransport(handler))
    response = asyncio.run(adapter.fetch(source("json_api")))
    asyncio.run(adapter.close())

    assert response.status_code == 200
    assert json.loads(response.content) == {"price": 42}


def test_http_adapter_supports_static_html() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"].startswith("text/html")
        return httpx.Response(
            200,
            text=(
                "<html><head><script>fake market payload</script></head>"
                "<body><h1>Finance news</h1><style>.hidden{}</style></body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    adapter = HttpAdapter(transport=httpx.MockTransport(handler))
    response = asyncio.run(adapter.fetch(source("static_html")))
    asyncio.run(adapter.close())

    assert response.status_code == 200
    assert response.content_type == "text/html"
    assert response.content == "Finance news"


def test_http_adapter_reports_invalid_json_without_throwing() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    adapter = HttpAdapter(transport=httpx.MockTransport(handler))
    response = asyncio.run(adapter.fetch(source("json_api")))
    asyncio.run(adapter.close())

    assert response.error.startswith("invalid JSON:")
    assert response.content == "not json"


def test_http_adapter_uses_feed_accept_header_and_cloudflare_relay_after_403() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "feed.example.com":
            return httpx.Response(403, text="Cloudflare challenge", request=request)
        return httpx.Response(
            200,
            text="<rss><item>market</item></rss>",
            headers={"Content-Type": "application/rss+xml"},
            request=request,
        )

    adapter = HttpAdapter(
        transport=httpx.MockTransport(handler),
        relay_base_url="https://relay.example.workers.dev",
    )
    feed = Source(
        id="feed",
        name="Feed",
        topic="finance",
        transport="rss",
        url="https://feed.example.com/index.rss",
        relay_path="/v1/feed/feed",
    )
    response = asyncio.run(adapter.fetch(feed))
    asyncio.run(adapter.close())

    assert [str(request.url) for request in requests] == [
        "https://feed.example.com/index.rss",
        "https://relay.example.workers.dev/v1/feed/feed",
    ]
    assert all(request.headers["Accept"].startswith("application/atom+xml") for request in requests)
    assert response.status_code == 200
    assert response.route == "cloudflare_relay"
    assert response.content_type == "application/rss+xml"
    assert len(response.prior_attempts) == 1
    assert response.prior_attempts[0].route == "direct"
    assert response.prior_attempts[0].status_code == 403


def test_http_adapter_does_not_relay_non_boundary_404() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, text="missing", request=request)

    adapter = HttpAdapter(
        transport=httpx.MockTransport(handler),
        relay_base_url="https://relay.example.workers.dev",
    )
    feed = Source(
        id="feed",
        name="Feed",
        topic="finance",
        transport="rss",
        url="https://feed.example.com/index.rss",
        relay_path="/v1/feed/feed",
    )
    response = asyncio.run(adapter.fetch(feed))
    asyncio.run(adapter.close())

    assert len(requests) == 1
    assert response.status_code == 404
    assert response.route == "direct"
    assert response.prior_attempts == ()


def test_http_adapter_preserves_direct_evidence_when_relay_network_fails() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "feed.example.com":
            return httpx.Response(403, text="Cloudflare challenge", request=request)
        raise httpx.ConnectError("relay unavailable", request=request)

    adapter = HttpAdapter(
        transport=httpx.MockTransport(handler),
        relay_base_url="https://relay.example.workers.dev",
    )
    feed = Source(
        id="feed",
        name="Feed",
        topic="finance",
        transport="rss",
        url="https://feed.example.com/index.rss",
        relay_path="/v1/feed/feed",
    )
    response = asyncio.run(adapter.fetch(feed))
    asyncio.run(adapter.close())

    assert response.status_code is None
    assert response.route == "cloudflare_relay"
    assert "relay unavailable" in response.error
    assert response.prior_attempts[0].status_code == 403


@pytest.mark.parametrize(
    "relay_base_url",
    [
        "http://relay.example.workers.dev",
        "https://user:password@relay.example.workers.dev",
        "https://relay.example.workers.dev/proxy?target=elsewhere",
        "https://127.0.0.1",
    ],
)
def test_http_adapter_rejects_unsafe_relay_origins(relay_base_url: str) -> None:
    with pytest.raises(ValueError, match="relay_base_url"):
        HttpAdapter(relay_base_url=relay_base_url)


def test_crawl4ai_adapter_enforces_robots_and_page_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class RunConfig:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    fake_module = SimpleNamespace(CacheMode=SimpleNamespace(BYPASS="bypass"), CrawlerRunConfig=RunConfig)
    monkeypatch.setitem(sys.modules, "crawl4ai", fake_module)

    class Crawler:
        async def arun(self, *, url: str, config: object) -> object:
            assert url == "https://example.com/data"
            assert isinstance(config, RunConfig)
            return SimpleNamespace(
                markdown=SimpleNamespace(raw_markdown="market evidence"),
                status_code=200,
                success=True,
                url="https://example.com/final",
            )

    async def robots_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="User-agent: *\nAllow: /\n",
            request=request,
        )

    adapter = Crawl4AIAdapter(robots_transport=httpx.MockTransport(robots_handler))
    adapter._crawler = Crawler()
    response = asyncio.run(adapter.fetch(source("browser")))
    asyncio.run(adapter.close())

    assert response.content == "market evidence"
    assert response.final_url == "https://example.com/final"
    assert response.route == "crawl4ai"
    assert captured["check_robots_txt"] is True
    assert captured["page_timeout"] == 40_000
    assert captured["delay_before_return_html"] == 1.0


def test_crawl4ai_preflight_honors_explicit_disallow_in_http_418_body() -> None:
    requests: list[httpx.Request] = []

    async def robots_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            418,
            text=(
                "License: https://stackoverflow.com/license.xml\n\n"
                "User-agent: *\n"
                "Disallow: /\n"
            ),
            request=request,
        )

    adapter = Crawl4AIAdapter(robots_transport=httpx.MockTransport(robots_handler))
    response = asyncio.run(adapter.fetch(source("browser")))
    asyncio.run(adapter.close())

    assert [str(request.url) for request in requests] == ["https://example.com/robots.txt"]
    assert response.status_code == 418
    assert response.route == "robots_preflight"
    assert "robots.txt disallowed" in response.error
    assert adapter._crawler is None


def test_crawl4ai_uses_browser_default_user_agent_and_isolated_context(monkeypatch) -> None:
    browser_config: dict[str, object] = {}

    class BrowserConfig:
        def __init__(self, **kwargs: object) -> None:
            browser_config.update(kwargs)

    class AsyncWebCrawler:
        def __init__(self, *, config: object) -> None:
            assert isinstance(config, BrowserConfig)

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    fake_module = SimpleNamespace(AsyncWebCrawler=AsyncWebCrawler, BrowserConfig=BrowserConfig)
    monkeypatch.setitem(sys.modules, "crawl4ai", fake_module)

    adapter = Crawl4AIAdapter()
    asyncio.run(adapter._ensure_crawler())
    asyncio.run(adapter.close())

    assert "user_agent" not in browser_config
    assert browser_config["create_isolated_context"] is True


def test_markdown_text_handles_none_raw_and_plain_values() -> None:
    assert _markdown_text(None) == ""
    assert _markdown_text(SimpleNamespace(raw_markdown="raw")) == "raw"
    assert _markdown_text("plain") == "plain"
