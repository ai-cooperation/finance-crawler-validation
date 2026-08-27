"""Structural and identity audit for target-harness artifacts."""

from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from finance_crawler_poc.target_harness import artifact_prefix


def audit_target_artifacts(profile: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    target = profile.get("target") if isinstance(profile.get("target"), Mapping) else {}
    target_id = str(profile.get("target_id") or "target").casefold()
    prefix = artifact_prefix(profile)
    metadata_path = output_dir / f"{prefix}-run-metadata.json"
    depth_path = output_dir / f"{prefix}-financial-depth.json"
    report_path = output_dir / f"{prefix}-human-report.md"
    professional_report_path = output_dir / f"{prefix}-research-report.json"
    blocking: list[str] = []
    checks: list[dict[str, Any]] = []

    metadata = _load_or_fail(metadata_path, checks, blocking, "metadata_missing")
    depth = _load_or_fail(depth_path, checks, blocking, "financial_depth_missing")
    report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    if not report:
        blocking.append("report_missing")
        checks.append({"check_id": "report", "status": "fail"})
    else:
        checks.append({"check_id": "report", "status": "pass"})

    expected_symbol = str(target.get("symbol") or "")
    if isinstance(metadata, Mapping):
        passed = metadata.get("target_id") == target_id and metadata.get("target", {}).get("symbol") == expected_symbol
        checks.append({"check_id": "metadata_target_identity", "status": "pass" if passed else "fail"})
        if not passed:
            blocking.append("metadata_target_identity_mismatch")
        registry = metadata.get("source_registry")
        source_ids = [str(source.get("source_id")) for source in registry.get("sources", []) if isinstance(source, Mapping)] if isinstance(registry, Mapping) else []
        if target_id != "tsmc" and any("tsmc" in source_id.casefold() or "2330" in source_id for source_id in source_ids):
            blocking.append("source_registry_identity_leak")
            checks.append({"check_id": "source_registry_identity", "status": "fail"})
        else:
            checks.append({"check_id": "source_registry_identity", "status": "pass"})
        for relative in metadata.get("raw_capture_paths", []):
            capture_path = Path(str(relative))
            if not capture_path.is_absolute():
                capture_path = output_dir.parent.parent / capture_path
            if not capture_path.exists():
                blocking.append("raw_capture_missing")
                break
        official_meta = metadata.get("official") if isinstance(metadata.get("official"), Mapping) else {}
        official_relative = official_meta.get("raw_payload_path")
        if official_relative and official_meta.get("response_sha256"):
            official_path = output_dir.parent.parent / Path(str(official_relative))
            if not official_path.exists() or hashlib.sha256(official_path.read_bytes()).hexdigest() != str(official_meta["response_sha256"]):
                blocking.append("official_hash_mismatch")
                checks.append({"check_id": "official_raw_hash", "status": "fail"})
            else:
                checks.append({"check_id": "official_raw_hash", "status": "pass"})
        qualitative_failures = metadata.get("qualitative_fetch_failures")
        if isinstance(qualitative_failures, list):
            failures = [item for item in qualitative_failures if isinstance(item, Mapping)]
            required_failures = [item for item in failures if item.get("required", True) is not False]
            optional_failures = len(failures) - len(required_failures)
            checks.append({"check_id": "qualitative_source_fetches", "status": "pass" if not required_failures else "fail", "failure_count": len(required_failures), "optional_failure_count": optional_failures})
            if required_failures:
                blocking.append("qualitative_source_fetch_failed")
        else:
            checks.append({"check_id": "qualitative_source_fetches", "status": "not_run"})

    if isinstance(depth, Mapping):
        depth_status = depth.get("status")
        gate_status = depth.get("quality_gate", {}).get("status") if isinstance(depth.get("quality_gate"), Mapping) else None
        if depth_status != gate_status:
            blocking.append("depth_quality_gate_status_mismatch")
            checks.append({"check_id": "depth_quality_gate_status", "status": "fail"})
        else:
            checks.append({"check_id": "depth_quality_gate_status", "status": "pass"})
        series_id = depth.get("time_series", {}).get("series_id") if isinstance(depth.get("time_series"), Mapping) else None
        if series_id != expected_symbol:
            blocking.append("time_series_target_mismatch")
            checks.append({"check_id": "time_series_identity", "status": "fail"})
        else:
            checks.append({"check_id": "time_series_identity", "status": "pass"})
    # Identity is a primary-document property.  A target-specific news item
    # may legitimately mention TSMC (for example an ETF buying both TSMC and
    # Yageo); scanning every body token turns that co-mention into a false
    # leak.  The H1 title is the human report's identity declaration, while
    # metadata, registry and time-series identities are audited separately.
    report_heading = next((line.strip() for line in report.splitlines() if line.strip().startswith("# ")), "")
    if target_id != "tsmc" and any(token in report_heading.casefold() for token in ("tsmc", "台積電", "2330.tw", "2330")):
        blocking.append("target_identity_leak")
        checks.append({"check_id": "report_identity", "status": "fail"})
    else:
        checks.append({"check_id": "report_identity", "status": "pass"})

    professional_report = _load_optional(professional_report_path)
    if isinstance(professional_report, Mapping):
        report_run_metadata = professional_report.get("appendix", {}).get("run_metadata", {}) if isinstance(professional_report.get("appendix"), Mapping) else {}
        if isinstance(report_run_metadata, Mapping) and "qualitative_fetch_failures" in report_run_metadata:
            failures = [item for item in report_run_metadata.get("qualitative_fetch_failures", []) if isinstance(item, Mapping)]
            required_failures = [item for item in failures if item.get("required", True) is not False]
            optional_failures = len(failures) - len(required_failures)
            checks.append({"check_id": "qualitative_source_fetches_report", "status": "pass" if not required_failures else "fail", "failure_count": len(required_failures), "optional_failure_count": optional_failures})
            if required_failures:
                blocking.append("qualitative_source_fetch_failed")
        unresolved = professional_report.get("appendix", {}).get("unresolved", []) if isinstance(professional_report.get("appendix"), Mapping) else []
        coverage = professional_report.get("appendix", {}).get("claim_evidence_coverage", {}) if isinstance(professional_report.get("appendix"), Mapping) else {}
        coverage_ok = coverage.get("status") == "pass" and int(coverage.get("unlinked_claim_count") or 0) == 0
        checks.append({"check_id": "claim_evidence_coverage", "status": "pass" if coverage_ok else "fail"})
        if not coverage_ok:
            blocking.append("claim_evidence_coverage_incomplete")
        qualitative = professional_report.get("appendix", {}).get("qualitative_context") if isinstance(professional_report.get("appendix"), Mapping) else None
        if isinstance(qualitative, Mapping):
            qualitative_quality = qualitative.get("quality") if isinstance(qualitative.get("quality"), Mapping) else {}
            qualitative_validation = qualitative.get("validation") if isinstance(qualitative.get("validation"), Mapping) else {}
            qualitative_ready = (
                qualitative_validation.get("status") == "pass"
                and qualitative.get("overall_status") == "complete"
                and qualitative_quality.get("evidence_quality") == "complete"
                and professional_report.get("quality_gates", {}).get("qualitative_research") == "pass"
            )
            checks.append({"check_id": "qualitative_context_quality", "status": "pass" if qualitative_ready else "fail"})
            if not qualitative_ready:
                blocking.append("qualitative_context_not_ready")
        else:
            checks.append({"check_id": "qualitative_context_quality", "status": "not_run"})
        event = depth.get("event_alignment") if isinstance(depth, Mapping) and isinstance(depth.get("event_alignment"), Mapping) else {}
        event_complete = event.get("event_study_quality_status") in {None, "complete"}
        checks.append({"check_id": "event_study_completeness", "status": "pass" if event_complete else "fail"})
        if not event_complete:
            blocking.append("event_study_not_complete")
        shared_status = depth.get("quality_gate", {}).get("status") if isinstance(depth, Mapping) and isinstance(depth.get("quality_gate"), Mapping) else None
        report_level = professional_report.get("report_level")
        level_consistent = not (report_level == "L3" and shared_status != "professional_ready")
        checks.append({"check_id": "report_level_matches_shared_gate", "status": "pass" if level_consistent else "fail"})
        if not level_consistent:
            blocking.append("report_level_overstates_shared_gate")
        report_ready = report_level == "L3"
        checks.append({"check_id": "professional_report_level", "status": "pass" if report_ready else "fail"})
        if not report_ready:
            blocking.append("professional_report_not_l3")
        unresolved_risk = any(
            isinstance(item, Mapping) and str(item.get("probability") or "").casefold() == "unresolved"
            for chapter in professional_report.get("chapters", []) if isinstance(professional_report.get("chapters"), list)
            for item in (chapter.get("content", {}).get("risks", []) if isinstance(chapter, Mapping) and isinstance(chapter.get("content"), Mapping) else [])
        )
        text_consistent = not (not unresolved and unresolved_risk)
        checks.append({"check_id": "unresolved_disclosure_parity", "status": "pass" if text_consistent else "fail"})
        if not text_consistent:
            blocking.append("unresolved_disclosure_parity")

    # ``depth.status`` is the shared ingestion gate, not the human research
    # report level.  When the professional JSON exists, report that level so
    # an audit cannot label an L2 report as ``professional_ready`` merely
    # because the market-data depth artifact passed its own gate.  Fixtures
    # that only contain the legacy depth artifact retain the old status.
    audited_research_status = (
        str(professional_report.get("report_level"))
        if isinstance(professional_report, Mapping) and professional_report.get("report_level")
        else depth.get("status") if isinstance(depth, Mapping) else "unavailable"
    )
    return {
        "schema_version": 1,
        "target_id": target_id,
        "status": "fail" if blocking else "pass",
        "research_status": audited_research_status,
        "blocking_reasons": list(dict.fromkeys(blocking)),
        "checks": checks,
        "artifact_prefix": prefix,
    }


def _load_or_fail(path: Path, checks: list[dict[str, Any]], blocking: list[str], reason: str) -> Any:
    if not path.exists():
        checks.append({"check_id": reason, "status": "fail"})
        blocking.append(reason)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        checks.append({"check_id": reason, "status": "fail"})
        blocking.append(reason)
        return None
    checks.append({"check_id": reason, "status": "pass"})
    return payload


def _load_optional(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
