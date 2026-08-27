import json
from pathlib import Path

from finance_crawler_poc.qualitative_context_model import (
    _bounded_excerpt,
    build_qualitative_evidence_bundle,
    build_qualitative_model_input,
    invoke_opencode_model,
    invoke_qualitative_model,
    run_qualitative_context_model,
    render_qualitative_human_report,
    validate_qualitative_model_output,
)
from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.professional_equity import merge_qualitative_context_into_report


ROOT = Path(__file__).resolve().parents[1]


def _report_fixture() -> dict[str, object]:
    return {
        "target": {"symbol": "9999.TW", "name": "Fixture", "market": "TW", "currency": "TWD", "industry": "Test"},
        "generated_at": "2026-08-23T00:00:00Z",
        "appendix": {
            "evidence": {
                "items": [{"item_id": "N1", "title": "News", "content": "A short article", "published_at": "2026-08-22T00:00:00Z", "canonical_url": "https://example.test/news", "publisher_id": "news", "source_tier": "direct_secondary", "content_sha256": "a" * 64}],
                "qualitative_sources": {"company": [{"publisher": "Annual filing", "group": "official", "url": "https://example.test/filing", "response_sha256": "b" * 64, "raw_capture_path": "fixture.raw"}]},
            },
        },
        "chapters": [
            {"id": "6", "content": {"annual_periods": [{"year": 2025, "revenue": 100.0, "eps": 2.0}], "quarterly_periods": []}},
            {"id": "7", "content": {"forecast_periods": [{"year": 2026, "scenarios": {"base": {"revenue": 120.0, "eps": 2.4}}}], "assumptions": {"tax_rate": 0.2}}},
            {"id": "8", "content": {"methods": [{"method": "forward_pe"}], "target_range": {"low": 90, "base": 100, "high": 110}}},
            {"id": "9", "content": {"time_series": {"window_start": "2025-01-01", "window_end": "2026-08-22", "point_count": 2, "returns": {"observed_pct": 1.0}}}},
            {"id": "11", "content": {"source_conflicts": []}},
        ],
    }


def test_bundle_contains_target_scoped_extracts_and_deterministic_ids(tmp_path: Path) -> None:
    raw = tmp_path / "fixture.raw"
    raw.write_text("Business model: the company sells equipment. Board oversight and energy usage are disclosed.", encoding="utf-8")
    report = _report_fixture()
    report["appendix"]["evidence"]["qualitative_sources"]["company"][0]["raw_capture_path"] = str(raw)

    bundle = build_qualitative_evidence_bundle(report, project_root=tmp_path)

    assert bundle["target"]["symbol"] == "9999.TW"
    ids = {item["evidence_id"] for item in bundle["evidence_bundle"]}
    assert any(item_id.startswith("Q_company_") for item_id in ids)
    assert "N1" in ids
    company = next(item for item in bundle["evidence_bundle"] if item["evidence_id"].startswith("Q_company_"))
    assert company["excerpt"]
    assert company["response_sha256"] == "b" * 64


