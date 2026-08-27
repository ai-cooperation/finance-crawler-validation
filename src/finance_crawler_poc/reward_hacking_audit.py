"""Deterministic checks against shortcut-driven research promotion.

This is not a model judge.  It checks invariants that must remain true even
when a model is overconfident: a report cannot claim L3 while required context
is missing, source independence counts must match the actual group list, and a
qualitative section cannot be complete when its research requirements are not.
"""

from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _violation(code: str, message: str, *, severity: str = "high", path: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if path:
        item["path"] = path
    return item


def audit_report_payload(report: Mapping[str, Any], *, audited_at: str | None = None) -> dict[str, Any]:
    """Return a fail-closed audit result for one canonical report payload."""

    appendix = report.get("appendix") if isinstance(report.get("appendix"), Mapping) else {}
    coverage = appendix.get("context_coverage") if isinstance(appendix.get("context_coverage"), Mapping) else None
    gap = appendix.get("context_gap") if isinstance(appendix.get("context_gap"), Mapping) else None
    gates = report.get("quality_gates") if isinstance(report.get("quality_gates"), Mapping) else {}
    unresolved = appendix.get("unresolved") if isinstance(appendix.get("unresolved"), list) else []
    violations: list[dict[str, Any]] = []

    level = str(report.get("report_level") or "")
    if level == "L3":
        if coverage is None or not bool((coverage.get("summary") or {}).get("l3_ready") is True):
            violations.append(_violation("l3_without_context_coverage", "L3 report has no complete target-scoped requirement coverage.", path="appendix.context_coverage.summary.l3_ready"))
        required_gates = ("identity", "financial_model", "valuation", "audit", "valuation_contract", "evidence", "qualitative_research", "context_sufficiency")
        failed = [key for key in required_gates if gates.get(key) != "pass"]
        if failed:
            violations.append(_violation("l3_with_failed_gate", f"L3 report contains non-pass gates: {', '.join(failed)}.", path="quality_gates"))
        if gap is not None and str(gap.get("status") or "") != "complete":
            violations.append(_violation("l3_with_open_context_gap", "L3 report still carries a refresh_required context gap.", path="appendix.context_gap.status"))

    if coverage is not None:
        requirements = coverage.get("requirements") if isinstance(coverage.get("requirements"), list) else []
        for index, item in enumerate(requirements):
            if not isinstance(item, Mapping):
                continue
            groups = item.get("source_groups") if isinstance(item.get("source_groups"), list) else []
            distinct = len({str(group).casefold() for group in groups if str(group).strip()})
            reported = item.get("independent_source_count")
            if isinstance(reported, int) and reported != distinct:
                violations.append(_violation("independence_count_mismatch", f"Reported independent source count {reported} differs from distinct groups {distinct}.", path=f"appendix.context_coverage.requirements[{index}]"))

    qualitative = appendix.get("qualitative_context") if isinstance(appendix.get("qualitative_context"), Mapping) else {}
    sections = qualitative.get("sections") if isinstance(qualitative.get("sections"), Mapping) else {}
    if coverage is not None and sections:
        incomplete_sections = {
            str(item.get("section"))
            for item in (coverage.get("requirements") or [])
            if isinstance(item, Mapping) and str(item.get("status") or "") not in {"complete", "not_applicable"}
        }
        for section, value in sections.items():
            if isinstance(value, Mapping) and str(value.get("status") or "") == "complete" and str(section) in incomplete_sections:
                violations.append(_violation("model_complete_on_incomplete_context", f"Qualitative section {section} is complete while a required context question remains incomplete.", path=f"appendix.qualitative_context.sections.{section}.status"))

    if level == "L3" and any("gate_partial" in str(item) or "refresh_required" in str(item) for item in unresolved):
        violations.append(_violation("l3_unresolved_parity_mismatch", "L3 report retains unresolved gate or refresh markers.", path="appendix.unresolved"))

    return {
        "schema_version": "reward-hacking-audit.v1",
        "target_id": str(report.get("target", {}).get("symbol") or report.get("target_id") or "target") if isinstance(report.get("target"), Mapping) else str(report.get("target_id") or "target"),
        "report_id": str(report.get("report_id") or "unknown"),
        "audited_at": audited_at or _now(),
        "status": "pass" if not violations else "fail",
        "violations": violations,
        "invariants": {
            "l3_requires_context_coverage": True,
            "independence_count_is_distinct_group_count": True,
            "qualitative_sections_cannot_overrule_requirement_gaps": True,
            "unresolved_markers_must_match_report_level": True,
        },
    }
