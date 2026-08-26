"""Versioned provider capabilities discovered from public GitHub inventories.

Discovery proves only that a candidate exists.  Executability is a separate,
strictly validated integration state so an awesome-list row can never become a
collector route by accident.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from string import Formatter
from typing import Any
from urllib.parse import quote_plus

import yaml

from finance_crawler_poc.contracts import validate_contract


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "provider-catalog.yaml"
CALLABLE_STATUSES = frozenset({"verified_public", "verified_requires_key"})
NONCALLABLE_STATUSES = frozenset({
    "catalogued_unverified",
    "adapter_required",
    "commercial_only",
    "blocked",
    "deprecated",
})
SUPPORT_WEIGHTS = {"exact": 30, "derived": 20, "proxy": 10}
TIER_WEIGHTS = {
    "official": 30,
    "regulatory": 30,
    "direct_primary": 22,
    "direct_secondary": 14,
    "aggregator": 6,
    "unknown": 0,
}
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{1,127}$")


class ProviderCatalogError(ValueError):
    """Raised when catalog data or provider route parameters are unsafe."""


def load_provider_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    """Load, normalize and mechanically validate the provider catalog."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProviderCatalogError(f"cannot load provider catalog: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ProviderCatalogError("provider catalog root must be an object")
    normalized = _normalize_catalog(raw)
    _validate_invariants(normalized)
    validate_contract("provider-catalog", normalized)
    return normalized


def _normalize_catalog(raw: Mapping[str, Any]) -> dict[str, Any]:
    defaults = raw.get("provider_defaults")
    if defaults is not None and not isinstance(defaults, Mapping):
        raise ProviderCatalogError("provider_defaults must be an object")
    providers = raw.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ProviderCatalogError("providers must be a non-empty list")
    normalized = {
        "schema_version": raw.get("schema_version"),
        "catalog_id": raw.get("catalog_id"),
        "generated_at": raw.get("generated_at"),
        "scope": raw.get("scope"),
        "discovery_sources": deepcopy(raw.get("discovery_sources")),
        "providers": [_normalize_provider(item, defaults or {}) for item in providers],
    }
    normalized["providers"] = sorted(normalized["providers"], key=lambda item: item["provider_id"])
    return normalized


