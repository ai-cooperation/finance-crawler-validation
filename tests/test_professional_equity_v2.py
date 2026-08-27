from __future__ import annotations

from copy import deepcopy

import json
import re
from pathlib import Path

import pytest
import httpx

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.professional_equity import (
    build_forecast_model,
    build_professional_research_report,
    build_valuation_model,
    fetch_finmind_financial_history,
    fetch_research_context_evidence,
    finalize_professional_report,
    normalize_finmind_financial_history,
    _number_markdown_citations,
    _claim_evidence_coverage,
    _build_catalysts,
    _catalyst_display_event,
    merge_qualitative_context_into_report,
    render_professional_report,
    write_professional_artifacts,
    _inline_source,
    _explanatory_lens,
)


def test_catalyst_builder_does_not_pad_report_with_synthetic_placeholders() -> None:
    catalysts = _build_catalysts(
        {"market_drivers": {"news_driver_candidates": []}},
        {},
    )

    assert catalysts == []


def test_unresolved_single_source_news_lead_is_not_promoted_to_catalyst() -> None:
    catalysts = _build_catalysts(
        {
            "market_drivers": {
                "news_driver_candidates": [{
                    "title": "台泥與多檔股票的投資建議整理",
                    "label": "ETF flows",
                    "causal_status": "unresolved",
                    "source_count": 1,
                    "evidence_ids": ["E1"],
                }]
            }
        },
        {},
    )

    assert catalysts == []


def test_release_gate_removes_persisted_unverified_catalysts(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=0.0,
        peer_median_pe=18.0,
    )
    report = build_professional_research_report(
        profile={"target_id": "tcc", "target": {"symbol": "1101.TW", "name": "台泥", "market": "TW", "currency": "TWD", "industry": "Cement"}},
        history=history,
        forecast=forecast,
        valuation=valuation,
        depth={"time_series": {}, "event_alignment": {}, "evidence_pack": {"items": []}, "source_conflicts": [], "market_drivers": {}},
        metadata={"run_id": "run_old_persisted"},
    )
    stale = {"event": "外部新聞線索（待公司／監管原文驗證）", "probability": "unresolved", "evidence_ids": ["E1"]}
    for chapter in report["chapters"]:
        if chapter["id"] in {"1", "10"}:
            chapter["content"]["catalysts"] = [stale]
    report["executive_summary"]["catalysts"] = [stale]

    final_report, markdown, audit = finalize_professional_report(report)

    assert all(
        chapter["content"].get("catalysts") == []
        for chapter in final_report["chapters"]
        if chapter["id"] in {"1", "10"}
    )
    assert "外部新聞線索" not in markdown
    assert "unverified_catalysts_excluded" in final_report["appendix"]["unresolved"]
    assert "synthetic_research_placeholder" not in audit["summary"]["issue_counts"]


def test_executive_summary_blind_spot_is_issuer_specific() -> None:
    content = {
        "theses": [{"title": "電子材料組合改善"}],
        "catalysts": [],
        "risks": [{"risk": "資本支出回收期拉長"}],
    }
    report = {
        "target": {"currency": "TWD"},
        "decision_card": {
            "rating": "Cautious",
            "confidence": "low",
            "market_price": 180,
            "target_range": {"low": 12.46, "base": 12.48, "high": 20.28},
        },
        "chapters": [{
            "id": "3",
            "content": {
                "model_status": "complete",
                "model_blind_spots": ["各事業群獲利貢獻未量化揭露。"],
                "model_missing_evidence": ["各產品事業群獨立損益。"],
                "model_claims": [{"falsifier": "後續兩季毛利率回落。"}],
            },
        }],
    }
    rendered = _explanatory_lens("1", content, report)
    assert "各事業群獲利貢獻未量化揭露" in rendered
    assert "後續兩季毛利率回落" in rendered
    assert "各產品事業群獨立損益" in rendered
    assert "若三條論點都依賴同一個核心需求假設" not in rendered


def _finmind_payloads() -> dict[str, list[dict[str, object]]]:
    income: list[dict[str, object]] = []
    balance: list[dict[str, object]] = []
    cashflow: list[dict[str, object]] = []
    monthly: list[dict[str, object]] = []
    for year in range(2020, 2026):
        cumulative_cfo = 0.0
        cumulative_capex = 0.0
        cumulative_depreciation = 0.0
        for quarter, month in enumerate((3, 6, 9, 12), start=1):
            date = f"{year}-{month:02d}-{'31' if month in (3, 12) else '30'}"
            revenue = 1000.0 + (year - 2020) * 120.0 + quarter * 15.0
            for item_type, value in {
                "Revenue": revenue,
                "GrossProfit": revenue * 0.52,
                "OperatingIncome": revenue * 0.40,
                "IncomeAfterTaxes": revenue * 0.32,
                "EPS": revenue * 0.0032,
            }.items():
                income.append({"date": date, "type": item_type, "value": value})
            for item_type, value in {
                "TotalAssets": 12000.0 + (year - 2020) * 800.0,
                "Liabilities": 4200.0 + (year - 2020) * 200.0,
                "Equity": 7800.0 + (year - 2020) * 600.0,
                "CashAndCashEquivalents": 2500.0 + (year - 2020) * 200.0,
            }.items():
                balance.append({"date": date, "type": item_type, "value": value})
            cumulative_cfo += revenue * 0.45
            cumulative_capex += revenue * 0.20
            cumulative_depreciation += revenue * 0.15
            for item_type, value in {
                "CashFlowsFromOperatingActivities": cumulative_cfo,
                "PropertyAndPlantAndEquipment": -cumulative_capex,
                "Depreciation": cumulative_depreciation,
            }.items():
                cashflow.append({"date": date, "type": item_type, "value": value})
        for month in range(1, 13):
            monthly.append({
                "date": f"{year}-{month:02d}-01",
                "revenue_year": year,
                "revenue_month": month,
                "revenue": 350.0 + (year - 2020) * 15.0 + month,
            })
    return {
        "TaiwanStockFinancialStatements": income,
        "TaiwanStockBalanceSheet": balance,
        "TaiwanStockCashFlowsStatement": cashflow,
        "TaiwanStockMonthRevenue": monthly,
    }


