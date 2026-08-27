"""Target-specific public retrieval for evidence gaps in the full catalogue.

The 120-brand catalogue is intentionally broad and does not guarantee that a
specific issuer appears in every run.  This module adds a bounded, auditable
retrieval pass for the requested target using Yahoo's public RSS and search
routes.  It is supplemental evidence, not a replacement for the frozen
catalogue or a sentiment oracle.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from finance_crawler_poc.contracts import build_item_id
from finance_crawler_poc.research_routing import (
    annotate_attempt_outcomes,
    build_news_route_plan,
    evaluate_news_geo_coverage,
)
from finance_crawler_poc.target_scope import select_target_items


def fetch_yahoo_target_news(
    target: Mapping[str, Any],
    *,
    max_items: int = 30,
    timeout_seconds: float = 20.0,
    source_id_prefix: str | None = None,
) -> dict[str, Any]:
    """Fetch target-scoped Yahoo RSS/search items and preserve route evidence."""

    if max_items <= 0:
        raise ValueError("max_items must be positive")
    symbol = str(target.get("symbol") or "").strip().upper()
    name = str(target.get("name") or "").strip()
    if not symbol and not name:
        raise ValueError("target symbol or name is required")
    route_prefix = str(source_id_prefix or "target").strip().casefold() or "target"
    routes = build_news_route_plan(target, route_prefix=route_prefix, max_items=max_items)
    collected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    attempts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for route in routes:
        source_id = str(route["source_id"])
        transport = str(route["transport"])
        url = str(route["url"])
        try:
            response = _get(url, timeout_seconds=timeout_seconds)
            response_hash = hashlib.sha256(response.content).hexdigest()
            parsed = (
                _parse_rss(response.text, source_id=source_id, url=url, status_code=response.status_code, collected_at=collected_at)
                if transport == "rss"
                else _parse_search(response.json(), source_id=source_id, url=url, status_code=response.status_code, collected_at=collected_at)
            )
            for item in parsed:
                evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
                item["evidence"] = {
                    **evidence,
                    "query": route.get("query"),
                    "region_scope": route.get("region_scope"),
                    "language": route.get("language"),
                    "route_priority": route.get("priority"),
                }
            candidates.extend(parsed)
            attempts.append({
                "source_id": source_id,
                "transport": transport,
                "url": url,
                "status": "success",
                "status_code": response.status_code,
                "item_count": len(parsed),
                "response_sha256": response_hash,
                "query": route.get("query"),
                "region_scope": route.get("region_scope"),
                "language": route.get("language"),
                "priority": route.get("priority"),
            })
        except (httpx.HTTPError, json.JSONDecodeError, ET.ParseError, ValueError) as exc:
            attempts.append({
                "source_id": source_id,
                "transport": transport,
                "url": url,
                "status": "failed",
                "status_code": None,
                "item_count": 0,
                "error": f"{type(exc).__name__}: {exc}"[:500],
                "query": route.get("query"),
                "region_scope": route.get("region_scope"),
                "language": route.get("language"),
                "priority": route.get("priority"),
            })

    selected, scope = select_target_items(candidates, target=target, question="target-specific retrieval")
    unique: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in sorted(selected, key=lambda row: str(row.get("published_at") or ""), reverse=True):
        canonical_url = str(item.get("canonical_url") or "")
        if canonical_url in seen_urls:
            continue
        seen_urls.add(canonical_url)
        unique.append(item)
        if len(unique) >= max_items:
            break
    research_items = [item for item in unique if not _is_low_information_target_headline(item)]
    attempts = annotate_attempt_outcomes(attempts, research_items)
    noise_count = len(unique) - len(research_items)
    publisher_groups = {_publisher_group(item) for item in research_items}
    return {
        "status": "available" if research_items else "insufficient_data",
        "target": dict(target),
        "items": research_items,
        "all_items": unique,
        "noise_item_count": noise_count,
        "attempts": attempts,
        "geo_coverage": evaluate_news_geo_coverage(target, attempts),
        "target_scope": {
            **scope,
            "relevant_item_count": len(research_items),
            "relevant_source_group_count": len(publisher_groups),
            "relevant_route_count": len({item["source_id"] for item in research_items}),
            "input_item_count": len(candidates),
        },
        "missing_reason": None if research_items else "no_researchable_target_headlines_from_public_routes",
    }


def fetch_hackernews_target_community(
    target: Mapping[str, Any],
    *,
    max_items: int = 20,
    timeout_seconds: float = 20.0,
    source_id_prefix: str | None = None,
) -> dict[str, Any]:
    """Fetch target-scoped Hacker News discussions through Algolia.

    Hacker News is a public community API.  We keep the HN item URL as the
    canonical discussion source and the outbound article URL as evidence
    metadata; the discussion text is a narrative signal, never a fact layer.
    Failed/blocked routes are returned as explicit attempts so a report can
    distinguish "no discussion" from "route unavailable".
    """

    if max_items <= 0:
        raise ValueError("max_items must be positive")
    symbol = str(target.get("symbol") or "").strip()
    name = str(target.get("name") or "").strip()
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    # A target profile often lists the local-language name first.  HN is an
    # English-heavy community, so querying only aliases[0] silently turns a
    # successful API response into a false "no discussion" result.  Query
    # every non-empty identity term (bounded to keep the public API cheap),
    # then deduplicate stories by HN object ID.
    identity_terms = [
        *[str(value).strip() for value in aliases if str(value).strip()],
        name,
        symbol.removesuffix(".TW"),
    ]
    # Numeric tickers are highly collision-prone in broad community indexes
    # (for example, ``1101`` also names an unrelated galaxy).  They remain in
    # the target profile for financial identity, but are never used alone as
    # a social search/match term; a name or alphabetic alias must corroborate
    # the discussion before it enters the evidence pack.
    skipped_ambiguous_terms = sorted({term for term in identity_terms if re.fullmatch(r"\d+(?:\.TW)?", term, flags=re.IGNORECASE)})
    identity_terms = [term for term in identity_terms if term and not re.fullmatch(r"\d+(?:\.TW)?", term, flags=re.IGNORECASE)]
    queries: list[str] = []
    for term in identity_terms:
        if term and term not in queries:
            queries.append(term)
    queries = queries[:6]
    if not queries:
        raise ValueError("target symbol or name is required")
    route_prefix = str(source_id_prefix or "target").strip().casefold() or "target"
    source_id = f"hacker_news_{route_prefix}_api"
    collected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    attempts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen_object_ids: set[str] = set()
    detail_count = 0
    input_item_count = 0
    for query in queries:
        url = (
            "https://hn.algolia.com/api/v1/search_by_date?"
            f"query={quote(query)}&tags=story&hitsPerPage={int(max_items)}"
        )
        try:
            response = _get(url, timeout_seconds=timeout_seconds)
            response_hash = hashlib.sha256(response.content).hexdigest()
            payload = response.json()
            hits = payload.get("hits") if isinstance(payload, Mapping) else None
            if not isinstance(hits, list):
                raise ValueError("Hacker News Algolia response missing hits array")
            input_item_count += len(hits)
            accepted_count = 0
            for hit_index, hit in enumerate(hits):
                if len(candidates) >= max_items:
                    break
                if not isinstance(hit, Mapping):
                    continue
                object_id = _text(hit.get("objectID"))
                title = _text(hit.get("title"))
                if not object_id or not title or object_id in seen_object_ids:
                    continue
                discussion_url = f"https://news.ycombinator.com/item?id={quote(object_id, safe='')}"
                story_text = _strip_html(_text(hit.get("story_text")))
                outbound_url = _canonical_target_url(_text(hit.get("url")))
                headline_content = "\n".join(part for part in (title, story_text) if part).strip()
                if not _is_target_scoped_community_story(title, story_text, target):
                    continue
                seen_object_ids.add(object_id)
                accepted_count += 1
                comments: list[dict[str, Any]] = []
                # Keep the public story index broad, but cap expensive comment
                # detail calls across all identity queries.
                if detail_count < 10:
                    detail_count += 1
                    detail_url = f"https://hn.algolia.com/api/v1/items/{quote(object_id, safe='')}"
                    try:
                        detail_response = _get(detail_url, timeout_seconds=timeout_seconds)
                        detail_payload = detail_response.json()
                        children = detail_payload.get("children") if isinstance(detail_payload, Mapping) else None
                        if isinstance(children, list):
                            for child in children[:10]:
                                if not isinstance(child, Mapping):
                                    continue
                                comment_text = _strip_html(html.unescape(_text(child.get("text"))))
                                if not comment_text:
                                    continue
                                comments.append({
                                    "id": _text(child.get("id")) or None,
                                    "author": _text(child.get("author")) or None,
                                    "text": comment_text[:5000],
                                })
                        attempts.append({
                            "source_id": source_id,
                            "transport": "json_api",
                            "url": detail_url,
                            "query": query,
                            "status": "success",
                            "status_code": detail_response.status_code,
                            "item_count": len(comments),
                            "response_sha256": hashlib.sha256(detail_response.content).hexdigest(),
                        })
                    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                        attempts.append({
                            "source_id": source_id,
                            "transport": "json_api",
                            "url": detail_url,
                            "query": query,
                            "status": "failed",
                            "status_code": None,
                            "item_count": 0,
                            "error": f"{type(exc).__name__}: {exc}"[:500],
                        })
                comment_texts = [comment["text"] for comment in comments]
                content = "\n".join(part for part in (title, story_text, *comment_texts) if part).strip()
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                candidates.append({
                    "schema_version": 1,
                    "item_id": build_item_id(source_id, discussion_url, content_hash),
                    "source_id": source_id,
                    "canonical_url": discussion_url,
                    "title": title[:1000],
                    "summary": story_text[:5000],
                    "content": content[:1_000_000],
                    "published_at": _canonical_iso(hit.get("created_at")),
                    "collected_at": collected_at,
                    "transport": "json_api",
                    "kind": "social",
                    "layer": "social",
                    "content_sha256": content_hash,
                    "rights": {"redistribution": "metadata_only", "retention_days": 365, "public_excerpt_chars": 0},
                    "engagement": {
                        "score": _finite_int(hit.get("points")),
                        "comments": _finite_int(hit.get("num_comments")),
                        "shares": None,
                        "likes": None,
                    },
                    "evidence": {
                        "route": "hackernews_algolia",
                        "status_code": response.status_code,
                        "final_url": url,
                        "extraction_method": "hackernews_algolia_v1",
                        "response_sha256": response_hash,
                        "outbound_url": outbound_url or None,
                        "author": _text(hit.get("author")) or None,
                        "discussion_id": object_id,
                        "comments": comments,
                    },
                })
            attempts.append({
                "source_id": source_id,
                "transport": "json_api",
                "url": url,
                "query": query,
                "status": "success",
                "status_code": response.status_code,
                "item_count": accepted_count,
                "input_item_count": len(hits),
                "response_sha256": response_hash,
            })
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            attempts.append({
                "source_id": source_id,
                "transport": "json_api",
                "url": url,
                "query": query,
                "status": "failed",
                "status_code": None,
                "item_count": 0,
                "input_item_count": 0,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            })
    # Record the other public community boundaries even when Hacker News is
    # the only route from which we admit canonical social evidence.  A 403 or
    # challenge is a route limitation, not proof that the target has no
    # discussion; keeping it in the attempts ledger prevents silent coverage
    # claims and gives the report an actionable unresolved reason.
    attempts.extend(_probe_public_community_boundaries(
        target,
        timeout_seconds=timeout_seconds,
    ))
    if candidates:
        return {
            "status": "available",
            "target": dict(target),
            "items": candidates[:max_items],
            "all_items": candidates,
            "noise_item_count": 0,
            "attempts": attempts,
            "target_scope": {
                "relevant_item_count": len(candidates),
                "relevant_source_group_count": 1,
                "relevant_route_count": 1,
                "input_item_count": input_item_count,
                "query_count": len(queries),
                "queries": queries,
                "identity_terms": list(queries),
                "skipped_ambiguous_terms": skipped_ambiguous_terms,
            },
            "missing_reason": None,
        }
    failed = any(attempt.get("status") == "failed" for attempt in attempts)
    blocked = any(attempt.get("status") == "blocked" for attempt in attempts)
    return {
        "status": "unavailable" if failed and not any(attempt.get("status") == "success" for attempt in attempts) else "insufficient_data",
        "target": dict(target),
        "items": [],
        "all_items": [],
        "noise_item_count": 0,
        "attempts": attempts,
        "target_scope": {
            "relevant_item_count": 0,
            "relevant_source_group_count": 0,
            "relevant_route_count": 0,
            "input_item_count": input_item_count,
            "query_count": len(queries),
            "queries": queries,
            "identity_terms": list(queries),
            "skipped_ambiguous_terms": skipped_ambiguous_terms,
        },
        "missing_reason": (
            "hackernews_public_api_unavailable"
            if failed and not any(attempt.get("status") == "success" for attempt in attempts)
            else "community_routes_blocked_or_no_target_discussions"
            if blocked
            else "no_target_discussions_from_hackernews_public_api"
        ),
    }


def _get(url: str, *, timeout_seconds: float) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = httpx.get(
                url,
                timeout=timeout_seconds,
                headers={
                    "accept": "application/json,application/rss+xml,text/xml",
                    "user-agent": "finance-crawler-validation/1.0",
                },
            )
            if response.status_code == 429 and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 2:
                break
    raise last_error or RuntimeError("target retrieval failed")


def _probe_public_community_boundaries(
    target: Mapping[str, Any],
    *,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Probe public Reddit/Stocktwits boundaries for auditable status only.

    These routes frequently require OAuth or reject datacenter clients.  This
    bounded probe deliberately does not treat an HTTP 200 payload as social
    evidence; canonical items still require a target-specific adapter and
    preserved original discussion content.  The probe records whether the
    route is available, blocked, or failed so the Research Pack can distinguish
    "no matching discussion" from "platform access boundary".
    """

    symbol = str(target.get("symbol") or "").strip()
    name = str(target.get("name") or "").strip()
    query = next((str(value).strip() for value in (target.get("aliases") or []) if str(value).strip()), name or symbol)
    routes = [
        (
            "reddit_public_search",
            "https://www.reddit.com/search.json?"
            f"q={quote(query)}&restrict_sr=false&sort=new&limit=10",
        ),
        (
            "stocktwits_public_symbol_stream",
            f"https://api.stocktwits.com/api/2/streams/symbol/{quote(symbol, safe='.-')}.json",
        ),
    ]
    attempts: list[dict[str, Any]] = []
    for source_id, url in routes:
        try:
            response = _get(url, timeout_seconds=timeout_seconds)
            response_hash = hashlib.sha256(response.content).hexdigest()
            payload = response.json()
            if source_id == "reddit_public_search":
                data = payload.get("data") if isinstance(payload, Mapping) else {}
                rows = data.get("children") if isinstance(data, Mapping) else []
            else:
                rows = payload.get("messages") if isinstance(payload, Mapping) else []
            attempts.append({
                "source_id": source_id,
                "transport": "json_api",
                "url": url,
                "query": query,
                "status": "success",
                "status_code": response.status_code,
                "item_count": len(rows) if isinstance(rows, list) else 0,
                "response_sha256": response_hash,
                "evidence_admitted": False,
                "note": "boundary probe only; no canonical social adapter",
            })
        except httpx.HTTPStatusError as exc:
            response = exc.response
            code = response.status_code if response is not None else None
            attempts.append({
                "source_id": source_id,
                "transport": "json_api",
                "url": url,
                "query": query,
                "status": "blocked" if code in {401, 403, 429} else "failed",
                "status_code": code,
                "item_count": 0,
                "evidence_admitted": False,
                "error": f"HTTP {code}" if code is not None else "HTTP request failed",
            })
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            attempts.append({
                "source_id": source_id,
                "transport": "json_api",
                "url": url,
                "query": query,
                "status": "failed",
                "status_code": None,
                "item_count": 0,
                "evidence_admitted": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            })
    return attempts


