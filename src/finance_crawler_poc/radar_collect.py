from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

from finance_crawler_poc.adapters import Crawl4AIAdapter, HttpAdapter
from finance_crawler_poc.contracts import build_item_id, validate_contract
from finance_crawler_poc.models import FetchResponse, Source
from finance_crawler_poc.radar_manifest import RadarManifest, RadarSource
from finance_crawler_poc.radar_run_plan import CatchupWindow


Extractor = Callable[[RadarSource, str], Iterable[dict[str, Any]]]
TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class RadarCollection:
    items: tuple[dict[str, Any], ...]
    checkpoints: tuple[dict[str, Any], ...]
    source_results: tuple[dict[str, Any], ...]

    @property
    def successful_source_count(self) -> int:
        return sum(result["status"] == "success" for result in self.source_results)

    @property
    def failed_source_ids(self) -> tuple[str, ...]:
        return tuple(
            str(result["source_id"])
            for result in self.source_results
            if result["status"] != "success"
        )


def extract_source_items(
    source: RadarSource,
    response: FetchResponse,
    collected_at: str,
    *,
    published_since: str | None = None,
) -> list[dict[str, Any]]:
    if response.error:
        raise ValueError(response.error)
    if response.status_code is not None and not 200 <= response.status_code < 400:
        raise ValueError(f"HTTP {response.status_code}")
    if not response.content.strip():
        raise ValueError("empty response content")
    extractor = EXTRACTORS[source.extractor]
    records = list(extractor(source, response.content))
    if not records and published_since is not None:
        return []
    if not records:
        raise ValueError("extractor returned no items")
    if published_since is not None:
        records = [
            record for record in records
            if _record_is_in_window(record, published_since)
        ]
        if not records:
            return []
    records = records[: source.max_items]

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in records:
        item = _build_item(source, record, response, collected_at)
        if item["item_id"] in seen_ids:
            continue
        validate_contract("raw-item", item)
        seen_ids.add(item["item_id"])
        items.append(item)
    if not items:
        raise ValueError("extractor returned only duplicate items")
    return items


async def collect_radar_sources(
    manifest: RadarManifest,
    *,
    collected_at: str,
    catchup_windows: tuple[CatchupWindow, ...] | None = None,
) -> RadarCollection:
    http_adapter = HttpAdapter()
    browser_adapter = Crawl4AIAdapter()
    items: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    try:
        windows = catchup_windows or tuple(
            CatchupWindow(source.source_id, "latest_only", source.canonical_url, None)
            for source in manifest.sources
        )
        if [window.source_id for window in windows] != [
            source.source_id for source in manifest.sources
        ]:
            raise ValueError("catch-up windows do not match manifest source order")
        for source, window in zip(manifest.sources, windows, strict=True):
            adapter = browser_adapter if source.transport == "browser" else http_adapter
            response: FetchResponse | None = None
            try:
                response = await adapter.fetch(_as_probe_source(source, window.request_url))
                source_items = extract_source_items(
                    source,
                    response,
                    collected_at,
                    published_since=window.published_since,
                )
                remaining = manifest.maximum_items_per_run - len(items)
                source_items = source_items[:remaining]
                if remaining <= 0:
                    raise ValueError("maximum_items_per_run reached")
                items.extend(source_items)
                published = [
                    str(item["published_at"])
                    for item in source_items
                    if item["published_at"] is not None
                ]
                checkpoints.append(
                    {
                        "source_id": source.source_id,
                        "status": "success",
                        "last_successful_crawl": collected_at,
                        "last_article_date": max(published) if published else None,
                        "cursor": None,
                    }
                )
                results.append(
                    {
                        "source_id": source.source_id,
                        "transport": source.transport,
                        "status": "success",
                        "status_code": response.status_code,
                        "route": response.route,
                        "item_count": len(source_items),
                        "catchup_strategy": window.strategy,
                        "published_since": window.published_since,
                        "error": "",
                    }
                )
            except Exception as exc:
                checkpoints.append(
                    {
                        "source_id": source.source_id,
                        "status": "failed",
                        "last_successful_crawl": None,
                        "last_article_date": None,
                        "cursor": None,
                    }
                )
                failure = _source_failure_result(source, response, exc)
                failure["catchup_strategy"] = window.strategy
                failure["published_since"] = window.published_since
                results.append(failure)
    finally:
        await http_adapter.close()
        await browser_adapter.close()
    return RadarCollection(
        items=tuple(items),
        checkpoints=tuple(checkpoints),
        source_results=tuple(results),
    )