def test_inline_source_prefers_stable_citation_url() -> None:
    rendered = _inline_source([
        {
            "url": "https://query1.finance.yahoo.com/ws/fundamentals-time",
            "citation_url": "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/2330.TW?symbol=2330.TW&type=annualDilutedEPS",
        }
    ])
    assert "fundamentals-time]" not in rendered
    assert "fundamentals-timeseries/v1/finance/timeseries/2330.TW" in rendered


def test_numbered_citations_dedupe_urls_and_keep_full_reference_targets() -> None:
    body, refs = _number_markdown_citations(
        "營收 [證據](https://example.test/a)；現金流 [來源](https://example.test/a)。"
    )
    assert body == "營收 [[1]](#ref-1)；現金流 [[1]](#ref-1)。"
    assert refs == [{"number": 1, "label": "", "url": "https://example.test/a"}]


@pytest.fixture
def history() -> dict[str, object]:
    result = normalize_finmind_financial_history(
        _finmind_payloads(),
        target={"symbol": "2330.TW", "name": "TSMC", "currency": "TWD"},
        as_of="2026-08-23T00:00:00Z",
        source_refs=[{"url": "https://api.finmindtrade.com/api/v4/data", "response_sha256": "a" * 64}],
    )
    validate_contract("financial-history", result)
    return result


def test_financial_history_has_five_years_eight_quarters_and_reconciles(history: dict[str, object]) -> None:
    assert history["status"] == "available"
    assert len(history["annual_periods"]) == 5
    assert len(history["quarterly_periods"]) == 8
    assert history["annual_periods"][-1]["year"] == 2025
    assert history["annual_periods"][-1]["free_cash_flow"] > 0
    assert history["quarterly_periods"][-1]["operating_cash_flow"] < history["annual_periods"][-1]["operating_cash_flow"]
    assert history["annual_periods"][-1]["operating_cash_flow"] == pytest.approx(history["annual_periods"][-1]["revenue"] * 0.45)
    assert history["validation"]["balance_sheet_identity"] == "pass"
    assert history["validation"]["period_completeness"] == "pass"


def test_forecast_is_three_year_driver_based_and_replayable(history: dict[str, object]) -> None:
    first = build_forecast_model(history)
    second = build_forecast_model(history)
    validate_contract("forecast-model", first)
    assert first == second
    assert first["status"] == "available"
    assert len(first["forecast_periods"]) == 3
    assert set(first["forecast_periods"][0]["scenarios"]) == {"bear", "base", "bull"}
    assert first["assumptions"]["revenue_growth"]["lineage"]
    assert first["validation"]["formula_replay"] == "pass"


def test_forecast_handles_zero_prior_net_income_without_zero_division(history: dict[str, object]) -> None:
    edge = deepcopy(history)
    latest = edge["annual_periods"][-1]
    latest["net_income"] = 0.0
    latest["eps"] = 0.0
    latest["operating_income"] = 0.0
    latest["operating_margin"] = 0.0
    forecast = build_forecast_model(edge)
    assert forecast["status"] == "available"
    assert all(
        isinstance(period["scenarios"][scenario]["eps"], (int, float))
        for period in forecast["forecast_periods"]
        for scenario in ("bear", "base", "bull")
    )


def test_forecast_uses_period_specific_guidance_without_constant_cagr(history: dict[str, object]) -> None:
    guidance = {
        "revenue_growth": {"bear": [0.10, 0.08, 0.06], "base": [0.20, 0.15, 0.10], "bull": [0.25, 0.20, 0.15]},
        "operating_margin": {"bear": [0.35, 0.34, 0.33], "base": [0.45, 0.44, 0.43], "bull": [0.50, 0.49, 0.48]},
        "tax_rate": 0.17,
        "lineage": [{"url": "https://example.test/guidance", "claim": "fixture"}],
    }
    forecast = build_forecast_model(history, guidance=guidance)
    assert [period["scenarios"]["base"]["revenue_growth"] for period in forecast["forecast_periods"]] == [0.20, 0.15, 0.10]
    assert [period["scenarios"]["base"]["operating_margin"] for period in forecast["forecast_periods"]] == [0.45, 0.44, 0.43]
    assert forecast["assumptions"]["guidance_lineage"][0]["url"] == "https://example.test/guidance"


def test_valuation_requires_two_methods_and_has_sensitivity(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=-500.0,
        peer_median_pe=18.0,
        discount_rate=0.09,
        terminal_growth=0.03,
    )
    validate_contract("valuation-model", valuation)
    assert valuation["status"] == "available"
    assert {method["method"] for method in valuation["methods"]} == {"dcf_fcfe_proxy", "forward_pe"}
    assert len(valuation["sensitivity"]["matrix"]) == 9
    assert valuation["target_range"]["low"] <= valuation["target_range"]["base"] <= valuation["target_range"]["high"]
    assert valuation["assumptions"]["method_dispersion_pct"] >= 0
    assert valuation["target_range"]["method_envelope_low"] <= valuation["target_range"]["low"]
    assert valuation["target_range"]["method_envelope_high"] >= valuation["target_range"]["high"]