def _normalize_provider(raw: object, defaults: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ProviderCatalogError("provider entries must be objects")
    provider_id = str(raw.get("provider_id") or "").strip().casefold()
    publisher_id = str(raw.get("publisher_id") or provider_id).strip().casefold()
    independence_group = str(raw.get("independence_group") or publisher_id).strip().casefold()
    metrics = raw.get("metrics") or []
    metric_support: list[dict[str, str]] = []
    if not isinstance(metrics, list):
        raise ProviderCatalogError(f"metrics must be a list for {provider_id}")
    for value in metrics:
        if isinstance(value, str):
            metric_support.append({"metric_id": value, "support_level": "exact"})
        elif isinstance(value, Mapping):
            row = {
                "metric_id": str(value.get("metric_id") or ""),
                "support_level": str(value.get("support_level") or "exact"),
            }
            if value.get("notes"):
                row["notes"] = str(value["notes"])
            metric_support.append(row)
        else:
            raise ProviderCatalogError(f"invalid metric entry for {provider_id}")
    access = {**dict(defaults.get("access") or {}), **dict(raw.get("access") or {})}
    rights = {**dict(defaults.get("rights") or {}), **dict(raw.get("rights") or {})}
    rights.setdefault("terms_url", str(raw.get("documentation_url") or raw.get("homepage_url") or ""))
    integration = {**dict(defaults.get("integration") or {}), **dict(raw.get("integration") or {})}
    if integration.get("callable") is True and integration.get("last_verified_at") and not integration.get("verification_method"):
        integration["verification_method"] = "official_documentation"
    return {
        "provider_id": provider_id,
        "name": str(raw.get("name") or provider_id),
        "provider_type": str(raw.get("provider_type") or defaults.get("provider_type") or "publisher"),
        "publisher_id": publisher_id,
        "independence_group": independence_group,
        "source_tier": str(raw.get("source_tier") or defaults.get("source_tier") or "unknown"),
        "discovered_via": sorted({str(value) for value in raw.get("discovered_via", [])}),
        "homepage_url": str(raw.get("homepage_url") or ""),
        "documentation_url": str(raw.get("documentation_url") or raw.get("homepage_url") or ""),
        "categories": sorted({str(value) for value in raw.get("categories", ["finance"])}),
        "requirement_ids": sorted({str(value) for value in raw.get("requirement_ids", [])}),
        "metric_support": sorted(metric_support, key=lambda item: (item["metric_id"], item["support_level"])),
        "geographies": list(dict.fromkeys(str(value) for value in raw.get("geographies", ["global"]))),
        "asset_classes": sorted({str(value) for value in raw.get("asset_classes", ["equity"])}),
        "languages": sorted({str(value) for value in raw.get("languages", ["en"])}),
        "access": access,
        "rights": rights,
        "integration": integration,
    }


def _validate_invariants(catalog: Mapping[str, Any]) -> None:
    discovery = catalog.get("discovery_sources")
    if not isinstance(discovery, list):
        raise ProviderCatalogError("discovery_sources must be a list")
    discovery_ids = {str(item.get("discovery_id")) for item in discovery if isinstance(item, Mapping)}
    seen: set[str] = set()
    for provider in catalog.get("providers", []):
        provider_id = str(provider.get("provider_id") or "")
        if not _IDENTIFIER.fullmatch(provider_id):
            raise ProviderCatalogError(f"invalid provider_id: {provider_id!r}")
        if provider_id in seen:
            raise ProviderCatalogError(f"duplicate provider_id: {provider_id}")
        seen.add(provider_id)
        if not set(provider.get("discovered_via") or []) <= discovery_ids:
            raise ProviderCatalogError(f"unknown discovery source for {provider_id}")
        integration = provider.get("integration") or {}
        access = provider.get("access") or {}
        status = str(integration.get("status") or "")
        callable_flag = integration.get("callable") is True
        if callable_flag and status not in CALLABLE_STATUSES:
            raise ProviderCatalogError(f"non-verified provider cannot be callable: {provider_id}")
        if status in NONCALLABLE_STATUSES and callable_flag:
            raise ProviderCatalogError(f"blocked provider cannot be callable: {provider_id}")
        if callable_flag:
            if integration.get("adapter") == "none" or not integration.get("endpoint_template"):
                raise ProviderCatalogError(f"callable provider needs adapter and endpoint: {provider_id}")
        if status == "verified_public" and access.get("auth") != "none":
            raise ProviderCatalogError(f"verified_public provider must not require auth: {provider_id}")
        if status == "verified_requires_key" and not integration.get("credential_env"):
            raise ProviderCatalogError(f"credential env missing for {provider_id}")
        if integration.get("auth_injection") in {"header", "bearer_header"} and not integration.get("auth_field"):
            raise ProviderCatalogError(f"auth header name missing for {provider_id}")
        if provider.get("rights", {}).get("public_raw_storage") == "allowed" and access.get("cost_tier") == "paid":
            raise ProviderCatalogError(f"paid provider raw data cannot be marked public: {provider_id}")


def query_providers(
    catalog: Mapping[str, Any], *, metric_ids: Iterable[str] = (), requirement_ids: Iterable[str] = (),
    geographies: Iterable[str] = (), support_levels: set[str] | None = None, callable_only: bool = False,
) -> list[dict[str, Any]]:
    """Return ranked providers matching every requested metric or requirement."""

    wanted_metrics = {str(value) for value in metric_ids if str(value)}
    wanted_requirements = {str(value) for value in requirement_ids if str(value)}
    wanted_geographies = [str(value) for value in geographies if str(value)]
    allowed_support = support_levels or set(SUPPORT_WEIGHTS)
    rows: list[dict[str, Any]] = []
    for value in catalog.get("providers", []):
        if not isinstance(value, Mapping):
            continue
        provider = deepcopy(dict(value))
        integration = provider.get("integration") if isinstance(provider.get("integration"), Mapping) else {}
        if callable_only and integration.get("callable") is not True:
            continue
        metric_levels = {
            str(item.get("metric_id")): str(item.get("support_level"))
            for item in provider.get("metric_support", [])
            if isinstance(item, Mapping) and item.get("support_level") in allowed_support
        }
        if wanted_metrics and not wanted_metrics <= metric_levels.keys():
            continue
        if wanted_requirements and not wanted_requirements & set(provider.get("requirement_ids") or []):
            continue
        provider_geographies = [str(value) for value in provider.get("geographies") or []]
        if wanted_geographies and not ({item.casefold() for item in wanted_geographies} & {item.casefold() for item in provider_geographies} or "global" in {item.casefold() for item in provider_geographies}):
            continue
        support_score = min((SUPPORT_WEIGHTS[metric_levels[metric]] for metric in wanted_metrics), default=0)
        geography_score = _geography_score(provider_geographies, wanted_geographies)
        rank_score = (
            support_score
            + geography_score
            + TIER_WEIGHTS.get(str(provider.get("source_tier")), 0)
            + (10 if integration.get("callable") is True else 0)
        )
        provider["matched_metric_support"] = {metric: metric_levels[metric] for metric in sorted(wanted_metrics)}
        provider["rank_score"] = rank_score
        rows.append(provider)
    return sorted(rows, key=lambda item: (-int(item["rank_score"]), item["provider_id"]))


def _geography_score(provider_geographies: list[str], wanted: list[str]) -> int:
    provider_set = {value.casefold() for value in provider_geographies}
    for index, value in enumerate(wanted):
        if value.casefold() in provider_set:
            return max(20, 50 - index * 10)
    if "global" in provider_set:
        return 10
    return 0


def providers_for_gaps(
    catalog: Mapping[str, Any], *, gaps: Iterable[Mapping[str, Any]], configured_credentials: set[str],
    route_parameters: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build a Broker handoff without promoting blocked candidates to routes."""

    result: dict[str, list[dict[str, Any]]] = {}
    for gap in gaps:
        requirement_id = str(gap.get("requirement_id") or "")
        missing_metrics = [str(value) for value in gap.get("missing_metrics") or [] if str(value)]
        by_provider: dict[str, dict[str, Any]] = {}
        metric_batches = [[metric] for metric in missing_metrics] or [[]]
        for metric_batch in metric_batches:
            for provider in query_providers(
                catalog,
                metric_ids=metric_batch,
                requirement_ids=[requirement_id],
                geographies=gap.get("missing_geographies") or [],
            ):
                provider_id = str(provider["provider_id"])
                previous = by_provider.get(provider_id)
                if previous is None:
                    by_provider[provider_id] = provider
                    continue
                previous["matched_metric_support"].update(provider["matched_metric_support"])
                previous["rank_score"] = max(int(previous["rank_score"]), int(provider["rank_score"]))
        rows = sorted(
            by_provider.values(),
            key=lambda item: (
                -len(item["matched_metric_support"]),
                -int(item["rank_score"]),
                item["provider_id"],
            ),
        )
        handoff: list[dict[str, Any]] = []
        for provider in rows:
            integration = provider["integration"]
            reasons: list[str] = []
            if integration.get("callable") is not True:
                reasons.append("provider_not_callable")
            credential = str(integration.get("credential_env") or "")
            if credential and credential not in configured_credentials:
                reasons.append("credential_not_configured")
            supplied_parameters = set((route_parameters or {}).get(provider["provider_id"], {}))
            required_parameters = set(integration.get("required_parameters") or [])
            if credential and credential in configured_credentials:
                required_parameters.discard("api_key")
            missing_parameters = sorted(required_parameters - supplied_parameters)
            if missing_parameters:
                reasons.append("route_parameters_missing")
            auth_injection = str(integration.get("auth_injection") or "")
            auth_field = str(integration.get("auth_field") or "")
            handoff.append({
                "provider_id": provider["provider_id"],
                "rank_score": provider["rank_score"],
                "covered_metric_count": len(provider["matched_metric_support"]),
                "matched_metric_support": provider["matched_metric_support"],
                "transport": provider["access"]["transports"][0],
                "adapter": integration["adapter"],
                "endpoint_template": integration.get("endpoint_template", ""),
                **({"auth_injection": auth_injection} if auth_injection else {}),
                **({"auth_field": auth_field} if auth_field else {}),
                "missing_parameters": missing_parameters,
                "callable_now": not reasons,
                "blocked_reasons": reasons,
            })
        result[requirement_id] = handoff
    return result


def render_provider_url(provider: Mapping[str, Any], parameters: Mapping[str, object]) -> str:
    """Render one allow-listed endpoint template with URL-encoded values."""

    integration = provider.get("integration") if isinstance(provider.get("integration"), Mapping) else {}
    if integration.get("callable") is not True:
        raise ProviderCatalogError("provider is not callable")
    template = str(integration.get("endpoint_template") or "")
    fields = {name for _, name, _, _ in Formatter().parse(template) if name}
    required = set(integration.get("required_parameters") or fields)
    optional = set(integration.get("optional_parameters") or [])
    supplied = {str(key) for key in parameters}
    missing = required - supplied
    unknown = supplied - required - optional
    if missing:
        raise ProviderCatalogError(f"missing template parameters: {sorted(missing)}")
    if unknown:
        raise ProviderCatalogError(f"unknown template parameters: {sorted(unknown)}")
    encoded = {key: quote_plus(str(value), safe="") for key, value in parameters.items()}
    try:
        return template.format(**encoded)
    except KeyError as exc:
        raise ProviderCatalogError(f"missing template parameter: {exc.args[0]}") from exc
