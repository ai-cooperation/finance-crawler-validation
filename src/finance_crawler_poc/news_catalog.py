from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
REGION_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")
LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
BRAND_CLASSES = frozenset({"finance_specialist", "general_finance_desk"})
TRANSPORTS = frozenset({"browser", "json_api", "rss", "static_html"})
CATALOG_STATUSES = frozenset({"draft", "complete"})


class NewsCatalogError(ValueError):
    """Raised when the unique-news-brand contract is invalid."""


@dataclass(frozen=True)
class NewsTarget:
    total_brands: int
    finance_specialist: int
    general_finance_desk: int


@dataclass(frozen=True)
class NewsEndpoint:
    id: str
    transport: str
    url: str
    required_capabilities: frozenset[str]
    relay_path: str = ""
    # Alternative routes are attempted only after all primary routes fail.
    # They remain attached to the same publisher brand, so brand-level
    # success is still counted once and the route used is auditable.
    fallback_rank: int = 1


@dataclass(frozen=True)
class NewsBrand:
    id: str
    name: str
    canonical_domain: str
    brand_class: str
    region: str
    languages: tuple[str, ...]
    endpoints: tuple[NewsEndpoint, ...]
    alternative_endpoints: tuple[NewsEndpoint, ...] = ()

    @property
    def all_endpoints(self) -> tuple[NewsEndpoint, ...]:
        return self.endpoints + self.alternative_endpoints


@dataclass(frozen=True)
class NewsCatalog:
    version: int
    status: str
    target: NewsTarget
    brands: tuple[NewsBrand, ...]

    @property
    def brand_count(self) -> int:
        # INVARIANT: one publisher brand is one source. Adding RSS/API/Browser
        # endpoints must never increase this denominator.
        return len(self.brands)

    @property
    def endpoint_count(self) -> int:
        # Primary catalog size is frozen for historical P2 comparisons.
        return sum(len(brand.endpoints) for brand in self.brands)

    @property
    def alternative_endpoint_count(self) -> int:
        return sum(len(brand.alternative_endpoints) for brand in self.brands)

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"


def load_news_catalog(path: Path) -> NewsCatalog:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise NewsCatalogError(f"cannot read news catalog: {exc}") from exc
    if not isinstance(raw, dict):
        raise NewsCatalogError("news catalog root must be a mapping")
    if raw.get("version") != 1:
        raise NewsCatalogError("news catalog version must be 1")

    status = raw.get("status")
    if status not in CATALOG_STATUSES:
        raise NewsCatalogError(f"status must be one of {sorted(CATALOG_STATUSES)}")
    target = _parse_target(raw.get("target"))
    brand_items = raw.get("brands")
    if not isinstance(brand_items, list):
        raise NewsCatalogError("brands must be a list")

    brands: list[NewsBrand] = []
    brand_ids: set[str] = set()
    domains: set[str] = set()
    endpoint_ids: set[str] = set()
    for index, item in enumerate(brand_items):
        brand = _parse_brand(item, index)
        if brand.id in brand_ids:
            raise NewsCatalogError(f"duplicate brand id: {brand.id}")
        if brand.canonical_domain in domains:
            raise NewsCatalogError(
                f"duplicate canonical domain: {brand.canonical_domain}"
            )
        duplicates = endpoint_ids.intersection(endpoint.id for endpoint in brand.all_endpoints)
        if duplicates:
            raise NewsCatalogError(f"duplicate endpoint id: {sorted(duplicates)[0]}")
        brand_ids.add(brand.id)
        domains.add(brand.canonical_domain)
        endpoint_ids.update(endpoint.id for endpoint in brand.all_endpoints)
        brands.append(brand)

    catalog = NewsCatalog(version=1, status=status, target=target, brands=tuple(brands))
    _validate_catalog_size(catalog)
    return catalog


