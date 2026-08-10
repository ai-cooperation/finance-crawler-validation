import pytest

from finance_crawler_poc.classification import classify_failure
from finance_crawler_poc.models import Outcome


@pytest.mark.parametrize(
    ("status_code", "error", "content", "expected"),
    [
        (429, "", "", Outcome.RATE_LIMITED),
        (401, "authentication required", "", Outcome.AUTH_REQUIRED),
        (400, "", "Variable api_key has not been set", Outcome.AUTH_REQUIRED),
        (400, "", "Bad Request. Variable api_key is not set.", Outcome.AUTH_REQUIRED),
        (200, "", "The parameter apikey is invalid or missing", Outcome.AUTH_REQUIRED),
        (403, "", "API key not valid. Please pass a valid API key.", Outcome.AUTH_REQUIRED),
        (
            403,
            "",
            "Method doesn't allow unregistered callers. Please use API Key.",
            Outcome.AUTH_REQUIRED,
        ),
        (403, "", "Access denied", Outcome.BLOCKED),
        (403, "", "HTML stylesheet contains a tls class name", Outcome.BLOCKED),
        (200, "", "Cloudflare CAPTCHA challenge", Outcome.BLOCKED),
        (
            200,
            "Blocked by anti-bot protection: script_heavy_shell",
            "",
            Outcome.BLOCKED,
        ),
        (200, "", "Akamai Technologies stock news", Outcome.ERROR),
        (None, "certificate verify failed", "", Outcome.TLS_ERROR),
        (None, "TLS handshake failed", "", Outcome.TLS_ERROR),
        (None, "operation timed out", "", Outcome.TIMEOUT),
        (503, "service unavailable", "", Outcome.HTTP_ERROR),
        (None, "browser crashed", "", Outcome.ERROR),
    ],
)
def test_classify_failure(
    status_code: int | None,
    error: str,
    content: str,
    expected: Outcome,
) -> None:
    assert classify_failure(status_code=status_code, error=error, content=content) is expected
