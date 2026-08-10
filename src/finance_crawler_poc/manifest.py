from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from finance_crawler_poc.models import Manifest, Source


ALLOWED_TRANSPORTS = frozenset({"browser", "json_api", "rss"})
ALLOWED_KINDS = frozenset(
    {
        "aggregator",
        "community",
        "developer_community",
        "market_data",
        "news",
        "official_data",
        "official_news",
        "other",
        "reference",
    }
)
ALLOWED_COMMUNITY_TYPES = frozenset(
    {
        "active_trading",
        "crypto",
        "developer_ecosystem",
        "not_applicable",
        "personal_finance",
        "professional_finance",
        "quantitative",
        "retail_investing",
        "social_investing",
        "value_investing",
    }
)
ALLOWED_ACCESS_TIERS = frozenset(
    {
        "auth_boundary",
        "commercial_api",
        "credentialed_api",
        "member_only",
        "public_api",
        "public_feed",
        "public_web",
    }
)
SOURCE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
REGION_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")
BASE_DEFAULTS: Mapping[str, int] = {
    "timeout_seconds": 40,
    "retries": 1,
    "min_content_chars": 300,
}


class ManifestError(ValueError):
    """Raised when a source manifest violates its input contract."""


def load_manifest(path: Path) -> Manifest:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ManifestError("manifest root must be a mapping")
    if raw.get("version") != 1:
        raise ManifestError("manifest version must be 1")

    defaults = raw.get("defaults", {})
    if not isinstance(defaults, Mapping):
        raise ManifestError("defaults must be a mapping")
    source_items = raw.get("sources")
    if not isinstance(source_items, list) or not source_items:
        raise ManifestError("sources must be a non-empty list")

    sources: list[Source] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(source_items):
        if not isinstance(item, Mapping):
            raise ManifestError(f"source {index} must be a mapping")
        merged = {**BASE_DEFAULTS, **defaults, **item}
        source = _parse_source(merged, index)
        if source.id in seen_ids:
            raise ManifestError(f"duplicate source id: {source.id}")
        seen_ids.add(source.id)
        sources.append(source)

    return Manifest(version=1, sources=tuple(sources))


