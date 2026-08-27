"""Reusable, target-configured research runner for equity verticals.

The runner owns orchestration and artifact isolation.  Financial-depth,
canonical evidence, quality gates and provenance remain shared domain logic.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from finance_crawler_poc import market_depth
from finance_crawler_poc.canonical_evidence import build_canonical_evidence_pack
from finance_crawler_poc.market_depth import (
    build_financial_depth,
    fetch_market_history,
    fetch_yahoo_fundamentals,
    fetch_yahoo_peer_valuation,
    fetch_yahoo_volume,
)
from finance_crawler_poc.official_sources import (
    fetch_sec_filing_evidence,
    fetch_twse_company_profile_evidence,
    fetch_twse_financial_statement_evidence,
)
from finance_crawler_poc.source_registry import build_registry_for_items, build_source_registry
from finance_crawler_poc.target_community import fetch_target_community
from finance_crawler_poc.target_retrieval import fetch_yahoo_target_news
from finance_crawler_poc.target_scope import select_target_items
from finance_crawler_poc.target_profiles import target_source_registry


def artifact_prefix(profile: Mapping[str, Any]) -> str:
    """Return a stable, filesystem-safe prefix such as ``delta-2308tw``."""

    target_id = re.sub(r"[^a-z0-9]+", "-", str(profile.get("target_id") or "target").casefold()).strip("-")
    target = profile.get("target") if isinstance(profile.get("target"), Mapping) else {}
    symbol = re.sub(r"[^a-z0-9]+", "", str(target.get("symbol") or "target").casefold())
    return f"{target_id}-{symbol or 'target'}"


def build_target_run_id(profile: Mapping[str, Any], run_date: str) -> str:
    date = re.sub(r"[^0-9]", "", str(run_date))
    if len(date) != 8:
        raise ValueError("run_date must contain an eight-digit YYYYMMDD value")
    return f"{date}-{artifact_prefix(profile)}"


def build_target_question(profile: Mapping[str, Any]) -> str:
    question = str(profile.get("question") or "").strip()
    if not question:
        target = profile.get("target") if isinstance(profile.get("target"), Mapping) else {}
        question = f"{target.get('name') or target.get('symbol') or 'target'} 的標的研究"
    return question


def build_target_official_source(profile: Mapping[str, Any]) -> dict[str, Any]:
    official = profile.get("official") if isinstance(profile.get("official"), Mapping) else None
    if not official or not official.get("kind"):
        raise ValueError(f"target profile {profile.get('target_id')} has no official source adapter")
    return dict(official)


def run_target_research(
    profile: Mapping[str, Any],
    *,
    frozen_dir: Path,
    output_root: Path,
    days: int = 365,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Run one target through the complete local research path.

    Network failures are recorded as explicit provider statuses.  A missing
    non-critical provider produces a partial quality gate; it never fabricates
    an empty observation or aborts artifact generation.
    """

    target = dict(profile.get("target") or {})
    target_id = str(profile.get("target_id") or "target").strip().casefold()
    if not target.get("symbol"):
        raise ValueError("target profile requires target.symbol")
    prefix = artifact_prefix(profile)
    out = output_root / target_id
    out.mkdir(parents=True, exist_ok=True)
    raw_items = _load_json(frozen_dir / "raw-items.json")
    topic_snapshot = _load_json(frozen_dir / "topic-snapshot.json")
    catalog_report = _load_json(frozen_dir / "full-catalog-report.json")
    if not isinstance(raw_items, list):
        raise ValueError("frozen raw-items.json must contain an array")
    collected_at = str(topic_snapshot.get("as_of") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    run_date = collected_at[:10].replace("-", "")
    run_id = build_target_run_id(profile, run_date)
    question = build_target_question(profile)

    catalog_selected, scope = select_target_items(raw_items, target=target, question=question)
    captures: list[tuple[str, bytes, int, str]] = []
    original_get = httpx.get

    def capture_get(url: str, **kwargs: Any) -> httpx.Response:
        response = original_get(url, **kwargs)
        captures.append((str(url), response.content, response.status_code, str(response.headers.get("content-type") or "")))
        return response

    target_news: dict[str, Any]
    target_community: dict[str, Any]
    official: dict[str, Any] | None = None
    official_error: str | None = None
    benchmark_error: str | None = None
    try:
        httpx.get = capture_get
        points, provider, history_url, history_hash = fetch_market_history(target, days=days, timeout_seconds=timeout_seconds)
        fundamentals = fetch_yahoo_fundamentals(target, timeout_seconds=timeout_seconds)
        volume_data = fetch_yahoo_volume(target, days=days, timeout_seconds=timeout_seconds)
        peer_valuation = fetch_yahoo_peer_valuation(
            target,
            timeout_seconds=timeout_seconds,
            target_as_of=fundamentals.get("as_of") if isinstance(fundamentals, Mapping) else None,
        )
        # A peer P/E set is an optional cross-check, not a reason to discard a
        # target with an otherwise valid DCF.  Preserve the raw peer response
        # but explicitly mark the quality-gate fallback so the report can say
        # why no P/E method was published.
        if str(peer_valuation.get("status") or "") != "available":
            peer_valuation = {
                **dict(peer_valuation),
                "dcf_only_fallback_eligible": True,
                "dcf_only_fallback_reason": str(peer_valuation.get("missing_reason") or "peer_valuation_unavailable"),
                "period_alignment_status": "not_applicable",
            }
        target_news = fetch_yahoo_target_news(
            target,
            max_items=100,
            timeout_seconds=timeout_seconds,
            source_id_prefix=target_id,
        )
        target_community = fetch_target_community(
            target,
            max_items=50,
            timeout_seconds=timeout_seconds,
            source_id_prefix=target_id,
        )
        try:
            official = _fetch_official(profile, target, timeout_seconds=timeout_seconds)
        except (httpx.HTTPError, RuntimeError, ValueError, TypeError) as exc:
            official_error = f"{type(exc).__name__}: {exc}"[:500]
        benchmark = profile.get("benchmark") if isinstance(profile.get("benchmark"), Mapping) else {}
        benchmark_target = {"kind": "equity", "symbol": str(benchmark.get("symbol") or "^TWII")}
        try:
            benchmark_points, benchmark_provider, benchmark_url, benchmark_hash = fetch_market_history(
                benchmark_target, days=days, timeout_seconds=timeout_seconds
            )
        except (RuntimeError, ValueError, TypeError, httpx.HTTPError) as exc:
            benchmark_points, benchmark_provider, benchmark_url, benchmark_hash = None, None, None, None
            benchmark_error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        httpx.get = original_get

    if not points:
        raise RuntimeError(f"{target_id}: market history returned no observations")
    # The frozen news catalog and live market provider have independent
    # clocks.  Using the catalog timestamp as the financial ``as_of`` can
    # reject valid live observations collected after the catalog snapshot.
    # Keep ``collected_at`` for news provenance/run identity, but advance the
    # analytical as-of to the latest observed target/benchmark point.
    observed_timestamps = [
        str(point.get("observed_at"))
        for point in [*(points or []), *(benchmark_points or [])]
        if isinstance(point, Mapping) and point.get("observed_at")
    ]
    depth_as_of = max([collected_at, *observed_timestamps]) if observed_timestamps else collected_at
    supplement_items = target_news.get("items", []) if isinstance(target_news.get("items"), list) else []
    community_items = target_community.get("items", []) if isinstance(target_community.get("items"), list) else []
    all_supplement_items = [
        *(target_news.get("all_items", supplement_items) if isinstance(target_news.get("all_items", supplement_items), list) else supplement_items),
        *(target_community.get("all_items", community_items) if isinstance(target_community.get("all_items", community_items), list) else community_items),
    ]
    if not isinstance(all_supplement_items, list):
        all_supplement_items = supplement_items
    existing_ids = {str(item.get("item_id")) for item in catalog_selected if isinstance(item, Mapping)}
    selected_supplements = [*supplement_items, *community_items]
    selected = [*catalog_selected, *[item for item in selected_supplements if str(item.get("item_id")) not in existing_ids]]
    official_raw_bytes = None
    if official is not None:
        official_raw_bytes = official.pop("raw_bytes", None)
        financial_fields = official.get("financial_fields")
        if isinstance(financial_fields, Mapping):
            base_fundamentals = dict(fundamentals) if isinstance(fundamentals, Mapping) else {}
            fundamentals = {**base_fundamentals, **dict(financial_fields)}
        selected.append(official)

    target_registry = target_source_registry(dict(profile))
    merged_registry = _merge_registry(target_registry, [*catalog_selected, *selected_supplements])
    scope = {
        **scope,
        "catalog_relevant_item_count": len(catalog_selected),
        "target_retrieval_item_count": len(supplement_items),
        "target_community_item_count": len(community_items),
        "target_retrieval_raw_item_count": len(all_supplement_items),
        "target_retrieval_noise_item_count": int(target_news.get("noise_item_count") or 0),
        "target_retrieval_source_group_count": len({_publisher_group(item) for item in supplement_items}),
        "target_retrieval_route_count": len({item.get("source_id") for item in supplement_items}),
        "target_community_source_group_count": len({_publisher_group(item) for item in community_items}),
        "target_community_route_count": len({item.get("source_id") for item in community_items}),
        "relevant_item_count": len(selected),
        "relevant_source_group_count": len({_publisher_group(item) for item in selected}),
        "source_ids": list(dict.fromkeys(str(item.get("source_id")) for item in selected if item.get("source_id"))),
    }
    benchmark = profile.get("benchmark") if isinstance(profile.get("benchmark"), Mapping) else {}
    benchmark_symbol = str(benchmark.get("symbol") or "^TWII")
    snapshot = {
        "schema_version": 1,
        "snapshot_id": f"market_{run_id}",
        "as_of": depth_as_of,
        "provider": provider,
        "target": target,
        "instruments": [{
            "symbol": target["symbol"],
            "asset_type": target.get("kind", "equity"),
            "currency": target.get("currency") or "TWD",
            "price": points[-1]["value"],
            "observed_at": points[-1]["observed_at"],
            "source_item_ids": [],
        }],
    }
    depth = build_financial_depth(
        target=target,
        market_snapshot=snapshot,
        history_points=points,
        history_provider=provider,
        history_url=history_url,
        as_of=depth_as_of,
        evidence=selected,
        fundamentals=fundamentals,
        peer_valuation=peer_valuation,
        history_response_sha256=history_hash,
        benchmark_points=benchmark_points,
        benchmark_provider=benchmark_provider,
        benchmark_url=benchmark_url,
        benchmark_response_sha256=benchmark_hash,
        benchmark_symbol=benchmark_symbol,
        provider_data={
            "volume": volume_data,
            "etf_flows": {"status": "not_applicable", "reason": "target_is_not_crypto"},
            "derivatives": {"status": "not_applicable", "reason": "target_is_not_crypto"},
            "on_chain": {"status": "not_applicable", "reason": "target_is_not_crypto"},
        },
        source_registry=merged_registry,
    )
    snapshot["financial_depth"] = depth

    raw_capture_paths, capture_metadata = _persist_captures(captures, out / "raw-captures", root=output_root.parent)
    official_path, official_hash = _persist_official_capture(
        official, official_raw_bytes, out, prefix, captures, root=output_root.parent
    )
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "target_id": target_id,
        "target": target,
        "question": question,
        "frozen_news_capture": str(frozen_dir),
        "news_capture_as_of": collected_at,
        "financial_analysis_as_of": depth_as_of,
        "provider": provider,
        "history_url": history_url,
        "history_response_sha256": history_hash,
        "fundamentals": {
            "provider": fundamentals.get("provider") if isinstance(fundamentals, Mapping) else None,
            "status": fundamentals.get("status") if isinstance(fundamentals, Mapping) else "unavailable",
            "as_of": fundamentals.get("as_of") if isinstance(fundamentals, Mapping) else None,
            "response_sha256": fundamentals.get("source_ref", {}).get("response_sha256") if isinstance(fundamentals, Mapping) and isinstance(fundamentals.get("source_ref"), Mapping) else None,
        },
        "benchmark": {
            "symbol": benchmark_symbol,
            "provider": benchmark_provider,
            "url": benchmark_url,
            "response_sha256": benchmark_hash,
            "error": benchmark_error,
        },
        "official": {
            "kind": build_target_official_source(profile).get("kind"),
            "source_id": official.get("source_id") if isinstance(official, Mapping) else build_target_official_source(profile).get("source_id"),
            "status": "available" if official else "unavailable",
            "error": official_error,
            "official_scope": official.get("official_scope") if isinstance(official, Mapping) else None,
            "fiscal_period_end": official.get("fiscal_period_end") if isinstance(official, Mapping) else None,
            "canonical_url": official.get("canonical_url") if isinstance(official, Mapping) else None,
            "raw_payload_path": official_path,
            "response_sha256": official_hash,
        },
        "peer_valuation": peer_valuation,
        "target_retrieval": {
            "status": target_news.get("status"),
            "item_count": len(supplement_items),
            "raw_item_count": len(all_supplement_items),
            "noise_item_count": int(target_news.get("noise_item_count") or 0),
            "source_group_count": len({_publisher_group(item) for item in supplement_items}),
            "route_count": len({item.get("source_id") for item in supplement_items}),
            "attempts": target_news.get("attempts", []),
            "geo_coverage": target_news.get("geo_coverage"),
            "community": {
                "status": target_community.get("status"),
                "item_count": len(community_items),
                "raw_item_count": len(target_community.get("all_items", community_items)) if isinstance(target_community.get("all_items", community_items), list) else len(community_items),
                "source_group_count": len({_publisher_group(item) for item in community_items}),
                "route_count": len({item.get("source_id") for item in community_items}),
                "attempts": target_community.get("attempts", []),
                "coverage": target_community.get("coverage"),
                "missing_reason": target_community.get("missing_reason"),
            },
        },
        "source_registry": merged_registry,
        "point_count": len(points),
        "target_scope": scope,
        "scope_quality": {
            "selected_item_count": len(selected),
            "selected_source_count": int(depth["evidence_pack"].get("source_group_count") or 0),
            "selected_route_count": len({str(item.get("source_id")) for item in selected if item.get("source_id")}),
            "exact_identity_title_matches": _exact_identity_title_matches(selected, target),
            "identity_terms": scope.get("identity_terms", []),
            "policy": scope.get("policy"),
            "matcher_version": scope.get("matcher_version"),
        },
        "catalog_report": {
            "normalized_item_count": catalog_report.get("normalized_item_count"),
            "source_group_count": catalog_report.get("source_group_count"),
        },
        "raw_capture_paths": raw_capture_paths,
        "raw_captures": capture_metadata,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "remote_actions_dispatched": False,
    }
    _write_json(out / f"{prefix}-market-snapshot.json", snapshot)
    _write_json(out / f"{prefix}-financial-depth.json", depth)
    _write_json(out / f"{prefix}-target-items.json", selected)
    _write_json(out / f"{prefix}-target-retrieval-items.json", all_supplement_items)
    _write_json(out / f"{prefix}-run-metadata.json", metadata)
    return {"output_dir": out, "prefix": prefix, "metadata": metadata, "depth": depth}


