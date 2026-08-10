from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from finance_crawler_poc.contracts import ContractValidationError, validate_contract


EXTRACTOR_TRANSPORTS = {
    "browser_document": "browser",
    "coingecko_markets": "json_api",
    "github_issues": "json_api",
    "hn_algolia": "json_api",
    "rss": "rss",
    "stackexchange": "json_api",
    "world_bank": "json_api",
}
SOURCE_SCHEMA_KEYS = frozenset(
    {
        "schema_version",
        "source_id",
        "name",
        "kind",
        "layer",
        "transport",
        "canonical_url",
        "freshness_sla_minutes",
        "rights",
    }
)
SOURCE_KEYS = SOURCE_SCHEMA_KEYS | {"extractor", "max_items", "timeout_seconds"}


class RadarManifestError(ValueError):
    """Raised when a topic-radar source manifest violates its boundary contract."""


@dataclass(frozen=True)
class RadarSource:
    schema_version: int
    source_id: str
    name: str
    kind: str
    layer: str
    transport: str
    canonical_url: str
    freshness_sla_minutes: int
    rights: dict[str, object]
    extractor: str
    max_items: int
    timeout_seconds: int


@dataclass(frozen=True)
class RadarManifest:
    version: int
    minimum_successful_sources: int
    maximum_items_per_run: int
    sources: tuple[RadarSource, ...]


def load_radar_manifest(path: Path) -> RadarManifest:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RadarManifestError(f"cannot read radar manifest: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "version",
        "minimum_successful_sources",
        "maximum_items_per_run",
        "sources",
    }:
        raise RadarManifestError("radar manifest root has invalid fields")
    if raw["version"] != 1:
        raise RadarManifestError("radar manifest version must be 1")
    source_items = raw["sources"]
    if not isinstance(source_items, list) or not 12 <= len(source_items) <= 20:
        raise RadarManifestError("radar vertical slice must contain 12 to 20 sources")

    sources = tuple(_parse_source(item, index) for index, item in enumerate(source_items))
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise RadarManifestError("radar source_id values must be unique")

    minimum = _bounded_integer(
        raw["minimum_successful_sources"], "minimum_successful_sources", 1, len(sources)
    )
    maximum = _bounded_integer(
        raw["maximum_items_per_run"], "maximum_items_per_run", 1, 100
    )
    if sum(source.max_items for source in sources) > maximum:
        raise RadarManifestError("source max_items exceed maximum_items_per_run")
    return RadarManifest(
        version=1,
        minimum_successful_sources=minimum,
        maximum_items_per_run=maximum,
        sources=sources,
    )


def _parse_source(raw: Any, index: int) -> RadarSource:
    if not isinstance(raw, dict) or set(raw) != SOURCE_KEYS:
        raise RadarManifestError(f"source {index} has invalid fields")
    record = {key: raw[key] for key in SOURCE_SCHEMA_KEYS}
    try:
        validate_contract("source-record", record)
    except ContractValidationError as exc:
        raise RadarManifestError(str(exc)) from exc
    source_id = str(raw["source_id"])
    _validate_public_url(str(raw["canonical_url"]), source_id)

    extractor = raw["extractor"]
    if extractor not in EXTRACTOR_TRANSPORTS:
        raise RadarManifestError(f"source {source_id} has unknown extractor")
    if raw["transport"] != EXTRACTOR_TRANSPORTS[extractor]:
        raise RadarManifestError(f"source {source_id} extractor and transport disagree")
    max_items = _bounded_integer(raw["max_items"], f"source {source_id} max_items", 1, 10)
    timeout = _bounded_integer(
        raw["timeout_seconds"], f"source {source_id} timeout_seconds", 5, 60
    )
    return RadarSource(
        schema_version=1,
        source_id=source_id,
        name=str(raw["name"]),
        kind=str(raw["kind"]),
        layer=str(raw["layer"]),
        transport=str(raw["transport"]),
        canonical_url=str(raw["canonical_url"]),
        freshness_sla_minutes=int(raw["freshness_sla_minutes"]),
        rights=dict(raw["rights"]),
        extractor=str(extractor),
        max_items=max_items,
        timeout_seconds=timeout,
    )


def _bounded_integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RadarManifestError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def _validate_public_url(value: str, source_id: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RadarManifestError(f"source {source_id} must use a public HTTPS URL")
    lowered = parsed.hostname.lower()
    if lowered == "localhost" or lowered.endswith(".local"):
        raise RadarManifestError(f"source {source_id} must target a public host")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return
    if not address.is_global:
        raise RadarManifestError(f"source {source_id} must target a public host")
