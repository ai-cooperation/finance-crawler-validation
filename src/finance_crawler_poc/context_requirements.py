"""Research-problem-oriented context contracts and deterministic coverage.

The qualitative model must not infer research completeness from evidence volume.
This module turns an issuer/industry profile into explicit requirements, builds
small context-pack summaries, and produces a gap report before the model is
allowed to write a professional conclusion.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from collections.abc import Mapping
from pathlib import Path
from typing import Any


_UNIVERSAL_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "requirement_id": "company.business_model",
        "section": "company",
        "required": True,
        "minimum_independent_groups": 1,
        "required_roles": ["official", "annual_report"],
        "required_metrics": ["products_or_services", "customers_or_regions"],
        "period_policy": "latest_available",
        "recommended_routes": ["official_ir", "annual_report", "regulatory_filing"],
    },
    {
        "requirement_id": "segment.disclosure",
        "section": "company",
        "required": True,
        "minimum_independent_groups": 1,
        "required_roles": ["annual_report", "financial_statement"],
        "required_metrics": ["segment_revenue", "segment_period", "currency", "unit"],
        "period_policy": "five_year_or_latest_disclosed",
        "recommended_routes": ["annual_report_pdf", "investor_presentation", "regulatory_filing"],
        "not_applicable_rule": "company_does_not_disclose_segment_profit",
    },
    {
        "requirement_id": "industry.market_demand",
        "section": "industry",
        "required": True,
        "minimum_independent_groups": 3,
        "required_roles": ["industry_statistic", "peer_filing", "company_disclosure"],
        "required_metrics": ["market_size_or_demand", "period", "unit"],
        "period_policy": "latest_available_plus_3y_history",
        "recommended_routes": ["industry_statistics_api", "regulatory_statistics", "peer_annual_report"],
    },
    {
        "requirement_id": "industry.price_capacity_cycle",
        "section": "industry",
        "required": True,
        "minimum_independent_groups": 3,
        "required_roles": ["price_index", "capacity_or_utilization", "independent_secondary"],
        "required_metrics": ["price_or_spread", "capacity_or_utilization", "period", "unit"],
        "period_policy": "latest_available_plus_3y_history",
        "recommended_routes": ["industry_statistics_api", "commodity_api", "regulatory_statistics", "peer_annual_report"],
    },
    {
        "requirement_id": "industry.competitive_position",
        "section": "industry",
        "required": True,
        "minimum_independent_groups": 3,
        "required_roles": ["peer_filing", "industry_statistic", "company_disclosure"],
        "required_metrics": ["peer_set", "market_share_or_position", "margin_or_cost_comparison"],
        "period_policy": "latest_common_period",
        "recommended_routes": ["peer_annual_report", "industry_statistics_api", "regulatory_statistics"],
    },
    {
        "requirement_id": "peer.comparison",
        "section": "industry",
        "required": True,
        "minimum_independent_groups": 3,
        "required_roles": ["peer_filing", "market_data"],
        "required_metrics": ["revenue_growth", "gross_margin", "operating_margin", "roic", "valuation"],
        "period_policy": "latest_common_fiscal_period",
        "recommended_routes": ["peer_financial_api", "peer_annual_report", "market_data_api"],
    },
    {
        "requirement_id": "governance.board_and_ownership",
        "section": "governance",
        "required": True,
        "minimum_independent_groups": 2,
        "required_roles": ["annual_report", "governance_filing", "regulatory_ownership"],
        "required_metrics": ["board", "independent_directors", "ownership", "committee"],
        "period_policy": "latest_available",
        "recommended_routes": ["governance_page", "annual_report", "regulatory_ownership"],
    },
    {
        "requirement_id": "governance.capital_allocation",
        "section": "governance",
        "required": True,
        "minimum_independent_groups": 2,
        "required_roles": ["annual_report", "financial_statement", "shareholder_filing"],
        "required_metrics": ["capex", "dividend_or_buyback", "debt", "m_and_a"],
        "period_policy": "five_year_history",
        "recommended_routes": ["annual_report", "financial_statement", "shareholder_filing"],
    },
    {
        "requirement_id": "esg.materiality_kpi",
        "section": "esg",
        "required": True,
        "minimum_independent_groups": 2,
        "required_roles": ["sustainability_report", "regulatory_or_governance"],
        "required_metrics": ["material_topic", "baseline_kpi", "target", "progress", "financial_transmission"],
        "period_policy": "latest_report_plus_baseline",
        "recommended_routes": ["sustainability_report", "governance_page", "environmental_regulator"],
    },
)


_INDUSTRY_FAMILY_ALIASES = {
    "cement & building materials": "cement",
    "cement": "cement",
    "steel": "steel",
    "petrochemicals": "petrochemical",
    "diversified chemicals": "petrochemical",
    "electronic components": "electronic_components",
    "computer hardware": "pc_hardware",
    "electronics manufacturing services": "pc_hardware",
}


_INDUSTRY_REQUIRED_IDS = {
    "cement": ("industry.market_demand", "industry.price_capacity_cycle", "industry.competitive_position"),
    "steel": ("industry.market_demand", "industry.price_capacity_cycle", "industry.competitive_position"),
    "petrochemical": ("industry.market_demand", "industry.price_capacity_cycle", "industry.competitive_position"),
    "electronic_components": ("industry.market_demand", "industry.price_capacity_cycle", "industry.competitive_position"),
    "pc_hardware": ("industry.market_demand", "industry.price_capacity_cycle", "industry.competitive_position"),
}

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TEXT_CACHE: dict[str, str] = {}


def _source_text(source: Mapping[str, Any]) -> str:
    """Read a saved capture only; never fetch or invent context at gate time."""

    raw_path = str(source.get("raw_capture_path") or "").strip()
    if not raw_path:
        return ""
    if raw_path in _TEXT_CACHE:
        return _TEXT_CACHE[raw_path]
    path = Path(raw_path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.exists() or not path.is_file():
        _TEXT_CACHE[raw_path] = ""
        return ""
    try:
        if path.suffix.casefold() == ".pdf":
            completed = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, timeout=20)
            text = completed.stdout.decode("utf-8", errors="ignore")
        else:
            text = path.read_bytes().decode("utf-8", errors="ignore")
    except (OSError, subprocess.SubprocessError):
        text = ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    _TEXT_CACHE[raw_path] = text
    return text


def _metric_signals(rows: list[Mapping[str, Any]], tokens: tuple[str, ...]) -> list[str]:
    signals: list[str] = []
    for row in rows:
        text = _source_text(row).casefold()
        if not text:
            continue
        matched = [token for token in tokens if token.casefold() in text]
        if matched:
            signals.extend(matched)
    return sorted(set(signals))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def industry_family(profile: Mapping[str, Any]) -> str:
    target = profile.get("target") if isinstance(profile.get("target"), Mapping) else profile
    industry = str(target.get("industry") or "").strip().casefold()
    return _INDUSTRY_FAMILY_ALIASES.get(industry, "generic_equity")


def build_research_requirements(profile: Mapping[str, Any], *, generated_at: str | None = None) -> dict[str, Any]:
    """Build a versioned requirement matrix for one target."""

    target = dict(profile.get("target") or {})
    family = industry_family(profile)
    configured = profile.get("research_requirements")
    if isinstance(configured, list) and configured:
        requirements = deepcopy(configured)
    else:
        requirements = deepcopy(list(_UNIVERSAL_REQUIREMENTS))
    industry_ids = set(_INDUSTRY_REQUIRED_IDS.get(family, ()))
    for item in requirements:
        if not isinstance(item, dict):
            continue
        item.setdefault("industry_family", family)
        item.setdefault("required", True)
        if str(item.get("requirement_id")) in industry_ids:
            item["industry_family"] = family
    return {
        "schema_version": 1,
        "target_id": str(profile.get("target_id") or target.get("symbol") or "target").casefold(),
        "target": target,
        "industry_family": family,
        "generated_at": generated_at or _now(),
        "requirements": requirements,
    }


def _source_group(source: Mapping[str, Any]) -> str:
    return str(source.get("independence_group") or source.get("group") or source.get("publisher") or "unknown").strip().casefold()


def _source_success(source: Mapping[str, Any]) -> bool:
    return str(source.get("fetch_status") or "success").casefold() == "success" and bool(source.get("response_sha256"))


def _source_id(source: Mapping[str, Any]) -> str:
    digest = str(source.get("response_sha256") or "")
    url = str(source.get("url") or source.get("canonical_url") or "")
    material = digest or url or repr(sorted(source.items()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _metric_attestations(source: Mapping[str, Any], requirement_id: str | None = None) -> list[dict[str, Any]]:
    """Return explicit metric facts only; keyword presence is not an L3 fact.

    A successful download proves transport, not that a research question was
    answered.  Metric attestations are produced by a parser/extractor and must
    retain value, period, unit and geography.  This function intentionally has
    no fallback to prose keyword counts.
    """

    values = source.get("metric_attestations")
    if not isinstance(values, list):
        return []
    rows: list[dict[str, Any]] = []
    source_digest = str(source.get("response_sha256") or "")
    for value in values:
        if not isinstance(value, Mapping) or not str(value.get("metric") or "").strip():
            continue
        if source_digest and str(value.get("source_response_sha256") or "") != source_digest:
            continue
        if value.get("value") in (None, ""):
            continue
        if not str(value.get("period") or "").strip() or not str(value.get("unit") or "").strip():
            continue
        if not str(value.get("target_id") or "").strip():
            continue
        scoped = value.get("requirement_ids")
        if requirement_id and isinstance(scoped, list) and scoped and requirement_id not in {str(item) for item in scoped}:
            continue
        rows.append(dict(value))
    return rows


def _metric_coverage(rows: list[Mapping[str, Any]], requirement_id: str) -> set[str]:
    coverage: set[str] = set()
    for row in rows:
        for fact in _metric_attestations(row, requirement_id):
            metric = str(fact.get("metric") or "").strip()
            if metric:
                coverage.add(metric)
            if str(fact.get("period") or "").strip():
                coverage.add("period")
            if str(fact.get("unit") or "").strip():
                coverage.add("unit")
            if str(fact.get("currency") or "").strip():
                coverage.add("currency")
    return coverage


def _source_geographies(source: Mapping[str, Any]) -> set[str]:
    configured = source.get("geography_scope")
    if isinstance(configured, str):
        values = [configured]
    elif isinstance(configured, list):
        values = configured
    else:
        values = []
    normalized = {str(value).strip().casefold() for value in values if str(value).strip()}
    if normalized:
        return normalized
    text = " ".join(str(source.get(key) or "").casefold() for key in ("url", "group", "publisher"))
    if any(token in text for token in ("usgs", "eia.gov", "united states", "u.s.")):
        return {"us"}
    if any(token in text for token in ("twse", ".tw/", "taiwan")):
        return {"tw"}
    if any(token in text for token in ("oecd", "worldsteel", "wsts", "global", "world")):
        return {"global"}
    return set()


def _required_geographies(profile: Mapping[str, Any]) -> set[str]:
    target = profile.get("target") if isinstance(profile.get("target"), Mapping) else profile
    configured = target.get("research_geographies")
    if not isinstance(configured, list) or not configured:
        configured = target.get("region_priority")
    values = {str(value).strip().casefold() for value in configured or [] if str(value).strip()}
    # Source routing priorities contain ``global`` as a fallback.  It is a
    # valid market scope, but never makes a US-only series relevant by itself.
    return values or {str(target.get("primary_region") or target.get("domicile_country") or "").casefold()}


def _geography_matches(source: Mapping[str, Any], required: set[str]) -> bool:
    scopes = _source_geographies(source)
    return bool(scopes & required or "global" in scopes)


def _requirements_for_source(source: Mapping[str, Any], section: str) -> set[str]:
    configured = source.get("requirement_ids")
    if isinstance(configured, list) and configured:
        return {str(item) for item in configured if str(item)}
    # Backward-compatible inference for old profiles. New registry entries
    # should always declare requirement_ids explicitly.
    if section == "company":
        return {"company.business_model", "segment.disclosure"}
    if section == "industry":
        return {"industry.market_demand", "industry.price_capacity_cycle", "industry.competitive_position"}
    if section == "governance":
        return {"governance.board_and_ownership", "governance.capital_allocation"}
    if section == "esg":
        return {"esg.materiality_kpi"}
    return set()


def _flatten_sources(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section, values in context.items():
        if not isinstance(values, Mapping) or not isinstance(values.get("sources"), list):
            continue
        for source in values["sources"]:
            if not isinstance(source, Mapping):
                continue
            row = dict(source)
            row["section"] = str(section)
            row["requirement_ids"] = sorted(_requirements_for_source(source, str(section)))
            rows.append(row)
    return rows


def build_context_packs(
    profile: Mapping[str, Any], *, context: Mapping[str, Any], history: Mapping[str, Any], valuation: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Create deterministic pack summaries; no pack is promoted by count alone."""

    target_id = str(profile.get("target_id") or "target").casefold()
    rows = _flatten_sources(context)
    successful = [row for row in rows if _source_success(row)]
    packs: dict[str, dict[str, Any]] = {}

    industry_rows = [row for row in successful if row.get("section") == "industry"]
    industry_groups = sorted({_source_group(row) for row in industry_rows})
    external_groups = [group for group in industry_groups if target_id not in group and group not in {"twse", "twse_profile", "twse_financial"}]
    industry_req_ids = ("industry.market_demand", "industry.price_capacity_cycle", "industry.competitive_position")
    industry_metric_by_requirement = {
        req_id: sorted(_metric_coverage([row for row in industry_rows if req_id in row.get("requirement_ids", [])], req_id))
        for req_id in industry_req_ids
    }
    required_by_id = {
        str(item.get("requirement_id")): {str(metric) for metric in item.get("required_metrics", []) if str(metric)}
        for item in build_research_requirements(profile).get("requirements", [])
        if isinstance(item, Mapping)
    }
    industry_missing_metrics = {
        req_id: sorted(required_by_id.get(req_id, set()) - set(industry_metric_by_requirement.get(req_id, [])))
        for req_id in industry_req_ids
    }
    required_geo = _required_geographies(profile)
    geo_ready = any(
        _geography_matches(row, required_geo)
        for row in industry_rows
        if str(row.get("evidence_role") or "") not in {"company_disclosure", "official", "annual_report", "financial_statement"}
    )
    industry_ready = (
        len(industry_groups) >= 3
        and bool(external_groups)
        and geo_ready
        and all(not values for values in industry_missing_metrics.values())
    )
    industry_missing_reasons: list[str] = []
    if len(industry_groups) < 3 or not external_groups:
        industry_missing_reasons.append("three_independent_industry_groups_required")
    if not geo_ready:
        industry_missing_reasons.append("target_geography_evidence_missing")
    if any(industry_missing_metrics.values()):
        industry_missing_reasons.append("requirement_scoped_industry_metrics_missing")
    packs["industry"] = {
        "schema_version": 1,
        "pack_type": "industry",
        "target_id": target_id,
        "status": "available" if industry_ready else "partial",
        "requirement_ids": sorted({req for row in industry_rows for req in row.get("requirement_ids", [])}),
        "evidence_ids": sorted({_source_id(row) for row in industry_rows}),
        "evidence_count": len(industry_rows),
        "source_groups": industry_groups,
        "independent_source_count": len(industry_groups),
        "metric_count": len({metric for values in industry_metric_by_requirement.values() for metric in values}),
        "metric_coverage": sorted({metric for values in industry_metric_by_requirement.values() for metric in values}),
        "requirement_metric_coverage": industry_metric_by_requirement,
        "geography_scopes": sorted({scope for row in industry_rows for scope in _source_geographies(row)}),
        "missing_reasons": industry_missing_reasons,
    }

    segment_periods = history.get("segment_periods") if isinstance(history, Mapping) else None
    segment_rows = [row for row in successful if "segment.disclosure" in row.get("requirement_ids", [])]
    segment_signals = _metric_signals(
        segment_rows,
        (
            "segment",
            "business unit",
            "revenue by",
            "operating income by",
            "部門",
            "部門資訊",
            "部門損益",
            "營業結果",
            "產品營收",
            "營收占",
            "銷售比率",
            "產品銷售",
            # Taiwan annual reports commonly disclose product-segment
            # revenue as 「主要產品／依產品別之內外銷情形」 rather than
            # using the English word segment.  These are valid segment
            # metrics when the table also exposes period, currency and unit.
            "主要產品",
            "產品別",
            "銷值",
            "銷量",
            "銷售地區",
            "內銷",
            "外銷",
        ),
    )
    segment_metric_count = len(segment_signals)
    segment_available = (isinstance(segment_periods, list) and bool(segment_periods)) or segment_metric_count >= 2
    packs["segment"] = {
        "schema_version": 1,
        "pack_type": "segment",
        "target_id": target_id,
        "status": "available" if segment_available else "partial",
        "requirement_ids": ["segment.disclosure"],
        "evidence_ids": sorted({_source_id(row) for row in segment_rows}),
        "evidence_count": len(segment_rows),
        "source_groups": sorted({_source_group(row) for row in segment_rows}),
        "independent_source_count": len({_source_group(row) for row in segment_rows}),
        "metric_count": segment_metric_count,
        "missing_reasons": [] if segment_available else ["segment_financial_pack_not_extracted"],
    }

    peer_set = valuation.get("peer_set") if isinstance(valuation, Mapping) else None
    peer_count = len(peer_set) if isinstance(peer_set, list) else int(valuation.get("usable_peer_count") or 0) if isinstance(valuation, Mapping) else 0
    peer_rows = [row for row in successful if "peer.comparison" in row.get("requirement_ids", [])]
    peer_available = peer_count >= 3 and bool(peer_set or peer_rows)
    packs["peer"] = {
        "schema_version": 1,
        "pack_type": "peer",
        "target_id": target_id,
        "status": "available" if peer_available else "partial",
        "requirement_ids": ["peer.comparison"],
        "evidence_ids": sorted({_source_id(row) for row in peer_rows}),
        "evidence_count": len(peer_rows),
        "source_groups": sorted({_source_group(row) for row in peer_rows}),
        "independent_source_count": len({_source_group(row) for row in peer_rows}),
        "peer_count": peer_count,
        "missing_reasons": [] if peer_available else ["three_comparable_peers_required"],
    }

    governance_rows = [row for row in successful if row.get("section") == "governance"]
    governance_groups = sorted({_source_group(row) for row in governance_rows})
    packs["governance"] = {
        "schema_version": 1,
        "pack_type": "governance",
        "target_id": target_id,
        "status": "available" if len(governance_groups) >= 2 else "partial",
        "requirement_ids": sorted({req for row in governance_rows for req in row.get("requirement_ids", [])}),
        "evidence_ids": sorted({_source_id(row) for row in governance_rows}),
        "evidence_count": len(governance_rows),
        "source_groups": governance_groups,
        "independent_source_count": len(governance_groups),
        "missing_reasons": [] if len(governance_groups) >= 2 else ["two_governance_source_groups_required"],
    }

    esg_rows = [row for row in successful if row.get("section") == "esg"]
    esg_groups = sorted({_source_group(row) for row in esg_rows})
    esg_metrics = context.get("esg", {}).get("kpis") if isinstance(context.get("esg"), Mapping) else None
    esg_signals = _metric_signals(esg_rows, ("scope 1", "scope 2", "emission", "renewable energy", "water", "target", "progress"))
    esg_metric_count = len(esg_metrics) if isinstance(esg_metrics, list) else len(esg_signals)
    esg_available = len(esg_groups) >= 2 and esg_metric_count >= 2
    packs["esg"] = {
        "schema_version": 1,
        "pack_type": "governance_esg",
        "target_id": target_id,
        "status": "available" if esg_available else "partial",
        "requirement_ids": ["esg.materiality_kpi"],
        "evidence_ids": sorted({_source_id(row) for row in esg_rows}),
        "evidence_count": len(esg_rows),
        "source_groups": esg_groups,
        "independent_source_count": len(esg_groups),
        "metric_count": esg_metric_count,
        "missing_reasons": [] if esg_available else ["materiality_kpi_pack_required"],
    }
    return packs