def _parse_source(item: Mapping[str, Any], index: int) -> Source:
    required_strings = ("id", "name", "topic", "transport", "url")
    values: dict[str, str] = {}
    for key in required_strings:
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ManifestError(f"source {index} field {key} must be a non-empty string")
        values[key] = value.strip()

    if not SOURCE_ID_PATTERN.fullmatch(values["id"]):
        raise ManifestError(f"source {values['id']} has invalid id")
    if values["transport"] not in ALLOWED_TRANSPORTS:
        raise ManifestError(
            f"source {values['id']} transport must be one of {sorted(ALLOWED_TRANSPORTS)}"
        )
    _validate_public_http_url(values["url"], values["id"])

    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ManifestError(f"source {values['id']} enabled must be boolean")
    disabled_reason = item.get("disabled_reason", "")
    if not isinstance(disabled_reason, str):
        raise ManifestError(f"source {values['id']} disabled_reason must be a string")
    if not enabled and not disabled_reason.strip():
        raise ManifestError(f"source {values['id']} disabled_reason is required")

    required_terms_raw = item.get("required_terms", [])
    if not isinstance(required_terms_raw, list) or not all(
        isinstance(term, str) and term.strip() for term in required_terms_raw
    ):
        raise ManifestError(f"source {values['id']} required_terms must be strings")

    timeout_seconds = _bounded_int(item, "timeout_seconds", values["id"], 5, 120)
    retries = _bounded_int(item, "retries", values["id"], 0, 3)
    min_content_chars = _bounded_int(item, "min_content_chars", values["id"], 1, 1_000_000)
    provenance = item.get("provenance", "curated")
    if not isinstance(provenance, str) or not provenance.strip():
        raise ManifestError(f"source {values['id']} provenance must be a string")
    kind = item.get("kind", "other")
    if not isinstance(kind, str) or kind not in ALLOWED_KINDS:
        raise ManifestError(f"source {values['id']} kind must be one of {sorted(ALLOWED_KINDS)}")
    selection_evidence = item.get("selection_evidence", "")
    if not isinstance(selection_evidence, str):
        raise ManifestError(f"source {values['id']} selection_evidence must be a string")
    if selection_evidence:
        try:
            _validate_public_http_url(selection_evidence, values["id"])
        except ManifestError as exc:
            raise ManifestError(
                f"source {values['id']} selection_evidence must use a public http or https URL"
            ) from exc
    community_type = item.get("community_type", "not_applicable")
    if not isinstance(community_type, str) or community_type not in ALLOWED_COMMUNITY_TYPES:
        raise ManifestError(
            f"source {values['id']} community_type must be one of "
            f"{sorted(ALLOWED_COMMUNITY_TYPES)}"
        )
    region = item.get("region", "global")
    if not isinstance(region, str) or not REGION_PATTERN.fullmatch(region):
        raise ManifestError(
            f"source {values['id']} region must be a 2-32 character region code"
        )
    default_access_tier = {
        "browser": "public_web",
        "json_api": "public_api",
        "rss": "public_feed",
    }[values["transport"]]
    access_tier = item.get("access_tier", default_access_tier)
    if not isinstance(access_tier, str) or access_tier not in ALLOWED_ACCESS_TIERS:
        raise ManifestError(
            f"source {values['id']} access_tier must be one of {sorted(ALLOWED_ACCESS_TIERS)}"
        )
    route_group = item.get("route_group", values["id"])
    if not isinstance(route_group, str) or not SOURCE_ID_PATTERN.fullmatch(route_group):
        raise ManifestError(f"source {values['id']} route_group must be a valid source-style id")
    relay_path = item.get("relay_path", "")
    if not isinstance(relay_path, str):
        raise ManifestError(f"source {values['id']} relay_path must be a string")
    expected_relay_path = f"/v1/feed/{values['id']}"
    if relay_path and (
        values["transport"] != "rss" or relay_path != expected_relay_path
    ):
        raise ManifestError(
            f"source {values['id']} relay_path must equal {expected_relay_path} for RSS sources"
        )

    robots_denied = item.get("robots_denied", False)
    if not isinstance(robots_denied, bool):
        raise ManifestError(f"source {values['id']} robots_denied must be boolean")
    robots_evidence = item.get("robots_evidence", "")
    robots_checked_at = item.get("robots_checked_at", "")
    if not isinstance(robots_evidence, str):
        raise ManifestError(f"source {values['id']} robots_evidence must be a string")
    if not isinstance(robots_checked_at, str):
        raise ManifestError(f"source {values['id']} robots_checked_at must be a string")
    if robots_denied:
        if values["transport"] != "browser":
            raise ManifestError(
                f"source {values['id']} robots_denied is only valid for browser sources"
            )
        if not robots_evidence.strip():
            raise ManifestError(f"source {values['id']} robots_evidence is required")
        _validate_public_http_url(robots_evidence.strip(), values["id"])
        if not robots_checked_at.strip():
            raise ManifestError(f"source {values['id']} robots_checked_at is required")
        try:
            date.fromisoformat(robots_checked_at.strip())
        except ValueError as exc:
            raise ManifestError(
                f"source {values['id']} robots_checked_at must be an ISO date"
            ) from exc
    elif robots_evidence or robots_checked_at:
        raise ManifestError(
            f"source {values['id']} robots evidence requires robots_denied: true"
        )

    return Source(
        id=values["id"],
        name=values["name"],
        topic=values["topic"],
        transport=values["transport"],
        url=values["url"],
        required_terms=tuple(term.strip() for term in required_terms_raw),
        min_content_chars=min_content_chars,
        timeout_seconds=timeout_seconds,
        retries=retries,
        enabled=enabled,
        disabled_reason=disabled_reason.strip(),
        provenance=provenance.strip(),
        kind=kind,
        selection_evidence=selection_evidence.strip(),
        community_type=community_type,
        region=region,
        access_tier=access_tier,
        route_group=route_group,
        relay_path=relay_path,
        robots_denied=robots_denied,
        robots_evidence=robots_evidence.strip(),
        robots_checked_at=robots_checked_at.strip(),
    )


def _bounded_int(
    item: Mapping[str, Any], key: str, source_id: str, minimum: int, maximum: int
) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ManifestError(f"source {source_id} {key} must be between {minimum} and {maximum}")
    return value


def _validate_public_http_url(url: str, source_id: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ManifestError(f"source {source_id} url must use http or https")
    if parsed.username or parsed.password:
        raise ManifestError(f"source {source_id} url must not contain credentials")

    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ManifestError(f"source {source_id} url must target a public host")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ManifestError(f"source {source_id} url must target a public host")
