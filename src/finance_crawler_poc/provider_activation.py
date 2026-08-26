"""Activation contracts for provider candidates that are not data routes yet.

The provider catalog records discovery and research relevance.  This module
adds the missing operational handoff: every non-route-integrated provider gets
an adapter family, execution boundary, health probe and exact next action.
Control-plane reachability is deliberately kept separate from proof that a
research payload can be collected and normalized.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from collections.abc import Mapping
from typing import Any

import httpx


NON_ROUTE_INTEGRATED_STATUSES = frozenset({
    "catalogued_unverified",
    "adapter_required",
    "commercial_only",
    "blocked",
    "deprecated",
})
NON_EXECUTABLE_STATUSES = frozenset({"commercial_only", "blocked", "deprecated"})

# Fixed, low-volume probes that return actual source data rather than a docs or
# marketing page.  A successful probe proves only transport/payload survival;
# it does not prove semantic parsing, target relevance or redistribution rights.
DATA_PAYLOAD_PROBES: dict[str, str] = {
    "census": "https://api.census.gov/data/2023/cbp?get=NAME,EMP&for=us:*",
    "cftc": "https://publicreporting.cftc.gov/resource/jun7-fc8e.json?$limit=1",
    "coincap": "https://api.coincap.io/v2/assets?limit=1",
    "defillama": "https://api.llama.fi/protocols",
    "ecb": (
        "https://data-api.ecb.europa.eu/service/data/EXR/"
        "D.USD.EUR.SP00.A?startPeriod=2026-08-01&format=csvdata"
    ),
    "eurostat": (
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
        "namq_10_gdp?geo=EU27_2020&na_item=B1GQ&unit=CLV10_MEUR"
    ),
    "famafrench": (
        "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
        "F-F_Research_Data_Factors_CSV.zip"
    ),
    "fed_treasury": (
        "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/"
        "accounting/od/rates_of_exchange?page%5Bsize%5D=1"
    ),
    "federal_reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "imf": "https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH",
    "sec_edgar": "https://data.sec.gov/submissions/CIK0001046179.json",
}
DATA_PAYLOAD_FORMATS = {
    "census": "json",
    "cftc": "json",
    "coincap": "json",
    "defillama": "json",
    "ecb": "csv",
    "eurostat": "json",
    "famafrench": "zip",
    "fed_treasury": "json",
    "federal_reserve": "rss",
    "imf": "json",
    "sec_edgar": "json",
}
MAX_PROBE_SAMPLE_BYTES = 65_536


def build_provider_activation_registry(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Build one auditable connector contract per non-route provider."""

    connections = [
        _build_connection(provider)
        for provider in catalog.get("providers", [])
        if isinstance(provider, Mapping)
        and str((provider.get("integration") or {}).get("status"))
        in NON_ROUTE_INTEGRATED_STATUSES
    ]
    return {
        "schema_version": 1,
        "registry_id": "investment_research_provider_activation_v1",
        "generated_at": catalog.get("generated_at"),
        "catalog_id": catalog.get("catalog_id"),
        "connections": sorted(connections, key=lambda row: row["provider_id"]),
    }


def summarize_activation_registry(registry: Mapping[str, Any]) -> dict[str, int]:
    rows = [row for row in registry.get("connections", []) if isinstance(row, Mapping)]
    policies = Counter(str(row.get("execution_policy")) for row in rows)
    scopes = Counter(str((row.get("probe") or {}).get("scope")) for row in rows)
    return {
        "total": len(rows),
        "technically_connectable": len(rows) - policies["not_executable"],
        "not_executable": policies["not_executable"],
        "data_payload_probe_defined": scopes["data_payload"],
        "control_plane_probe_only": scopes["control_plane"],
    }