def build_context_coverage(
    profile: Mapping[str, Any], *, context: Mapping[str, Any], packs: Mapping[str, Mapping[str, Any]], generated_at: str | None = None
) -> dict[str, Any]:
    requirements = build_research_requirements(profile, generated_at=generated_at)
    rows = _flatten_sources(context)
    result: list[dict[str, Any]] = []
    for requirement in requirements["requirements"]:
        req_id = str(requirement["requirement_id"])
        candidates = [row for row in rows if req_id in row.get("requirement_ids", []) and _source_success(row)]
        source_groups = sorted({_source_group(row) for row in candidates})
        evidence_roles = sorted({str(row.get("evidence_role") or "") for row in candidates if str(row.get("evidence_role") or "")})
        evidence_ids = sorted({_source_id(row) for row in candidates})
        independent_count = len(source_groups)
        required = bool(requirement.get("required", True))
        pack_name = "segment" if req_id.startswith("segment.") else "peer" if req_id.startswith("peer.") else "governance" if req_id.startswith("governance.") else "esg" if req_id.startswith("esg.") else "industry" if req_id.startswith("industry.") else ""
        pack = packs.get(pack_name, {}) if pack_name else {}
        minimum = int(requirement.get("minimum_independent_groups") or 1)
        reasons: list[str] = []
        if not candidates:
            reasons.append("no_successful_requirement_scoped_evidence")
        if independent_count < minimum:
            reasons.append(f"minimum_independent_groups_{minimum}")
        required_roles = {str(role) for role in (requirement.get("required_roles") or []) if str(role)}
        missing_roles = sorted(required_roles - set(evidence_roles))
        role_ready = not missing_roles
        if not role_ready:
            reasons.append("required_evidence_roles_missing")
        required_metrics = {str(metric) for metric in (requirement.get("required_metrics") or []) if str(metric)}
        observed_metrics = _metric_coverage(candidates, req_id)
        pack_metrics_by_requirement = pack.get("requirement_metric_coverage") if isinstance(pack.get("requirement_metric_coverage"), Mapping) else {}
        if isinstance(pack_metrics_by_requirement.get(req_id), list):
            observed_metrics.update(str(metric) for metric in pack_metrics_by_requirement.get(req_id, []) if str(metric))
        elif pack and not req_id.startswith("industry.") and isinstance(pack.get("metric_coverage"), list):
            observed_metrics.update(str(metric) for metric in pack.get("metric_coverage", []) if str(metric))
        missing_metrics = sorted(required_metrics - observed_metrics)
        metrics_ready = not missing_metrics
        if not metrics_ready:
            reasons.append("required_metrics_missing")
        geographies = sorted({scope for row in candidates for scope in _source_geographies(row)})
        required_geographies = _required_geographies(profile)
        external_geo_candidates = [
            row
            for row in candidates
            if str(row.get("evidence_role") or "") not in {"company_disclosure", "official", "annual_report", "financial_statement"}
        ]
        geography_ready = not req_id.startswith("industry.") or any(_geography_matches(row, required_geographies) for row in external_geo_candidates)
        if not geography_ready:
            reasons.append("target_geography_evidence_missing")
        if pack and pack.get("status") != "available":
            reasons.extend(str(item) for item in pack.get("missing_reasons", []) if item)
        pack_ready = not pack or pack.get("status") == "available"
        status = (
            "complete"
            if candidates and independent_count >= minimum and role_ready and metrics_ready and geography_ready and pack_ready
            else "partial" if candidates or pack else "unresolved"
        )
        if status == "complete":
            reasons = []
        result.append({
            "requirement_id": req_id,
            "section": requirement.get("section"),
            "required": required,
            "status": status,
            "evidence_ids": evidence_ids,
            "source_groups": source_groups,
            "evidence_roles": evidence_roles,
            "independent_source_count": independent_count,
            "metric_coverage": sorted(observed_metrics),
            "missing_metrics": missing_metrics,
            "missing_roles": missing_roles,
            "geography_scopes": geographies,
            "required_geography_scopes": sorted(required_geographies) if req_id.startswith("industry.") else [],
            "missing_reasons": sorted(set(reasons)),
        })
    required_items = [item for item in result if item["required"]]
    complete_count = sum(1 for item in required_items if item["status"] in {"complete", "not_applicable"})
    l3_ready = bool(required_items) and complete_count == len(required_items)
    return {
        "schema_version": 1,
        "target_id": requirements["target_id"],
        "industry_family": requirements["industry_family"],
        "generated_at": generated_at or requirements["generated_at"],
        "requirements": result,
        "summary": {
            "required_count": len(required_items),
            "complete_count": complete_count,
            "coverage_ratio": round(complete_count / len(required_items), 6) if required_items else 0.0,
            "l3_ready": l3_ready,
        },
        "status": "complete" if l3_ready else "partial",
    }


