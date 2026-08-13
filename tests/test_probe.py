import asyncio

from finance_crawler_poc.models import FetchResponse, FetchSnapshot, Outcome, Source
from finance_crawler_poc.probe import probe_source


class FakeAdapter:
    def __init__(self, responses: list[FetchResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def fetch(self, source: Source) -> FetchResponse:
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


def make_source(**overrides: object) -> Source:
    values: dict[str, object] = {
        "id": "source",
        "name": "Source",
        "topic": "finance",
        "transport": "browser",
        "url": "https://example.com",
        "required_terms": ("Market",),
        "min_content_chars": 10,
        "timeout_seconds": 10,
        "retries": 1,
    }
    values.update(overrides)
    return Source(**values)


def test_probe_requires_content_contract_not_only_http_200() -> None:
    adapter = FakeAdapter([FetchResponse(status_code=200, content="too short")])

    result = asyncio.run(probe_source(make_source(retries=0), adapter, sleep=lambda _: _done()))

    assert result.outcome is Outcome.INVALID_CONTENT
    assert "required term" in result.error


def test_probe_classifies_adapter_format_errors_as_invalid_content() -> None:
    adapter = FakeAdapter(
        [
            FetchResponse(
                status_code=200,
                content="<html>market news</html>",
                error="invalid RSS/Atom feed: root element is html",
            )
        ]
    )

    result = asyncio.run(
        probe_source(make_source(retries=0), adapter, sleep=lambda _: _done())
    )

    assert result.outcome is Outcome.INVALID_CONTENT
    assert result.error == "invalid RSS/Atom feed: root element is html"


def test_probe_excludes_catalog_verified_robots_denial_before_adapter_call() -> None:
    adapter = FakeAdapter([])
    source = make_source(
        robots_denied=True,
        robots_evidence="https://example.com/robots.txt",
        robots_checked_at="2026-08-09",
    )

    result = asyncio.run(probe_source(source, adapter, sleep=lambda _: _done()))

    assert result.outcome is Outcome.ROBOTS_DENIED
    assert result.attempts == 0
    assert result.status_code is None
    assert result.final_url == source.url
    assert "2026-08-09" in result.error
    assert adapter.calls == 0


def test_probe_retries_rate_limit_and_records_success_evidence() -> None:
    adapter = FakeAdapter(
        [
            FetchResponse(status_code=429, content="slow down"),
            FetchResponse(status_code=200, content="Market data is available today"),
        ]
    )

    result = asyncio.run(
        probe_source(make_source(kind="community"), adapter, sleep=lambda _: _done(), run_index=2)
    )

    assert result.outcome is Outcome.SUCCESS
    assert result.attempts == 2
    assert result.content_chars == 30
    assert len(result.content_sha256) == 64
    assert result.preview == "Market data is available today"
    assert result.kind == "community"
    assert result.run_index == 2


def test_probe_classifies_http_200_api_key_message_as_auth_required() -> None:
    adapter = FakeAdapter(
        [FetchResponse(status_code=200, content="The parameter apikey is invalid or missing" * 10)]
    )

    result = asyncio.run(probe_source(make_source(retries=0), adapter, sleep=lambda _: _done()))

    assert result.outcome is Outcome.AUTH_REQUIRED
    assert result.error == "authentication requirement found in response"


def test_probe_converts_exception_to_classified_result() -> None:
    adapter = FakeAdapter([TimeoutError("operation timed out")])

    result = asyncio.run(probe_source(make_source(retries=0), adapter, sleep=lambda _: _done()))

    assert result.outcome is Outcome.TIMEOUT
    assert result.attempts == 1


def test_probe_records_http_failure_and_content_evidence() -> None:
    adapter = FakeAdapter([FetchResponse(status_code=503, content="maintenance")])

    result = asyncio.run(probe_source(make_source(retries=0), adapter, sleep=lambda _: _done()))

    assert result.outcome is Outcome.HTTP_ERROR
    assert result.status_code == 503
    assert result.error == "HTTP 503"
    assert result.content_chars == len("maintenance")


def test_probe_rejects_antibot_page_returned_with_http_200() -> None:
    adapter = FakeAdapter([FetchResponse(status_code=200, content="Cloudflare CAPTCHA" * 20)])

    result = asyncio.run(probe_source(make_source(retries=0), adapter, sleep=lambda _: _done()))

    assert result.outcome is Outcome.BLOCKED
    assert result.error == "anti-bot marker found in content"


def test_probe_rejects_content_below_minimum_after_terms_pass() -> None:
    adapter = FakeAdapter([FetchResponse(status_code=200, content="Market")])

    result = asyncio.run(probe_source(make_source(retries=0), adapter, sleep=lambda _: _done()))

    assert result.outcome is Outcome.INVALID_CONTENT
    assert result.error == "content shorter than minimum: 6 < 10"


def test_probe_requires_at_least_one_semantic_term_when_configured() -> None:
    adapter = FakeAdapter(
        [FetchResponse(status_code=200, content="generic landing page" * 20)]
    )
    semantic_source = make_source(
        required_terms=(),
        required_any_terms=("finance", "market", "invest"),
        retries=0,
    )

    result = asyncio.run(
        probe_source(semantic_source, adapter, sleep=lambda _: _done())
    )

    assert result.outcome is Outcome.INVALID_CONTENT
    assert result.error == "none of the required semantic terms were found"


def test_probe_records_direct_failure_and_relay_recovery_separately() -> None:
    adapter = FakeAdapter(
        [
            FetchResponse(
                status_code=200,
                content="Market data is available today",
                route="cloudflare_relay",
                prior_attempts=(
                    FetchSnapshot(
                        route="direct",
                        status_code=403,
                        content="Cloudflare challenge",
                        error="",
                    ),
                ),
            )
        ]
    )

    result = asyncio.run(probe_source(make_source(retries=0), adapter, sleep=lambda _: _done()))

    assert result.outcome is Outcome.SUCCESS
    assert [attempt.route for attempt in result.delivery_attempts] == [
        "direct",
        "cloudflare_relay",
    ]
    assert [attempt.outcome for attempt in result.delivery_attempts] == [
        Outcome.BLOCKED,
        Outcome.SUCCESS,
    ]


def test_probe_classifies_login_redirect_as_auth_required() -> None:
    adapter = FakeAdapter(
        [
            FetchResponse(
                status_code=200,
                content="Market community login page" * 20,
                final_url="https://example.com/login",
                route="crawl4ai",
            )
        ]
    )

    result = asyncio.run(probe_source(make_source(retries=0), adapter, sleep=lambda _: _done()))

    assert result.outcome is Outcome.AUTH_REQUIRED
    assert result.error == "authentication redirect detected"


def test_disabled_source_never_calls_adapter() -> None:
    adapter = FakeAdapter([])

    result = asyncio.run(
        probe_source(
            make_source(enabled=False, disabled_reason="identity required"),
            adapter,
            sleep=lambda _: _done(),
        )
    )

    assert result.outcome is Outcome.DISABLED
    assert result.error == "identity required"
    assert adapter.calls == 0


async def _done() -> None:
    return None
