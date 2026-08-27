"""Layer 2: translate material evidence gaps into bounded source routes."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from finance_crawler_poc.provider_catalog import load_provider_catalog, providers_for_gaps


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _route_id(section: str, url: str) -> str:
    return f"route_{hashlib.sha256(f'{section}\0{url}'.encode('utf-8')).hexdigest()[:20]}"


def _transport(url: str, source: Mapping[str, Any]) -> str:
    configured = str(source.get("transport") or "").strip().casefold()
    if configured in {"api", "rss", "browser"}:
        return configured
    lowered = url.casefold()
    if "openapi" in lowered or "query1.finance.yahoo.com" in lowered or "/api/" in lowered:
        return "api"
    if "rss" in lowered or "feed" in lowered:
        return "rss"
    return "browser"


def _catalog(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    context = profile.get("research_context") if isinstance(profile.get("research_context"), Mapping) else {}
    by_binding: dict[tuple[str, str], dict[str, Any]] = {}
    for section, section_data in context.items():
        if not isinstance(section_data, Mapping) or not isinstance(section_data.get("sources"), list):
            continue
        for value in section_data["sources"]:
            if not isinstance(value, Mapping):
                continue
            url = str(value.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            binding = (str(section), url)
            row = by_binding.setdefault(binding, {
                "route_id": _route_id(str(section), url),
                "url": url,
                "section": str(section),
                "requirement_ids": set(),
                "evidence_roles": set(),
                "independence_groups": set(),
                "fallback_rank": int(value.get("fallback_rank") or 1),
                "transport": _transport(url, value),
            })
            row["requirement_ids"].update(str(item) for item in value.get("requirement_ids", []) if str(item))
            if value.get("evidence_role"):
                row["evidence_roles"].add(str(value["evidence_role"]))
            row["independence_groups"].add(str(value.get("independence_group") or value.get("group") or value.get("publisher") or "unknown"))
            # A target source may declare same-publisher route variants (for
            # example RSS -> JSON or an alternate language page).  Materialize
            # them as broker routes, but keep the original independence group
            # so a fallback cannot inflate corroboration counts.
            alternatives = value.get("alternative_urls")
            if isinstance(alternatives, list):
                for alternative_url in alternatives:
                    alt_url = str(alternative_url or "").strip()
                    if not alt_url.startswith(("http://", "https://")):
                        continue
                    alt_binding = (str(section), alt_url)
                    alt = by_binding.setdefault(alt_binding, {
                        "route_id": _route_id(str(section), alt_url),
                        "url": alt_url,
                        "section": str(section),
                        "requirement_ids": set(),
                        "evidence_roles": set(),
                        "independence_groups": set(),
                        "fallback_rank": int(value.get("fallback_rank") or 1) + 1,
                        "transport": _transport(alt_url, value),
                    })
                    alt["requirement_ids"].update(str(item) for item in value.get("requirement_ids", []) if str(item))
                    if value.get("evidence_role"):
                        alt["evidence_roles"].add(str(value["evidence_role"]))
                    alt["independence_groups"].add(str(value.get("independence_group") or value.get("group") or value.get("publisher") or "unknown"))
    result: list[dict[str, Any]] = []
    for row in by_binding.values():
        result.append({
            **row,
            "requirement_ids": sorted(row["requirement_ids"]),
            "evidence_roles": sorted(row["evidence_roles"]),
            "independence_groups": sorted(row["independence_groups"]),
        })
    return sorted(result, key=lambda item: (item["fallback_rank"], item["route_id"]))


def build_gap_plan(
    profile: Mapping[str, Any], *, research_plan: Mapping[str, Any], coverage: Mapping[str, Any], round_number: int,
    attempted_route_ids: list[str], generated_at: str | None = None,
    provider_catalog: Mapping[str, Any] | None = None,
    configured_credentials: set[str] | None = None,
) -> dict[str, Any]:
    """Select routes only for material requirements not yet satisfied."""

    if round_number < 1 or round_number > 4:
        raise ValueError("round_number must be between 1 and 4")
    requirements = {
        str(item.get("requirement_id")): item
        for item in research_plan.get("requirements", [])
        if isinstance(item, Mapping) and item.get("requirement_id")
    }
    current = {
        str(item.get("requirement_id")): item
        for item in coverage.get("requirements", [])
        if isinstance(item, Mapping) and item.get("requirement_id")
    }
    gaps: list[dict[str, Any]] = []
    for requirement_id, requirement in requirements.items():
        coverage_item = current.get(requirement_id, {})
        status = str(coverage_item.get("status") or "unresolved")
        materiality = str(requirement.get("decision_materiality") or ("high" if requirement.get("required", True) else "low"))
        if status in {"complete", "not_applicable"} or materiality == "low" or not bool(requirement.get("required", True)):
            continue
        missing_metrics = list(coverage_item.get("missing_metrics") or requirement.get("required_metrics") or [])
        missing_roles = list(coverage_item.get("missing_roles") or requirement.get("required_roles") or [])
        missing_geographies = list(coverage_item.get("required_geography_scopes") or requirement.get("geography_policy") or [])
        weight = 1.0 if materiality == "high" else 0.6
        information_gain = round(min(1.0, weight * (0.4 + 0.1 * len(missing_metrics) + 0.05 * len(missing_roles))), 6)
        gaps.append({
            "requirement_id": requirement_id,
            "parent_question_id": str(requirement.get("parent_question_id") or "Q-UNMAPPED"),
            "section": str(requirement.get("section") or "industry"),
            "status": status if status in {"partial", "unresolved"} else "unresolved",
            "decision_materiality": materiality,
            "priority": information_gain,
            "missing_metrics": sorted({str(item) for item in missing_metrics if str(item)}),
            "missing_roles": sorted({str(item) for item in missing_roles if str(item)}),
            "missing_geographies": sorted({str(item) for item in missing_geographies if str(item)}),
            "conflicts": list(coverage_item.get("conflicts") or []),
            "expected_information_gain": information_gain,
        })
    attempted = set(attempted_route_ids)
    routes: list[dict[str, Any]] = []
    for source in _catalog(profile):
        if source["route_id"] in attempted:
            continue
        matched = [
            gap["requirement_id"]
            for gap in gaps
            if gap["requirement_id"] in source["requirement_ids"]
            or (gap["section"] == source["section"] and gap["requirement_id"].startswith("industry.") and source["section"] == "industry")
        ]
        if not matched:
            continue
        routes.append({
            "route_id": source["route_id"],
            "url": source["url"],
            "section": source["section"],
            "transport": source["transport"],
            "requirement_ids": sorted(set(matched)),
            "evidence_roles": source["evidence_roles"],
            "independence_groups": source["independence_groups"],
            "priority": max(gap["priority"] for gap in gaps if gap["requirement_id"] in matched),
            "fallback_rank": source["fallback_rank"],
            "reason": "material_requirement_gap",
        })
    remaining_by_requirement = {
        gap["requirement_id"]: [route["route_id"] for route in routes if gap["requirement_id"] in route["requirement_ids"]]
        for gap in gaps
    }
    for gap in gaps:
        gap["attempted_routes"] = sorted(attempted)
        gap["remaining_routes"] = remaining_by_requirement[gap["requirement_id"]]
    provider_candidates = providers_for_gaps(
        provider_catalog or load_provider_catalog(),
        gaps=gaps,
        configured_credentials=configured_credentials or set(),
    )
    return {
        "schema_version": 1,
        "target_id": str(research_plan.get("target_id") or profile.get("target_id") or "target"),
        "question_sha256": str(research_plan.get("question_sha256") or ""),
        "industry_family": str(research_plan.get("industry_family") or "generic_equity"),
        "generated_at": generated_at or _now(),
        "round": round_number,
        "status": "planned" if routes else "no_action",
        "attempted_route_ids": sorted(attempted),
        "gaps": sorted(gaps, key=lambda item: (-float(item["priority"]), item["requirement_id"])),
        "routes": sorted(routes, key=lambda item: (-float(item["priority"]), int(item["fallback_rank"]), item["route_id"])),
        "provider_candidates": provider_candidates,
    }


def select_context_for_gap_plan(profile: Mapping[str, Any], gap_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Materialize only broker-approved URLs for the collector boundary."""

    route_requirements = {
        (str(route.get("section")), str(route.get("url"))): {str(item) for item in route.get("requirement_ids", []) if str(item)}
        for route in gap_plan.get("routes", [])
        if isinstance(route, Mapping) and route.get("url")
    }
    source_context = profile.get("research_context") if isinstance(profile.get("research_context"), Mapping) else {}
    selected: dict[str, Any] = {}
    for section, section_data in source_context.items():
        if not isinstance(section_data, Mapping):
            continue
        output = {key: deepcopy(value) for key, value in section_data.items() if key != "sources"}
        sources: list[dict[str, Any]] = []
        for value in section_data.get("sources", []):
            if not isinstance(value, Mapping):
                continue
            url = str(value.get("url") or "")
            scoped_requirements = route_requirements.get((str(section), url))
            if not scoped_requirements:
                continue
            source = dict(value)
            existing = {str(item) for item in source.get("requirement_ids", []) if str(item)}
            source["requirement_ids"] = sorted(existing | scoped_requirements)
            source["broker_round"] = int(gap_plan.get("round") or 1)
            source["broker_question_sha256"] = str(gap_plan.get("question_sha256") or "")
            sources.append(source)
        if sources:
            output["sources"] = sources
            selected[str(section)] = output
    return selected
