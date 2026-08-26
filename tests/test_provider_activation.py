from __future__ import annotations

import asyncio

import httpx

from finance_crawler_poc.provider_activation import (
    NON_ROUTE_INTEGRATED_STATUSES,
    build_provider_activation_registry,
    build_provider_runtime_registry,
    probe_activation_registry,
    summarize_activation_registry,
)
from finance_crawler_poc.provider_catalog import load_provider_catalog
from finance_crawler_poc.contracts import validate_contract


def test_every_non_route_integrated_provider_has_an_activation_contract() -> None:
    catalog = load_provider_catalog()
    registry = build_provider_activation_registry(catalog)
    validate_contract("provider-activation-registry", registry)
    expected = {
        provider["provider_id"]
        for provider in catalog["providers"]
        if provider["integration"]["status"] in NON_ROUTE_INTEGRATED_STATUSES
    }

    assert len(expected) == 60
    assert {row["provider_id"] for row in registry["connections"]} == expected
    assert all(row["probe"]["url"].startswith("https://") for row in registry["connections"])
    assert all(row["next_action"] for row in registry["connections"])


def test_policy_gated_providers_are_never_promoted_to_executable_routes() -> None:
    registry = build_provider_activation_registry(load_provider_catalog())
    by_id = {row["provider_id"]: row for row in registry["connections"]}

    for provider_id in {
        "associated_press",
        "benzinga",
        "gartner",
        "intrinio",
        "tradingeconomics",
        "trendforce",
        "wsj",
        "seeking_alpha",
        "iex_cloud",
    }:
        assert by_id[provider_id]["execution_policy"] == "not_executable"
        assert by_id[provider_id]["data_plane_state"] in {
            "commercial_onboarding_required",
            "policy_review_required",
            "retired",
        }


def test_transport_adapters_cover_all_technically_connectable_backlog() -> None:
    registry = build_provider_activation_registry(load_provider_catalog())
    rows = [
        row
        for row in registry["connections"]
        if row["execution_policy"] != "not_executable"
    ]

    assert len(rows) == 51
    assert all(row["adapter"] != "none" for row in rows)
    assert {row["runtime"] for row in rows} <= {
        "github_actions",
        "cloudflare_worker",
        "hybrid",
    }
    assert all(row["probe"]["scope"] in {"control_plane", "data_payload"} for row in rows)


def test_activation_summary_keeps_control_plane_and_payload_proof_separate() -> None:
    registry = build_provider_activation_registry(load_provider_catalog())
    summary = summarize_activation_registry(registry)

    assert summary == {
        "total": 60,
        "technically_connectable": 51,
        "not_executable": 9,
        "data_payload_probe_defined": summary["data_payload_probe_defined"],
        "control_plane_probe_only": summary["control_plane_probe_only"],
    }
    assert summary["data_payload_probe_defined"] + summary["control_plane_probe_only"] == 60


def test_runtime_registry_exposes_all_providers_without_secret_values() -> None:
    catalog = load_provider_catalog()
    activation = build_provider_activation_registry(catalog)
    runtime = build_provider_runtime_registry(catalog, activation)

    assert runtime["summary"] == {
        "total": 110,
        "route_integrated": 50,
        "activation_backlog": 60,
        "technically_connectable_backlog": 51,
        "not_executable": 9,
    }
    assert len(runtime["providers"]) == 110
    serialized = str(runtime)
    assert "api_key_value" not in serialized
    assert all("connection" in provider for provider in runtime["providers"])


def test_probe_retries_transient_failure_and_records_bounded_evidence() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="temporary")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"ok": True},
        )

    registry = {
        "schema_version": 1,
        "registry_id": "test_registry",
        "generated_at": "2026-08-26T00:00:00Z",
        "catalog_id": "test_catalog",
        "connections": [{
            "provider_id": "test_provider",
            "catalog_status": "adapter_required",
            "execution_policy": "probe_only",
            "data_plane_state": "payload_probe_ready",
            "adapter": "generic_json",
            "runtime": "cloudflare_worker",
            "transports": ["json_api"],
            "required_configuration": [],
            "probe": {
                "method": "GET",
                "url": "https://api.example.test/data",
                "scope": "data_payload",
                "expected_content": "json",
                "expected_statuses": [200, 401, 403],
                "max_attempts": 3,
                "timeout_seconds": 15,
            },
            "next_action": "parse",
        }],
    }
    async def run_probe() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await probe_activation_registry(
                registry,
                client=client,
                checked_at="2026-08-26T01:00:00Z",
                concurrency=1,
            )

    report = asyncio.run(run_probe())

    result = report["results"][0]
    validate_contract("provider-activation-report", report)
    assert result["attempt_count"] == 2
    assert result["survival_verified"] is True
    assert result["data_payload_verified"] is True
    assert result["response_sha256"]
    assert result["sample_bytes"] <= 65_536


def test_probe_does_not_mistake_html_or_not_found_for_data_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="login")

    registry = build_provider_activation_registry(load_provider_catalog())
    one = next(row for row in registry["connections"] if row["provider_id"] == "census")
    async def run_probe() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await probe_activation_registry(
                {**registry, "connections": [one]},
                client=client,
                checked_at="2026-08-26T01:00:00Z",
                concurrency=1,
            )

    report = asyncio.run(run_probe())

    result = report["results"][0]
    assert result["survival_verified"] is True
    assert result["data_payload_verified"] is False
    assert result["outcome"] == "content_mismatch"


def test_failed_payload_probe_can_still_verify_control_plane_survival() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.census.gov":
            raise httpx.ConnectTimeout("payload timeout", request=request)
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="official docs")

    registry = build_provider_activation_registry(load_provider_catalog())
    census = next(row for row in registry["connections"] if row["provider_id"] == "census")

    async def run_probe() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await probe_activation_registry(
                {**registry, "connections": [census]},
                client=client,
                checked_at="2026-08-26T01:00:00Z",
                concurrency=1,
            )

    report = asyncio.run(run_probe())
    result = report["results"][0]
    assert result["outcome"] == "transport_error"
    assert result["survival_verified"] is True
    assert result["data_payload_verified"] is False
    assert result["survival_fallback"]["outcome"] == "reachable"