def test_negative_trailing_eps_uses_explicit_dcf_only_mode(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=0.0,
        peer_median_pe=None,
        peer_basis="dcf_only",
        target_eps=-5.2,
        target_period_key="2025",
    )
    validate_contract("valuation-model", valuation)
    assert valuation["status"] == "available"
    assert valuation["assumptions"]["peer_multiple_basis"] == "dcf_only"
    assert [method["method"] for method in valuation["methods"]] == ["dcf_fcfe_proxy"]
    assert valuation["target_range"] is not None


def test_negative_trailing_eps_can_use_period_aligned_price_to_sales_crosscheck(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=0.0,
        peer_median_pe=None,
        peer_basis="trailing_ps",
        peer_median_ps=2.0,
        target_revenue=10_000.0,
        target_eps=-5.2,
        target_period_key="2025",
    )
    validate_contract("valuation-model", valuation)
    assert valuation["status"] == "available"
    assert {method["method"] for method in valuation["methods"]} == {"dcf_fcfe_proxy", "trailing_ps"}
    assert valuation["assumptions"]["peer_multiple_basis"] == "trailing_ps"
    assert valuation["assumptions"]["peer_target_revenue"] == 10_000.0


def test_non_positive_base_dcf_switches_to_explicit_sales_crosscheck(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    forecast["forecast_periods"][0]["scenarios"]["base"]["free_cash_flow"] = -1.0
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=0.0,
        peer_median_pe=20.0,
        peer_median_ps=2.0,
        target_revenue=10_000.0,
        peer_basis="trailing_pe",
        target_eps=5.0,
        target_period_key="2025",
    )
    assert valuation["assumptions"]["peer_multiple_basis"] == "trailing_ps"
    assert valuation["assumptions"]["multiple_fallback_reason"] == "dcf_base_value_non_positive"


def test_valuation_fails_closed_without_forecast(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    forecast["status"] = "insufficient_data"
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=0.0,
        peer_median_pe=18.0,
    )
    assert valuation["rating"] == "Not Rated"
    assert valuation["target_range"] is None


def test_professional_report_contains_all_chapters_and_hides_machine_tokens(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=-500.0,
        peer_median_pe=18.0,
    )
    report = build_professional_research_report(
        profile={
            "target_id": "tsmc",
            "target": {
                "symbol": "2330.TW",
                "name": "Taiwan Semiconductor Manufacturing Company Limited",
                "aliases": ["台積電"],
                "market": "TW",
                "currency": "TWD",
                "sector": "Technology",
                "industry": "Semiconductors",
            },
        },
        history=history,
        forecast=forecast,
        valuation=valuation,
        depth={
            "time_series": {"window_end": "2026-08-21", "returns": {"365d_observed_pct": 12.5}},
            "event_alignment": {"aligned_event_count": 3, "event_study_event_count": 2},
            "evidence_pack": {"item_count": 12, "source_group_count": 6, "canonical_story_count": 10, "items": []},
            "source_conflicts": [],
            "market_drivers": {"news_driver_candidates": []},
        },
        metadata={
            "run_id": "run_20260823_tsmc",
            "history_url": "https://example.test/history",
            "target_retrieval": {"community": {"status": "available"}},
        },
    )
    validate_contract("professional-equity-report", report)
    assert report["report_level"] == "L2"
    assert report["quality_gates"]["risk_probability"] == "partial"
    assert "risk_probability_unresolved" in report["appendix"]["unresolved"]
    assert [chapter["id"] for chapter in report["chapters"]] == [str(i) for i in range(15)]
    markdown = render_professional_report(report)
    for heading in ("投資論點與差異觀點", "公司與商業模式", "歷史財務與盈餘品質", "估值與敏感度", "投資風險與 bear case"):
        assert heading in markdown
    assert "方法分歧" in markdown
    body = markdown.split("## 附錄 A", 1)[0]
    assert "professional_ready" not in body
    assert "provider" not in body
    assert '"audit":' not in markdown
    assert "social_narrative_source_unavailable" not in markdown
    annual_row = next(line for line in markdown.splitlines() if line.startswith("| 2025 |"))
    assert re.search(r"\[\d+\]", annual_row)
    references = markdown.split("## 參考來源", 1)[1]
    assert "https://api.finmindtrade.com/api/v4/data" in references
    assert markdown.count("### 研究判讀與盲點") >= 10
    assert "盲點檢查" in markdown
    assert "本案的三條論點" in markdown
    assert "基準收入增速" in markdown
    for phrase in (
        "本案歷史財務的解讀",
        "本案預測的判讀",
        "本案估值的判讀",
        "本案價格與基本面的關係",
        "沒有可由官方時點或至少兩個獨立來源支持的未來催化劑",
        "本案來源衝突的判讀",
        "本案風險的判讀",
        "本案監測計畫的判讀",
    ):
        assert phrase in markdown
    assert len(markdown) >= 10_000


