from __future__ import annotations

import json
import hashlib

from finance_crawler_poc.target_audit import audit_target_artifacts
from finance_crawler_poc.target_profiles import get_target_profile


def test_audit_rejects_tsmc_identity_leak_in_non_tsmc_artifacts(tmp_path) -> None:
    profile = get_target_profile("delta")
    prefix = "delta-2308tw"
    metadata = {
        "target_id": "delta",
        "target": profile["target"],
        "source_registry": {"sources": [{"source_id": "twse_delta_company_profile", "source_tier": "official"}]},
        "raw_capture_paths": [],
    }
    depth = {
        "status": "professional_partial",
        "quality_gate": {"status": "professional_partial", "blocking_reasons": []},
        "time_series": {"series_id": "2308.TW"},
        "evidence_pack": {"items": [{"source_tier": "official"}]},
    }
    out = tmp_path
    (out / f"{prefix}-run-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (out / f"{prefix}-financial-depth.json").write_text(json.dumps(depth), encoding="utf-8")
    (out / f"{prefix}-human-report.md").write_text("# 台積電（2330.TW）", encoding="utf-8")

    result = audit_target_artifacts(profile, out)

    assert result["status"] == "fail"
    assert "target_identity_leak" in result["blocking_reasons"]


def test_audit_allows_incidental_tsmc_mention_in_target_news_headline(tmp_path) -> None:
    profile = get_target_profile("yageo")
    prefix = "yageo-2327tw"
    metadata = {
        "target_id": "yageo",
        "target": profile["target"],
        "source_registry": {"sources": [{"source_id": "twse_yageo_company_profile"}]},
        "raw_capture_paths": [],
    }
    depth = {
        "status": "professional_partial",
        "quality_gate": {"status": "professional_partial", "blocking_reasons": []},
        "time_series": {"series_id": "2327.TW"},
    }
    (tmp_path / f"{prefix}-run-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (tmp_path / f"{prefix}-financial-depth.json").write_text(json.dumps(depth), encoding="utf-8")
    (tmp_path / f"{prefix}-human-report.md").write_text(
        "# 國巨（2327.TW）研究摘要\n\n- ETF 加碼台積電，同時國巨上榜。",
        encoding="utf-8",
    )

    result = audit_target_artifacts(profile, tmp_path)

    assert result["status"] == "pass"
    assert "target_identity_leak" not in result["blocking_reasons"]


def test_audit_passes_structurally_valid_partial_artifacts(tmp_path) -> None:
    profile = get_target_profile("delta")
    prefix = "delta-2308tw"
    metadata = {
        "target_id": "delta",
        "target": profile["target"],
        "source_registry": {"sources": [{"source_id": "twse_delta_company_profile", "source_tier": "official"}]},
        "raw_capture_paths": [],
    }
    depth = {
        "status": "professional_partial",
        "quality_gate": {"status": "professional_partial", "blocking_reasons": ["event_study_required"]},
        "time_series": {"series_id": "2308.TW"},
        "evidence_pack": {"items": [{"source_tier": "official"}]},
    }
    out = tmp_path
    (out / f"{prefix}-run-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (out / f"{prefix}-financial-depth.json").write_text(json.dumps(depth), encoding="utf-8")
    (out / f"{prefix}-human-report.md").write_text("# 台達電（2308.TW）", encoding="utf-8")

    result = audit_target_artifacts(profile, out)

    assert result["status"] == "pass"
    assert result["research_status"] == "professional_partial"


def test_audit_reports_missing_and_mismatched_artifacts(tmp_path) -> None:
    profile = get_target_profile("delta")
    result = audit_target_artifacts(profile, tmp_path)
    assert result["status"] == "fail"
    assert "metadata_missing" in result["blocking_reasons"]
    assert "financial_depth_missing" in result["blocking_reasons"]
    assert "report_missing" in result["blocking_reasons"]


def test_audit_catches_quality_status_and_raw_capture_mismatch(tmp_path) -> None:
    profile = get_target_profile("delta")
    prefix = "delta-2308tw"
    metadata = {
        "target_id": "delta",
        "target": profile["target"],
        "source_registry": {"sources": []},
        "raw_capture_paths": ["targets/delta/missing.raw"],
    }
    depth = {
        "status": "professional_ready",
        "quality_gate": {"status": "professional_partial"},
        "time_series": {"series_id": "wrong"},
    }
    (tmp_path / f"{prefix}-run-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (tmp_path / f"{prefix}-financial-depth.json").write_text(json.dumps(depth), encoding="utf-8")
    (tmp_path / f"{prefix}-human-report.md").write_text("# 台達電（2308.TW）", encoding="utf-8")

    result = audit_target_artifacts(profile, tmp_path)

    assert "raw_capture_missing" in result["blocking_reasons"]
    assert "depth_quality_gate_status_mismatch" in result["blocking_reasons"]
    assert "time_series_target_mismatch" in result["blocking_reasons"]


def test_audit_verifies_official_raw_hash(tmp_path) -> None:
    profile = get_target_profile("delta")
    prefix = "delta-2308tw"
    out_root = tmp_path / "output" / "targets"
    out = out_root / "delta"
    out.mkdir(parents=True)
    official_path = out / "delta-official.raw.json"
    official_path.write_bytes(b"official")
    metadata = {
        "target_id": "delta",
        "target": profile["target"],
        "source_registry": {"sources": []},
        "raw_capture_paths": [],
        "official": {"raw_payload_path": "targets/delta/delta-official.raw.json", "response_sha256": hashlib.sha256(b"wrong").hexdigest()},
    }
    depth = {"status": "research_only", "quality_gate": {"status": "research_only"}, "time_series": {"series_id": "2308.TW"}}
    (out / f"{prefix}-run-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (out / f"{prefix}-financial-depth.json").write_text(json.dumps(depth), encoding="utf-8")
    (out / f"{prefix}-human-report.md").write_text("# 台達電（2308.TW）", encoding="utf-8")
    result = audit_target_artifacts(profile, out)
    assert "official_hash_mismatch" in result["blocking_reasons"]
