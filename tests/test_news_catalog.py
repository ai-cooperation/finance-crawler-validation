from collections import Counter
from pathlib import Path

import pytest

from finance_crawler_poc.news_catalog import NewsCatalogError, load_news_catalog


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def write_catalog(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "news-sources.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_catalog_counts_unique_brands_instead_of_endpoints(tmp_path: Path) -> None:
    catalog = load_news_catalog(
        write_catalog(
            tmp_path,
            """
version: 1
status: draft
target:
  total_brands: 120
  finance_specialist: 100
  general_finance_desk: 20
brands:
  - id: specialist_one
    name: Specialist One
    canonical_domain: specialist.example
    brand_class: finance_specialist
    region: global
    languages: [en]
    endpoints:
      - id: specialist_one_rss
        transport: rss
        url: https://specialist.example/rss
        relay_path: /v1/feed/specialist_one_rss
        required_capabilities: [http, rss]
      - id: specialist_one_browser
        transport: browser
        url: https://specialist.example/markets
        required_capabilities: [http, javascript]
  - id: general_one
    name: General One Finance
    canonical_domain: general.example
    brand_class: general_finance_desk
    region: US
    languages: [en]
    endpoints:
      - id: general_one_api
        transport: json_api
        url: https://general.example/api/business
        required_capabilities: [http, json]
""",
        )
    )

    assert catalog.brand_count == 2
    assert catalog.endpoint_count == 3
    assert catalog.brands[0].endpoints[0].relay_path == (
        "/v1/feed/specialist_one_rss"
    )
    assert catalog.target.total_brands == 120
    assert catalog.is_complete is False
    assert {brand.canonical_domain for brand in catalog.brands} == {
        "general.example",
        "specialist.example",
    }


@pytest.mark.parametrize(
    ("transport", "relay_path", "message"),
    [
        ("static_html", "/v1/feed/specialist_one_html", "only for rss endpoints"),
        ("rss", "/v1/feed/some_other_feed", "must match endpoint id"),
        ("rss", "https://proxy.example/feed", "must match endpoint id"),
    ],
)
def test_catalog_relay_path_is_a_fixed_rss_allowlist_route(
    tmp_path: Path, transport: str, relay_path: str, message: str
) -> None:
    path = write_catalog(
        tmp_path,
        f"""
version: 1
status: draft
target: {{total_brands: 120, finance_specialist: 100, general_finance_desk: 20}}
brands:
  - id: specialist_one
    name: Specialist One
    canonical_domain: specialist.example
    brand_class: finance_specialist
    region: global
    languages: [en]
    endpoints:
      - id: specialist_one_{transport}
        transport: {transport}
        url: https://specialist.example/data
        relay_path: {relay_path}
        required_capabilities: [http, {transport}]
""",
    )

    with pytest.raises(NewsCatalogError, match=message):
        load_news_catalog(path)


def test_complete_catalog_must_match_100_plus_20_contract(tmp_path: Path) -> None:
    body = """
version: 1
status: complete
target:
  total_brands: 120
  finance_specialist: 100
  general_finance_desk: 20
brands:
  - id: specialist_one
    name: Specialist One
    canonical_domain: specialist.example
    brand_class: finance_specialist
    region: global
    languages: [en]
    endpoints:
      - id: specialist_one_rss
        transport: rss
        url: https://specialist.example/rss
        required_capabilities: [http, rss]
"""

    with pytest.raises(NewsCatalogError, match="complete catalog must contain 120 brands"):
        load_news_catalog(write_catalog(tmp_path, body))


def test_catalog_rejects_duplicate_brand_domains_and_endpoint_ids(tmp_path: Path) -> None:
    duplicate_domain = """
version: 1
status: draft
target: {total_brands: 120, finance_specialist: 100, general_finance_desk: 20}
brands:
  - id: first_brand
    name: First
    canonical_domain: finance.example
    brand_class: finance_specialist
    region: global
    languages: [en]
    endpoints:
      - {id: shared_feed, transport: rss, url: https://finance.example/rss, required_capabilities: [http, rss]}
  - id: second_brand
    name: Second
    canonical_domain: finance.example
    brand_class: general_finance_desk
    region: US
    languages: [en]
    endpoints:
      - {id: second_feed, transport: rss, url: https://finance.example/business/rss, required_capabilities: [http, rss]}
"""
    with pytest.raises(NewsCatalogError, match="duplicate canonical domain"):
        load_news_catalog(write_catalog(tmp_path, duplicate_domain))

    duplicate_endpoint = duplicate_domain.replace(
        "canonical_domain: finance.example\n    brand_class: general_finance_desk",
        "canonical_domain: general.example\n    brand_class: general_finance_desk",
    ).replace("id: second_feed", "id: shared_feed")
    with pytest.raises(NewsCatalogError, match="duplicate endpoint id"):
        load_news_catalog(write_catalog(tmp_path, duplicate_endpoint))


def test_target_composition_must_sum_to_unique_brand_target(tmp_path: Path) -> None:
    body = """
version: 1
status: draft
target: {total_brands: 120, finance_specialist: 90, general_finance_desk: 20}
brands: []
"""

    with pytest.raises(NewsCatalogError, match="target composition must sum to 120"):
        load_news_catalog(write_catalog(tmp_path, body))


def test_repository_news_catalog_freezes_the_120_brand_denominator() -> None:
    catalog = load_news_catalog(REPOSITORY_ROOT / "news-sources.yaml")

    assert catalog.status == "complete"
    assert catalog.target.total_brands == 120
    assert catalog.target.finance_specialist == 100
    assert catalog.target.general_finance_desk == 20
    assert catalog.brand_count == 120
    assert catalog.endpoint_count >= 120
    assert Counter(brand.brand_class for brand in catalog.brands) == {
        "finance_specialist": 100,
        "general_finance_desk": 20,
    }
    assert {
        endpoint.transport
        for brand in catalog.brands
        for endpoint in brand.endpoints
    } == {"browser", "json_api", "rss", "static_html"}


def test_robots_blocked_brands_use_explicit_first_party_podcast_feeds() -> None:
    catalog = load_news_catalog(REPOSITORY_ROOT / "news-sources.yaml")
    brands = {brand.id: brand for brand in catalog.brands}

    assert [endpoint.id for endpoint in brands["reuters"].endpoints] == [
        "reuters_morning_bid_podcast_rss",
        "reuters_browser",
    ]
    assert brands["reuters"].endpoints[0].url == (
        "https://feeds.megaphone.fm/THRH7907651499"
    )
    assert [endpoint.id for endpoint in brands["barrons"].endpoints] == [
        "barrons_streetwise_podcast_rss",
        "barrons_browser",
    ]
    assert brands["barrons"].endpoints[0].url == (
        "https://video-api.shdsvc.dowjones.io/api/podcasts/feed/streetwise"
    )


def test_curated_rss_endpoints_use_the_current_official_news_feeds() -> None:
    catalog = load_news_catalog(REPOSITORY_ROOT / "news-sources.yaml")
    brands = {brand.id: brand for brand in catalog.brands}

    assert brands["private_banker_international"].endpoints[0].url == (
        "https://www.privatebankerinternational.com/news/feed/"
    )
    assert brands["advisor_hub"].endpoints[0].url == "https://www.advisorhub.com/feed/"


def test_blocked_news_pages_use_active_first_party_podcast_feeds() -> None:
    catalog = load_news_catalog(REPOSITORY_ROOT / "news-sources.yaml")
    brands = {brand.id: brand for brand in catalog.brands}

    assert [endpoint.id for endpoint in brands["citywire"].endpoints] == [
        "citywire_advice_show_podcast_rss",
        "citywire_html",
    ]
    assert brands["citywire"].endpoints[0].url == (
        "https://feeds.transistor.fm/the-advice-show"
    )
    assert [endpoint.id for endpoint in brands["livewire_markets"].endpoints] == [
        "livewire_markets_podcast_rss",
        "livewire_markets_browser",
    ]
    assert brands["livewire_markets"].endpoints[0].url == (
        "https://feed.podbean.com/successandmoreinterestingstuff/feed.xml"
    )


def test_sifted_uses_its_published_startup_europe_podcast_feed() -> None:
    catalog = load_news_catalog(REPOSITORY_ROOT / "news-sources.yaml")
    brands = {brand.id: brand for brand in catalog.brands}

    assert [endpoint.id for endpoint in brands["sifted"].endpoints] == [
        "sifted_startup_europe_podcast_rss"
    ]
    assert brands["sifted"].endpoints[0].url == (
        "https://feeds.buzzsprout.com/1877446.rss"
    )


def test_recovered_publishers_prefer_first_party_rss_before_html() -> None:
    catalog = load_news_catalog(REPOSITORY_ROOT / "news-sources.yaml")
    brands = {brand.id: brand for brand in catalog.brands}

    assert brands["international_banker"].endpoints[0].url == (
        "https://internationalbanker.com/feed/"
    )
    assert brands["leaprate"].endpoints[0].url == "https://www.leaprate.com/feed/"
    assert brands["businessline"].endpoints[0].url == (
        "https://www.thehindubusinessline.com/markets/feeder/default.rss"
    )
    assert brands["bankless"].endpoints[0].url == "https://feeds.libsyn.com/548227/rss"