def test_partial_qualitative_model_does_not_leak_model_blind_spots_or_requirement_ids_into_body(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=0.0,
        peer_median_pe=18.0,
    )
    context_coverage = {
        "summary": {"l3_ready": False},
        "requirements": [
            {
                "requirement_id": "company.business_model",
                "section": "company",
                "status": "partial",
                "missing_metrics": ["products_or_services", "customers_or_regions"],
                "missing_roles": [],
            },
            {
                "requirement_id": "segment.disclosure",
                "section": "company",
                "status": "partial",
                "missing_metrics": ["segment_revenue", "segment_period", "currency", "unit"],
                "missing_roles": [],
            },
        ],
    }
    report = build_professional_research_report(
        profile={"target_id": "tcc", "target": {"symbol": "1101.TW", "name": "台泥", "market": "TW", "currency": "TWD", "industry": "Cement"}},
        history=history,
        forecast=forecast,
        valuation=valuation,
        depth={"time_series": {}, "event_alignment": {}, "evidence_pack": {"items": [], "source_group_count": 1, "canonical_story_count": 1}, "source_conflicts": [], "market_drivers": {"news_driver_candidates": []}},
        metadata={"run_id": "run_partial_model", "context_coverage": context_coverage},
    )
    envelope = {
        "run_id": "qual_partial",
        "model": "test",
        "validation": {"status": "partial", "decision_quality_violations": ["company:decision_link_missing"]},
        "result": {
            "overall_status": "partial",
            "quality": {"evidence_quality": "partial", "decision_quality": "partial"},
            "sections": {
                "company": {
                    "status": "partial",
                    "summary": "模型摘要不得進正文",
                    "claims": [{"claim_id": "company-001", "text": "未驗證主張", "decision_quality_status": "invalid"}],
                    "blind_spots": ["模型生成但未驗證的盲點"],
                    "missing_evidence": ["模型自行列出的缺證"],
                }
            },
        },
    }

    merged = merge_qualitative_context_into_report(report, envelope)
    markdown = render_professional_report(merged)
    body = markdown.split("## 附錄 A", 1)[0]

    assert "模型生成但未驗證的盲點" not in body
    assert "模型自行列出的缺證" not in body
    assert "company.business_model" not in body
    assert "segment.disclosure" not in body
    assert "產品／服務組合" in body
    assert "分部營收" in body


