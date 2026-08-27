from __future__ import annotations

from finance_crawler_poc.reward_hacking_audit import audit_report_payload


def _report(*, level: str = "L2", l3_ready: bool = False) -> dict[str, object]:
    return {
        "schema_version": 2,
        "report_id": "equity_test_report",
        "report_level": level,
        "target": {"symbol": "2002.TW"},
        "quality_gates": {"identity": "pass", "financial_model": "pass", "valuation": "pass", "audit": "pass", "valuation_contract": "pass", "evidence": "pass", "qualitative_research": "pass", "context_sufficiency": "pass" if l3_ready else "partial"},
        "appendix": {
            "unresolved": [] if l3_ready else ["context_sufficiency_gate_partial"],
            "context_coverage": {"summary": {"l3_ready": l3_ready}, "requirements": []},
            "context_gap": {"status": "complete" if l3_ready else "refresh_required", "missing_requirements": [] if l3_ready else [{"requirement_id": "industry.market_demand"}]},
            "evidence": {"items": []},
        },
    }


def test_audit_rejects_l3_without_context_coverage() -> None:
    result = audit_report_payload(_report(level="L3", l3_ready=False))

    assert result["status"] == "fail"
    assert "l3_without_context_coverage" in {item["code"] for item in result["violations"]}


def test_audit_rejects_duplicate_independence_count() -> None:
    report = _report(level="L2", l3_ready=False)
    report["appendix"]["context_coverage"]["requirements"] = [
        {"requirement_id": "industry.market_demand", "status": "partial", "independent_source_count": 3, "source_groups": ["same", "same"]}
    ]

    result = audit_report_payload(report)

    assert result["status"] == "fail"
    assert "independence_count_mismatch" in {item["code"] for item in result["violations"]}


def test_audit_passes_consistent_partial_report() -> None:
    result = audit_report_payload(_report(level="L2", l3_ready=False))

    assert result["status"] == "pass"
    assert result["violations"] == []