def _parse_target(raw: Any) -> NewsTarget:
    if not isinstance(raw, dict):
        raise NewsCatalogError("target must be a mapping")
    values: dict[str, int] = {}
    for key in ("total_brands", "finance_specialist", "general_finance_desk"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise NewsCatalogError(f"target {key} must be a non-negative integer")
        values[key] = value
    if values["finance_specialist"] + values["general_finance_desk"] != values[
        "total_brands"
    ]:
        raise NewsCatalogError(
            f"target composition must sum to {values['total_brands']}"
        )
    return NewsTarget(**values)


def _parse_brand(raw: Any, index: int) -> NewsBrand:
    if not isinstance(raw, dict):
        raise NewsCatalogError(f"brand {index} must be a mapping")
    brand_id = _required_string(raw, "id", f"brand {index}")
    if not ID_PATTERN.fullmatch(brand_id):
        raise NewsCatalogError(f"brand {brand_id} has invalid id")
    name = _required_string(raw, "name", f"brand {brand_id}")
    canonical_domain = _required_string(
        raw, "canonical_domain", f"brand {brand_id}"
    ).lower()
    _validate_domain(canonical_domain, brand_id)
    brand_class = _required_string(raw, "brand_class", f"brand {brand_id}")
    if brand_class not in BRAND_CLASSES:
        raise NewsCatalogError(
            f"brand {brand_id} brand_class must be one of {sorted(BRAND_CLASSES)}"
        )
    region = _required_string(raw, "region", f"brand {brand_id}")
    if not REGION_PATTERN.fullmatch(region):
        raise NewsCatalogError(f"brand {brand_id} has invalid region")

    languages_raw = raw.get("languages")
    if not isinstance(languages_raw, list) or not languages_raw:
        raise NewsCatalogError(f"brand {brand_id} languages must be a non-empty list")
    if not all(
        isinstance(language, str) and LANGUAGE_PATTERN.fullmatch(language)
        for language in languages_raw
    ):
        raise NewsCatalogError(f"brand {brand_id} has invalid language code")

    endpoint_items = raw.get("endpoints")
    if not isinstance(endpoint_items, list) or not endpoint_items:
        raise NewsCatalogError(f"brand {brand_id} endpoints must be non-empty")
    endpoints = tuple(
        _parse_endpoint(item, brand_id, endpoint_index, fallback_rank=1)
        for endpoint_index, item in enumerate(endpoint_items)
    )
    local_ids = [endpoint.id for endpoint in endpoints]
    if len(local_ids) != len(set(local_ids)):
        raise NewsCatalogError(f"duplicate endpoint id in brand {brand_id}")

    alternatives_raw = raw.get("alternative_endpoints", [])
    if not isinstance(alternatives_raw, list):
        raise NewsCatalogError(f"brand {brand_id} alternative_endpoints must be a list")
    alternatives = tuple(
        _parse_endpoint(item, brand_id, endpoint_index, fallback_rank=2)
        for endpoint_index, item in enumerate(alternatives_raw)
    )
    local_all_ids = [endpoint.id for endpoint in (*endpoints, *alternatives)]
    if len(local_all_ids) != len(set(local_all_ids)):
        raise NewsCatalogError(f"duplicate endpoint id in brand {brand_id}")

    return NewsBrand(
        id=brand_id,
        name=name,
        canonical_domain=canonical_domain,
        brand_class=brand_class,
        region=region,
        languages=tuple(languages_raw),
        endpoints=endpoints,
        alternative_endpoints=alternatives,
    )


def _parse_endpoint(raw: Any, brand_id: str, index: int, *, fallback_rank: int = 1) -> NewsEndpoint:
    if not isinstance(raw, dict):
        raise NewsCatalogError(f"brand {brand_id} endpoint {index} must be a mapping")
    endpoint_id = _required_string(raw, "id", f"brand {brand_id} endpoint {index}")
    if not ID_PATTERN.fullmatch(endpoint_id):
        raise NewsCatalogError(f"endpoint {endpoint_id} has invalid id")
    transport = _required_string(raw, "transport", f"endpoint {endpoint_id}")
    if transport not in TRANSPORTS:
        raise NewsCatalogError(
            f"endpoint {endpoint_id} transport must be one of {sorted(TRANSPORTS)}"
        )
    url = _required_string(raw, "url", f"endpoint {endpoint_id}")
    _validate_public_url(url, endpoint_id)
    capabilities_raw = raw.get("required_capabilities")
    if not isinstance(capabilities_raw, list) or not capabilities_raw:
        raise NewsCatalogError(
            f"endpoint {endpoint_id} required_capabilities must be non-empty"
        )
    if not all(
        isinstance(capability, str) and ID_PATTERN.fullmatch(capability)
        for capability in capabilities_raw
    ):
        raise NewsCatalogError(f"endpoint {endpoint_id} has invalid capability")
    relay_path = raw.get("relay_path", "")
    if not isinstance(relay_path, str):
        raise NewsCatalogError(f"endpoint {endpoint_id} relay_path must be a string")
    relay_path = relay_path.strip()
    if relay_path and transport not in {"rss", "json_api", "static_html"}:
        raise NewsCatalogError(
            "relay_path is allowed only for rss, json_api, or static_html endpoints"
        )
    if relay_path and relay_path != f"/v1/feed/{endpoint_id}":
        raise NewsCatalogError(
            f"endpoint {endpoint_id} relay_path must match endpoint id"
        )
    return NewsEndpoint(
        id=endpoint_id,
        transport=transport,
        url=url,
        required_capabilities=frozenset(capabilities_raw),
        relay_path=relay_path,
        fallback_rank=fallback_rank,
    )


def _validate_catalog_size(catalog: NewsCatalog) -> None:
    counts = {
        brand_class: sum(
            brand.brand_class == brand_class for brand in catalog.brands
        )
        for brand_class in BRAND_CLASSES
    }
    if catalog.brand_count > catalog.target.total_brands:
        raise NewsCatalogError("draft catalog exceeds target brand count")
    if any(
        counts[brand_class] > getattr(catalog.target, brand_class)
        for brand_class in BRAND_CLASSES
    ):
        raise NewsCatalogError("draft catalog exceeds target brand composition")
    if catalog.status != "complete":
        return
    if catalog.brand_count != catalog.target.total_brands:
        raise NewsCatalogError(
            f"complete catalog must contain {catalog.target.total_brands} brands"
        )
    for brand_class in BRAND_CLASSES:
        expected = getattr(catalog.target, brand_class)
        if counts[brand_class] != expected:
            raise NewsCatalogError(
                f"complete catalog must contain {expected} {brand_class} brands"
            )


def _required_string(raw: dict[str, Any], key: str, owner: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise NewsCatalogError(f"{owner} field {key} must be a non-empty string")
    return value.strip()


def _validate_domain(domain: str, brand_id: str) -> None:
    parsed = urlparse(f"//{domain}")
    if (
        not parsed.hostname
        or parsed.hostname != domain
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise NewsCatalogError(f"brand {brand_id} canonical_domain must be a hostname")
    _validate_public_host(parsed.hostname, f"brand {brand_id}")


def _validate_public_url(url: str, endpoint_id: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise NewsCatalogError(f"endpoint {endpoint_id} url must use http or https")
    if parsed.username or parsed.password:
        raise NewsCatalogError(f"endpoint {endpoint_id} url must not contain credentials")
    _validate_public_host(parsed.hostname, f"endpoint {endpoint_id}")


def _validate_public_host(hostname: str, owner: str) -> None:
    lowered = hostname.lower()
    if lowered == "localhost" or lowered.endswith(".local"):
        raise NewsCatalogError(f"{owner} must target a public host")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return
    if not address.is_global:
        raise NewsCatalogError(f"{owner} must target a public host")