def build_context_gap_report(
    profile: Mapping[str, Any], *, requirements: Mapping[str, Any], coverage: Mapping[str, Any], generated_at: str | None = None,
    round_number: int = 1,
) -> dict[str, Any]:
    requirement_map = {str(item.get("requirement_id")): item for item in requirements.get("requirements", []) if isinstance(item, Mapping)}
    coverage_map = {str(item.get("requirement_id")): item for item in coverage.get("requirements", []) if isinstance(item, Mapping)}
    missing: list[dict[str, Any]] = []
    for req_id, requirement in requirement_map.items():
        current = coverage_map.get(req_id, {})
        if str(current.get("status")) in {"complete", "not_applicable"}:
            continue
        missing.append({
            "requirement_id": req_id,
            "parent_question_id": str(requirement.get("parent_question_id") or "Q-UNMAPPED"),
            "section": requirement.get("section"),
            "status": current.get("status") or "unresolved",
            "decision_materiality": str(requirement.get("decision_materiality") or ("high" if requirement.get("required", True) else "low")),
            "reason": ";".join(current.get("missing_reasons") or ["coverage_not_satisfied"]),
            "recommended_routes": list(requirement.get("recommended_routes") or []),
            "required_roles": list(requirement.get("required_roles") or []),
            "required_metrics": list(requirement.get("required_metrics") or []),
            "missing_roles": list(current.get("missing_roles") or requirement.get("required_roles") or []),
            "missing_metrics": list(current.get("missing_metrics") or requirement.get("required_metrics") or []),
            "missing_geographies": list(current.get("required_geography_scopes") or requirement.get("geography_policy") or []),
        })
    return {
        "schema_version": 1,
        "target_id": str(profile.get("target_id") or "target").casefold(),
        "industry_family": industry_family(profile),
        "generated_at": generated_at or _now(),
        "status": "complete" if not missing else "refresh_required",
        "round": max(0, min(4, int(round_number))),
        "missing_requirements": missing,
        "next_action": "publish_l3" if not missing else "collect_missing_context",
    }
