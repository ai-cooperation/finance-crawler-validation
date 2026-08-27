from __future__ import annotations

import json

import httpx

from finance_crawler_poc.target_community import fetch_target_community


TARGET = {
    "kind": "equity",
    "symbol": "2357.TW",
    "name": "ASUSTeK Computer Inc.",
    "aliases": ["華碩", "ASUS", "ASUSTeK", "2357"],
    "market": "TW",
    "primary_region": "TW",
    "local_names": ["華碩"],
}


SEARCH_HTML = """<html><body>
<div class="r-ent">
  <div class="nrec"><span class="hl f3">12</span></div>
  <div class="title"><a href="/bbs/Stock/M.1786526212.A.4D7.html">[情報] 2357 華碩最新季報</a></div>
  <div class="meta"><div class="author">investor</div><div class="date">8/12</div></div>
</div>
</body></html>"""

ARTICLE_HTML = """<html><body><div id="main-content">
<div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">investor</span></div>
華碩公布最新季報，討論伺服器需求、毛利率與後續風險。
<div class="push"><span class="push-userid">reader</span><span class="push-content">: 關注毛利率</span><span class="push-ipdatetime"> 8/12 12:00</span></div>
</div></body></html>"""


def test_taiwan_community_route_preserves_ptt_original_and_blocked_boundaries(monkeypatch) -> None:
    def fake_get(url: str, **kwargs):
        if "/bbs/Stock/search" in url:
            return httpx.Response(200, content=SEARCH_HTML.encode(), request=httpx.Request("GET", url))
        if "/bbs/Stock/M." in url:
            return httpx.Response(200, content=ARTICLE_HTML.encode(), request=httpx.Request("GET", url))
        if "dcard.tw" in url or "mobile01.com" in url or "reddit.com" in url or "stocktwits.com" in url:
            return httpx.Response(403, content=b"forbidden", request=httpx.Request("GET", url))
        if "hn.algolia.com" in url:
            return httpx.Response(200, content=json.dumps({"hits": []}).encode(), request=httpx.Request("GET", url))
        raise AssertionError(url)

    monkeypatch.setattr("httpx.get", fake_get)

    result = fetch_target_community(TARGET, max_items=10, source_id_prefix="asus")

    assert result["status"] == "available"
    assert result["coverage"]["status"] == "complete"
    assert result["coverage"]["local_route_attempt_count"] >= 3
    assert result["coverage"]["local_successful_query_count"] >= 1
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["source_id"] == "ptt_stock_asus_search"
    assert item["canonical_url"].endswith("M.1786526212.A.4D7.html")
    assert "毛利率" in item["content"]
    assert item["evidence"]["comments"][0]["author"] == "reader"
    assert item["evidence"]["response_sha256"]
    blocked = {attempt["source_id"] for attempt in result["attempts"] if attempt.get("route_status") == "blocked"}
    assert {"dcard_money_public_search", "mobile01_finance_public_search"} <= blocked


def test_successful_local_query_with_no_hits_is_not_reported_as_no_market_discussion(monkeypatch) -> None:
    def fake_get(url: str, **kwargs):
        if "/bbs/Stock/search" in url:
            return httpx.Response(200, content=b"<html><body></body></html>", request=httpx.Request("GET", url))
        if "dcard.tw" in url or "mobile01.com" in url or "reddit.com" in url or "stocktwits.com" in url:
            return httpx.Response(403, content=b"forbidden", request=httpx.Request("GET", url))
        return httpx.Response(200, content=b'{"hits": []}', request=httpx.Request("GET", url))

    monkeypatch.setattr("httpx.get", fake_get)

    result = fetch_target_community(TARGET, max_items=10)

    assert result["status"] == "insufficient_data"
    assert result["missing_reason"] == "local_community_search_completed_without_target_hits"
    ptt = next(attempt for attempt in result["attempts"] if str(attempt["source_id"]).startswith("ptt_stock"))
    assert ptt["query_status"] == "success_no_hits"
    assert ptt["route_status"] == "success"


def test_ambiguous_local_name_uses_ticker_qualified_query_and_admits_correct_original(monkeypatch) -> None:
    target = {
        "kind": "equity",
        "symbol": "1303.TW",
        "name": "Nan Ya Plastics Corporation",
        "aliases": ["南亞", "南亞塑膠", "Nan Ya Plastics", "1303"],
        "local_names": ["南亞"],
        "ambiguous_aliases": ["南亞"],
        "identity_context_terms": ["1303", "南亞塑膠", "塑膠", "電子材料"],
        "identity_exclude_terms": ["南亞科", "2408"],
        "market": "TW",
        "primary_region": "TW",
    }
    search = SEARCH_HTML.replace("2357 華碩最新季報", "1303 南亞最新季報")
    article = ARTICLE_HTML.replace("華碩", "南亞塑膠")

    def fake_get(url: str, **kwargs):
        if "/bbs/Stock/search" in url:
            return httpx.Response(200, content=search.encode(), request=httpx.Request("GET", url))
        if "/bbs/Stock/M." in url:
            return httpx.Response(200, content=article.encode(), request=httpx.Request("GET", url))
        if "dcard.tw" in url or "mobile01.com" in url or "reddit.com" in url or "stocktwits.com" in url:
            return httpx.Response(403, content=b"forbidden", request=httpx.Request("GET", url))
        if "hn.algolia.com" in url:
            return httpx.Response(200, content=json.dumps({"hits": []}).encode(), request=httpx.Request("GET", url))
        raise AssertionError(url)

    monkeypatch.setattr("httpx.get", fake_get)

    result = fetch_target_community(target, max_items=10, source_id_prefix="nanya")

    ptt = next(attempt for attempt in result["attempts"] if attempt["source_id"] == "ptt_stock_nanya_search")
    assert ptt["query"] == "1303 南亞"
    assert ptt["admission_status"] == "admitted"
    assert result["coverage"]["status"] == "complete"
    assert result["items"][0]["canonical_url"].endswith("M.1786526212.A.4D7.html")
