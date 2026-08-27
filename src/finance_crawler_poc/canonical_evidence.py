"""Canonicalize raw evidence without discarding the audit trail."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.source_registry import source_metadata


_TIER_PRIORITY = {
    "official": 0,
    "regulatory": 1,
    "direct_primary": 2,
    "direct_secondary": 3,
    "aggregator": 4,
    "unknown": 5,
}
_TRACKING_KEYS = frozenset({"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "oc"})


def build_canonical_evidence_pack(
    items: Iterable[Mapping[str, Any]],
    *,
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a canonical evidence pack while preserving every raw item."""

    raw_items = list(items)
    registry_sources = registry.get("sources") if isinstance(registry, Mapping) else None
    if not isinstance(registry_sources, list):
        raise ValueError("source registry must contain a sources array")
    # An empty evidence run is allowed to carry an empty registry so that a
    # missing-data state remains explicit.  Any non-empty registry must still
    # cross the shared contract boundary before it can influence canonicalization.
    if registry_sources:
        validate_contract("source-registry", registry)
    elif raw_items:
        raise ValueError("non-empty evidence requires a non-empty source registry")

    enriched: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ValueError("evidence items must be objects")
        item_id = str(raw.get("item_id") or "").strip()
        source_id = str(raw.get("source_id") or "").strip().casefold()
        if not item_id or not source_id:
            raise ValueError("evidence item_id and source_id are required")
        metadata = source_metadata(registry, source_id, item=raw)
        canonical_url = _canonical_url(str(raw.get("canonical_url") or ""))
        content_sha256 = _content_hash(raw)
        story_key = _story_key(raw)
        story_id = hashlib.sha256(story_key.encode("utf-8")).hexdigest()
        enriched.append({
            **dict(raw),
            "canonical_url": canonical_url,
            "content_sha256": content_sha256,
            "canonical_story_id": story_id,
            "publisher_id": metadata["publisher_id"],
            "source_tier": metadata["source_tier"],
            "independence_group": metadata["independence_group"],
            "duplicate_of": None,
        })

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in enriched:
        groups[item["canonical_story_id"]].append(item)
    canonical_items: list[dict[str, Any]] = []
    story_groups: list[dict[str, Any]] = []
    for story_id, group in sorted(groups.items()):
        ordered = sorted(group, key=_canonical_rank)
        canonical = dict(ordered[0])
        canonical["duplicate_of"] = None
        canonical_items.append(canonical)
        for duplicate in ordered[1:]:
            duplicate["duplicate_of"] = canonical["item_id"]
        story_groups.append({
            "canonical_story_id": story_id,
            "canonical_item_id": canonical["item_id"],
            "item_ids": [item["item_id"] for item in ordered],
            "independence_groups": sorted({item["independence_group"] for item in ordered}),
            "publisher_ids": sorted({item["publisher_id"] for item in ordered}),
        })

    raw_items = [item for group in groups.values() for item in group]
    independent_groups = {item["independence_group"] for item in raw_items}
    payload_without_id = {
        "schema_version": 1,
        "status": "available" if canonical_items else "insufficient_data",
        "item_count": len(raw_items),
        "canonical_story_count": len(canonical_items),
        "duplicate_item_count": len(raw_items) - len(canonical_items),
        "source_group_count": len(independent_groups),
        "independent_publisher_count": len(independent_groups),
        "items": sorted(enriched, key=lambda item: item["item_id"]),
        "canonical_items": sorted(canonical_items, key=lambda item: item["item_id"]),
        "story_groups": story_groups,
    }
    pack_id = hashlib.sha256(
        json.dumps(payload_without_id, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = {"pack_id": pack_id, **payload_without_id}
    validate_contract("canonical-evidence", payload)
    return payload


def _canonical_rank(item: Mapping[str, Any]) -> tuple[int, float, str]:
    tier = _TIER_PRIORITY.get(str(item.get("source_tier") or "unknown"), 5)
    published = str(item.get("published_at") or "")
    try:
        timestamp = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        timestamp = 0.0
    return tier, -timestamp, str(item.get("item_id") or "")


def _canonical_url(value: str) -> str:
    if not value:
        return value
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return value
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_KEYS
    ]
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path, urlencode(query), ""))


def _content_hash(item: Mapping[str, Any]) -> str:
    existing = str(item.get("content_sha256") or "").strip().casefold()
    if re.fullmatch(r"[a-f0-9]{64}", existing):
        return existing
    content = "\n".join(str(item.get(field) or "").strip() for field in ("title", "summary", "content"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _story_key(item: Mapping[str, Any]) -> str:
    title = str(item.get("title") or "").casefold()
    title = re.sub(r"\s+-\s+(?:aol\.com|yahoo finance|gurufocus|motley fool)$", "", title)
    normalized = re.sub(r"[^a-z0-9]+", " ", title).strip()
    if normalized:
        return normalized
    return _content_hash(item)
