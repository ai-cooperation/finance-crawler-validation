"""Shared source registry for all research applications.

The registry is the boundary between a transport route and an evidence
publisher.  A route (RSS, search, browser, or API) is not automatically an
independent source; ``independence_group`` makes that invariant explicit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from finance_crawler_poc.contracts import validate_contract


SOURCE_TIERS = frozenset({
    "official",
    "regulatory",
    "direct_primary",
    "direct_secondary",
    "aggregator",
    "unknown",
})
TRANSPORTS = frozenset({"browser", "json_api", "rss", "static_html", "file"})
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,127}$")


class SourceRegistryError(ValueError):
    """Raised when a source registry violates the shared boundary contract."""


def build_source_registry(
    entries: Iterable[Mapping[str, Any]],
    *,
    registry_id: str = "standard_research_sources_v1",
) -> dict[str, Any]:
    """Validate and normalize source definitions into a versioned registry."""

    if not _ID_PATTERN.fullmatch(registry_id):
        raise SourceRegistryError("registry_id must be a lowercase identifier")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise SourceRegistryError("source registry entries must be objects")
        source_id = _required_id(raw, "source_id")
        if source_id in seen:
            raise SourceRegistryError(f"duplicate source_id: {source_id}")
        seen.add(source_id)
        publisher_id = _required_id(raw, "publisher_id")
        independence_group = _required_id(raw, "independence_group")
        source_tier = str(raw.get("source_tier") or "").strip().casefold()
        if source_tier not in SOURCE_TIERS:
            raise SourceRegistryError(f"invalid source_tier for {source_id}: {source_tier!r}")
        transport = str(raw.get("transport") or "").strip().casefold()
        if transport not in TRANSPORTS:
            raise SourceRegistryError(f"invalid transport for {source_id}: {transport!r}")
        canonical_url = str(raw.get("canonical_url") or "").strip()
        parsed = urlsplit(canonical_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SourceRegistryError(f"canonical_url must be http(s) for {source_id}")
        entry = {
            "source_id": source_id,
            "publisher_id": publisher_id,
            "source_tier": source_tier,
            "independence_group": independence_group,
            "transport": transport,
            "canonical_url": canonical_url,
        }
        for optional_key in ("region", "language", "priority", "access_tier", "route_role"):
            value = raw.get(optional_key)
            if isinstance(value, str) and value.strip():
                entry[optional_key] = value.strip()
            elif optional_key == "priority" and isinstance(value, int) and value > 0:
                entry[optional_key] = value
        if isinstance(raw.get("rights"), Mapping):
            entry["rights"] = dict(raw["rights"])
        normalized.append(entry)
    if not normalized:
        raise SourceRegistryError("source registry must contain at least one source")
    payload = {
        "schema_version": 1,
        "registry_id": registry_id,
        "sources": sorted(normalized, key=lambda item: item["source_id"]),
    }
    validate_contract("source-registry", payload)
    return payload


def build_registry_for_items(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Create an explicit unknown-tier registry for legacy/untyped evidence.

    This fallback keeps old callers replayable, but the quality gate will not
    treat unknown-tier sources as official or primary evidence.
    """

    entries: dict[str, dict[str, Any]] = {}
    for item in items:
        source_id = str(item.get("source_id") or "").strip().casefold()
        if not source_id:
            continue
        canonical_url = str(item.get("canonical_url") or "").strip()
        if not canonical_url:
            canonical_url = f"https://finance-crawler.example/unknown/{source_id}"
        evidence_meta = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
        transport = str(item.get("transport") or evidence_meta.get("transport") or "file").strip().casefold()
        if transport not in TRANSPORTS:
            transport = "file"
        entries.setdefault(source_id, {
            "source_id": source_id,
            "publisher_id": source_id,
            "source_tier": "unknown",
            "independence_group": source_id,
            "transport": transport,
            "canonical_url": canonical_url,
        })
    return build_source_registry(entries.values(), registry_id="legacy_item_sources_v1")


def source_metadata(
    registry: Mapping[str, Any],
    source_id: str,
    *,
    item: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return registry metadata, resolving a verified aggregator publisher.

    A Google News (or similar) route is only promoted to the declared
    publisher when the raw RSS item carries a publisher URL and the parser
    marked that source element as verified.  A title suffix alone is never
    trusted as an independence claim.
    """

    for source in registry.get("sources", []):
        if isinstance(source, Mapping) and source.get("source_id") == source_id:
            metadata = dict(source)
            if metadata.get("source_tier") == "aggregator":
                evidence = item.get("evidence") if isinstance(item, Mapping) else None
                if isinstance(evidence, Mapping) and evidence.get("publisher_verified") is True:
                    publisher_id = str(evidence.get("publisher_id") or "").strip().casefold()
                    publisher_url = str(evidence.get("publisher_url") or "").strip()
                    if _ID_PATTERN.fullmatch(publisher_id) and urlsplit(publisher_url).scheme in {"http", "https"} and urlsplit(publisher_url).netloc:
                        metadata.update({
                            "publisher_id": publisher_id,
                            "source_tier": "direct_secondary",
                            "independence_group": publisher_id,
                            "resolved_publisher_url": publisher_url,
                            "publisher_resolution": evidence.get("publisher_resolution"),
                        })
            return metadata
    return {
        "source_id": source_id,
        "publisher_id": source_id or "unknown",
        "source_tier": "unknown",
        "independence_group": source_id or "unknown",
        "transport": "file",
        "canonical_url": f"https://finance-crawler.example/unknown/{source_id or 'source'}",
    }


def _required_id(raw: Mapping[str, Any], field: str) -> str:
    value = str(raw.get(field) or "").strip().casefold()
    if not _ID_PATTERN.fullmatch(value):
        raise SourceRegistryError(f"{field} must be a lowercase identifier")
    return value
