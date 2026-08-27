from __future__ import annotations

from finance_crawler_poc.target_report import _check_status, _fmt, _human_conclusion, _status_sentence, render_human_report
from finance_crawler_poc.target_profiles import get_target_profile


def test_render_human_report_uses_target_identity_and_explicit_partial_status() -> None:
    profile = get_target_profile("delta")
    depth = {
        "status": "professional_partial",
        "time_series": {
            "status": "available",
            "provider": "yahoo_finance",
            "currency": "TWD",
            "point_count": 2,
            "window_start": "2026-08-20T00:00:00Z",
            "window_end": "2026-08-21T00:00:00Z",
            "points": [{"value": 100}, {"value": 110}],
            "returns": {"observed_pct": 10.0},
            "volatility_annualized_pct": 12.0,
            "max_drawdown_pct": -2.0,
        },
        "fundamentals": {"status": "insufficient_data"},
        "valuation": {"status": "insufficient_data", "missing_fields": ["peer_median_pe"]},
        "event_alignment": {"event_study_status": "insufficient_data"},
        "quality_gate": {"blocking_reasons": ["event_study_required"]},
        "evidence_pack": {"item_count": 2, "canonical_story_count": 1, "source_group_count": 1},
        "source_conflicts": [{"conflict_level": "none"}],
    }
    metadata = {
        "target": profile["target"],
        "target_id": "delta",
        "run_id": "20260822-delta-2308tw",
        "official": {"status": "available", "kind": "twse_company_profile"},
        "target_retrieval": {"item_count": 2, "raw_item_count": 3, "noise_item_count": 1},
        "scope_quality": {"exact_identity_title_matches": 1},
        "raw_capture_paths": [],
    }

    report = render_human_report(profile, depth, metadata)

    assert "台達電" in report
    assert "professional_partial" in report
    assert "event_study_required" in report
    assert "台積電" not in report


def test_report_exposes_official_period_and_price_to_book_basis() -> None:
    profile = get_target_profile("tatung")
    depth = {
        "status": "professional_ready",
        "time_series": {"status": "available", "currency": "TWD", "points": [{"value": 27.6}], "returns": {}},
        "fundamentals": {"status": "available", "as_of": "2025-12-31"},
        "valuation": {
            "status": "available",
            "method": "price_to_book",
            "period_alignment_status": "aligned",
            "observed_multiples": {"price_to_book": 1.17098, "book_value_per_share": 23.57, "book_value_as_of": "2026-06-30"},
            "implied_value": None,
        },
        "event_alignment": {"event_study_status": "available"},
        "quality_gate": {"blocking_reasons": [], "checks": []},
        "evidence_pack": {"item_count": 1, "canonical_story_count": 1, "source_group_count": 1},
    }
    report = render_human_report(
        profile,
        depth,
        {"target_id": "tatung", "run_id": "run", "official": {"status": "available", "kind": "twse_financial_statement", "fiscal_period_end": "2026-06-30"}, "raw_capture_paths": []},
    )
    assert "官方財報期間" in report
    assert "2026-06-30" in report
    assert "P/B" in report


def test_report_helpers_cover_ready_partial_and_unknown_states() -> None:
    assert "已通過" in _status_sentence("professional_ready", [])
    assert "event_study_required" in _status_sentence("professional_partial", ["event_study_required"])
    assert "研究摘要" in _status_sentence("research_only", [])
    assert _check_status({"checks": [{"check_id": "x", "status": "pass"}]}, "x") == "pass"
    assert _check_status({}, "x") == "not_recorded"
    assert _fmt(None) == "n/a"
    assert _fmt("not-a-number") == "not-a-number"


def test_human_summary_hides_internal_provider_and_gate_names() -> None:
    profile = get_target_profile("delta")
    depth = {
        "status": "professional_ready",
        "time_series": {
            "status": "available",
            "provider": "yahoo_finance",
            "currency": "TWD",
            "point_count": 2,
            "window_start": "2026-08-20T00:00:00Z",
            "window_end": "2026-08-21T00:00:00Z",
            "points": [{"value": 1700}, {"value": 1750}],
            "returns": {"365d_observed_pct": 10.0},
            "volatility_annualized_pct": 20.0,
            "max_drawdown_pct": -5.0,
        },
        "fundamentals": {"status": "available", "as_of": "2025-12-31", "eps": 23.08, "revenue": 554885168000},
        "valuation": {
            "status": "available",
            "method": "fundamental_multiples",
            "period_alignment_status": "aligned",
            "observed_multiples": {"trailing_pe": 75.82},
            "implied_value": {"value": 829.42},
        },
        "event_alignment": {"event_study_status": "available", "aligned_event_count": 2, "event_study_event_count": 2, "event_study_sample_status": "descriptive_only", "event_study_significance_status": "not_computed"},
        "quality_gate": {"status": "professional_ready", "blocking_reasons": [], "checks": [{"check_id": "official_financial_source", "status": "pass"}]},
        "evidence_pack": {"item_count": 2, "canonical_story_count": 2, "source_group_count": 2, "canonical_items": []},
        "source_conflicts": [{"conflict_level": "low", "counts": {"positive": 1, "negative": 0, "unknown": 1}}],
        "market_drivers": {"news_driver_candidates": []},
        "scenarios": {"scenarios": {"bear": {"price": 1500}, "bull": {"price": 1800}}},
    }
    report = render_human_report(
        profile,
        depth,
        {
            "target_id": "delta",
            "run_id": "run",
            "official": {"status": "available", "kind": "twse_financial_statement", "fiscal_period_end": "2026-06-30", "canonical_url": "https://official.example/financial"},
            "target_retrieval": {"item_count": 2, "raw_item_count": 2, "noise_item_count": 0},
            "scope_quality": {"exact_identity_title_matches": 2},
            "benchmark": {"symbol": "^TWII", "url": "https://example.com/benchmark"},
            "history_url": "https://example.com/history",
            "history_response_sha256": "a" * 64,
            "raw_capture_paths": [],
        },
    )
    summary = report.split("## 六、稽核附錄", 1)[0]
    assert "twse_financial_statement" not in summary
    assert "event_study" not in summary
    assert "官方財報" in summary
    assert "事件與股價" in summary


def test_human_conclusion_covers_reader_facing_paths() -> None:
    assert "研究起點" in _human_conclusion("professional_partial", ["event_study_required"], {}, {}, {}, {})
    assert "研究摘要" in _human_conclusion("research_only", [], {}, {}, {}, {})
    assert "轉盈" in _human_conclusion("professional_ready", {}, {}, {}, {"eps": -1}, {})
    assert "高於同業" in _human_conclusion(
        "professional_ready",
        [],
        {"365d_observed_pct": 50},
        {"observed_multiples": {"trailing_pe": 40}, "assumptions": {"peer_median_pe": 20}},
        {"eps": 1},
        {},
    )
    assert "低於同業" in _human_conclusion(
        "professional_ready",
        [],
        {"365d_observed_pct": 50},
        {"observed_multiples": {"trailing_pe": 10}, "assumptions": {"peer_median_pe": 20}},
        {"eps": 1},
        {},
    )
    assert "交叉檢視" in _human_conclusion("professional_ready", [], {}, {}, {"eps": 1}, {"aligned_event_count": 1})