def test_professional_report_renders_verifiable_social_originals(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(forecast, market_price=100.0, shares_outstanding=100.0, net_debt=0.0, peer_median_pe=18.0)
    social_item = {
        "source_id": "hacker_news_tsmc_api",
        "kind": "social",
        "layer": "social",
        "title": "TSMC capacity discussion",
        "canonical_url": "https://news.ycombinator.com/item?id=49386030",
        "engagement": {"score": 42, "comments": 7},
        "evidence": {"comments": [{"author": "commenter", "text": "Yield and customer demand are the key constraints."}]},
    }
    report = build_professional_research_report(
        profile={"target_id": "tsmc", "target": {"symbol": "2330.TW", "name": "TSMC", "market": "TW", "currency": "TWD", "industry": "Semiconductors"}},
        history=history,
        forecast=forecast,
        valuation=valuation,
        depth={"time_series": {}, "event_alignment": {}, "evidence_pack": {"items": [social_item], "source_group_count": 1, "canonical_story_count": 1}, "source_conflicts": [], "market_drivers": {"news_driver_candidates": []}},
        metadata={"run_id": "run_social"},
    )
    chapter = next(item for item in report["chapters"] if item["id"] == "11")
    assert chapter["content"]["social_item_count"] == 1
    assert chapter["content"]["social_candidates"][0]["url"] == social_item["canonical_url"]
    assert chapter["content"]["social_candidates"][0]["comment_excerpts"][0]["author"] == "commenter"
    markdown = render_professional_report(report)
    assert "社群原文候選" in markdown
    assert social_item["canonical_url"] in markdown
    assert "Yield and customer demand" in markdown


def test_raw_english_news_headline_is_scoped_to_audit_appendix(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(forecast, market_price=100.0, shares_outstanding=100.0, net_debt=0.0, peer_median_pe=18.0)
    headline = "Nan Ya Plastics Unit Shelves Plans of $10 Million China Investment - marketscreener.com"
    report = build_professional_research_report(
        profile={"target_id": "fixture", "target": {"symbol": "9999.TW", "name": "Fixture", "market": "TW", "currency": "TWD", "sector": "Test", "industry": "Test"}},
        history=history,
        forecast=forecast,
        valuation=valuation,
        depth={
            "time_series": {},
            "event_alignment": {},
            "evidence_pack": {"items": []},
            "source_conflicts": [],
            "market_drivers": {"news_driver_candidates": [{"title": headline, "label": "Investment", "evidence_ids": []}]},
        },
        metadata={"run_id": "run_fixture"},
    )
    chapter_one = next(chapter for chapter in report["chapters"] if chapter["id"] == "1")
    assert chapter_one["content"]["catalysts"] == []
    markdown = render_professional_report(report)
    body, appendix = markdown.split("## 附錄 A", 1)
    assert headline not in body
    assert "尚未形成可驗證事件" in body
    assert headline not in appendix


def test_news_evidence_summary_is_rendered_with_source_metadata(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(forecast, market_price=100.0, shares_outstanding=100.0, net_debt=0.0, peer_median_pe=18.0)
    item_id = "news-evidence-1"
    report = build_professional_research_report(
        profile={"target_id": "fixture", "target": {"symbol": "9999.TW", "name": "Fixture", "market": "TW", "currency": "TWD", "sector": "Test", "industry": "Test"}},
        history=history,
        forecast=forecast,
        valuation=valuation,
        depth={
            "time_series": {},
            "event_alignment": {},
            "evidence_pack": {
                "items": [{
                    "item_id": item_id,
                    "kind": "news",
                    "layer": "news",
                    "title": "Fixture expands capacity",
                    "canonical_url": "https://news.example/fixture",
                    "published_at": "2026-08-21T00:00:00Z",
                    "summary": "Fixture expansion summary",
                    "source_tier": "direct_secondary",
                    "evidence": {"publisher_name": "Example Wire"},
                }],
                "source_group_count": 1,
                "canonical_story_count": 1,
            },
            "source_conflicts": [],
            "market_drivers": {"news_driver_candidates": [{"title": "Fixture expands capacity", "label": "Investment", "evidence_ids": [item_id]}]},
        },
        metadata={"run_id": "run_news"},
    )
    markdown = render_professional_report(report)
    assert "媒體原文與證據摘要" in markdown
    assert "Fixture expansion summary" in markdown
    assert "https://news.example/fixture" in markdown


def test_complete_qualitative_context_is_required_for_l3(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(forecast, market_price=100.0, shares_outstanding=100.0, net_debt=0.0, peer_median_pe=18.0)
    profile = {
        "target_id": "fixture",
        "target": {"symbol": "9999.TW", "name": "Fixture", "market": "TW", "currency": "TWD", "sector": "Test", "industry": "Test"},
        "research_context": {
            "company": {"business_model": "model", "scale": "scale", "platform_mix": {}, "geography_mix": {}, "moat": ["moat"], "sources": [{"group": "official", "url": "https://example.test/annual", "response_sha256": "a" * 64}]},
            "industry": {"position": "position", "cycle": "cycle", "capacity": "capacity", "sources": [{"group": "a", "url": "https://a.test", "response_sha256": "a" * 64}, {"group": "b", "url": "https://b.test", "response_sha256": "b" * 64}, {"group": "c", "url": "https://c.test", "response_sha256": "c" * 64}]},
            "governance": {"summary": "summary", "capital_allocation": "allocation", "ownership": "ownership", "sources": [{"group": "official", "url": "https://example.test/governance", "response_sha256": "d" * 64}]},
            "esg": {"summary": "material", "sources": [{"group": "esg", "url": "https://example.test/esg", "response_sha256": "e" * 64}]},
        },
    }
    report = build_professional_research_report(
        profile=profile, history=history, forecast=forecast, valuation=valuation,
        depth={"time_series": {}, "event_alignment": {}, "evidence_pack": {"item_count": 10, "source_group_count": 5, "canonical_story_count": 10, "items": []}, "source_conflicts": [], "market_drivers": {}},
        metadata={"run_id": "run_fixture"},
    )
    assert report["report_level"] == "L3"
    assert report["quality_gates"]["qualitative_research"] == "pass"
    assert all(report["chapters"][index]["status"] == "complete" for index in (3, 4, 5, 9, 13))
    assert "social_narrative_source_unavailable" in report["appendix"]["unresolved"]
    company_chapter = next(chapter for chapter in report["chapters"] if chapter["id"] == "3")
    company_chapter["content"].update({
        "model_summary": "Fixture 的個案摘要：產品組合改善，但客戶集中度仍需追蹤。",
        "model_claims": [{
            "claim_id": "company-001",
            "text": "高毛利產品占比提高。",
            "type": "inference",
            "confidence": "medium",
            "evidence_quality": "direct",
            "evidence_ids": [],
            "mechanism": "產品組合改善會先推升毛利率，再改善營業現金流。",
            "falsifier": "若連續兩季毛利率下降，則主張失效。",
        }],
        "model_blind_spots": ["客戶集中度尚未揭露。"],
    })
    markdown = render_professional_report(report)
    scale_line = next(line for line in markdown.splitlines() if line.startswith("**營運規模：**"))
    assert re.search(r"\[\d+\]", scale_line)
    assert "https://example.test/annual" in markdown.split("## 參考來源", 1)[1]
    assert "**平台組合：** 。" not in markdown
    assert "**客戶總部地區組合：** 。" not in markdown
    assert "未取得可比較的結構化產品／地區占比" in markdown
    assert all(term not in markdown for term in ("AI／HPC", "先進製程", "純晶圓代工", "良率爬坡"))
    company_section = markdown.split("## 3、公司與商業模式", 1)[1].split("## 4、", 1)[0]
    assert "Fixture 的個案摘要" in company_section
    assert "產品組合改善會先推升毛利率" in company_section
    assert "若連續兩季毛利率下降" in company_section
    assert "本案公司模式的讀法" not in company_section


def test_production_l3_requires_complete_geo_coverage_and_local_community_original(history: dict[str, object]) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=0.0,
        peer_median_pe=18.0,
    )
    profile = {
        "target_id": "fixture",
        "target": {
            "symbol": "9999.TW", "name": "Fixture Taiwan", "aliases": ["測試公司"],
            "market": "TW", "currency": "TWD", "sector": "Test", "industry": "Test",
        },
        "research_context": {
            "company": {"business_model": "model", "scale": "scale", "moat": ["moat"], "sources": [{"group": "official", "url": "https://example.test/annual", "response_sha256": "a" * 64}]},
            "industry": {"position": "position", "cycle": "cycle", "capacity": "capacity", "sources": [{"group": "a", "url": "https://a.test", "response_sha256": "a" * 64}, {"group": "b", "url": "https://b.test", "response_sha256": "b" * 64}, {"group": "c", "url": "https://c.test", "response_sha256": "c" * 64}]},
            "governance": {"summary": "summary", "capital_allocation": "allocation", "ownership": "ownership", "sources": [{"group": "official", "url": "https://example.test/governance", "response_sha256": "d" * 64}]},
            "esg": {"summary": "material", "sources": [{"group": "esg", "url": "https://example.test/esg", "response_sha256": "e" * 64}]},
        },
    }
    news_item = {"item_id": "news-1", "kind": "news", "layer": "news", "title": "測試公司營運更新", "canonical_url": "https://news.test/1", "published_at": "2026-08-21T00:00:00Z", "content_sha256": "1" * 64}
    social_item = {"item_id": "social-1", "kind": "social", "layer": "social", "title": "測試公司討論", "canonical_url": "https://www.ptt.cc/bbs/Stock/M.1.html", "published_at": "2026-08-21T00:00:00Z", "content_sha256": "2" * 64}
    depth = {
        "time_series": {}, "event_alignment": {},
        "evidence_pack": {"item_count": 12, "source_group_count": 6, "canonical_story_count": 10, "items": [news_item, social_item]},
        "source_conflicts": [], "market_drivers": {},
    }
    coverage = {"summary": {"l3_ready": True}, "requirements": []}
    incomplete = build_professional_research_report(
        profile=profile, history=history, forecast=forecast, valuation=valuation, depth=depth,
        metadata={
            "run_id": "run_geo_partial", "context_coverage": coverage,
            "target_retrieval": {
                "geo_coverage": {"status": "partial"},
                "community": {"status": "available", "coverage": {"status": "complete"}},
            },
        },
    )
    assert incomplete["quality_gates"]["geo_coverage"] == "partial"
    assert incomplete["report_level"] == "L2"

    complete = build_professional_research_report(
        profile=profile, history=history, forecast=forecast, valuation=valuation, depth=depth,
        metadata={
            "run_id": "run_geo_complete", "context_coverage": coverage,
            "target_retrieval": {
                "geo_coverage": {"status": "complete"},
                "community": {"status": "available", "coverage": {"status": "complete"}},
            },
        },
    )
    assert complete["quality_gates"]["geo_coverage"] == "pass"
    assert complete["quality_gates"]["news_social"] == "pass"
    assert complete["quality_gates"]["risk_probability"] == "pass"
    assert complete["report_level"] == "L3"
    risk_rows = next(chapter for chapter in complete["chapters"] if chapter["id"] == "12")["content"]["risks"]
    assert all(item.get("probability_basis") for item in risk_rows)


def test_write_professional_artifacts_is_complete_and_replayable(history: dict[str, object], tmp_path: Path) -> None:
    forecast = build_forecast_model(history)
    valuation = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=-500.0,
        peer_median_pe=18.0,
    )
    report = build_professional_research_report(
        profile={"target_id": "fixture", "target": {"symbol": "9999.TW", "name": "Fixture", "market": "TW", "currency": "TWD", "sector": "Test", "industry": "Test"}},
        history=history,
        forecast=forecast,
        valuation=valuation,
        depth={"time_series": {}, "event_alignment": {}, "evidence_pack": {"items": []}, "source_conflicts": [], "market_drivers": {}},
        metadata={"run_id": "run_fixture"},
    )
    paths = write_professional_artifacts(tmp_path, "fixture-9999tw", report, history, forecast, valuation)
    assert set(paths) == {"markdown", "report", "history", "forecast", "valuation", "evidence", "audit", "paragraph_audit"}
    for path in paths.values():
        assert Path(path).exists()
    saved = json.loads(Path(paths["report"]).read_text(encoding="utf-8"))
    assert saved["report_id"] == report["report_id"]
    assert saved["quality_gates"]["paragraph_quality"] in {"pass", "partial"}
    paragraph_audit = json.loads(Path(paths["paragraph_audit"]).read_text(encoding="utf-8"))
    assert paragraph_audit["summary"]["audited_block_count"] > 0


def test_fetch_finmind_history_captures_all_required_datasets_and_share_count() -> None:
    payloads = _finmind_payloads()
    payloads["TaiwanStockDividend"] = [{"date": "2026-06-01", "ParticipateDistributionOfTotalShares": 25_933_629_242.0}]
    calls: list[str] = []

    class Response:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, dataset: str) -> None:
            self.dataset = dataset
            self.content = json.dumps({"status": 200, "data": payloads[dataset]}, sort_keys=True).encode()

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"status": 200, "data": payloads[self.dataset]}

    def getter(url: str, **kwargs: object) -> Response:
        dataset = str(kwargs["params"]["dataset"])
        calls.append(dataset)
        return Response(dataset)

    result = fetch_finmind_financial_history(
        {"symbol": "2330.TW", "name": "TSMC", "currency": "TWD"},
        as_of="2026-08-23T00:00:00Z",
        get=getter,
    )
    assert set(calls) == {
        "TaiwanStockFinancialStatements",
        "TaiwanStockBalanceSheet",
        "TaiwanStockCashFlowsStatement",
        "TaiwanStockMonthRevenue",
        "TaiwanStockDividend",
    }
    assert result["history"]["shares_outstanding"] == 25_933_629_242.0
    assert len(result["raw_captures"]) == 5
    assert all(len(ref["response_sha256"]) == 64 for ref in result["history"]["source_refs"])


