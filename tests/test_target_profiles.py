from __future__ import annotations

import pytest

from finance_crawler_poc.target_profiles import (
    get_target_profile,
    list_target_profiles,
    target_source_registry,
)


@pytest.mark.parametrize(
    ("target_id", "symbol", "required_alias"),
    [
        ("tsmc", "2330.TW", "TSMC"),
        ("delta", "2308.TW", "台達電"),
        ("tatung", "2371.TW", "大同公司"),
        ("tcc", "1101.TW", "台泥"),
        ("csc", "2002.TW", "中鋼"),
        ("formosa", "1301.TW", "台塑"),
        ("nanya", "1303.TW", "南亞"),
        ("yageo", "2327.TW", "國巨"),
        ("asus", "2357.TW", "華碩"),
        ("wistron", "3231.TW", "緯創"),
    ],
)
def test_supported_profiles_have_identity_and_provider_contract(
    target_id: str, symbol: str, required_alias: str
) -> None:
    profile = get_target_profile(target_id)
    target = profile["target"]

    assert target["symbol"] == symbol
    assert required_alias in target["aliases"]
    assert target["currency"] == "TWD"
    assert profile["benchmark"]["symbol"] == "^TWII"
    assert profile["official"]["kind"] in {"sec_filing", "twse_financial_statement"}


def test_profiles_are_returned_without_mutating_registry() -> None:
    profiles = list_target_profiles()
    assert {profile["target_id"] for profile in profiles} >= {"tsmc", "delta", "tatung"}

    first = get_target_profile("delta")
    first["target"]["aliases"].append("MUTATION")
    assert "MUTATION" not in get_target_profile("delta")["target"]["aliases"]


@pytest.mark.parametrize("target_id", ["tcc", "csc", "formosa", "nanya", "yageo", "asus", "wistron"])
def test_taiwan_validation_profiles_expose_geo_research_contract(target_id: str) -> None:
    target = get_target_profile(target_id)["target"]

    assert target["domicile_country"] == "TW"
    assert target["primary_market"] == "TWSE"
    assert target["primary_region"] == "TW"
    assert target["languages"][:2] == ["zh-TW", "en"]
    assert target["region_priority"] == ["TW", "Asia", "global"]
    assert target["local_names"]
    assert target["international_names"]


def test_source_registry_is_target_scoped_and_never_reuses_tsmc_source_id() -> None:
    delta_registry = target_source_registry(get_target_profile("delta"))
    tatung_registry = target_source_registry(get_target_profile("tatung"))

    for registry in (delta_registry, tatung_registry):
        source_ids = {source["source_id"] for source in registry["sources"]}
        assert all("tsmc" not in source_id.casefold() for source_id in source_ids)
        assert any(source["source_tier"] in {"official", "regulatory"} for source in registry["sources"])


def test_unknown_target_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported target_id"):
        get_target_profile("unknown")
