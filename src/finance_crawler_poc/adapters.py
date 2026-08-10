from __future__ import annotations

import ipaddress
import json
import urllib.robotparser
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from finance_crawler_poc.models import FetchResponse, FetchSnapshot, Source


USER_AGENT = (
    "FinanceCrawlerCapabilityProbe/0.2 "
    "(+https://github.com/AlanChen75/finance-crawler-poc)"
)
ACCEPT_HEADERS = {
    "json_api": "application/json, application/problem+json;q=0.9, */*;q=0.1",
    "rss": (
        "application/atom+xml, application/rss+xml, application/xml, "
        "text/xml;q=0.9, */*;q=0.1"
    ),
    "static_html": "text/html, application/xhtml+xml;q=0.9, */*;q=0.1",
}
RELAYABLE_STATUS_CODES = frozenset({403, 429})


class HttpAdapter:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        relay_base_url: str | None = None,
    ) -> None:
        self._relay_base_url = _normalize_relay_base_url(relay_base_url)
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            http2=transport is None,
            transport=transport,
        )

    async def fetch(self, source: Source) -> FetchResponse:
        try:
            direct = await self._fetch_once(source.url, source, route="direct")
        except httpx.HTTPError as exc:
            if not self._can_relay(source):
                raise
            direct = FetchResponse(
                status_code=None,
                content="",
                error=f"{type(exc).__name__}: {exc}",
                route="direct",
                final_url=source.url,
            )

        if not self._should_relay(source, direct):
            return direct

        relay_url = f"{self._relay_base_url}{source.relay_path}"
        try:
            relay = await self._fetch_once(
                relay_url,
                source,
                route="cloudflare_relay",
            )
        except httpx.HTTPError as exc:
            relay = FetchResponse(
                status_code=None,
                content="",
                error=f"{type(exc).__name__}: {exc}",
                route="cloudflare_relay",
                final_url=relay_url,
            )
        return FetchResponse(
            status_code=relay.status_code,
            content=relay.content,
            error=relay.error,
            route=relay.route,
            content_type=relay.content_type,
            final_url=relay.final_url,
            prior_attempts=(
                FetchSnapshot(
                    route=direct.route,
                    status_code=direct.status_code,
                    content=direct.content,
                    error=direct.error,
                    content_type=direct.content_type,
                    final_url=direct.final_url,
                ),
            ),
        )

    async def _fetch_once(self, url: str, source: Source, *, route: str) -> FetchResponse:
        response = await self._client.get(
            url,
            timeout=source.timeout_seconds,
            headers={"Accept": ACCEPT_HEADERS[source.transport]},
        )
        content = response.text
        if source.transport == "json_api" and 200 <= response.status_code < 400:
            try:
                parsed: Any = response.json()
            except json.JSONDecodeError as exc:
                return FetchResponse(
                    status_code=response.status_code,
                    content=content,
                    error=f"invalid JSON: {exc}",
                    route=route,
                    content_type=_content_type(response),
                    final_url=str(response.url),
                )
            content = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        elif source.transport == "static_html":
            content = _visible_html_text(content)
        return FetchResponse(
            status_code=response.status_code,
            content=content,
            route=route,
            content_type=_content_type(response),
            final_url=str(response.url),
        )

    def _can_relay(self, source: Source) -> bool:
        return bool(self._relay_base_url and source.relay_path)

    def _should_relay(self, source: Source, response: FetchResponse) -> bool:
        if not self._can_relay(source):
            return False
        status = response.status_code
        return status is None or status in RELAYABLE_STATUS_CODES or status >= 500

    async def close(self) -> None:
        await self._client.aclose()


class Crawl4AIAdapter:
    """Lazy adapter so API/RSS probes still run if Chromium initialization fails."""

    def __init__(
        self, *, robots_transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._crawler: Any = None
        self._context: Any = None
        self._init_error: Exception | None = None
        self._robots_client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            transport=robots_transport,
        )

    async def fetch(self, source: Source) -> FetchResponse:
        robots_denial = await self._explicit_robots_denial(source)
        if robots_denial is not None:
            return robots_denial
        await self._ensure_crawler()
        if self._init_error is not None:
            raise RuntimeError(f"Crawl4AI initialization failed: {self._init_error}")

        from crawl4ai import CacheMode, CrawlerRunConfig

        config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            check_robots_txt=True,
            delay_before_return_html=1.0,
            page_timeout=source.timeout_seconds * 1_000,
            wait_until="domcontentloaded",
        )
        result = await self._crawler.arun(url=source.url, config=config)
        markdown = _markdown_text(result.markdown)
        response_headers = getattr(result, "response_headers", None) or {}
        return FetchResponse(
            status_code=(
                getattr(result, "redirected_status_code", None)
                or getattr(result, "status_code", None)
            ),
            content=markdown,
            error="" if result.success else str(getattr(result, "error_message", "crawl failed")),
            route="crawl4ai",
            content_type=str(response_headers.get("content-type", "")).split(";", 1)[0],
            final_url=str(getattr(result, "url", source.url)),
        )

    async def _explicit_robots_denial(self, source: Source) -> FetchResponse | None:
        robots_url = urljoin(source.url, "/robots.txt")
        try:
            response = await self._robots_client.get(
                robots_url,
                timeout=min(10, source.timeout_seconds),
            )
        except httpx.HTTPError:
            # Crawl4AI still performs its own robots check. This preflight exists
            # for explicit rules returned with non-standard HTTP status codes.
            return None

        body = response.text
        if "user-agent:" not in body.casefold():
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(str(response.url))
        parser.parse(body.splitlines())
        if parser.can_fetch("*", source.url):
            return None
        return FetchResponse(
            status_code=response.status_code,
            content="",
            error=f"robots.txt disallowed by explicit rule (HTTP {response.status_code})",
            route="robots_preflight",
            content_type=_content_type(response),
            final_url=source.url,
        )

    async def _ensure_crawler(self) -> None:
        if self._crawler is not None or self._init_error is not None:
            return
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig

            browser_config = BrowserConfig(
                headless=True,
                create_isolated_context=True,
                verbose=False,
            )
            self._context = AsyncWebCrawler(config=browser_config)
            self._crawler = await self._context.__aenter__()
        except Exception as exc:  # Keep a stable failure for every browser source.
            self._init_error = exc

    async def close(self) -> None:
        await self._robots_client.aclose()
        if self._context is not None:
            await self._context.__aexit__(None, None, None)


def _markdown_text(markdown: Any) -> str:
    if markdown is None:
        return ""
    raw_markdown = getattr(markdown, "raw_markdown", None)
    return str(raw_markdown if raw_markdown is not None else markdown)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0 and data.strip():
            self.parts.append(data.strip())


def _visible_html_text(content: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(content)
    return " ".join(" ".join(parser.parts).split())


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _normalize_relay_base_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("relay_base_url has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise ValueError("relay_base_url must be a credential-free public HTTPS origin")
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("relay_base_url must target a public host")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("relay_base_url must target a public host")
    return value.rstrip("/")
