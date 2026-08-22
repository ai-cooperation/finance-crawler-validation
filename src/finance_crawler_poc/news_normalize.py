"""Turn the frozen 120-brand raw capture into the ingest contract.

The news probe deliberately stores endpoint payloads verbatim.  This module is
the deterministic boundary between those payloads and the research system:
one normalized evidence item is emitted for each successful endpoint payload,
while endpoint failures remain in the checkpoint/observation counts.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urljoin

from finance_crawler_poc.contracts import ContractValidationError, validate_contract
from finance_crawler_poc.target_scope import select_target_items


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def normalize_news_capture(
    manifest_path: Path,
    *,
    workflow_run_id: str,
    commit_sha: str,
    collected_at: str,
    target: dict[str, Any] | None = None,
    question: str | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Build an ingest envelope from a frozen raw capture manifest.

    ``manifest_path`` is intentionally the only source of truth.  The
    generated envelope records the manifest hash, full-catalog counts, and
    every brand checkpoint so a later worker can prove what was attempted.
    """

    if not workflow_run_id.isdigit():
        raise ValueError("workflow_run_id must contain only digits")
    if len(commit_sha) != 40 or any(c not in "0123456789abcdef" for c in commit_sha):
        raise ValueError("commit_sha must be a lowercase 40-character Git SHA")
    collected = _parse_datetime(collected_at)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = payload.get("payloads")
    if not isinstance(records, list):
        raise ValueError("news raw manifest payloads must be an array")

    output_root = manifest_path.parent
    attempts_by_brand: dict[str, list[dict[str, Any]]] = {}
    items: list[dict[str, Any]] = []
    normalization_errors: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("raw manifest payload record must be an object")
        brand_id = _required_string(record, "brand_id")
        attempts_by_brand.setdefault(brand_id, []).append(record)
        payload_path = record.get("payload_path")
        if record.get("outcome") != "success" or not isinstance(payload_path, str):
            continue
        body_path = output_root / payload_path
        if not body_path.is_file():
            continue
        body = body_path.read_text(encoding="utf-8", errors="replace")
        if not body.strip():
            continue
        try:
            item = _normalize_payload(record, body, collected_at=collected_at)
            validate_contract("raw-item", item)
        except (ContractValidationError, ValueError) as exc:
            # One malformed extractor result must not discard the other 180+
            # endpoints.  Keep an auditable per-endpoint diagnostic; the raw
            # payload remains in the private boundary artifact for repair.
            normalization_errors.append({
                "brand_id": brand_id,
                "endpoint_id": _required_string(record, "endpoint_id"),
                "error": str(exc),
            })
            continue
        items.append(item)

    brands = sorted(attempts_by_brand)
    checkpoints = [_checkpoint(brand_id, attempts_by_brand[brand_id], collected_at) for brand_id in brands]
    successful_brands = sum(checkpoint["status"] == "success" for checkpoint in checkpoints)
    failed_endpoint_count = sum(
        record.get("outcome") != "success" for record in records if isinstance(record, dict)
    )
    scoped_items, target_scope = select_target_items(items, target=target, question=question)
    normalized = {
        "schema_version": 1,
        "operation": "upsert_items",
        "collection_scope": "full_catalog",
        "run_id": _run_id(collected),
        "workflow_run_id": workflow_run_id,
        "commit_sha": commit_sha,
        "snapshot_id": _snapshot_id(collected),
        "source_manifest_hash": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "collected_at": collected_at,
        "items": items,
        "checkpoints": checkpoints,
        "collection_source_group_count": len(brands),
        "endpoint_attempt_count": len(records),
        "successful_source_group_count": successful_brands,
        "failed_endpoint_count": failed_endpoint_count + len(normalization_errors),
        "normalization_error_count": len(normalization_errors),
        "normalization_errors": normalization_errors,
        "normalized_item_count": len(items),
        # Raw items stay complete/public.  These counters describe the
        # target-scoped view consumed by radar and report generation.
        "target_relevant_item_count": len(scoped_items),
        "model_context_item_count": len(scoped_items),
        "evidence_appendix_item_count": len(scoped_items),
        "target_scope": target_scope,
    }
    # A completely failed capture is still a useful diagnostic result, but it
    # cannot be submitted to the ingest endpoint because that contract
    # intentionally requires at least one evidence item.
    if items:
        validate_contract("ingest-envelope", {
            key: normalized[key]
            for key in (
                "schema_version", "operation", "run_id", "workflow_run_id", "commit_sha",
                "snapshot_id", "source_manifest_hash", "collected_at", "items", "checkpoints",
            )
        })
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return normalized