def test_research_context_sources_are_frozen_and_hashed() -> None:
    context = {
        "company": {"sources": [{"group": "official", "url": "https://example.test/annual"}]},
        "industry": {"sources": [{"group": "independent", "url": "https://example.test/industry"}]},
    }

    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}

        def __init__(self, url: str) -> None:
            self.content = f"source:{url}".encode()

        def raise_for_status(self) -> None:
            return None

    result = fetch_research_context_evidence(context, as_of="2026-08-23T00:00:00Z", get=lambda url, **kwargs: Response(url))
    assert len(result["raw_captures"]) == 2
    assert result["context"]["company"]["sources"][0]["response_sha256"]
    assert result["context"]["industry"]["sources"][0]["response_sha256"]


def test_research_context_tls_fallback_is_explicitly_recorded() -> None:
    calls: list[dict[str, object]] = []

    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}
        content = b"issuer context"

        def raise_for_status(self) -> None:
            return None

    def getter(url: str, **kwargs: object) -> Response:
        calls.append(dict(kwargs))
        if kwargs.get("verify") is not False:
            raise httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate",
                request=httpx.Request("GET", url),
            )
        return Response()

    result = fetch_research_context_evidence(
        {"company": {"sources": [{"group": "official", "url": "https://issuer.example/context"}]}},
        as_of="2026-08-23T00:00:00Z",
        get=getter,
    )
    source = result["context"]["company"]["sources"][0]
    assert len(calls) == 2
    assert calls[1]["verify"] is False
    assert source["tls_verification"] == "relaxed_fallback"
    assert source["quality_flags"] == ["tls_verify_relaxed_fallback"]
    assert result["raw_captures"][0]["tls_verification"] == "relaxed_fallback"