def _parse_rss(
    content: str,
    *,
    source_id: str,
    url: str,
    status_code: int,
    collected_at: str,
) -> list[dict[str, Any]]:
    root = ET.fromstring(content)
    rows: list[dict[str, Any]] = []
    for entry in root.findall(".//item"):
        title = _text(entry.findtext("title"))
        link = _canonical_target_url(_text(entry.findtext("link")))
        if not title or not link:
            continue
        summary = _text(entry.findtext("description"))
        publisher_node = entry.find("source")
        publisher_name = _text(publisher_node.text if publisher_node is not None else "")
        publisher_url = _canonical_target_url(_text(publisher_node.get("url") if publisher_node is not None else ""))
        published_at = _parse_rss_datetime(entry.findtext("pubDate"))
        rows.append(_build_item(
            source_id=source_id,
            canonical_url=link,
            title=title,
            summary=summary,
            published_at=published_at,
            collected_at=collected_at,
            transport="rss",
            status_code=status_code,
            final_url=url,
            route="target_rss",
            publisher_name=publisher_name,
            publisher_url=publisher_url,
        ))
    return rows


def _parse_search(
    payload: Any,
    *,
    source_id: str,
    url: str,
    status_code: int,
    collected_at: str,
) -> list[dict[str, Any]]:
    rows = payload.get("news") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("Yahoo target search response missing news array")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        title = _text(row.get("title"))
        link = _canonical_target_url(_text(row.get("link")))
        if not title or not link:
            continue
        timestamp = row.get("providerPublishTime")
        published_at = _epoch_to_iso(timestamp)
        publisher = _text(row.get("publisher"))
        parsed.append(_build_item(
            source_id=source_id,
            canonical_url=link,
            title=title,
            summary=publisher,
            published_at=published_at,
            collected_at=collected_at,
            transport="json_api",
            status_code=status_code,
            final_url=url,
            route="target_search",
        ))
    return parsed