def test_bundle_scopes_bulk_twse_json_to_target_row(tmp_path: Path) -> None:
    raw = tmp_path / "twse.json"
    raw.write_text(json.dumps([
        {"公司代號": "1101", "公司名稱": "台泥"},
        {"公司代號": "1301", "公司名稱": "台塑"},
    ], ensure_ascii=False), encoding="utf-8")
    report = _report_fixture()
    report["target"] = {"symbol": "1301.TW", "name": "Formosa Plastics", "aliases": ["台塑"], "market": "TW", "currency": "TWD", "industry": "Petrochemicals"}
    report["appendix"]["evidence"]["qualitative_sources"] = {
        "company": [{"publisher": "TWSE", "group": "twse_profile", "url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "response_sha256": "b" * 64, "raw_capture_path": str(raw)}]
    }
    bundle = build_qualitative_evidence_bundle(report, project_root=tmp_path)
    excerpt = next(item for item in bundle["evidence_bundle"] if item["evidence_id"].startswith("Q_company_"))["excerpt"]
    assert '"公司代號": "1301"' in excerpt
    assert '"公司代號": "1101"' not in excerpt


def test_bundle_uses_twse_issuer_code_before_ambiguous_alias(tmp_path: Path) -> None:
    raw = tmp_path / "twse-ambiguous.json"
    raw.write_text(json.dumps([
        {"公司代號": "1303", "公司名稱": "南亞塑膠股份有限公司"},
        {"公司代號": "2408", "公司名稱": "南亞科技股份有限公司"},
    ], ensure_ascii=False), encoding="utf-8")
    report = _report_fixture()
    report["target"] = {"symbol": "1303.TW", "name": "Nan Ya Plastics", "aliases": ["南亞", "南亞塑膠"], "market": "TW", "currency": "TWD", "industry": "Chemicals"}
    report["appendix"]["evidence"]["qualitative_sources"] = {
        "company": [{"publisher": "TWSE", "group": "twse_profile", "url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "response_sha256": "c" * 64, "raw_capture_path": str(raw)}]
    }

    bundle = build_qualitative_evidence_bundle(report, project_root=tmp_path)
    excerpt = next(item for item in bundle["evidence_bundle"] if item["evidence_id"].startswith("Q_company_"))["excerpt"]

    assert '"公司代號": "1303"' in excerpt
    assert '"公司代號": "2408"' not in excerpt


def test_bounded_excerpt_prioritizes_research_specific_chinese_disclosures() -> None:
    noise = ("market bond terms and generic revenue table " * 220)
    company_signal = "各主要產品表現：AI 與通用型伺服器營收實現三位數百分比增長，PC 出貨微幅成長。"
    industry_signal = "我國伺服器代工約一千二百萬台，本公司市場占有率約百分之二十二，主要競爭者包括鴻海與廣達。"
    text = f"{noise} {company_signal} {industry_signal}"

    assert company_signal in _bounded_excerpt(text, "company", max_chars=1600)
    assert industry_signal in _bounded_excerpt(text, "industry", max_chars=1600)


def test_bundle_preserves_shared_source_as_section_scoped_records(tmp_path: Path) -> None:
    raw = tmp_path / "shared.raw"
    raw.write_text("董事會與公司治理資訊；公司商業模式與產品資訊。", encoding="utf-8")
    report = _report_fixture()
    report["appendix"]["evidence"]["qualitative_sources"] = {
        "company": [{"publisher": "Official filing", "group": "official", "url": "https://example.test/shared", "response_sha256": "c" * 64, "raw_capture_path": str(raw)}],
        "governance": [{"publisher": "Official filing", "group": "official", "url": "https://example.test/shared", "response_sha256": "c" * 64, "raw_capture_path": str(raw)}],
    }

    bundle = build_qualitative_evidence_bundle(report, project_root=tmp_path)

    rows = [item for item in bundle["evidence_bundle"] if item["url"] == "https://example.test/shared"]
    assert {item["section"] for item in rows} == {"company", "governance"}
    assert {item["evidence_id"].split("_", 2)[1] for item in rows} == {"company", "governance"}


def test_model_input_keeps_citation_eligible_rows_when_requirement_coverage_exists() -> None:
    bundle = {
        "evidence_bundle": [
            {"evidence_id": "blocked-company", "section": "company", "excerpt": "", "quality_flags": ["excerpt_unavailable"]},
            {"evidence_id": "annual-company", "section": "company", "excerpt": "Annual report business model and segment table " + ("detail " * 700), "quality_flags": []},
            {"evidence_id": "gov", "section": "governance", "excerpt": "Board and committee disclosure", "quality_flags": []},
            {"evidence_id": "esg", "section": "esg", "excerpt": "Emissions and target disclosure", "quality_flags": []},
        ],
        "requirement_coverage": {"summary": {"l3_ready": True}},
        "financial_snapshot": {},
    }

    compact = build_qualitative_model_input(bundle)
    ids = {row["evidence_id"] for row in compact["evidence_bundle"]}
    assert "annual-company" in ids
    assert "blocked-company" not in ids
    assert compact["model_selection"]["citation_eligible_evidence_count"] == 3


def test_model_input_prioritizes_section_specific_documents() -> None:
    bundle = {
        "evidence_bundle": [
            {"evidence_id": "esg-api", "section": "esg", "publisher": "TWSE financial API", "url": "https://openapi.twse.test", "excerpt": "API row", "quality_flags": []},
            {"evidence_id": "esg-report", "section": "esg", "publisher": "Issuer sustainability / ESG", "url": "https://issuer.test/2024-sustainability-report.pdf", "excerpt": "Scope 1 and Scope 2 KPI table", "quality_flags": []},
            {"evidence_id": "gov-api", "section": "governance", "publisher": "TWSE financial API", "url": "https://openapi.twse.test", "excerpt": "API row", "quality_flags": []},
            {"evidence_id": "gov-report", "section": "governance", "publisher": "Issuer corporate governance", "url": "https://issuer.test/governance", "excerpt": "Board and committee disclosure", "quality_flags": []},
        ],
        "requirement_coverage": {"summary": {"l3_ready": True}},
        "financial_snapshot": {},
    }

    compact = build_qualitative_model_input(bundle)
    ids = {row["evidence_id"] for row in compact["evidence_bundle"]}
    assert {"esg-report", "gov-report"}.issubset(ids)


def test_model_input_compacts_full_bundle_without_losing_audit_count() -> None:
    bundle = {
        "evidence_bundle": [
            {"evidence_id": "Q1", "section": "company", "section_excerpts": {"company": "company text"}, "excerpt": "long", "url": "https://example.test/company"},
            *[{"evidence_id": f"N{i}", "section": "news_social", "published_at": f"2026-08-{i:02d}", "excerpt": "news"} for i in range(1, 70)],
        ],
        "financial_snapshot": {},
    }
    compact = build_qualitative_model_input(bundle)
    assert compact["model_selection"]["full_evidence_count"] == 70
    assert compact["model_selection"]["included_evidence_count"] == 61
    assert compact["model_selection"]["excluded_evidence_count"] == 9
    assert "section_excerpts" not in compact["evidence_bundle"][0]
    assert compact["evidence_bundle"][0]["citation_eligible"] is True
    assert compact["evidence_bundle"][0]["evidence_role"] == "citation"
    assert compact["model_selection"]["citation_eligible_evidence_count"] == 61


def test_model_input_marks_metadata_rows_as_context_only() -> None:
    bundle = {
        "evidence_bundle": [
            {"evidence_id": "N1", "section": "news_social", "published_at": "2026-08-23", "excerpt": "headline", "quality_flags": ["metadata_only"]},
        ],
        "financial_snapshot": {},
    }
    compact = build_qualitative_model_input(bundle)
    row = compact["evidence_bundle"][0]
    assert row["citation_eligible"] is False
    assert row["evidence_role"] == "context_only"
    assert compact["model_selection"]["context_only_evidence_count"] == 1


def test_validator_marks_context_only_citation_as_partial() -> None:
    output = {
        "schema_version": "qualitative-context.v1",
        "target_id": "fixture",
        "as_of": "2026-08-23T00:00:00Z",
        "sections": {name: {"status": "complete", "claims": []} for name in ("company", "industry", "governance", "esg")},
    }
    output["sections"]["company"]["claims"] = [{"claim_id": "c1", "type": "fact", "confidence": "high", "evidence_ids": ["N1"]}]
    checked = validate_qualitative_model_output(
        output,
        target_id="fixture",
        as_of="2026-08-23T00:00:00Z",
        valid_evidence_ids={"N1"},
        evidence_metadata={"N1": {"citation_eligible": False, "quality_flags": []}},
    )
    assert checked["quality"]["evidence_quality"] == "partial"
    assert checked["validation"]["evidence_quality_violations"][0]["reason"] == "context_only_source"
    assert checked["sections"]["company"]["status"] == "partial"


def test_validator_rejects_dangling_evidence_and_recomputes_quality() -> None:
    output = {
        "schema_version": "qualitative-context.v1",
        "target_id": "fixture",
        "as_of": "2026-08-23T00:00:00Z",
        "overall_status": "complete",
        "sections": {name: {"status": "complete", "claims": []} for name in ("company", "industry", "governance", "esg")},
        "quality": {"claim_count": 999, "claims_with_evidence": 999, "evidence_coverage_ratio": 1.0},
    }
    output["sections"]["company"]["claims"] = [{"claim_id": "c1", "type": "fact", "evidence_ids": ["missing"]}]

    checked = validate_qualitative_model_output(output, target_id="fixture", as_of="2026-08-23T00:00:00Z", valid_evidence_ids={"N1"})

    assert checked["validation"]["status"] == "fail"
    assert checked["validation"]["dangling_evidence_ids"] == ["missing"]
    assert checked["validation"]["claim_count"] == 1
    assert checked["validation"]["claims_with_evidence"] == 0
    assert checked["validation"]["evidence_coverage_ratio"] == 0.0


def test_validator_trims_prompt_limit_overflow_and_records_adjustment() -> None:
    output = {
        "schema_version": "qualitative-context.v1",
        "target_id": "fixture",
        "as_of": "2026-08-23T00:00:00Z",
        "overall_status": "complete",
        "sections": {
            name: {
                "status": "complete",
                "summary": "摘要",
                "claims": [],
                "blind_spots": ["一", "二", "三", "四"] if name == "governance" else [],
                "missing_evidence": [],
            }
            for name in ("company", "industry", "governance", "esg")
        },
        "cross_section_synthesis": {},
    }

    checked = validate_qualitative_model_output(
        output,
        target_id="fixture",
        as_of="2026-08-23T00:00:00Z",
        valid_evidence_ids=set(),
    )

    assert checked["sections"]["governance"]["blind_spots"] == ["一", "二", "三"]
    assert checked["validation"]["output_limit_adjustments"] == [{
        "section": "governance",
        "field": "blind_spots",
        "original_count": 4,
        "retained_count": 3,
    }]


def test_validator_accepts_claim_with_known_evidence() -> None:
    output = {
        "schema_version": "qualitative-context.v1",
        "target_id": "fixture",
        "as_of": "2026-08-23T00:00:00Z",
        "overall_status": "complete",
        "sections": {name: {"status": "complete", "claims": []} for name in ("company", "industry", "governance", "esg")},
        "quality": {},
    }
    output["sections"]["company"]["claims"] = [{"claim_id": "c1", "type": "fact", "evidence_ids": ["N1"]}]

    checked = validate_qualitative_model_output(output, target_id="fixture", as_of="2026-08-23T00:00:00Z", valid_evidence_ids={"N1"})

    assert checked["validation"]["status"] == "pass"
    assert checked["validation"]["evidence_coverage_ratio"] == 1.0


def test_validator_requires_decision_link_for_covered_production_section() -> None:
    output = {
        "schema_version": "qualitative-context.v1",
        "target_id": "fixture",
        "as_of": "2026-08-23T00:00:00Z",
        "overall_status": "complete",
        "sections": {
            name: {"status": "complete", "summary": "摘要", "claims": [], "blind_spots": [], "missing_evidence": []}
            for name in ("company", "industry", "governance", "esg")
        },
        "cross_section_synthesis": {},
    }
    output["sections"]["industry"]["claims"] = [{
        "claim_id": "industry-001",
        "text": "美國水泥政策可能支撐需求。",
        "type": "inference",
        "evidence_ids": ["E1"],
        "mechanism": "基礎建設會增加水泥消費。",
        "falsifier": "若需求沒有成長則不成立。",
        "confidence": "medium",
        "evidence_quality": "direct",
        "requirement_ids": ["industry.market_demand"],
    }]
    coverage = {
        "requirements": [
            {"requirement_id": "industry.market_demand", "section": "industry", "status": "complete"},
        ]
    }

    checked = validate_qualitative_model_output(
        output,
        target_id="fixture",
        as_of="2026-08-23T00:00:00Z",
        valid_evidence_ids={"E1"},
        evidence_metadata={"E1": {"citation_eligible": True, "requirement_ids": ["industry.market_demand"]}},
        requirement_coverage=coverage,
    )

    assert checked["sections"]["industry"]["status"] == "partial"
    reasons = {item["reason"] for item in checked["validation"]["decision_quality_violations"]}
    assert "decision_link_incomplete" in reasons
    assert checked["quality"]["decision_quality"] == "partial"


def test_validator_rejects_stale_falsifier_and_incomplete_requirement() -> None:
    decision_link = {
        "driver": "區域水泥銷量",
        "target_exposure": "台泥亞洲水泥業務",
        "kpi": "銷量與每噸售價",
        "financial_line": "營收與營業利益率",
        "scenario_implication": "低於門檻時下修基準情境收入",
    }
    output = {
        "schema_version": "qualitative-context.v1",
        "target_id": "tcc",
        "as_of": "2026-08-26T00:00:00Z",
        "overall_status": "complete",
        "sections": {
            name: {"status": "complete", "summary": "摘要", "claims": [], "blind_spots": [], "missing_evidence": []}
            for name in ("company", "industry", "governance", "esg")
        },
        "cross_section_synthesis": {},
    }
    output["sections"]["industry"]["claims"] = [{
        "claim_id": "industry-001", "text": "亞洲需求支撐台泥水泥銷量。", "type": "inference",
        "evidence_ids": ["E1"], "mechanism": "銷量提升帶動營收。",
        "falsifier": "若2025年亞洲水泥需求低於2％，則聲明不成立。",
        "confidence": "medium", "evidence_quality": "direct",
        "requirement_ids": ["industry.market_demand"], "decision_link": decision_link,
    }]
    coverage = {
        "requirements": [
            {"requirement_id": "industry.market_demand", "section": "industry", "status": "partial"},
        ]
    }

    checked = validate_qualitative_model_output(
        output, target_id="tcc", as_of="2026-08-26T00:00:00Z", valid_evidence_ids={"E1"},
        evidence_metadata={"E1": {"citation_eligible": True, "requirement_ids": ["industry.market_demand"]}},
        requirement_coverage=coverage,
    )

    violations = checked["validation"]["decision_quality_violations"]
    assert {item["reason"] for item in violations} >= {"stale_falsifier", "requirement_incomplete"}
    assert checked["sections"]["industry"]["status"] == "partial"


def test_validator_flags_high_confidence_claim_from_metadata_only_source() -> None:
    output = {
        "schema_version": "qualitative-context.v1",
        "target_id": "fixture",
        "as_of": "2026-08-23T00:00:00Z",
        "sections": {name: {"status": "partial", "claims": []} for name in ("company", "industry", "governance", "esg")},
    }
    output["sections"]["company"]["claims"] = [{"claim_id": "c1", "type": "fact", "confidence": "high", "evidence_ids": ["N1"]}]

    checked = validate_qualitative_model_output(
        output,
        target_id="fixture",
        as_of="2026-08-23T00:00:00Z",
        valid_evidence_ids={"N1"},
        evidence_metadata={"N1": {"quality_flags": ["metadata_only"]}},
    )

    assert checked["validation"]["status"] == "pass"
    assert checked["quality"]["evidence_quality"] == "insufficient"
    assert checked["overall_status"] == "partial"
    assert checked["validation"]["evidence_quality_violations"][0]["claim_id"] == "c1"
    assert checked["sections"]["company"]["status"] == "partial"
    assert checked["sections"]["company"]["claims"][0]["confidence"] == "unresolved"
    assert checked["sections"]["company"]["claims"][0]["evidence_quality"] == "insufficient"


def test_human_renderer_links_evidence_ids() -> None:
    bundle = {"target": {"name": "Fixture"}, "as_of": "2026-08-23T00:00:00Z", "evidence_bundle": [{"evidence_id": "N1", "url": "https://example.test/source"}]}
    envelope = {"model": "fixture", "input_sha256": "a" * 64, "result": {"as_of": "2026-08-23T00:00:00Z", "overall_status": "partial", "summary": "summary", "sections": {key: {"status": "partial", "claims": [{"claim_id": "c", "text": "claim", "type": "fact", "confidence": "low", "evidence_ids": ["N1"], "mechanism": "mechanism", "falsifier": "falsifier"}], "blind_spots": [], "missing_evidence": []} for key in ("company", "industry", "governance", "esg")}, "quality": {"claim_count": 4, "claims_with_evidence": 4, "evidence_coverage_ratio": 1.0, "dangling_evidence_ids": []}}}

    rendered = render_qualitative_human_report(envelope, bundle)

    assert "[N1](https://example.test/source)" in rendered
    assert "公司與商業模式" in rendered


def test_native_ollama_transport_disables_thinking() -> None:
    captured: dict[str, object] = {}

    class Response:
        is_error = False
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"message": {"content": "{\"ok\":true}"}, "eval_count": 2}

    def post(url: str, **kwargs: object) -> Response:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return Response()

    result = invoke_qualitative_model(
        endpoint="http://qwen.test",
        model="qwen",
        system_prompt="JSON",
        user_payload={"x": 1},
        native_ollama=True,
        post=post,
    )

    assert captured["url"] == "http://qwen.test/api/chat"
    assert captured["json"]["think"] is False
    assert result["content"] == '{"ok":true}'


def test_opencode_json_event_transport_extracts_text() -> None:
    class Completed:
        returncode = 0
        stderr = ""
        stdout = '\n'.join([
            json.dumps({"type": "step_start"}),
            json.dumps({"type": "text", "part": {"text": '{"ok":'}}),
            json.dumps({"type": "text", "part": {"text": "true}"}}),
        ])

    def run(command: list[str], **kwargs: object) -> Completed:
        assert command[:4] == ["opencode", "run", "--model", "opencode/big-pickle"]
        assert command[4:7] == ["--format", "json", "--pure"]
        assert kwargs["timeout"] == 5
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert Path(str(env["XDG_DATA_HOME"]).removesuffix("/data")).name.startswith("finance-crawler-opencode-")
        assert Path(str(env["XDG_STATE_HOME"]).removesuffix("/state")).name.startswith("finance-crawler-opencode-")
        return Completed()

    result = invoke_opencode_model(
        model="opencode/big-pickle",
        system_prompt="JSON",
        user_payload={"x": 1},
        timeout_seconds=5,
        run=run,
    )
    assert result["content"] == '{"ok":\ntrue}'
    assert result["usage"]["event_count"] == 3


def test_auto_provider_falls_back_to_qwen_after_opencode_failure(monkeypatch) -> None:
    import finance_crawler_poc.qualitative_context_model as module

    def fail_opencode(**kwargs: object) -> dict[str, object]:
        raise RuntimeError("OpenCode unavailable")

    valid = {
        "schema_version": "qualitative-context.v1",
        "target_id": "fixture",
        "as_of": "2026-08-23T00:00:00Z",
        "overall_status": "partial",
        "summary": "partial",
        "sections": {name: {"status": "partial", "summary": "partial", "claims": [], "blind_spots": [], "missing_evidence": []} for name in ("company", "industry", "governance", "esg")},
        "cross_section_synthesis": {"key_mechanisms": [], "contradictions": [], "monitoring": [], "unresolved_questions": []},
        "quality": {},
    }

    def fake_qwen(**kwargs: object) -> dict[str, object]:
        return {"content": json.dumps(valid, ensure_ascii=False), "response": {}, "usage": {"provider": "qwen"}}

    monkeypatch.setattr(module, "invoke_opencode_model", fail_opencode)
    monkeypatch.setattr(module, "invoke_qualitative_model", fake_qwen)
    envelope, _, _ = run_qualitative_context_model(
        _report_fixture(), project_root=ROOT, target_id="fixture", endpoint="http://qwen.test", model="qwen", timeout_seconds=5,
    )
    assert envelope["model"] == "qwen"
    assert envelope["input_summary"]["provider"] == "qwen"
    assert envelope["input_summary"]["provider_attempts"][0]["provider"] == "opencode"


def test_qualitative_run_contract_is_validated() -> None:
    result = {
        "schema_version": "qualitative-context.v1",
        "target_id": "fixture",
        "as_of": "2026-08-23T00:00:00Z",
        "overall_status": "complete",
        "sections": {
            key: {"status": "complete", "summary": "摘要", "claims": [], "blind_spots": [], "missing_evidence": []}
            for key in ("company", "industry", "governance", "esg")
        },
        "cross_section_synthesis": {"key_mechanisms": [], "contradictions": [], "monitoring": [], "unresolved_questions": []},
        "quality": {},
        "validation": {"status": "pass", "target_match": True, "as_of_match": True, "required_sections": True, "dangling_evidence_ids": []},
    }
    validate_contract("qualitative-context-result", result)
    validate_contract(
        "qualitative-context-model",
        {
            "schema_version": "qualitative-context-model-run.v1",
            "run_id": "qualitative_fixture_20260823T000000Z",
            "target_id": "fixture",
            "model": "fixture-model",
            "input_sha256": "a" * 64,
            "generated_at": "2026-08-23T00:00:00Z",
            "input_summary": {},
            "validation": result["validation"],
            "result": result,
        },
    )


def test_merge_qualitative_context_is_fail_closed_and_human_renderable() -> None:
    report = {
        "report_level": "L3",
        "quality_gates": {"qualitative_research": "pass"},
        "appendix": {"unresolved": [], "run_metadata": {}},
        "executive_summary": {},
        "decision_card": {"rating": "Positive", "target_range": {"base": 100}},
        "chapters": [{"id": str(i), "content": {}} for i in range(15)],
    }
    envelope = {
        "run_id": "qualitative_fixture_20260823T000000Z",
        "model": "fixture-model",
        "input_sha256": "a" * 64,
        "validation": {"status": "pass", "evidence_quality_violations": [{"claim_id": "company-001"}]},
        "result": {
            "overall_status": "partial",
            "sections": {
                "company": {"status": "complete", "summary": "公司摘要", "claims": [], "blind_spots": [], "missing_evidence": []},
                "industry": {"status": "complete", "summary": "產業摘要", "claims": [], "blind_spots": [], "missing_evidence": []},
                "governance": {"status": "partial", "summary": "治理摘要", "claims": [], "blind_spots": ["委員會資料"], "missing_evidence": ["最新年報"]},
                "esg": {"status": "partial", "summary": "ESG 摘要", "claims": [], "blind_spots": [], "missing_evidence": ["Scope 1-3"]},
            },
            "quality": {"evidence_quality": "insufficient"},
            "cross_section_synthesis": {"monitoring": []},
        },
    }
    merged = merge_qualitative_context_into_report(report, envelope, evidence_bundle=[])
    assert merged["report_level"] == "L2"
    assert merged["quality_gates"]["qualitative_research"] == "partial"
    assert "qualitative_model_gate_partial" in merged["appendix"]["unresolved"]
    assert merged["chapters"][3]["content"]["model_summary"] == ""
    assert merged["appendix"]["qualitative_context"]["sections"]["company"]["summary"] == "公司摘要"


def test_merge_keeps_qualitative_unresolved_when_valuation_contract_passes() -> None:
    report = {
        "report_level": "L2",
        "quality_gates": {"valuation_contract": "pass", "qualitative_research": "partial"},
        "appendix": {"unresolved": [], "run_metadata": {}},
        "decision_card": {"rating": "Not Rated", "target_range": None},
        "chapters": [{"id": str(i), "content": {}} for i in range(15)],
    }
    envelope = {
        "run_id": "partial-fixture",
        "model": "fixture-model",
        "validation": {"status": "pass", "evidence_quality_violations": []},
        "result": {
            "overall_status": "partial",
            "quality": {"evidence_quality": "partial"},
            "sections": {
                name: {"status": "partial" if name == "industry" else "complete", "claims": [], "missing_evidence": []}
                for name in ("company", "industry", "governance", "esg")
            },
            "cross_section_synthesis": {},
        },
    }

    merged = merge_qualitative_context_into_report(report, envelope)

    assert merged["report_level"] == "L2"
    assert "qualitative_model_gate_partial" in merged["appendix"]["unresolved"]