def test_annual_report_discovery_freezes_linked_pdf() -> None:
    landing = "https://issuer.example/investors"
    pdf = "https://issuer.example/files/2024-annual-report.pdf"

    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}

        def __init__(self, url: str) -> None:
            self.content = (f'<a href="{pdf}">Annual Report</a>'.encode() if url == landing else b"segment revenue 2024")

        def raise_for_status(self) -> None:
            return None

    calls: list[str] = []
    def getter(url: str, **kwargs: object) -> Response:
        calls.append(url)
        return Response(url)

    result = fetch_research_context_evidence(
        {"company": {"sources": [{"group": "issuer_annual_report", "url": landing, "discovery": "annual_report_pdf"}]}},
        as_of="2026-08-23T00:00:00Z",
        get=getter,
    )
    source = result["context"]["company"]["sources"][0]
    assert calls == [landing, pdf]
    assert source["url"] == pdf
    assert source["discovered_from"] == landing
    assert result["raw_captures"][0]["url"] == pdf


def test_mops_annual_report_discovery_resolves_javascript_pdf() -> None:
    landing = "https://doc.twse.com.tw/server-java/t57sb01?step=1&co_id=3231&year=115&mtype=F&"
    resolver = "https://doc.twse.com.tw/server-java/t57sb01?co_id=3231&colorchg=1&kind=F&step=9&filename=2025_3231_20260529F04.pdf"
    pdf = "https://doc.twse.com.tw/pdf/2025_3231_20260529F04_20260824_232502.pdf"

    class Response:
        status_code = 200

        def __init__(self, url: str) -> None:
            self.url = url
            if url == landing:
                self.headers = {"content-type": "text/html;charset=big5"}
                self.content = b'<a href="javascript:readfile2(\"F\",\"3231\",\"2025_3231_20260529F04.pdf\");">Annual Report</a>'
            elif url == resolver:
                self.headers = {"content-type": "text/html;charset=big5"}
                self.content = f'<a href="/pdf/{pdf.rsplit("/", 1)[1]}">{pdf.rsplit("/", 1)[1]}</a>'.encode()
            else:
                self.headers = {"content-type": "application/pdf"}
                self.content = b"segment revenue 2025"

        def raise_for_status(self) -> None:
            return None

    calls: list[str] = []

    def getter(url: str, **kwargs: object) -> Response:
        calls.append(url)
        return Response(url)

    result = fetch_research_context_evidence(
        {"company": {"sources": [{"group": "regulator_annual_report", "url": landing, "discovery": "annual_report_pdf"}]}},
        as_of="2026-08-23T00:00:00Z",
        get=getter,
    )
    source = result["context"]["company"]["sources"][0]
    assert calls == [landing, resolver, pdf]
    assert source["url"] == pdf
    assert source["discovered_from"] == landing
    assert result["raw_captures"][0]["content_type"] == "application/pdf"


def test_claim_coverage_does_not_hide_explicitly_unresolved_events() -> None:
    result = _claim_evidence_coverage([{
        "catalysts": [
            {"claim_id": "linked", "evidence_ids": ["E1"]},
            {"claim_id": "scheduled", "probability": "scheduled_or_unresolved", "evidence_ids": []},
            {"claim_id": "broken", "probability": "medium", "evidence_ids": []},
        ]
    }])
    assert result["explicitly_unresolved_claim_count"] == 1
    assert result["unlinked_claim_count"] == 1
    assert result["status"] == "partial"


def test_research_context_source_failure_is_preserved_without_aborting_batch() -> None:
    context = {
        "company": {"sources": [{"group": "official", "url": "https://example.test/blocked"}]},
        "industry": {"sources": [{"group": "independent", "url": "https://example.test/ok"}]},
    }

    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}
        content = b"ok"

        def raise_for_status(self) -> None:
            return None

    def getter(url: str, **kwargs: object) -> Response:
        if url.endswith("blocked"):
            raise httpx.ConnectError("blocked", request=httpx.Request("GET", url))
        return Response()

    result = fetch_research_context_evidence(context, as_of="2026-08-23T00:00:00Z", get=getter)
    blocked = result["context"]["company"]["sources"][0]
    assert blocked["fetch_status"] == "failed"
    assert blocked["quality_flags"] == ["fetch_failed"]
    assert result["fetch_failures"][0]["url"].endswith("blocked")
    assert len(result["raw_captures"]) == 1


def test_optional_research_context_source_failure_is_marked_non_required() -> None:
    context = {"esg": {"sources": [{"group": "official", "url": "https://example.test/optional", "required": False}]}}

    def getter(url: str, **kwargs: object) -> object:
        raise httpx.ConnectError("optional blocked", request=httpx.Request("GET", url))

    result = fetch_research_context_evidence(context, as_of="2026-08-23T00:00:00Z", get=getter)
    source = result["context"]["esg"]["sources"][0]
    assert source["fetch_status"] == "failed"
    assert source["required"] is False
    assert result["fetch_failures"][0]["required"] is False


def test_research_context_deduplicates_repeated_failed_urls() -> None:
    context = {
        "company": {"sources": [{"group": "official", "url": "https://example.test/repeated", "required": False}]},
        "industry": {"sources": [{"group": "official", "url": "https://example.test/repeated", "required": False}]},
    }
    calls: list[str] = []

    def getter(url: str, **kwargs: object) -> object:
        calls.append(url)
        raise httpx.ConnectError("repeated blocked", request=httpx.Request("GET", url))

    result = fetch_research_context_evidence(context, as_of="2026-08-23T00:00:00Z", get=getter)
    assert calls == ["https://example.test/repeated"]
    assert len(result["fetch_failures"]) == 1
    assert result["context"]["company"]["sources"][0]["fetch_status"] == "failed"
    assert result["context"]["industry"]["sources"][0]["fetch_status"] == "failed"


