from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Awaitable, Callable

from finance_crawler_poc.models import Outcome, ProbeResult, Source
from finance_crawler_poc.news_catalog import NewsBrand, NewsEndpoint
from finance_crawler_poc.probe import Adapter, probe_source
from finance_crawler_poc.resource_router import (
    Executor,
    ExecutorState,
    ResourceDemand,
    RoutingBlocked,
    select_executor,
)


EXPECTED_DURATION_SECONDS = {
    "json_api": 20,
    "rss": 20,
    "static_html": 30,
    "browser": 60,
}
EXPECTED_RESPONSE_BYTES = {
    "json_api": 2_000_000,
    "rss": 2_000_000,
    "static_html": 2_000_000,
    "browser": 5_000_000,
}
ACCESS_TIER = {
    "json_api": "public_api",
    "rss": "public_feed",
    "static_html": "public_web",
    "browser": "public_web",
}
FINANCE_SEMANTIC_TERMS = (
    "market",
    "business",
    "finance",
    "invest",
    "bank",
    "econom",
    "stock",
    "fund",
    "crypto",
    "money",
    "trade",
    "wealth",
    "insurance",
    "mortgage",
    "pension",
    "asset",
    "capital",
)


@dataclass(frozen=True)
class NewsEndpointAttempt:
    endpoint_id: str
    transport: str
    url: str
    executor_id: str
    outcome: str
    status_code: int | None
    elapsed_ms: int
    content_chars: int
    content_sha256: str
    preview: str
    error: str
    final_url: str = ""
    content_type: str = ""
    delivery_attempts: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class NewsBrandResult:
    brand_id: str
    name: str
    brand_class: str
    region: str
    success: bool
    successful_endpoint_id: str
    final_outcome: str
    endpoint_attempts: tuple[NewsEndpointAttempt, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["endpoint_attempts"] = [
            attempt.to_dict() for attempt in self.endpoint_attempts
        ]
        return payload


ProbeCallable = Callable[..., Awaitable[ProbeResult]]


async def probe_news_brand(
    brand: NewsBrand,
    *,
    executors: tuple[Executor, ...],
    states: dict[str, ExecutorState],
    adapters: dict[str, Adapter],
    probe: ProbeCallable = probe_source,
    run_index: int = 1,
) -> NewsBrandResult:
    attempts: list[NewsEndpointAttempt] = []
    for endpoint in brand.endpoints:
        try:
            executor = select_executor(
                resource_demand_for_endpoint(endpoint), executors, states
            )
        except RoutingBlocked as exc:
            attempts.append(_routing_blocked_attempt(endpoint, str(exc)))
            continue

        adapter = adapters.get(endpoint.transport)
        if adapter is None:
            attempts.append(
                _routing_blocked_attempt(
                    endpoint, f"no adapter for transport: {endpoint.transport}"
                )
            )
            continue
        result = await probe(
            _source_for_endpoint(brand, endpoint),
            adapter,
            run_index=run_index,
        )
        attempts.append(_endpoint_attempt(endpoint, executor.id, result))
        if result.outcome is Outcome.SUCCESS:
            return _brand_result(brand, attempts, endpoint.id)

    return _brand_result(brand, attempts, "")


def resource_demand_for_endpoint(endpoint: NewsEndpoint) -> ResourceDemand:
    return ResourceDemand(
        required_capabilities=endpoint.required_capabilities,
        expected_duration_seconds=EXPECTED_DURATION_SECONDS[endpoint.transport],
        expected_response_bytes=EXPECTED_RESPONSE_BYTES[endpoint.transport],
    )


def _source_for_endpoint(brand: NewsBrand, endpoint: NewsEndpoint) -> Source:
    return Source(
        id=endpoint.id,
        name=f"{brand.name} ({endpoint.transport})",
        topic="finance_news",
        transport=endpoint.transport,
        url=endpoint.url,
        required_any_terms=FINANCE_SEMANTIC_TERMS,
        min_content_chars=300,
        timeout_seconds=50 if endpoint.transport == "browser" else 20,
        retries=0,
        provenance="news_120_catalog_2026_08",
        kind="news",
        selection_evidence=endpoint.url,
        region=brand.region,
        access_tier=ACCESS_TIER[endpoint.transport],
        route_group=brand.id,
        relay_path=endpoint.relay_path,
    )


def _endpoint_attempt(
    endpoint: NewsEndpoint, executor_id: str, result: ProbeResult
) -> NewsEndpointAttempt:
    return NewsEndpointAttempt(
        endpoint_id=endpoint.id,
        transport=endpoint.transport,
        url=endpoint.url,
        executor_id=executor_id,
        outcome=result.outcome.value,
        status_code=result.status_code,
        elapsed_ms=result.elapsed_ms,
        content_chars=result.content_chars,
        content_sha256=result.content_sha256,
        preview=result.preview,
        error=result.error,
        final_url=result.final_url,
        content_type=result.content_type,
        delivery_attempts=tuple(
            delivery.to_dict() for delivery in result.delivery_attempts
        ),
    )


def _routing_blocked_attempt(
    endpoint: NewsEndpoint, error: str
) -> NewsEndpointAttempt:
    return NewsEndpointAttempt(
        endpoint_id=endpoint.id,
        transport=endpoint.transport,
        url=endpoint.url,
        executor_id="",
        outcome="routing_blocked",
        status_code=None,
        elapsed_ms=0,
        content_chars=0,
        content_sha256="",
        preview="",
        error=error,
    )


def _brand_result(
    brand: NewsBrand,
    attempts: list[NewsEndpointAttempt],
    successful_endpoint_id: str,
) -> NewsBrandResult:
    final_outcome = attempts[-1].outcome if attempts else "routing_blocked"
    return NewsBrandResult(
        brand_id=brand.id,
        name=brand.name,
        brand_class=brand.brand_class,
        region=brand.region,
        success=bool(successful_endpoint_id),
        successful_endpoint_id=successful_endpoint_id,
        final_outcome=final_outcome,
        endpoint_attempts=tuple(attempts),
    )