def _normalize_payload(record: dict[str, Any], body: str, *, collected_at: str) -> dict[str, Any]:
    source_id = _required_string(record, "brand_id")
    endpoint_id = _required_string(record, "endpoint_id")
    request_url = _required_string(record, "url")
    final_url = _safe_url(str(record.get("final_url") or request_url), fallback=request_url)
    title, summary, canonical_url, published_at = _extract_payload(
        body,
        transport=str(record.get("transport") or "static_html"),
        base_url=final_url,
    )
    canonical_url = _safe_url(canonical_url or final_url or request_url, fallback=final_url or request_url)
    title = title or f"{source_id} {endpoint_id} capture"
    summary = summary or _text(body)[:5000]
    content = body[:1_000_000]
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    item_id = hashlib.sha256(f"{source_id}|{canonical_url}|{content_hash}".encode()).hexdigest()
    transport = str(record.get("transport") or "static_html")
    kind = "news"
    brand_class = str(record.get("brand_class") or "")
    if "community" in brand_class:
        kind = "community"
    return {
        "schema_version": 1,
        "item_id": item_id,
        "source_id": source_id,
        "canonical_url": canonical_url,
        "title": title[:1000],
        "summary": summary[:5000],
        "content": content,
        "published_at": published_at,
        "collected_at": collected_at,
        "transport": transport,
        "kind": kind,
        "layer": "news",
        "content_sha256": content_hash,
        "rights": {"redistribution": "metadata_only", "retention_days": 365, "public_excerpt_chars": 0},
        "engagement": {"score": None, "comments": None, "shares": None, "likes": None},
        "evidence": {
            "route": endpoint_id,
            "status_code": _nullable_int(record.get("status_code")),
            "final_url": final_url,
            "extraction_method": f"news_raw_{transport}",
        },
    }


