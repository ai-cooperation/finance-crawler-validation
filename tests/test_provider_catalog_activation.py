from __future__ import annotations

from collections import Counter

import pytest

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.provider_catalog import (
    ProviderCatalogError,
    load_provider_catalog,
    providers_for_gaps,
    render_provider_url,
)


def test_provider_catalog_has_the_frozen_activation_boundary() -> None:
    catalog = load_provider_catalog()
    validate_contract("provider-catalog", catalog)
    counts = Counter(
        str(provider["integration"]["status"])
        for provider in catalog["providers"]
    )

    assert len(catalog["providers"]) == 110
    assert counts == {
        "verified_public": 24,
        "verified_requires_key": 26,
        "adapter_required": 28,
        "catalogued_unverified": 23,
        "commercial_only": 7,
        "blocked": 1,
        "deprecated": 1,
    }
    assert sum(
        provider["integration"]["callable"] is True
        for provider in catalog["providers"]
    ) == 50


def test_provider_handoff_preserves_auth_and_fail_closed_reasons() -> None:
    candidates = providers_for_gaps(
        load_provider_catalog(),
        gaps=[{
            "requirement_id": "peer.comparison",
            "missing_metrics": ["valuation"],
            "missing_geographies": ["TW", "Asia"],
        }],
        configured_credentials=set(),
    )["peer.comparison"]

    assert candidates
    assert any("credential_not_configured" in row["blocked_reasons"] for row in candidates)
    assert any("provider_not_callable" in row["blocked_reasons"] for row in candidates)
    assert all("auth_field" not in row or row["auth_field"] for row in candidates)
    assert all("auth_injection" not in row or row["auth_injection"] for row in candidates)


def test_route_renderer_encodes_values_and_rejects_unknown_parameters() -> None:
    provider = next(
        row for row in load_provider_catalog()["providers"]
        if row["provider_id"] == "finmind"
    )
    rendered = render_provider_url(
        provider,
        {
            "dataset": "TaiwanStockFinancialStatements",
            "data_id": "2330 TW",
            "start_date": "2025-01-01",
        },
    )

    assert "data_id=2330+TW" in rendered
    with pytest.raises(ProviderCatalogError, match="unknown template parameters"):
        render_provider_url(
            provider,
            {
                "dataset": "TaiwanStockFinancialStatements",
                "data_id": "2330",
                "start_date": "2025-01-01",
                "redirect_url": "https://evil.example",
            },
        )