def _source_failure_result(
    source: RadarSource,
    response: FetchResponse | None,
    error: Exception,
) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "transport": source.transport,
        "status": "failed",
        "status_code": response.status_code if response is not None else None,
        "route": response.route if response is not None else "unknown",
        "item_count": 0,
        "error": f"{type(error).__name__}: {error}"[:1000],
    }


def _as_probe_source(source: RadarSource, request_url: str) -> Source:
    return Source(
        id=source.source_id,
        name=source.name,
        topic="topic_radar",
        transport=source.transport,
        url=request_url,
        min_content_chars=1,
        timeout_seconds=source.timeout_seconds,
        retries=0,
        kind=source.kind,
    )


def _record_is_in_window(record: dict[str, Any], published_since: str) -> bool:
    normalized = _normalize_datetime(record.get("published_at"))
    if normalized is None:
        return True
    try:
        published = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        boundary = datetime.fromisoformat(published_since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("published_since must be RFC 3339") from exc
    return published >= boundary


def _build_item(
    source: RadarSource,
    record: dict[str, Any],
    response: FetchResponse,
    collected_at: str,
) -> dict[str, Any]:
    title = _clean_text(str(record.get("title", "")))[:1000]
    if not title:
        raise ValueError("item title is empty")
    summary = _clean_text(str(record.get("summary", "")))[:5000]
    content = str(record.get("content", "")).strip()[:1_000_000]
    if not content:
        content = f"{title}\n{summary}".strip()
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    canonical_url = _safe_item_url(str(record.get("canonical_url", "")), source)
    item_id = build_item_id(source.source_id, canonical_url, content_sha256)
    engagement = record.get("engagement")
    if not isinstance(engagement, dict):
        engagement = {}
    item = {
        "schema_version": 1,
        "item_id": item_id,
        "source_id": source.source_id,
        "canonical_url": canonical_url,
        "title": title,
        "summary": summary,
        "content": content,
        "published_at": _normalize_datetime(record.get("published_at")),
        "collected_at": collected_at,
        "transport": source.transport,
        "kind": source.kind,
        "layer": source.layer,
        "content_sha256": content_sha256,
        "rights": dict(source.rights),
        "engagement": {
            "score": _nullable_nonnegative(engagement.get("score")),
            "comments": _nullable_nonnegative(engagement.get("comments")),
            "shares": _nullable_nonnegative(engagement.get("shares")),
            "likes": _nullable_nonnegative(engagement.get("likes")),
        },
        "evidence": {
            "route": response.route or "unknown",
            "status_code": response.status_code,
            "final_url": response.final_url or source.canonical_url,
            "extraction_method": source.extractor,
        },
    }
    return item


def _extract_rss(source: RadarSource, content: str) -> Iterable[dict[str, Any]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"invalid RSS/XML: {exc}") from exc
    entries = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    for index, entry in enumerate(entries):
        title = _child_text(entry, ("title",))
        link = _entry_link(entry) or f"{source.canonical_url}#item-{index + 1}"
        description = _child_text(entry, ("description", "summary", "content", "encoded"))
        yield {
            "title": title,
            "canonical_url": link,
            "summary": description,
            "content": f"{title}\n{description}".strip(),
            "published_at": _child_text(entry, ("pubDate", "published", "updated", "date")),
        }


def _extract_hn(source: RadarSource, content: str) -> Iterable[dict[str, Any]]:
    data = _json_object(content)
    hits = data.get("hits")
    if not isinstance(hits, list):
        raise ValueError("Algolia response has no hits list")
    for item in hits:
        if not isinstance(item, dict):
            continue
        object_id = str(item.get("objectID", ""))
        title = str(item.get("title") or item.get("story_title") or "")
        url = str(item.get("url") or item.get("story_url") or "")
        if not url and object_id:
            url = f"https://news.ycombinator.com/item?id={object_id}"
        yield {
            "title": title,
            "canonical_url": url or source.canonical_url,
            "summary": "",
            "content": title,
            "published_at": item.get("updated_at") or item.get("created_at"),
            "engagement": {"score": item.get("points"), "comments": item.get("num_comments")},
        }


def _extract_stackexchange(source: RadarSource, content: str) -> Iterable[dict[str, Any]]:
    data = _json_object(content)
    entries = data.get("items")
    if not isinstance(entries, list):
        raise ValueError("Stack Exchange response has no items list")
    for item in entries:
        if not isinstance(item, dict):
            continue
        yield {
            "title": item.get("title", ""),
            "canonical_url": item.get("link", source.canonical_url),
            "summary": "",
            "content": str(item.get("title", "")),
            "published_at": item.get("creation_date"),
            "engagement": {"score": item.get("score"), "comments": item.get("answer_count")},
        }


def _extract_github_issues(source: RadarSource, content: str) -> Iterable[dict[str, Any]]:
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("GitHub response is not an issue list")
    for item in data:
        if not isinstance(item, dict) or "pull_request" in item:
            continue
        body = str(item.get("body") or "")
        title = str(item.get("title") or "")
        yield {
            "title": title,
            "canonical_url": item.get("html_url", source.canonical_url),
            "summary": body[:1000],
            "content": f"{title}\n{body}".strip(),
            "published_at": item.get("created_at"),
            "engagement": {"comments": item.get("comments")},
        }


def _extract_coingecko(source: RadarSource, content: str) -> Iterable[dict[str, Any]]:
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("CoinGecko response is not a market list")
    for item in data:
        if not isinstance(item, dict):
            continue
        coin_id = str(item.get("id", ""))
        name = str(item.get("name", coin_id))
        symbol = str(item.get("symbol", "")).upper()
        subset = {
            key: item.get(key)
            for key in (
                "id",
                "symbol",
                "name",
                "current_price",
                "market_cap",
                "price_change_percentage_24h",
                "last_updated",
            )
        }
        yield {
            "title": f"{source.name}: {name} ({symbol})",
            "canonical_url": f"https://www.coingecko.com/en/coins/{coin_id}" if coin_id else source.canonical_url,
            "summary": "",
            "content": json.dumps(subset, ensure_ascii=False, sort_keys=True),
            "published_at": item.get("last_updated"),
        }


def _extract_world_bank(source: RadarSource, content: str) -> Iterable[dict[str, Any]]:
    data = json.loads(content)
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        raise ValueError("World Bank response has no observation list")
    for item in data[1]:
        if not isinstance(item, dict) or item.get("value") is None:
            continue
        indicator = item.get("indicator") if isinstance(item.get("indicator"), dict) else {}
        country = item.get("country") if isinstance(item.get("country"), dict) else {}
        title = f"{source.name}: {indicator.get('value', 'Indicator')} — {country.get('value', '')} {item.get('date', '')}"
        subset = {
            "indicator": indicator,
            "country": country,
            "countryiso3code": item.get("countryiso3code"),
            "date": item.get("date"),
            "value": item.get("value"),
        }
        yield {
            "title": title,
            "canonical_url": source.canonical_url,
            "summary": "",
            "content": json.dumps(subset, ensure_ascii=False, sort_keys=True),
            "published_at": item.get("date"),
        }


def _extract_browser(source: RadarSource, content: str) -> Iterable[dict[str, Any]]:
    cleaned = " ".join(content.split())
    yield {
        "title": source.name,
        "canonical_url": source.canonical_url,
        "summary": cleaned[:1000],
        "content": cleaned[:100_000],
        "published_at": None,
    }


EXTRACTORS: dict[str, Extractor] = {
    "browser_document": _extract_browser,
    "coingecko_markets": _extract_coingecko,
    "github_issues": _extract_github_issues,
    "hn_algolia": _extract_hn,
    "rss": _extract_rss,
    "stackexchange": _extract_stackexchange,
    "world_bank": _extract_world_bank,
}


def _json_object(content: str) -> dict[str, Any]:
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("JSON response is not an object")
    return data


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(entry: ET.Element, names: tuple[str, ...]) -> str:
    for child in entry:
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _entry_link(entry: ET.Element) -> str:
    for child in entry:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text:
            return child.text.strip()
    return ""


def _clean_text(value: str) -> str:
    return " ".join(html.unescape(TAG_PATTERN.sub(" ", value)).split())


def _safe_item_url(value: str, source: RadarSource) -> str:
    absolute = urljoin(source.canonical_url, value.strip()) if value.strip() else source.canonical_url
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return source.canonical_url
    return absolute[:4096]


def _normalize_datetime(value: object) -> str | None:
    if value is None or value == "":
        return None
    parsed: datetime
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        elif isinstance(value, str) and re.fullmatch(r"\d{4}", value):
            parsed = datetime(int(value), 1, 1, tzinfo=timezone.utc)
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                parsed = parsedate_to_datetime(value)
        else:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nullable_nonnegative(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value