def build_provider_runtime_registry(
    catalog: Mapping[str, Any],
    activation_registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the public, secret-free provider registry embedded in the Worker."""

    activation = {
        str(row.get("provider_id")): dict(row)
        for row in activation_registry.get("connections", [])
        if isinstance(row, Mapping)
    }
    providers: list[dict[str, Any]] = []
    route_integrated = 0
    for provider in catalog.get("providers", []):
        if not isinstance(provider, Mapping):
            continue
        provider_id = str(provider["provider_id"])
        integration = provider.get("integration") if isinstance(provider.get("integration"), Mapping) else {}
        access = provider.get("access") if isinstance(provider.get("access"), Mapping) else {}
        rights = provider.get("rights") if isinstance(provider.get("rights"), Mapping) else {}
        connection = activation.get(provider_id)
        if connection is None:
            route_integrated += 1
            credential_env = str(integration.get("credential_env") or "")
            transports = [str(value) for value in access.get("transports", [])]
            connection = {
                "provider_id": provider_id,
                "catalog_status": integration.get("status"),
                "execution_policy": "enabled_if_configured" if credential_env else "enabled",
                "data_plane_state": "route_integrated",
                "adapter": integration.get("adapter"),
                "runtime": _runtime(transports),
                "transports": transports,
                "required_configuration": [credential_env] if credential_env else [],
                "endpoint_template": integration.get("endpoint_template"),
                **({"auth_injection": integration.get("auth_injection")} if integration.get("auth_injection") else {}),
                **({"auth_field": integration.get("auth_field")} if integration.get("auth_field") else {}),
                "last_verified_at": integration.get("last_verified_at"),
                "next_action": (
                    "Configure the named credential and execute the target-scoped route."
                    if credential_env
                    else "Execute the target-scoped route and retain freshness evidence."
                ),
            }
        providers.append({
            "provider_id": provider_id,
            "name": provider.get("name"),
            "provider_type": provider.get("provider_type"),
            "source_tier": provider.get("source_tier"),
            "homepage_url": provider.get("homepage_url"),
            "documentation_url": provider.get("documentation_url"),
            "categories": provider.get("categories", []),
            "requirement_ids": provider.get("requirement_ids", []),
            "metric_support": provider.get("metric_support", []),
            "geographies": provider.get("geographies", []),
            "asset_classes": provider.get("asset_classes", []),
            "languages": provider.get("languages", []),
            "access": {
                "transports": access.get("transports", []),
                "auth": access.get("auth"),
                "cost_tier": access.get("cost_tier"),
            },
            "rights": {"public_raw_storage": rights.get("public_raw_storage")},
            "integration": {
                "status": integration.get("status"),
                "callable": integration.get("callable") is True,
            },
            "connection": connection,
        })
    activation_summary = summarize_activation_registry(activation_registry)
    return {
        "schema_version": 1,
        "registry_id": "investment_research_provider_runtime_v1",
        "generated_at": catalog.get("generated_at"),
        "catalog_id": catalog.get("catalog_id"),
        "summary": {
            "total": len(providers),
            "route_integrated": route_integrated,
            "activation_backlog": activation_summary["total"],
            "technically_connectable_backlog": activation_summary["technically_connectable"],
            "not_executable": activation_summary["not_executable"],
        },
        "providers": sorted(providers, key=lambda row: row["provider_id"]),
    }


def _build_connection(provider: Mapping[str, Any]) -> dict[str, Any]:
    provider_id = str(provider["provider_id"])
    integration = provider.get("integration") if isinstance(provider.get("integration"), Mapping) else {}
    access = provider.get("access") if isinstance(provider.get("access"), Mapping) else {}
    status = str(integration.get("status") or "catalogued_unverified")
    transports = [str(value) for value in access.get("transports", [])]
    probe_url = DATA_PAYLOAD_PROBES.get(provider_id) or str(
        provider.get("documentation_url") or provider.get("homepage_url")
    )
    survival_url = str(provider.get("documentation_url") or provider.get("homepage_url"))
    scope = "data_payload" if provider_id in DATA_PAYLOAD_PROBES else "control_plane"
    execution_policy = "not_executable" if status in NON_EXECUTABLE_STATUSES else "probe_only"
    credential_env = str(integration.get("credential_env") or "")
    required_configuration = [credential_env] if credential_env else []
    if access.get("auth") in {"api_key", "oauth", "contact_identity"} and not credential_env:
        required_configuration.append(f"{provider_id.upper()}_AUTH_CONFIGURATION")
    return {
        "provider_id": provider_id,
        "catalog_status": status,
        "execution_policy": execution_policy,
        "data_plane_state": _data_plane_state(status, scope),
        "adapter": _adapter(integration, transports),
        "runtime": _runtime(transports),
        "transports": transports,
        "required_configuration": required_configuration,
        "probe": {
            "method": "GET",
            "url": probe_url,
            "scope": scope,
            "expected_content": DATA_PAYLOAD_FORMATS.get(provider_id, "any"),
            **({"survival_url": survival_url} if scope == "data_payload" and survival_url != probe_url else {}),
            "expected_statuses": [200, 401, 403],
            "max_attempts": 3,
            "timeout_seconds": 15,
        },
        "next_action": _next_action(status, scope),
    }


def _adapter(integration: Mapping[str, Any], transports: list[str]) -> str:
    declared = str(integration.get("adapter") or "none")
    if declared != "none":
        return declared
    if "json_api" in transports or "websocket" in transports:
        return "generic_json"
    if "rss" in transports:
        return "generic_rss"
    if "browser" in transports:
        return "generic_browser"
    if "file" in transports:
        return "generic_file"
    if "python_library" in transports:
        return "python_library"
    return "provider_specific"


def _runtime(transports: list[str]) -> str:
    action_only = {"browser", "file", "python_library"}
    worker_ready = {"json_api", "rss", "websocket"}
    transport_set = set(transports)
    if transport_set & action_only and transport_set & worker_ready:
        return "hybrid"
    if transport_set & action_only:
        return "github_actions"
    return "cloudflare_worker"


def _data_plane_state(status: str, scope: str) -> str:
    if status == "commercial_only":
        return "commercial_onboarding_required"
    if status == "blocked":
        return "policy_review_required"
    if status == "deprecated":
        return "retired"
    if scope == "data_payload":
        return "payload_probe_ready"
    if status == "adapter_required":
        return "adapter_contract_ready"
    return "survival_probe_ready"


def _next_action(status: str, scope: str) -> str:
    if status == "commercial_only":
        return "Obtain a commercial data contract and scoped credential before enabling any request."
    if status == "blocked":
        return "Complete terms, robots and redistribution review; keep execution disabled meanwhile."
    if status == "deprecated":
        return "Keep disabled and map every dependent requirement to a supported replacement provider."
    if scope == "data_payload":
        return "Run the bounded live payload probe, then add semantic parser and canonical-evidence tests."
    if status == "adapter_required":
        return "Resolve one stable data endpoint, run a bounded payload probe, then implement its parser."
    return "Verify service survival and official documentation before defining a data endpoint."


async def probe_activation_registry(
    registry: Mapping[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    checked_at: str,
    concurrency: int = 4,
) -> dict[str, Any]:
    """Probe every activation contract with bounded concurrency and retries.

    The probe reads at most ``MAX_PROBE_SAMPLE_BYTES`` from each response and
    never promotes a provider.  Promotion remains a separate reviewed catalog
    change after parser, rights and canonical-evidence tests pass.
    """

    if concurrency < 1 or concurrency > 8:
        raise ValueError("concurrency must be between 1 and 8")
    rows = [row for row in registry.get("connections", []) if isinstance(row, Mapping)]
    semaphore = asyncio.Semaphore(concurrency)
    owns_client = client is None
    resolved_client = client or httpx.AsyncClient(
        follow_redirects=True,
        headers={
            "Accept": "application/json,application/xml,text/xml,text/csv,*/*;q=0.5",
            "User-Agent": "finance-crawler-validation/1.0 (provider health verification)",
        },
    )
    try:
        results = await asyncio.gather(*[
            _probe_connection(row, resolved_client, semaphore) for row in rows
        ])
    finally:
        if owns_client:
            await resolved_client.aclose()
    counts = Counter(str(result["outcome"]) for result in results)
    return {
        "schema_version": 1,
        "registry_id": registry.get("registry_id"),
        "catalog_id": registry.get("catalog_id"),
        "checked_at": checked_at,
        "summary": {
            "total": len(results),
            "survival_verified": sum(bool(row["survival_verified"]) for row in results),
            "data_payload_verified": sum(bool(row["data_payload_verified"]) for row in results),
            "outcomes": dict(sorted(counts.items())),
        },
        "results": sorted(results, key=lambda row: row["provider_id"]),
    }


async def _probe_connection(
    connection: Mapping[str, Any],
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    probe = connection.get("probe") if isinstance(connection.get("probe"), Mapping) else {}
    max_attempts = min(3, max(1, int(probe.get("max_attempts") or 1)))
    timeout_seconds = min(30.0, max(1.0, float(probe.get("timeout_seconds") or 15)))
    statuses = {int(value) for value in probe.get("expected_statuses", [])}
    url = str(probe.get("url") or "")
    attempts = 0
    last: dict[str, Any] = {
        "outcome": "transport_error",
        "status_code": None,
        "final_url": url,
        "content_type": None,
        "sample_bytes": 0,
        "response_sha256": None,
        "error": "probe_not_started",
    }
    async with semaphore:
        while attempts < max_attempts:
            attempts += 1
            try:
                async with client.stream(
                    str(probe.get("method") or "GET"),
                    url,
                    timeout=timeout_seconds,
                    follow_redirects=True,
                ) as response:
                    sample = await _read_bounded_sample(response)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0]
                    content_matches = _content_matches(
                        str(probe.get("expected_content") or "any"), content_type, sample
                    )
                    outcome, retry = _classify_probe(
                        response.status_code,
                        expected_statuses=statuses,
                        content_matches=content_matches,
                    )
                    last = {
                        "outcome": outcome,
                        "status_code": response.status_code,
                        "final_url": str(response.url),
                        "content_type": content_type or None,
                        "sample_bytes": len(sample),
                        "response_sha256": hashlib.sha256(sample).hexdigest(),
                        "error": None,
                    }
            except (httpx.HTTPError, ValueError) as exc:
                retry = True
                last = {
                    "outcome": "transport_error",
                    "status_code": None,
                    "final_url": url,
                    "content_type": None,
                    "sample_bytes": 0,
                    "response_sha256": None,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            if not retry or attempts >= max_attempts:
                break
            await asyncio.sleep(0.2 * attempts)

    scope = str(probe.get("scope") or "control_plane")
    survival_verified = last["outcome"] in {"reachable", "auth_required", "content_mismatch"}
    survival_fallback: dict[str, Any] | None = None
    fallback_url = str(probe.get("survival_url") or "")
    if not survival_verified and scope == "data_payload" and fallback_url:
        async with semaphore:
            survival_fallback = await _probe_survival_url(
                client,
                fallback_url,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
            )
        survival_verified = survival_fallback["outcome"] in {"reachable", "auth_required"}
    data_payload_verified = (
        scope == "data_payload" and last["outcome"] == "reachable"
    )
    result = {
        "provider_id": connection.get("provider_id"),
        "probe_scope": scope,
        "execution_policy": connection.get("execution_policy"),
        "attempt_count": attempts,
        "survival_verified": survival_verified,
        "data_payload_verified": data_payload_verified,
        **last,
    }
    if survival_fallback is not None:
        result["survival_fallback"] = survival_fallback
    return result


async def _probe_survival_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout_seconds: float,
    max_attempts: int,
) -> dict[str, Any]:
    attempts = 0
    result: dict[str, Any] = {"outcome": "transport_error", "status_code": None, "url": url}
    while attempts < max_attempts:
        attempts += 1
        try:
            async with client.stream("GET", url, timeout=timeout_seconds, follow_redirects=True) as response:
                sample = await _read_bounded_sample(response)
                if response.status_code in {401, 403}:
                    outcome, retry = "auth_required", False
                elif 200 <= response.status_code < 400 and sample:
                    outcome, retry = "reachable", False
                elif response.status_code in {404, 410}:
                    outcome, retry = "not_found", False
                elif response.status_code == 429:
                    outcome, retry = "rate_limited", True
                elif response.status_code >= 500:
                    outcome, retry = "server_error", True
                else:
                    outcome, retry = "unexpected_status", False
                result = {
                    "outcome": outcome,
                    "status_code": response.status_code,
                    "url": str(response.url),
                }
        except httpx.HTTPError:
            retry = True
            result = {"outcome": "transport_error", "status_code": None, "url": url}
        if not retry or attempts >= max_attempts:
            break
        await asyncio.sleep(0.2 * attempts)
    return {**result, "attempt_count": attempts}


async def _read_bounded_sample(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    length = 0
    async for chunk in response.aiter_bytes():
        remaining = MAX_PROBE_SAMPLE_BYTES - length
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        length += min(len(chunk), remaining)
        if length >= MAX_PROBE_SAMPLE_BYTES:
            break
    return b"".join(chunks)


def _classify_probe(
    status_code: int,
    *,
    expected_statuses: set[int],
    content_matches: bool,
) -> tuple[str, bool]:
    if status_code in {401, 403} and status_code in expected_statuses:
        return "auth_required", False
    if 200 <= status_code < 400 and status_code in expected_statuses:
        return ("reachable", False) if content_matches else ("content_mismatch", False)
    if status_code in {404, 410}:
        return "not_found", False
    if status_code == 429:
        return "rate_limited", True
    if status_code >= 500:
        return "server_error", True
    return "unexpected_status", False


def _content_matches(expected: str, content_type: str, sample: bytes) -> bool:
    if expected == "any":
        return bool(sample)
    lowered_type = content_type.casefold()
    stripped = sample.lstrip()
    if expected == "json":
        return "json" in lowered_type or stripped.startswith((b"{", b"["))
    if expected == "csv":
        return "csv" in lowered_type or (b"," in sample and b"\n" in sample)
    if expected == "rss":
        return "xml" in lowered_type or stripped.startswith((b"<?xml", b"<rss", b"<feed"))
    if expected == "zip":
        return "zip" in lowered_type or sample.startswith(b"PK")
    return False