def _build_item(
    *,
    source_id: str,
    canonical_url: str,
    title: str,
    summary: str,
    published_at: str | None,
    collected_at: str,
    transport: str,
    status_code: int,
    final_url: str,
    route: str,
    publisher_name: str = "",
    publisher_url: str = "",
) -> dict[str, Any]:
    content = "\n".join(part for part in (title, summary) if part).strip()
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    evidence: dict[str, Any] = {
        "route": route,
        "status_code": status_code,
        "final_url": final_url,
        "extraction_method": f"target_yahoo_{transport}",
    }
    normalized_publisher_id = _publisher_id(publisher_name)
    if normalized_publisher_id and publisher_url:
        evidence.update({
            "publisher_verified": True,
            "publisher_id": normalized_publisher_id,
            "publisher_name": publisher_name[:300],
            "publisher_url": publisher_url,
            "publisher_resolution": "rss_source_element_v1",
        })
    return {
        "schema_version": 1,
        "item_id": build_item_id(source_id, canonical_url, content_hash),
        "source_id": source_id,
        "canonical_url": canonical_url,
        "title": title[:1000],
        "summary": summary[:5000],
        "content": content[:1_000_000],
        "published_at": published_at,
        "collected_at": collected_at,
        "transport": transport,
        "kind": "news",
        "layer": "news",
        "content_sha256": content_hash,
        "rights": {"redistribution": "metadata_only", "retention_days": 365, "public_excerpt_chars": 0},
        "engagement": {"score": None, "comments": None, "shares": None, "likes": None},
        "evidence": evidence,
    }


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _strip_html(value: str) -> str:
    return _text(re.sub(r"<[^>]+>", " ", value))


