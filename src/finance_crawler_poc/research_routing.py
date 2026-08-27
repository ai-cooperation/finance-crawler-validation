"""Target-aware geographic routing for research retrieval.

Transport capability and target coverage are deliberately separate.  This
module turns a target profile into an auditable query plan and evaluates only
whether the required geographic routes were actually exercised; it never
promotes a route merely because an HTTP request returned 200.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote


def is_taiwan_target(target: Mapping[str, Any]) -> bool:
    market = str(target.get("market") or target.get("primary_market") or "").strip().upper()
    region = str(target.get("primary_region") or "").strip().upper()
    return market in {"TW", "TWSE", "TPEX"} or region == "TW"


def build_news_route_plan(
    target: Mapping[str, Any],
    *,
    route_prefix: str,
    max_items: int,
) -> list[dict[str, Any]]:
    """Build a deterministic local -> Asia -> global query plan."""

    symbol = str(target.get("symbol") or "").strip().upper()
    name = str(target.get("name") or "").strip()
    if not symbol and not name:
        raise ValueError("target symbol or name is required")
    if not is_taiwan_target(target):
        rss_query = symbol or name
        search_query = name or symbol
        return [
            _route(
                f"yahoo_finance_{route_prefix}_rss", "rss",
                "https://feeds.finance.yahoo.com/rss/2.0/headline?"
                f"s={quote(rss_query, safe='.-')}&region=US&lang=en-US",
                query=rss_query,
            ),
            _route(
                f"yahoo_finance_{route_prefix}_search", "json_api",
                "https://query1.finance.yahoo.com/v1/finance/search?"
                f"q={quote(search_query)}&newsCount={max_items}&quotesCount=0",
                query=search_query,
            ),
            _route(
                f"google_news_{route_prefix}_rss", "rss",
                "https://news.google.com/rss/search?"
                f"q={quote(search_query)}&hl=en-US&gl=US&ceid=US:en",
                query=search_query,
            ),
        ]

    local_query = _first_text(target.get("local_names")) or _first_cjk_alias(target) or name or symbol
    international_query = _first_text(target.get("international_names")) or name or symbol
    return [
        _route(
            f"yahoo_finance_{route_prefix}_tw_rss", "rss",
            "https://feeds.finance.yahoo.com/rss/2.0/headline?"
            f"s={quote(symbol or local_query, safe='.-')}&region=TW&lang=zh-Hant-TW",
            query=symbol or local_query, region_scope="TW", language="zh-TW", priority=1,
        ),
        _route(
            f"google_news_{route_prefix}_tw_rss", "rss",
            "https://news.google.com/rss/search?"
            f"q={quote(local_query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
            query=local_query, region_scope="TW", language="zh-TW", priority=1,
        ),
        _route(
            f"google_news_{route_prefix}_asia_rss", "rss",
            "https://news.google.com/rss/search?"
            f"q={quote(international_query)}&hl=en-SG&gl=SG&ceid=SG:en",
            query=international_query, region_scope="Asia", language="en", priority=2,
        ),
        _route(
            f"yahoo_finance_{route_prefix}_search", "json_api",
            "https://query1.finance.yahoo.com/v1/finance/search?"
            f"q={quote(international_query)}&newsCount={max_items}&quotesCount=0",
            query=international_query, region_scope="global", language="en", priority=3,
        ),
        _route(
            f"google_news_{route_prefix}_global_rss", "rss",
            "https://news.google.com/rss/search?"
            f"q={quote(international_query)}&hl=en-US&gl=US&ceid=US:en",
            query=international_query, region_scope="global", language="en", priority=3,
        ),
    ]


def annotate_attempt_outcomes(
    attempts: list[dict[str, Any]],
    admitted_items: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    admitted_by_route: dict[str, int] = {}
    for item in admitted_items:
        source_id = str(item.get("source_id") or "")
        admitted_by_route[source_id] = admitted_by_route.get(source_id, 0) + 1
    normalized: list[dict[str, Any]] = []
    for attempt in attempts:
        current = dict(attempt)
        legacy_status = str(current.get("status") or "failed")
        current["route_status"] = "success" if legacy_status == "success" else legacy_status
        if current["route_status"] == "success":
            raw_count = int(current.get("item_count") or 0)
            relevant_count = admitted_by_route.get(str(current.get("source_id") or ""), 0)
            current["query_status"] = "success_with_hits" if raw_count else "success_no_hits"
            current["content_status"] = "target_relevant" if relevant_count else "noise_only" if raw_count else "unavailable"
            current["admission_status"] = "admitted" if relevant_count else "rejected" if raw_count else "unresolved"
            current["relevant_item_count"] = relevant_count
        else:
            current["query_status"] = "failed"
            current["content_status"] = "unavailable"
            current["admission_status"] = "unresolved"
            current["relevant_item_count"] = 0
        normalized.append(current)
    return normalized


def evaluate_news_geo_coverage(
    target: Mapping[str, Any],
    attempts: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if not is_taiwan_target(target):
        return {"status": "not_applicable", "policy": "geo_news_v1"}
    successful_regions = {
        str(item.get("region_scope"))
        for item in attempts
        if item.get("route_status") == "success" and item.get("region_scope")
    }
    local_relevant = sum(
        int(item.get("relevant_item_count") or 0)
        for item in attempts if item.get("region_scope") == "TW"
    )
    complete = local_relevant >= 1 and {"TW", "Asia", "global"} <= successful_regions
    blocking_reasons: list[str] = []
    if local_relevant < 1:
        blocking_reasons.append("local_news_target_hit_missing")
    blocking_reasons.extend(
        f"news_region_not_successful:{region}"
        for region in ("TW", "Asia", "global")
        if region not in successful_regions
    )
    return {
        "status": "complete" if complete else "partial",
        "policy": "tw_local_asia_global_v1",
        "required_regions": ["TW", "Asia", "global"],
        "successful_regions": sorted(successful_regions),
        "local_relevant_item_count": local_relevant,
        "blocking_reasons": [] if complete else blocking_reasons,
    }


def _route(
    source_id: str,
    transport: str,
    url: str,
    *,
    query: str,
    region_scope: str | None = None,
    language: str | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "transport": transport,
        "url": url,
        "query": query,
        "region_scope": region_scope,
        "language": language,
        "priority": priority,
    }


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        return next((str(item).strip() for item in value if str(item).strip()), "")
    return ""


def _first_cjk_alias(target: Mapping[str, Any]) -> str:
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    return next((str(item).strip() for item in aliases if any("\u4e00" <= char <= "\u9fff" for char in str(item))), "")