def _fetch_official(profile: Mapping[str, Any], target: Mapping[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    official = build_target_official_source(profile)
    if official["kind"] == "sec_filing":
        return fetch_sec_filing_evidence(
            cik=str(official["cik"]),
            accession=str(official["accession"]),
            document=str(official["document"]),
            filing_date=str(official["filing_date"]),
            fiscal_period_end=str(official["fiscal_period_end"]),
            timeout_seconds=timeout_seconds,
            source_id=str(official["source_id"]),
            issuer_name=str(target.get("name") or target.get("symbol")),
            form_label=str(official.get("form_label") or "filing"),
        )
    if official["kind"] == "twse_company_profile":
        return fetch_twse_company_profile_evidence(
            target,
            timeout_seconds=timeout_seconds,
            source_id=str(official.get("source_id") or ""),
        )
    if official["kind"] == "twse_financial_statement":
        return fetch_twse_financial_statement_evidence(
            target,
            timeout_seconds=timeout_seconds,
            source_id=str(official.get("source_id") or ""),
            income_url=str(official.get("income_url") or "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"),
            balance_url=str(official.get("balance_url") or "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci"),
        )
    raise ValueError(f"unsupported official source kind: {official['kind']}")


def _merge_registry(base: Mapping[str, Any], items: list[Mapping[str, Any]]) -> dict[str, Any]:
    inferred = build_registry_for_items(items) if items else {"sources": []}
    entries: dict[str, dict[str, Any]] = {}
    for source in inferred.get("sources", []):
        if isinstance(source, Mapping):
            entries[str(source["source_id"])] = dict(source)
    for source in base.get("sources", []):
        if isinstance(source, Mapping):
            entries[str(source["source_id"])] = dict(source)
    return build_source_registry(entries.values(), registry_id="target_research_sources_v2")


def _persist_captures(captures: list[tuple[str, bytes, int, str]], directory: Path, *, root: Path) -> tuple[list[str], list[dict[str, Any]]]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    metadata: list[dict[str, Any]] = []
    for index, (url, content, status_code, content_type) in enumerate(captures):
        response_hash = hashlib.sha256(content).hexdigest()
        path = directory / f"{index:03d}-{response_hash[:16]}.raw"
        if not path.exists():
            path.write_bytes(content)
        relative = str(path.relative_to(root))
        paths.append(relative)
        metadata.append({"url": url, "status_code": status_code, "content_type": content_type, "response_sha256": response_hash, "path": relative})
    return list(dict.fromkeys(paths)), metadata


def _persist_official_capture(
    official: Mapping[str, Any] | None,
    raw_bytes: bytes | None,
    out: Path,
    prefix: str,
    captures: list[tuple[str, bytes, int, str]],
    *,
    root: Path,
) -> tuple[str | None, str | None]:
    if not official:
        return None, None
    url = str(official.get("canonical_url") or "")
    content = raw_bytes
    if content is None:
        matches = [row for row in captures if row[0] == url]
        content = matches[-1][1] if matches else None
    if content is None:
        return None, None
    response_hash = hashlib.sha256(content).hexdigest()
    suffix = ".html" if "sec.gov" in url else ".json"
    path = out / f"{prefix}-official.raw{suffix}"
    path.write_bytes(content)
    return str(path.relative_to(root)), response_hash


def _persist_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    _persist_json(path, payload)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _publisher_group(item: Mapping[str, Any]) -> str:
    evidence = item.get("evidence")
    if isinstance(evidence, Mapping) and evidence.get("publisher_verified") is True and evidence.get("publisher_id"):
        return str(evidence["publisher_id"])
    return str(item.get("source_id") or "unknown")


def _exact_identity_title_matches(items: list[Mapping[str, Any]], target: Mapping[str, Any]) -> int:
    terms = [str(target.get("symbol") or "").casefold(), *(str(value).casefold() for value in target.get("aliases", []) if isinstance(value, str))]
    terms = [term for term in terms if term]
    return sum(1 for item in items if any(term in str(item.get("title") or "").casefold() for term in terms))
