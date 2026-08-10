from collections import Counter
from pathlib import Path

from finance_crawler_poc.manifest import load_manifest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
POPULARITY_RESEARCH_SOURCES = {
    "bogleheads_personal_investments",
    "bitcointalk_speculation",
    "dcard_money",
    "mobile01_finance",
    "openbb_github_discussions",
    "quant_stackexchange_hot",
    "tradingview_ideas",
}


def test_catalog_covers_source_and_transport_boundaries() -> None:
    manifest = load_manifest(REPOSITORY_ROOT / "sources.yaml")
    enabled = [source for source in manifest.sources if source.enabled]

    transports = Counter(source.transport for source in enabled)
    kinds = Counter(source.kind for source in enabled)

    assert len(manifest.sources) >= 35
    assert transports.keys() == {"browser", "json_api", "rss"}
    assert kinds.keys() >= {
        "aggregator",
        "community",
        "developer_community",
        "market_data",
        "news",
        "official_data",
        "official_news",
        "reference",
    }
    assert all(source.kind != "other" for source in manifest.sources)


def test_researched_community_sources_keep_selection_evidence() -> None:
    manifest = load_manifest(REPOSITORY_ROOT / "sources.yaml")
    sources = {source.id: source for source in manifest.sources}

    assert POPULARITY_RESEARCH_SOURCES <= sources.keys()
    for source_id in POPULARITY_RESEARCH_SOURCES:
        source = sources[source_id]
        assert source.provenance == "popularity_research_2026_08_07"
        assert source.selection_evidence.startswith("https://")
