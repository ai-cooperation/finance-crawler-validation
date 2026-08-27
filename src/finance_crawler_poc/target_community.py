"""Target-scoped community retrieval with Taiwan-local routes first."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin

import httpx

from finance_crawler_poc.contracts import build_item_id
from finance_crawler_poc.research_routing import is_taiwan_target
from finance_crawler_poc.target_retrieval import fetch_hackernews_target_community
from finance_crawler_poc.target_scope import select_target_items


PTT_ROOT = "https://www.ptt.cc"


def fetch_target_community(
    target: Mapping[str, Any],
    *,
    max_items: int = 20,
    timeout_seconds: float = 20.0,
    source_id_prefix: str | None = None,
) -> dict[str, Any]:
    """Collect local community originals, then add global community context."""

    if max_items <= 0:
        raise ValueError("max_items must be positive")
    prefix = str(source_id_prefix or "target").strip().casefold() or "target"
    local = (
        _fetch_ptt_stock(target, max_items=max_items, timeout_seconds=timeout_seconds, source_id_prefix=prefix)
        if is_taiwan_target(target)
        else {"items": [], "attempts": [], "status": "not_applicable"}
    )
    boundary_attempts = (
        _probe_taiwan_community_boundaries(target, timeout_seconds=timeout_seconds)
        if is_taiwan_target(target)
        else []
    )
    global_result = fetch_hackernews_target_community(
        target,
        max_items=max_items,
        timeout_seconds=timeout_seconds,
        source_id_prefix=prefix,
    )
    global_attempts = [_normalize_global_attempt(item) for item in global_result.get("attempts", [])]
    items = [
        *[dict(item) for item in local.get("items", []) if isinstance(item, Mapping)],
        *[dict(item) for item in global_result.get("items", []) if isinstance(item, Mapping)],
    ]
    attempts = [*local.get("attempts", []), *boundary_attempts, *global_attempts]
    local_attempts = [item for item in attempts if item.get("region_scope") == "TW"]
    local_successes = [
        item for item in local_attempts
        if item.get("route_status") == "success" and item.get("query_status") in {"success_with_hits", "success_no_hits"}
    ]
    local_items = [item for item in items if (item.get("evidence") or {}).get("region_scope") == "TW"]
    coverage_complete = len(local_attempts) >= 3 and bool(local_successes) and bool(local_items)
    blocking_reasons: list[str] = []
    if len(local_attempts) < 3:
        blocking_reasons.append("local_community_route_attempts_insufficient")
    if not local_successes:
        blocking_reasons.append("local_community_query_not_successful")
    if not local_items:
        blocking_reasons.append("local_community_original_missing")
    coverage = {
        "status": "complete" if coverage_complete else "partial",
        "policy": "tw_local_first_community_v1" if is_taiwan_target(target) else "global_community_v1",
        "local_route_attempt_count": len(local_attempts),
        "local_successful_query_count": len(local_successes),
        "local_admitted_item_count": len(local_items),
        "global_route_attempt_count": len(attempts) - len(local_attempts),
        "blocking_reasons": [] if coverage_complete else blocking_reasons,
    }
    if items:
        status = "available"
        missing_reason = None
    elif local_successes:
        status = "insufficient_data"
        missing_reason = "local_community_search_completed_without_target_hits"
    else:
        status = "unavailable"
        missing_reason = "local_community_routes_unavailable"
    return {
        "status": status,
        "target": dict(target),
        "items": items[:max_items],
        "all_items": items,
        "attempts": attempts,
        "coverage": coverage,
        "missing_reason": missing_reason,
    }


def _fetch_ptt_stock(
    target: Mapping[str, Any],
    *,
    max_items: int,
    timeout_seconds: float,
    source_id_prefix: str,
) -> dict[str, Any]:
    query = _local_query(target)
    source_id = f"ptt_stock_{source_id_prefix}_search"
    url = f"{PTT_ROOT}/bbs/Stock/search?q={quote(query)}"
    collected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    attempts: list[dict[str, Any]] = []
    try:
        response = _get(url, timeout_seconds=timeout_seconds)
        rows = _parse_ptt_search(response.text, source_id=source_id, collected_at=collected_at)
        selected, _ = select_target_items(rows, target=target, question="PTT Stock target search")
        attempts.append({
            "source_id": source_id,
            "transport": "static_html",
            "url": url,
            "query": query,
            "region_scope": "TW",
            "language": "zh-TW",
            "route_status": "success",
            "query_status": "success_with_hits" if rows else "success_no_hits",
            "content_status": "target_relevant" if selected else "noise_only" if rows else "unavailable",
            "admission_status": "admitted" if selected else "rejected" if rows else "unresolved",
            "item_count": len(rows),
            "relevant_item_count": len(selected),
            "status_code": response.status_code,
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
        })
    except (httpx.HTTPError, ValueError) as exc:
        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        route_status = "blocked" if status_code == 403 else "failed"
        attempts.append(_failed_attempt(source_id, url, query, route_status, status_code, exc))
        return {"status": "unavailable", "items": [], "attempts": attempts}

    items: list[dict[str, Any]] = []
    for row in selected[:max_items]:
        article_url = str(row.get("canonical_url") or "")
        try:
            detail = _get(article_url, timeout_seconds=timeout_seconds)
            parsed = _parse_ptt_article(detail.text)
            content = parsed["content"] or str(row.get("title") or "")
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            item = {
                **dict(row),
                "item_id": build_item_id(source_id, article_url, content_hash),
                "content": content[:1_000_000],
                "summary": content[:1200],
                "content_sha256": content_hash,
                "engagement": {
                    "score": row.get("engagement", {}).get("score") if isinstance(row.get("engagement"), Mapping) else None,
                    "comments": len(parsed["comments"]),
                    "shares": None,
                    "likes": None,
                },
                "evidence": {
                    **dict(row.get("evidence") or {}),
                    "route": "ptt_stock_search_and_detail",
                    "region_scope": "TW",
                    "language": "zh-TW",
                    "author": row.get("evidence", {}).get("author") if isinstance(row.get("evidence"), Mapping) else None,
                    "comments": parsed["comments"],
                    "response_sha256": hashlib.sha256(detail.content).hexdigest(),
                    "status_code": detail.status_code,
                    "final_url": str(detail.url),
                    "extraction_method": "ptt_stock_html_v1",
                },
            }
            items.append(item)
            attempts.append({
                "source_id": source_id,
                "transport": "static_html",
                "url": article_url,
                "query": query,
                "region_scope": "TW",
                "language": "zh-TW",
                "route_status": "success",
                "query_status": "success_with_hits",
                "content_status": "target_relevant",
                "admission_status": "admitted",
                "item_count": 1,
                "relevant_item_count": 1,
                "status_code": detail.status_code,
                "response_sha256": hashlib.sha256(detail.content).hexdigest(),
            })
        except (httpx.HTTPError, ValueError) as exc:
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            attempts.append(_failed_attempt(source_id, article_url, query, "blocked" if status_code == 403 else "failed", status_code, exc))
    return {"status": "available" if items else "insufficient_data", "items": items, "attempts": attempts}


def _probe_taiwan_community_boundaries(target: Mapping[str, Any], *, timeout_seconds: float) -> list[dict[str, Any]]:
    query = _local_query(target)
    routes = [
        ("dcard_money_public_search", f"https://www.dcard.tw/service/api/v2/search/posts?query={quote(query)}&forum=money", "json_api"),
        ("mobile01_finance_public_search", f"https://www.mobile01.com/topiclist.php?f=291&q={quote(query)}", "static_html"),
    ]
    attempts: list[dict[str, Any]] = []
    for source_id, url, transport in routes:
        try:
            response = _get(url, timeout_seconds=timeout_seconds)
            attempts.append({
                "source_id": source_id, "transport": transport, "url": url, "query": query,
                "region_scope": "TW", "language": "zh-TW", "route_status": "success",
                "query_status": "not_evaluated", "content_status": "metadata_only",
                "admission_status": "unresolved", "item_count": 0, "relevant_item_count": 0,
                "status_code": response.status_code, "response_sha256": hashlib.sha256(response.content).hexdigest(),
                "evidence_admitted": False, "note": "boundary probe only; canonical adapter unavailable",
            })
        except httpx.HTTPStatusError as exc:
            attempts.append(_failed_attempt(source_id, url, query, "blocked" if exc.response.status_code == 403 else "failed", exc.response.status_code, exc))
        except httpx.HTTPError as exc:
            attempts.append(_failed_attempt(source_id, url, query, "failed", None, exc))
    return attempts


def _parse_ptt_search(content: str, *, source_id: str, collected_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in re.findall(r'<div class="r-ent">(.*?)(?=<div class="r-ent">|</body>)', content, flags=re.DOTALL):
        title_match = re.search(r'<div class="title">.*?<a href="([^"]+)">(.*?)</a>', block, flags=re.DOTALL)
        if not title_match:
            continue
        canonical_url = urljoin(PTT_ROOT, html.unescape(title_match.group(1)))
        title = _plain_text(title_match.group(2))
        author = _capture_text(block, "author")
        date_text = _capture_text(block, "date")
        score_text = _capture_text(block, "nrec")
        published_at = _ptt_date_to_iso(date_text, collected_at)
        content_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()
        rows.append({
            "schema_version": 1,
            "item_id": build_item_id(source_id, canonical_url, content_hash),
            "source_id": source_id,
            "canonical_url": canonical_url,
            "title": title,
            "summary": "",
            "content": title,
            "published_at": published_at,
            "collected_at": collected_at,
            "transport": "static_html",
            "kind": "social",
            "layer": "social",
            "content_sha256": content_hash,
            "rights": {"redistribution": "metadata_only", "retention_days": 365, "public_excerpt_chars": 0},
            "engagement": {"score": _ptt_score(score_text), "comments": None, "shares": None, "likes": None},
            "evidence": {"route": "ptt_stock_search", "region_scope": "TW", "language": "zh-TW", "author": author, "date_text": date_text},
        })
    return rows


def _parse_ptt_article(content: str) -> dict[str, Any]:
    main = re.search(r'<div id="main-content"[^>]*>(.*?)</body>', content, flags=re.DOTALL)
    if not main:
        raise ValueError("PTT article missing main-content")
    block = main.group(1)
    comments = []
    for push in re.findall(r'<div class="push">(.*?)</div>', block, flags=re.DOTALL):
        author = _capture_text(push, "push-userid")
        text = _capture_text(push, "push-content").lstrip(": ")
        timestamp = _capture_text(push, "push-ipdatetime")
        if text:
            comments.append({"author": author or None, "text": text[:2000], "timestamp": timestamp or None})
    without_pushes = re.sub(r'<div class="push">.*?</div>', " ", block, flags=re.DOTALL)
    without_meta = re.sub(r'<div class="article-metaline.*?</div>', " ", without_pushes, flags=re.DOTALL)
    return {"content": _plain_text(without_meta), "comments": comments}


def _normalize_global_attempt(attempt: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(attempt)
    status = str(current.get("status") or "failed")
    current.setdefault("region_scope", "global")
    current.setdefault("language", "en")
    current["route_status"] = "success" if status == "success" else status
    if current["route_status"] == "success":
        current["query_status"] = "success_with_hits" if int(current.get("item_count") or 0) else "success_no_hits"
        current["content_status"] = "target_relevant" if int(current.get("item_count") or 0) else "unavailable"
        current["admission_status"] = "admitted" if int(current.get("item_count") or 0) else "unresolved"
    else:
        current["query_status"] = "failed"
        current["content_status"] = "unavailable"
        current["admission_status"] = "unresolved"
    return current


def _failed_attempt(source_id: str, url: str, query: str, route_status: str, status_code: int | None, exc: Exception) -> dict[str, Any]:
    return {
        "source_id": source_id, "transport": "static_html", "url": url, "query": query,
        "region_scope": "TW", "language": "zh-TW", "route_status": route_status,
        "query_status": "failed", "content_status": "unavailable", "admission_status": "unresolved",
        "item_count": 0, "relevant_item_count": 0, "status_code": status_code,
        "error": f"{type(exc).__name__}: {exc}"[:500], "evidence_admitted": False,
    }


def _local_query(target: Mapping[str, Any]) -> str:
    names = target.get("local_names") if isinstance(target.get("local_names"), list) else []
    if names:
        local_name = str(names[0]).strip()
        ambiguous = {
            str(value).strip().casefold()
            for value in target.get("ambiguous_aliases", [])
            if isinstance(value, str) and value.strip()
        }
        if local_name.casefold() in ambiguous:
            ticker = str(target.get("symbol") or "").split(".", 1)[0].strip()
            if ticker:
                # A bare search for 「南亞」 is dominated by 南亞科技.  The
                # ticker-qualified issuer name is also how local investors
                # title company-specific posts, so it improves recall without
                # weakening the downstream identity/exclusion gate.
                return f"{ticker} {local_name}"
        return local_name
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    return next((str(value).strip() for value in aliases if any("\u4e00" <= char <= "\u9fff" for char in str(value))), str(target.get("name") or target.get("symbol") or "").strip())


def _get(url: str, *, timeout_seconds: float) -> httpx.Response:
    response = httpx.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; finance-research-validation/1.0)"},
        follow_redirects=True,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response


def _capture_text(content: str, class_name: str) -> str:
    match = re.search(rf'class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</', content, flags=re.DOTALL)
    return _plain_text(match.group(1)) if match else ""


def _plain_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _ptt_date_to_iso(date_text: str, collected_at: str) -> str | None:
    match = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})\s*", date_text)
    if not match:
        return None
    collected = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
    month, day = int(match.group(1)), int(match.group(2))
    year = collected.year - 1 if (month, day) > (collected.month, collected.day) else collected.year
    return datetime(year, month, day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _ptt_score(value: str) -> int | None:
    text = str(value or "").strip().upper()
    if text == "爆":
        return 100
    if text.startswith("X") and text[1:].isdigit():
        return -int(text[1:])
    return int(text) if text.isdigit() else None