def _extract_payload(body: str, *, transport: str, base_url: str) -> tuple[str, str, str, str | None]:
    if transport == "rss" or "<rss" in body[:500].lower() or "<feed" in body[:500].lower():
        try:
            root = ET.fromstring(body)
            entry = next(iter(root.findall(".//item")), None)
            if entry is None:
                entry = next(iter(root.findall(".//{*}entry")), None)
            if entry is not None:
                title = _element_text(entry, "title")
                link = _element_text(entry, "link") or _element_attr(entry, "link", "href")
                summary = _element_text(entry, "description") or _element_text(entry, "summary") or _element_text(entry, "content")
                date = _element_text(entry, "pubDate") or _element_text(entry, "published") or _element_text(entry, "updated")
                return title, _text(summary), _safe_url(urljoin(base_url, link), fallback=base_url), _parse_published(date)
        except ET.ParseError:
            pass
    if transport == "json_api" or body.lstrip().startswith(("{", "[")):
        try:
            value = json.loads(body)
            record = _first_mapping(value)
            if record is not None:
                title = _first_value(record, ("title", "name", "headline"))
                summary = _first_value(record, ("summary", "description", "excerpt", "content", "text"))
                link = _first_value(record, ("url", "link", "canonical_url", "web_url"))
                date = _first_value(record, ("published_at", "published", "date", "updated_at"))
                return str(title or ""), _text(str(summary or "")), _safe_url(urljoin(base_url, str(link or "")), fallback=base_url), _parse_published(str(date or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    plain = _text(body)
    title = _html_meta(body, "og:title") or _html_tag(body, "title")
    link = _html_meta(body, "og:url") or (_URL_RE.search(body).group(0) if _URL_RE.search(body) else base_url)
    summary = _html_meta(body, "description") or plain[:5000]
    return title, summary, _safe_url(urljoin(base_url, link), fallback=base_url), None


def _checkpoint(brand_id: str, records: list[dict[str, Any]], collected_at: str) -> dict[str, Any]:
    successful = [r for r in records if r.get("outcome") == "success"]
    return {
        "source_id": brand_id,
        "status": "success" if successful else "failed",
        "last_successful_crawl": collected_at if successful else None,
        "last_article_date": None,
        "cursor": None,
    }


def _first_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        for key in ("data", "items", "results", "articles", "feed"):
            nested = value.get(key)
            if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                return nested[0]
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _first_value(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if value.get(key) not in (None, ""):
            return value[key]
    return None


def _element_text(parent: ET.Element, name: str) -> str:
    node = next(iter(parent.findall(name)), None)
    if node is None:
        node = next(iter(parent.findall(f".//{{*}}{name}")), None)
    return "" if node is None else "".join(node.itertext()).strip()


def _element_attr(parent: ET.Element, name: str, attr: str) -> str:
    node = next(iter(parent.findall(name)), None)
    if node is None:
        node = next(iter(parent.findall(f".//{{*}}{name}")), None)
    return "" if node is None else str(node.attrib.get(attr, ""))


def _html_meta(body: str, name: str) -> str:
    match = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)', body, re.I)
    return html.unescape(match.group(1)).strip() if match else ""


def _html_tag(body: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", body, re.I | re.S)
    return _text(match.group(1)) if match else ""


def _text(value: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def _parse_published(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            return None


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("collected_at must include timezone")
    return parsed.astimezone(UTC)


def _safe_url(value: str, *, fallback: str) -> str:
    """Strip markup punctuation accidentally captured from HTML/Markdown."""
    candidate = html.unescape(str(value or "")).strip()
    # HTML-to-text extractors occasionally join Markdown links, yielding
    # ``https://first.example/a)](https://second.example/b``.  Preserve the
    # first URL rather than allowing the second URL's scheme into its path.
    candidate = re.split(r"\]\s*\(|\)\s*!\[|!\[", candidate, maxsplit=1)[0]
    candidate = candidate.rstrip("![]()]>}.,;:'\"`")
    if not candidate.startswith(("http://", "https://")):
        candidate = fallback
    return candidate


def _run_id(value: datetime) -> str:
    return "run_" + value.strftime("%Y%m%dt%H%M%Sz")


def _snapshot_id(value: datetime) -> str:
    return "radar_" + value.strftime("%Y%m%dt%H%M%Sz")


def _required_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"raw manifest field {key} must be a non-empty string")
    return value


def _nullable_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize a full news raw capture into an ingest envelope")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workflow-run-id", default="0")
    parser.add_argument("--commit-sha", default="0" * 40)
    parser.add_argument("--collected-at", default=datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    parser.add_argument("--target-json")
    parser.add_argument("--question")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = normalize_news_capture(
        args.manifest,
        workflow_run_id=args.workflow_run_id,
        commit_sha=args.commit_sha,
        collected_at=args.collected_at,
        target=json.loads(args.target_json) if args.target_json else None,
        question=args.question,
        output_path=args.output,
    )
    print(json.dumps({key: result[key] for key in (
        "run_id", "snapshot_id", "collection_source_group_count", "endpoint_attempt_count",
        "normalized_item_count", "failed_endpoint_count",
        "normalization_error_count",
    )}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
