from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Protocol
from urllib.parse import urlparse

from finance_crawler_poc.classification import classify_failure
from finance_crawler_poc.models import (
    DeliveryAttempt,
    FetchResponse,
    FetchSnapshot,
    Outcome,
    ProbeResult,
    Source,
)


class Adapter(Protocol):
    async def fetch(self, source: Source) -> FetchResponse: ...


Sleep = Callable[[float], Awaitable[None]]
RETRYABLE = frozenset({Outcome.RATE_LIMITED, Outcome.TIMEOUT, Outcome.HTTP_ERROR})


async def probe_source(
    source: Source,
    adapter: Adapter,
    *,
    sleep: Sleep = asyncio.sleep,
    run_index: int = 1,
) -> ProbeResult:
    started = time.perf_counter()
    if not source.enabled:
        return _result(
            source,
            outcome=Outcome.DISABLED,
            attempts=0,
            elapsed_ms=_elapsed_ms(started),
            error=source.disabled_reason,
            run_index=run_index,
        )
    if source.robots_denied:
        return _result(
            source,
            outcome=Outcome.ROBOTS_DENIED,
            attempts=0,
            elapsed_ms=_elapsed_ms(started),
            error=(
                f"catalog robots.txt disallow verified {source.robots_checked_at}: "
                f"{source.robots_evidence}"
            ),
            run_index=run_index,
            final_url=source.url,
        )

    last_result: ProbeResult | None = None
    for attempt in range(1, source.retries + 2):
        try:
            response = await adapter.fetch(source)
        except Exception as exc:  # External adapters are an explicit failure boundary.
            error = f"{type(exc).__name__}: {exc}"
            outcome = classify_failure(status_code=None, error=error, content="")
            last_result = _result(
                source,
                outcome=outcome,
                attempts=attempt,
                elapsed_ms=_elapsed_ms(started),
                error=error,
                run_index=run_index,
            )
        else:
            last_result = _evaluate_response(source, response, attempt, started, run_index)

        if last_result.outcome is Outcome.SUCCESS:
            return last_result
        if last_result.outcome not in RETRYABLE or attempt > source.retries:
            return last_result
        await sleep(float(2 ** (attempt - 1)))

    if last_result is None:  # Defensive invariant; the loop always executes at least once.
        raise RuntimeError("probe loop produced no result")
    return last_result


def _evaluate_response(
    source: Source,
    response: FetchResponse,
    attempt: int,
    started: float,
    run_index: int,
) -> ProbeResult:
    outcome, error = _response_outcome(source, response)
    delivery_attempts = tuple(
        [
            _delivery_attempt(source, prior)
            for prior in response.prior_attempts
        ]
        + [_delivery_attempt(source, response)]
    )
    return _result(
        source,
        outcome=outcome,
        status_code=response.status_code,
        attempts=attempt,
        elapsed_ms=_elapsed_ms(started),
        content=response.content,
        error=error,
        run_index=run_index,
        final_url=response.final_url,
        content_type=response.content_type,
        delivery_attempts=delivery_attempts,
    )


def _response_outcome(
    source: Source, response: FetchResponse | FetchSnapshot
) -> tuple[Outcome, str]:
    if _is_auth_redirect(source.url, response.final_url):
        return Outcome.AUTH_REQUIRED, "authentication redirect detected"
    if response.error.startswith(("invalid JSON:", "invalid RSS/Atom feed:")):
        return Outcome.INVALID_CONTENT, response.error
    if response.error or response.status_code is None or not 200 <= response.status_code < 400:
        outcome = classify_failure(
            status_code=response.status_code,
            error=response.error,
            content=response.content,
        )
        return outcome, response.error or f"HTTP {response.status_code}"

    barrier_outcome = classify_failure(
        status_code=response.status_code,
        error="",
        content=response.content[:5_000],
    )
    if barrier_outcome in {Outcome.AUTH_REQUIRED, Outcome.BLOCKED, Outcome.ROBOTS_DENIED}:
        barrier_errors = {
            Outcome.AUTH_REQUIRED: "authentication requirement found in response",
            Outcome.BLOCKED: "anti-bot marker found in content",
            Outcome.ROBOTS_DENIED: "robots denial found in response",
        }
        return barrier_outcome, barrier_errors[barrier_outcome]

    lowered_content = response.content.casefold()
    missing_terms = [term for term in source.required_terms if term.casefold() not in lowered_content]
    if missing_terms:
        return Outcome.INVALID_CONTENT, f"required term missing: {', '.join(missing_terms)}"
    if source.required_any_terms and not any(
        term.casefold() in lowered_content for term in source.required_any_terms
    ):
        return Outcome.INVALID_CONTENT, "none of the required semantic terms were found"
    if len(response.content) < source.min_content_chars:
        return Outcome.INVALID_CONTENT, (
            f"content shorter than minimum: {len(response.content)} "
            f"< {source.min_content_chars}"
        )
    return Outcome.SUCCESS, ""


def _delivery_attempt(
    source: Source, response: FetchResponse | FetchSnapshot
) -> DeliveryAttempt:
    outcome, error = _response_outcome(source, response)
    content = response.content
    return DeliveryAttempt(
        route=response.route,
        outcome=outcome,
        status_code=response.status_code,
        content_chars=len(content),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest() if content else "",
        preview=" ".join(content.split())[:500],
        error=error,
    )


def _is_auth_redirect(source_url: str, final_url: str) -> bool:
    if not final_url or final_url == source_url:
        return False
    source = urlparse(source_url)
    final = urlparse(final_url)
    if source.hostname != final.hostname:
        return False
    final_path = final.path.rstrip("/").casefold()
    return final_path in {"/auth", "/login", "/sign-in", "/signin"}


def _result(
    source: Source,
    *,
    outcome: Outcome,
    attempts: int,
    elapsed_ms: int,
    status_code: int | None = None,
    content: str = "",
    error: str = "",
    run_index: int = 1,
    final_url: str = "",
    content_type: str = "",
    delivery_attempts: tuple[DeliveryAttempt, ...] = (),
) -> ProbeResult:
    normalized_preview = " ".join(content.split())[:500]
    return ProbeResult(
        source_id=source.id,
        name=source.name,
        topic=source.topic,
        transport=source.transport,
        url=source.url,
        outcome=outcome,
        status_code=status_code,
        attempts=attempts,
        elapsed_ms=elapsed_ms,
        content_chars=len(content),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest() if content else "",
        preview=normalized_preview,
        error=error,
        kind=source.kind,
        provenance=source.provenance,
        selection_evidence=source.selection_evidence,
        run_index=run_index,
        community_type=source.community_type,
        region=source.region,
        access_tier=source.access_tier,
        route_group=source.route_group or source.id,
        final_url=final_url,
        content_type=content_type,
        delivery_attempts=delivery_attempts,
        content=content,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1_000))