def test_merge_downgrades_model_complete_section_when_context_requirement_is_partial() -> None:
    from finance_crawler_poc.professional_equity import merge_qualitative_context_into_report

    report = {
        "report_level": "L2",
        "target": {"symbol": "1301.TW"},
        "quality_gates": {"identity": "pass", "financial_model": "pass", "valuation": "pass", "audit": "pass", "valuation_contract": "pass"},
        "appendix": {
            "context_coverage": {
                "summary": {"l3_ready": False},
                "requirements": [{"requirement_id": "esg.materiality_kpi", "section": "esg", "status": "partial"}],
            },
            "unresolved": [],
        },
        "chapters": [{"id": "8", "content": {"methods": [], "target_range": None}}],
    }
    envelope = {
        "run_id": "fixture",
        "model": "fixture",
        "validation": {"status": "pass", "evidence_quality_violations": []},
        "result": {
            "overall_status": "complete",
            "quality": {"evidence_quality": "complete"},
            "sections": {name: {"status": "complete", "claims": [], "missing_evidence": []} for name in ("company", "industry", "governance", "esg")},
            "cross_section_synthesis": {},
        },
    }
    merged = merge_qualitative_context_into_report(report, envelope)
    assert merged["appendix"]["qualitative_context"]["sections"]["esg"]["status"] == "partial"
    assert "esg.materiality_kpi" in merged["appendix"]["qualitative_context"]["sections"]["esg"]["missing_evidence"]
    assert merged["quality_gates"]["qualitative_research"] == "partial"


def test_merge_downgrades_complete_model_when_decision_quality_fails() -> None:
    from finance_crawler_poc.professional_equity import merge_qualitative_context_into_report

    coverage = {"summary": {"l3_ready": True}, "requirements": []}
    report = {
        "report_level": "L3",
        "target": {"symbol": "1101.TW"},
        "quality_gates": {
            "identity": "pass", "financial_model": "pass", "valuation": "pass", "audit": "pass",
            "valuation_contract": "pass", "evidence": "pass", "qualitative_research": "pass",
            "context_sufficiency": "pass",
        },
        "decision_card": {"rating": "Positive", "target_range": {"base": 100}, "market_price": 90},
        "appendix": {"context_coverage": coverage, "unresolved": [], "run_metadata": {}},
        "chapters": [{"id": str(index), "content": {"methods": [], "target_range": None} if index == 8 else {}} for index in range(15)],
    }
    envelope = {
        "run_id": "fixture", "model": "fixture",
        "validation": {
            "status": "pass", "evidence_quality_violations": [],
            "decision_quality_violations": [{"claim_id": "industry-001", "reason": "decision_link_incomplete"}],
        },
        "result": {
            "overall_status": "complete",
            "quality": {"evidence_quality": "complete", "decision_quality": "partial"},
            "sections": {name: {"status": "complete", "claims": [], "missing_evidence": []} for name in ("company", "industry", "governance", "esg")},
            "cross_section_synthesis": {},
        },
    }

    merged = merge_qualitative_context_into_report(report, envelope)

    assert merged["report_level"] == "L2"
    assert merged["quality_gates"]["qualitative_research"] == "partial"
    assert "qualitative_model_decision_quality_violation" in merged["appendix"]["unresolved"]


def test_qualitative_chapter_renders_one_human_synthesis_without_machine_payload() -> None:
    from finance_crawler_poc.professional_equity import _explanatory_lens, _render_chapter

    summary = "台泥的亞洲水泥需求須以銷量與每噸售價共同判讀。"
    content = {
        "industry": "Cement", "peer_set": [], "framework": [], "sources": [],
        "model_status": "complete", "model_summary": summary,
        "model_claims": [{
            "claim_id": "industry-001", "text": "亞洲需求改善才可支持台泥收入上修。", "type": "inference",
            "confidence": "medium", "evidence_quality": "direct", "evidence_ids": ["E1"],
            "mechanism": "銷量與售價提高會帶動水泥營收與營業利益率。",
            "falsifier": "若2026-Q4銷量年減5％則下修基準情境。",
            "decision_link": {
                "driver": "亞洲水泥需求", "target_exposure": "台泥亞洲水泥業務", "kpi": "銷量與每噸售價",
                "financial_line": "營收與營業利益率", "scenario_implication": "未達門檻時下修基準情境收入",
            },
        }],
        "model_blind_spots": ["土耳其與歐洲區域量價尚未對齊。"],
        "model_missing_evidence": ["區域銷量與售價時間序列。"],
    }
    report = {
        "target": {"name": "台泥", "symbol": "1101.TW", "currency": "TWD"},
        "decision_card": {},
        "appendix": {"qualitative_context": {"evidence_bundle": [{"evidence_id": "E1", "url": "https://example.test/e1"}]}},
    }

    rendered = _render_chapter("4", content, report) + "\n\n" + _explanatory_lens("4", content, report)

    assert rendered.count(summary) == 1
    assert "industry-001" not in rendered
    assert "類型／信心／證據品質" not in rendered
    assert "標的曝險" in rendered
    assert "營收與營業利益率" in rendered


def test_source_urls_with_spaces_are_percent_encoded_before_numbering() -> None:
    from finance_crawler_poc.professional_equity import _number_markdown_citations, _render_sources

    rendered = _render_sources([{"publisher": "年報", "url": "https://example.test/annual report 2025.pdf"}])
    body, references = _number_markdown_citations(rendered)

    assert "annual report" not in body
    assert references[0]["url"] == "https://example.test/annual%20report%202025.pdf"
