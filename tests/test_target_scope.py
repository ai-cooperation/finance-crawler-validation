from __future__ import annotations

from finance_crawler_poc.target_scope import select_target_items


def _item(item_id: str, title: str) -> dict[str, str]:
    return {
        "item_id": item_id,
        "source_id": "google_news_target_rss",
        "title": title,
        "summary": title,
        "published_at": "2026-08-21T00:00:00Z",
        "kind": "news",
    }


def test_numeric_ticker_does_not_match_decimal_number() -> None:
    target = {"kind": "equity", "symbol": "2371.TW", "name": "Tatung Company", "aliases": ["Tatung Company", "大同公司", "2371"]}
    selected, _ = select_target_items([_item("x", "XRP spot ETF inflows reach $13.2371 million")], target=target)
    assert selected == []


def test_ambiguous_brand_aliases_are_not_required_for_configured_target() -> None:
    target = {"kind": "equity", "symbol": "2308.TW", "name": "Delta Electronics, Inc.", "aliases": ["Delta Electronics", "2308"]}
    selected, _ = select_target_items(
        [_item("air", "Delta CEO outlines two new airline routes"), _item("company", "Delta Electronics expands AI data-center power systems")],
        target=target,
    )
    assert [item["item_id"] for item in selected] == ["company"]


def test_ambiguous_alias_requires_target_context_terms() -> None:
    target = {
        "kind": "equity",
        "symbol": "2371.TW",
        "name": "Tatung Company",
        "aliases": ["Tatung Company", "Tatung", "2371"],
        "ambiguous_aliases": ["Tatung"],
        "identity_context_terms": ["company", "electric", "energy", "taiwan"],
        "identity_exclude_terms": ["fc", "taipower"],
    }
    selected, _ = select_target_items(
        [_item("sports", "Tatung FC draws in league match"), _item("company", "Tatung expands Taiwan energy storage operations")],
        target=target,
    )
    assert [item["item_id"] for item in selected] == ["company"]


def test_crypto_scope_does_not_use_generic_global_market_as_identity() -> None:
    target = {
        "kind": "crypto",
        "symbol": "BTC",
        "name": "Bitcoin",
        "market": "global",
    }
    selected, scope = select_target_items(
        [
            _item("unrelated", "Global AI policy outlook for financial markets"),
            _item("btc", "Bitcoin market structure and ETF flows"),
        ],
        target=target,
    )

    assert [item["item_id"] for item in selected] == ["btc"]
    assert "global" not in scope["identity_terms"]
    assert scope["matcher_version"] == "target_identity_v3"
