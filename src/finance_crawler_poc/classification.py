from __future__ import annotations

from finance_crawler_poc.models import Outcome


BLOCK_MARKERS = (
    "access denied",
    "captcha",
    "blocked by anti-bot protection",
    "attention required! | cloudflare",
    "cf-chl-",
    "datadome",
    "verify you are human",
    "bot detection",
)
TLS_MARKERS = (
    "certificate verify failed",
    "ssl certificate",
    "tls handshake",
    "cert_verify_failed",
)
TIMEOUT_MARKERS = ("timed out", "timeout")
ROBOTS_MARKERS = ("robots.txt", "robots denied", "disallowed by robots")
AUTH_MARKERS = (
    "variable api_key has not been set",
    "variable api_key is not set",
    "parameter apikey is invalid or missing",
    "invalid api key",
    "api key not valid",
    "please use api key",
    "missing api key",
    "api key required",
    "requires an api key",
)


def classify_failure(*, status_code: int | None, error: str, content: str) -> Outcome:
    combined = f"{error}\n{content}".lower()
    if status_code == 429:
        return Outcome.RATE_LIMITED
    if any(marker in combined for marker in ROBOTS_MARKERS):
        return Outcome.ROBOTS_DENIED
    if status_code == 401 or any(marker in combined for marker in AUTH_MARKERS):
        return Outcome.AUTH_REQUIRED
    # A received HTTP 403 proves transport completed. Classify it before scanning a
    # potentially large HTML body for transport words such as "TLS".
    if status_code == 403 or any(marker in combined for marker in BLOCK_MARKERS):
        return Outcome.BLOCKED
    if any(marker in combined for marker in TLS_MARKERS):
        return Outcome.TLS_ERROR
    if any(marker in combined for marker in TIMEOUT_MARKERS):
        return Outcome.TIMEOUT
    if status_code is not None and status_code >= 400:
        return Outcome.HTTP_ERROR
    return Outcome.ERROR
