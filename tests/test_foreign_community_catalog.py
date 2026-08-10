from collections import Counter
from pathlib import Path

from finance_crawler_poc.manifest import load_manifest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPOSITORY_ROOT / "foreign-community-sources.yaml"
REQUIRED_FLAGSHIPS = {
    "advfn_uk_share_chat",
    "bogleheads_personal_investments",
    "ethereum_research_json",
    "financial_wisdom_forum",
    "financial_wisdom_forum_feed",
    "github_freqtrade_issues",
    "hotcopper_home",
    "investorshub_most_read",
    "money_stackexchange_api",
    "moneysavingexpert_savings",
    "rankia_forums",
    "reddit_investing_json",
    "tradingview_ideas",
    "valuepickr_json",
    "wertpapier_forum",
    "x_recent_search_without_token",
}


def test_foreign_catalog_is_broad_traceable_and_machine_validated() -> None:
    manifest = load_manifest(CATALOG_PATH)
    enabled = [source for source in manifest.sources if source.enabled]
    ids = {source.id for source in manifest.sources}
    transports = Counter(source.transport for source in enabled)
    regions = {source.region for source in manifest.sources}
    access_tiers = {source.access_tier for source in manifest.sources}

    assert len(manifest.sources) >= 70
    assert len(enabled) >= 55
    assert REQUIRED_FLAGSHIPS <= ids
    assert transports["browser"] >= 25
    assert transports["json_api"] >= 15
    assert transports["rss"] >= 10
    assert regions >= {"global", "US", "UK", "AU", "CA", "IN", "ES", "DE"}
    assert access_tiers >= {
        "auth_boundary",
        "commercial_api",
        "credentialed_api",
        "member_only",
        "public_api",
        "public_feed",
        "public_web",
    }
    assert all(source.kind in {"community", "developer_community"} for source in manifest.sources)
    assert all(source.community_type != "not_applicable" for source in manifest.sources)
    assert all(source.selection_evidence.startswith("https://") for source in manifest.sources)


def test_disabled_catalog_entries_explain_the_authorization_boundary() -> None:
    manifest = load_manifest(CATALOG_PATH)
    disabled = [source for source in manifest.sources if not source.enabled]

    assert len(disabled) >= 8
    assert all(source.disabled_reason for source in disabled)
    assert all(
        source.access_tier in {"commercial_api", "credentialed_api", "member_only"}
        for source in disabled
    )


def test_financial_wisdom_browser_and_feed_share_a_fallback_group() -> None:
    manifest = load_manifest(CATALOG_PATH)
    sources = {source.id: source for source in manifest.sources}

    assert sources["financial_wisdom_forum"].route_group == "financial_wisdom_forum"
    assert sources["financial_wisdom_forum_feed"].route_group == "financial_wisdom_forum"
    assert sources["financial_wisdom_forum_feed"].relay_path.endswith(
        "/financial_wisdom_forum_feed"
    )


def test_browser_cohort_has_versioned_robots_exclusions() -> None:
    manifest = load_manifest(CATALOG_PATH)
    excluded = {source.id: source for source in manifest.sources if source.robots_denied}

    assert set(excluded) == {
        "bitcoin_stackexchange_hot",
        "money_stackexchange_hot",
        "quant_stackexchange_hot",
        "quora_investing",
        "reddit_investing_html",
        "valueinvestorsclub_ideas",
        "x_finance_search_web",
    }
    assert all(source.robots_evidence.endswith("/robots.txt") for source in excluded.values())
    assert all(source.robots_checked_at == "2026-08-09" for source in excluded.values())