def _canonical_iso(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _matches_target_community(content: str, target: Mapping[str, Any]) -> bool:
    text = content.casefold()
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    symbol = str(target.get("symbol") or "").strip()
    identity_values = [*aliases, target.get("name"), symbol, symbol.removesuffix(".TW")]
    terms = list(dict.fromkeys(str(value).strip().casefold() for value in identity_values if str(value).strip() and not re.fullmatch(r"\d+(?:\.tw)?", str(value).strip(), flags=re.IGNORECASE)))
    for term in terms:
        if " " in term:
            if term in text:
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            return True
    return False


def _is_target_scoped_community_story(title: str, body: str, target: Mapping[str, Any]) -> bool:
    """Require a title or sufficiently strong body match for social evidence.

    A long forum post can mention a ticker or brand once while discussing a
    competitor (for example, an ASUS laptop appearing in a Dell question).
    Title matches and full legal-name matches are strong; a short alias in the
    body must occur at least twice before the story is admitted.  This keeps
    incidental mentions out of the L3 social gate without discarding genuine
    discussions whose title uses a generic forum prefix.
    """

    if _matches_target_community(title, target):
        return True
    title_body = f"{title}\n{body}".casefold()
    legal_name = str(target.get("name") or "").strip().casefold()
    if legal_name and len(legal_name) >= 8 and legal_name in title_body:
        return True
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    symbol = str(target.get("symbol") or "").strip()
    values = [*aliases, symbol, symbol.removesuffix(".TW")]
    terms = [
        str(value).strip().casefold()
        for value in values
        if str(value).strip() and not re.fullmatch(r"\d+(?:\.tw)?", str(value).strip(), flags=re.IGNORECASE)
    ]
    return any(len(term) >= 4 and title_body.count(term) >= 2 for term in dict.fromkeys(terms))


def _publisher_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized[:120]


def _publisher_group(item: Mapping[str, Any]) -> str:
    evidence = item.get("evidence")
    if isinstance(evidence, Mapping) and evidence.get("publisher_verified") is True and evidence.get("publisher_id"):
        return str(evidence["publisher_id"])
    return str(item.get("source_id") or "unknown")


def _is_low_information_target_headline(item: Mapping[str, Any]) -> bool:
    """Exclude obvious affiliate, ownership, and opinion filler from research evidence."""

    title = str(item.get("title") or "").casefold()
    patterns = (
        r"\bconsensus recommendation\b",
        r"\bshares?\b.*\b(?:purchased|bought|acquires?|acquired)\b",
        r"\bpurchases?\b.*\bshares?\b",
        r"\b(?:buy|buys|buying)\b.*\bshares?\b",
        r"\bholdings?\b.*\b(?:purchased|bought|acquires?|acquired)\b",
        r"\bacquires?\b.*\bnew position\b",
        r"\bpurchases?\b.*\bnew holdings?\b",
        r"\b\d[\d,]*\s+shares?\b.*\b(?:bought|purchased)\b",
        r"\bwhich .*\bstock\b.*\bbetter buy\b",
        r"\bvs\.?\b.*\bbetter buy\b",
        r"\bwhat(?:'s| is) going on with .*\bstock\b",
        r"\breasons? to be bullish\b",
        r"\bworth investing in\b.*\bbullish views?\b",
        r"\bprediction\s*:",
        r"\bhistory of\b.*\bstock\b",
        r"\blooks built for long-term growth\b",
        r"\b(?:vp|spouse)\b.*\b(?:buy|buys|buying|purchase|purchases)\b",
        r"\b(?:lp|partners?|management)\b.*\binvests?\b.*\b(?:million|shares?|stock)\b",
    )
    return any(re.search(pattern, title) for pattern in patterns)


def _canonical_target_url(value: str) -> str:
    """Remove Yahoo's transport tracking suffix before evidence de-duplication."""

    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return value
    query = parts.query
    if query == ".tsrc=rss":
        query = ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _parse_rss_datetime(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return None


def _epoch_to_iso(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
