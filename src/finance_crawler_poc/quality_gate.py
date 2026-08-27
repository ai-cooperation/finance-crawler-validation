"""Shared, fail-closed readiness evaluation for research packs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from finance_crawler_poc.contracts import validate_contract


_EQUITY_KINDS = frozenset({"equity", "company", "etf"})


def evaluate_quality_gate(
    *,
    target: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    time_series: Mapping[str, Any],
    fundamentals: Mapping[str, Any],
    valuation: Mapping[str, Any],
    market_drivers: Mapping[str, Any],
    event_alignment: Mapping[str, Any],
    provider_data: Mapping[str, Any] | None = None,
    requirement_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the shared readiness contract without caller overrides."""

    kind = str(target.get("kind") or "unknown").strip().casefold()
    evidence_count = int(evidence_pack.get("canonical_story_count") or 0)
    source_groups = _source_groups(evidence_pack)
    tiers = _source_tiers(evidence_pack)
    checks: list[dict[str, Any]] = []
    blocking: list[str] = []

    _record(checks, blocking, "target_evidence", evidence_count >= 1, True, "target evidence is required")
    _record(checks, blocking, "time_series", time_series.get("status") == "available", True, "time series must be available")

    # Evidence volume and transport success are not substitutes for the
    # frozen research-question matrix.  Once a run supplies a coverage
    # payload, every mandatory requirement must be complete before this gate
    # can emit professional_ready.  The check is deliberately fail-closed:
    # malformed or absent summary metadata is a blocker, not an optimistic
    # pass based on source count.
    if requirement_coverage is not None:
        coverage_summary = requirement_coverage.get("summary") if isinstance(requirement_coverage.get("summary"), Mapping) else {}
        coverage_ready = coverage_summary.get("l3_ready") is True
        _record(
            checks,
            blocking,
            "requirement_coverage",
            coverage_ready,
            True,
            "all mandatory research requirements must be complete",
            reason="requirement_coverage_required",
        )

    if kind in _EQUITY_KINDS:
        _record(
            checks,
            blocking,
            "official_source",
            bool(tiers & {"official", "regulatory"}),
            True,
            "official or regulatory source is required",
            reason="official_source_required",
        )
        has_financial_official = any(
            isinstance(item, Mapping)
            and (
                item.get("source_tier") == "regulatory"
                or item.get("official_scope") in {"financial_filing", "annual_report", "financial_statement"}
            )
            for item in evidence_pack.get("items", [])
        )
        _record(
            checks,
            blocking,
            "official_financial_source",
            has_financial_official,
            True,
            "an official filing or financial-statement source is required",
            reason="official_financial_source_required",
        )
        direct_groups = _source_groups_by_tier(evidence_pack, {"direct_primary", "direct_secondary"})
        _record(
            checks,
            blocking,
            "independent_direct_sources",
            len(direct_groups) >= 2,
            True,
            "at least two independent direct publishers are required",
            reason="independent_direct_sources_required",
        )
        valuation_reason = (
            "valuation_positive_eps_required"
            if "positive_eps_for_pe_valuation" in (valuation.get("missing_fields") or [])
            else "valuation_period_alignment_required"
        )
        _record(
            checks,
            blocking,
            "valuation_period_alignment",
            (
                valuation.get("status") == "available" and valuation.get("period_alignment_status") == "aligned"
            )
            or (
                valuation.get("dcf_only_fallback_eligible") is True
                and valuation.get("period_alignment_status") == "not_applicable"
            ),
            True,
            "valuation inputs must use aligned periods",
            reason=valuation_reason,
        )
        _record(
            checks,
            blocking,
            "event_study",
            event_alignment.get("event_study_status") == "available",
            True,
            "benchmark event study is required for equity/company research",
            reason="event_study_required",
        )
        event_complete = (
            event_alignment.get("event_study_status") == "available"
            and event_alignment.get("event_study_quality_status") == "complete"
            and event_alignment.get("event_study_significance_status") == "computed"
            and int(event_alignment.get("event_study_unique_event_date_count") or 0) >= 8
            and int(event_alignment.get("unresolved_event_count") or 0) == 0
        )
        _record(
            checks,
            blocking,
            "event_study_completeness",
            event_complete,
            True,
            "event study must include a complete sample, inference and no unresolved event records",
            reason="event_study_not_complete",
        )
    else:
        _record(
            checks,
            blocking,
            "independent_sources",
            len(source_groups) >= 2,
            True,
            "at least two independent source groups are required",
            reason="independent_sources_required",
        )
        _record(checks, blocking, "valuation", valuation.get("status") == "not_applicable", False, "not applicable for this target kind")

    if kind == "crypto":
        provider_map = provider_data or {}
        provider_ready = all(
            isinstance(provider_map.get(name), Mapping)
            and provider_map[name].get("status") in {"available", "not_applicable"}
            for name in ("volume", "etf_flows", "derivatives", "on_chain")
        )
        _record(
            checks,
            blocking,
            "market_provider_bundle",
            provider_ready and market_drivers.get("status") == "available",
            True,
            "market confirmation provider bundle must be available",
            reason="market_provider_bundle_required",
        )

    if evidence_count == 0:
        status = "research_only"
    elif time_series.get("status") != "available":
        status = "research_only"
    elif blocking:
        status = "professional_partial"
    else:
        status = "professional_ready"

    payload = {
        "schema_version": 1,
        "gate_id": "shared_research_quality_gate_v1",
        "target_kind": kind,
        "status": status,
        "checks": checks,
        "blocking_reasons": list(dict.fromkeys(blocking)),
        "metrics": {
            "canonical_story_count": evidence_count,
            "source_group_count": len(source_groups),
            "source_tiers": sorted(tiers),
            **({
                "requirement_coverage_ratio": _number_or_zero(coverage_summary.get("coverage_ratio")),
                "required_requirement_count": _int_or_zero(coverage_summary.get("required_count")),
                "complete_requirement_count": _int_or_zero(coverage_summary.get("complete_count")),
            } if requirement_coverage is not None else {}),
        },
    }
    validate_contract("quality-gate", payload)
    return payload


def _record(
    checks: list[dict[str, Any]],
    blocking: list[str],
    check_id: str,
    passed: bool,
    required: bool,
    explanation: str,
    *,
    reason: str | None = None,
) -> None:
    """Append one check and its stable blocking reason."""

    status = "pass" if passed else "fail"
    check = {"check_id": check_id, "status": status, "required": required}
    if not passed:
        check["reason"] = explanation
        if required and reason:
            blocking.append(reason)
    elif not required:
        check["status"] = "not_applicable"
    checks.append(check)


def _source_groups(evidence_pack: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("independence_group"))
        for item in evidence_pack.get("items", [])
        if isinstance(item, Mapping) and item.get("independence_group")
    }


def _source_groups_by_tier(evidence_pack: Mapping[str, Any], tiers: set[str]) -> set[str]:
    return {
        str(item.get("independence_group"))
        for item in evidence_pack.get("items", [])
        if isinstance(item, Mapping)
        and item.get("source_tier") in tiers
        and item.get("independence_group")
    }


def _source_tiers(evidence_pack: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("source_tier"))
        for item in evidence_pack.get("items", [])
        if isinstance(item, Mapping) and item.get("source_tier")
    }


def _number_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
