from __future__ import annotations

from pathlib import Path

import pytest

from finance_crawler_poc.radar_manifest import (
    RadarManifestError,
    load_radar_manifest,
    select_radar_sources,
)


ROOT = Path(__file__).resolve().parents[1]


def test_vertical_slice_manifest_has_balanced_unique_sources() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")

    assert len(manifest.sources) == 15
    assert len({source.source_id for source in manifest.sources}) == 15
    assert manifest.minimum_successful_sources == 12
    assert manifest.maximum_items_per_run == 60
    assert {
        transport: sum(source.transport == transport for source in manifest.sources)
        for transport in ("rss", "json_api", "browser")
    } == {"rss": 5, "json_api": 7, "browser": 3}
    assert {
        strategy: sum(source.catchup_strategy == strategy for source in manifest.sources)
        for strategy in ("rss_window", "api_since", "latest_only")
    } == {"rss_window": 5, "api_since": 5, "latest_only": 5}


def test_manifest_rejects_private_network_targets(tmp_path: Path) -> None:
    manifest_path = tmp_path / "radar.yaml"
    valid_manifest = (ROOT / "radar-sources.yaml").read_text(encoding="utf-8")
    manifest_path.write_text(
        valid_manifest.replace(
            "https://www.federalreserve.gov/feeds/press_all.xml",
            "https://127.0.0.1/admin",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RadarManifestError, match="public host"):
        load_radar_manifest(manifest_path)


def test_select_radar_sources_preserves_order_and_uses_bounded_target_gate() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    selected = select_radar_sources(
        manifest,
        [
            "coingecko_markets_api",
            "bbc_business_rss",
            "cnbc_top_news_rss",
            "marketwatch_topstories_rss",
            "federal_reserve_press_rss",
            "ecb_press_rss",
            "hacker_news_finance_api",
            "money_stackexchange_api",
            "quant_stackexchange_api",
            "openbb_github_issues_api",
            "tradingagents_github_issues_api",
            "tradingview_ideas_browser",
        ],
    )
    assert [source.source_id for source in selected.sources] == [
        "coingecko_markets_api",
        "bbc_business_rss",
        "cnbc_top_news_rss",
        "marketwatch_topstories_rss",
        "federal_reserve_press_rss",
        "ecb_press_rss",
        "hacker_news_finance_api",
        "money_stackexchange_api",
        "quant_stackexchange_api",
        "openbb_github_issues_api",
        "tradingagents_github_issues_api",
        "tradingview_ideas_browser",
    ]
    assert selected.minimum_successful_sources == 10
    assert selected.maximum_items_per_run == 34


def test_select_radar_sources_rejects_unknown_or_too_small_bundles() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    with pytest.raises(RadarManifestError, match="unknown source"):
        select_radar_sources(
            manifest,
            [source.source_id for source in manifest.sources[:11]] + ["unknown_source"],
        )
    with pytest.raises(RadarManifestError, match="12 to 20"):
        select_radar_sources(manifest, ["coingecko_markets_api"])
