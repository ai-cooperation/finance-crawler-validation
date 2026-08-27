from __future__ import annotations

import pytest

from finance_crawler_poc.professional_analysis import (
    build_event_alignment,
    build_market_driver_snapshot,
    build_scenario_analysis,
    build_source_conflict_report,
    build_stance_calibration_report,
    build_time_series_snapshot,
    build_valuation_snapshot,
)


def test_time_series_sorts_deduplicates_and_computes_observed_metrics() -> None:
    snapshot = build_time_series_snapshot(
        [
            {"observed_at": "2026-01-03T00:00:00Z", "value": 110},
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 105},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 105},
        ],
        series_id="BTC",
        provider="fixture",
        as_of="2026-01-03T00:00:00Z",
        source_item_ids=["a" * 64],
    )

    assert [point["value"] for point in snapshot["points"]] == [100.0, 105.0, 110.0]
    assert snapshot["status"] == "available"
    assert snapshot["returns"]["observed_pct"] == 10.0
    assert snapshot["max_drawdown_pct"] == 0.0
    assert snapshot["source_item_ids"] == ["a" * 64]


def test_time_series_fails_closed_with_one_point() -> None:
    snapshot = build_time_series_snapshot(
        [{"observed_at": "2026-01-01T00:00:00Z", "value": 100}],
        series_id="BTC",
        provider="fixture",
        as_of="2026-01-01T00:00:00Z",
        source_item_ids=[],
    )

    assert snapshot["status"] == "insufficient_data"
    assert snapshot["returns"]["observed_pct"] is None
    assert snapshot["missing_reason"] == "at_least_two_points_required"


def test_valuation_never_fills_missing_inputs_or_claims_crypto_intrinsic_value() -> None:
    crypto = build_valuation_snapshot(
        {"kind": "crypto", "symbol": "BTC"},
        fundamentals=None,
        market_price=70000,
        source_item_ids=["a" * 64],
    )
    equity = build_valuation_snapshot(
        {"kind": "equity", "symbol": "ABC"},
        fundamentals={"eps": None, "revenue": 1000},
        market_price=10,
        source_item_ids=["a" * 64],
    )

    assert crypto["status"] == "not_applicable"
    assert crypto["method"] is None
    assert equity["status"] == "insufficient_data"
    assert equity["missing_fields"] == ["eps", "net_debt"]


def test_valuation_exposes_observed_pe_without_turning_it_into_a_target_value() -> None:
    valuation = build_valuation_snapshot(
        {"kind": "equity", "symbol": "2330.TW"},
        fundamentals={"eps": 65.47, "revenue": 3_809_054_300_000, "net_debt": -1_703_273_700_000},
        market_price=2410,
        source_item_ids=[],
    )

    assert valuation["status"] == "insufficient_data"
    assert valuation["missing_fields"] == ["peer_median_pe"]
    assert valuation["observed_multiples"]["trailing_pe"] == pytest.approx(2410 / 65.47, rel=1e-6)
    assert valuation["implied_value"] is None


def test_valuation_uses_only_explicit_peer_median_and_keeps_assumptions_auditable() -> None:
    valuation = build_valuation_snapshot(
        {"kind": "equity", "symbol": "2330.TW"},
        fundamentals={"eps": 65.47, "revenue": 3_809_054_300_000, "net_debt": -1_703_273_700_000},
        market_price=2410,
        source_item_ids=[],
        peer_valuation={
            "status": "available",
            "median_pe": 20.0,
            "period_alignment_status": "aligned",
            "period_alignment_basis": "fiscal_year_label",
            "target_period_key": "2025",
            "peer_set": [{"symbol": "NVDA", "trailing_pe": 20.0}],
            "assumptions": {"selection_rule": "fixture_peer_set_v1"},
        },
    )

    assert valuation["status"] == "available"
    assert valuation["missing_fields"] == []
    assert valuation["implied_value"] == {
        "value": 1309.4,
        "basis": "annual_diluted_eps_times_peer_median_pe",
        "not_a_forecast": True,
    }
    assert valuation["peer_set"][0]["symbol"] == "NVDA"
    assert valuation["period_alignment_status"] == "aligned"
    assert valuation["period_alignment_basis"] == "fiscal_year_label"
    assert valuation["target_period_key"] == "2025"
    assert valuation["assumptions"]["selection_rule"] == "fixture_peer_set_v1"


def test_valuation_does_not_publish_negative_implied_value_for_non_positive_target_eps() -> None:
    valuation = build_valuation_snapshot(
        {"kind": "equity", "symbol": "2371.TW"},
        fundamentals={"eps": -5.2, "revenue": 50_000, "net_debt": 10_000},
        market_price=27.6,
        source_item_ids=[],
        peer_valuation={
            "status": "available",
            "median_pe": 20.0,
            "period_alignment_status": "aligned",
            "period_alignment_basis": "fiscal_year_label",
            "target_period_key": "2025",
            "peer_set": [{"symbol": "2301.TW", "trailing_pe": 20.0}],
            "assumptions": {"selection_rule": "fixture_peer_set_v1"},
        },
    )

    assert valuation["status"] == "insufficient_data"
    assert valuation["implied_value"] is None
    assert valuation["missing_fields"] == ["positive_eps_for_pe_valuation"]
    assert valuation["reason"] == "target_eps_non_positive"


def test_valuation_uses_explicit_price_to_sales_for_non_positive_eps() -> None:
    valuation = build_valuation_snapshot(
        {"kind": "equity", "symbol": "1303.TW"},
        fundamentals={"eps": -1.0, "revenue": 100_000, "shares": 1_000, "net_debt": 10_000},
        market_price=100,
        source_item_ids=[],
        peer_valuation={
            "status": "available",
            "multiple_basis": "trailing_ps",
            "median_ps": 2.0,
            "period_alignment_status": "aligned",
            "period_alignment_basis": "fiscal_year_label",
            "target_period_key": "2025",
            "peer_set": [{"symbol": "1301.TW", "trailing_ps": 2.0}] * 3,
            "assumptions": {"selection_rule": "fixture_peer_set_v1"},
        },
    )

    assert valuation["status"] == "available"
    assert valuation["method"] == "price_to_sales"
    assert valuation["period_alignment_status"] == "aligned"
    assert valuation["assumptions"]["peer_median_ps"] == 2.0
    assert valuation["implied_value"] is None


def test_valuation_uses_descriptive_price_to_book_when_eps_is_non_positive() -> None:
    valuation = build_valuation_snapshot(
        {"kind": "equity", "symbol": "2371.TW"},
        fundamentals={
            "eps": -5.2,
            "revenue": 50_000,
            "net_debt": 10_000,
            "book_value_per_share": 23.57,
            "book_value_as_of": "2026-06-30",
        },
        market_price=27.6,
        source_item_ids=[],
        peer_valuation=None,
    )

    assert valuation["status"] == "available"
    assert valuation["method"] == "price_to_book"
    assert valuation["period_alignment_status"] == "aligned"
    assert valuation["implied_value"] is None
    assert valuation["observed_multiples"]["price_to_book"] == pytest.approx(27.6 / 23.57, rel=1e-6)
    assert valuation["reason"] == "pe_not_applicable_non_positive_eps"


def test_valuation_allows_explicit_dcf_only_fallback_when_peer_multiple_is_unavailable() -> None:
    valuation = build_valuation_snapshot(
        {"kind": "equity", "symbol": "1303.TW"},
        fundamentals={"eps": 0.57, "revenue": 100_000, "net_debt": 10_000},
        market_price=59.4,
        source_item_ids=[],
        peer_valuation={
            "status": "insufficient_data",
            "missing_reason": "at_least_three_positive_peer_multiples_required",
            "dcf_only_fallback_eligible": True,
            "dcf_only_fallback_reason": "peer_data_unavailable",
            "period_alignment_status": "not_applicable",
        },
    )
    assert valuation["status"] == "available"
    assert valuation["method"] == "dcf_only_fallback"
    assert valuation["period_alignment_status"] == "not_applicable"
    assert valuation["implied_value"] is None


def test_scenarios_are_mechanical_and_marked_not_a_forecast() -> None:
    time_series = build_time_series_snapshot(
        [
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 120},
            {"observed_at": "2026-01-03T00:00:00Z", "value": 90},
        ],
        series_id="BTC",
        provider="fixture",
        as_of="2026-01-03T00:00:00Z",
        source_item_ids=["a" * 64],
    )
    scenarios = build_scenario_analysis(time_series, current_price=90, horizon="observed_window")

    assert scenarios["not_a_forecast"] is True
    assert scenarios["status"] == "available"
    assert scenarios["scenarios"]["base"]["price"] == 90.0
    assert scenarios["scenarios"]["bull"]["price"] == 120.0
    assert scenarios["scenarios"]["bear"]["price"] == 90.0


def test_conflict_report_preserves_unknown_and_detects_mixed_stances() -> None:
    report = build_source_conflict_report([
        {"item_id": "a" * 64, "source_id": "news_a", "title": "Bitcoin rises on strong demand", "summary": "positive"},
        {"item_id": "b" * 64, "source_id": "news_b", "title": "Bitcoin falls as risk grows", "summary": "negative"},
        {"item_id": "c" * 64, "source_id": "news_c", "title": "Bitcoin update", "summary": ""},
    ], topic_id="digital_assets")

    assert report["conflict_level"] == "high"
    assert report["counts"] == {"positive": 1, "negative": 1, "neutral": 0, "unknown": 1}
    assert set(report["evidence_ids"]) == {"a" * 64, "b" * 64, "c" * 64}
    assert report["method"] == "source_conflict_screen_v2"
    assert report["calibration_status"] == "calibrated"
    assert report["classifier_version"] == "lexical_stance_v2_calibrated"
    assert report["calibration"]["metrics"]["positive"]["f1"] == 1.0


def test_conflict_report_does_not_count_route_aliases_as_independent_publishers() -> None:
    report = build_source_conflict_report([
        {"item_id": "a" * 64, "source_id": "yahoo_finance_target_rss", "canonical_url": "https://example/a", "title": "TSMC demand rises", "summary": ""},
        {"item_id": "b" * 64, "source_id": "yahoo_finance_target_search", "canonical_url": "https://example/a", "title": "TSMC demand rises", "summary": ""},
    ], topic_id="target")

    assert report["independent_source_count"] == 1
    assert report["cluster_count"] == 1


def test_conflict_report_uses_canonical_independence_groups_for_verified_publishers() -> None:
    report = build_source_conflict_report([
        {
            "item_id": "a" * 64,
            "source_id": "google_news_target_rss",
            "publisher_id": "publisher_a",
            "independence_group": "publisher_a",
            "canonical_url": "https://example/a",
            "title": "TSMC demand rises",
            "summary": "",
        },
        {
            "item_id": "b" * 64,
            "source_id": "google_news_target_rss",
            "publisher_id": "publisher_b",
            "independence_group": "publisher_b",
            "canonical_url": "https://example/b",
            "title": "TSMC capex rises",
            "summary": "",
        },
    ], topic_id="target")

    assert report["independent_source_count"] == 2
    assert {row["independence_group"] for row in report["observations"]} == {"publisher_a", "publisher_b"}


def test_stance_calibration_is_frozen_and_reproducible() -> None:
    first = build_stance_calibration_report()
    second = build_stance_calibration_report()

    assert first == second
    assert first["status"] == "calibrated"
    assert first["sample_count"] == 6
    assert first["macro_f1"] == 1.0


def test_time_series_exposes_short_observed_return_windows() -> None:
    snapshot = build_time_series_snapshot(
        [{"observed_at": f"2026-01-{day:02d}T00:00:00Z", "value": float(day)} for day in range(1, 9)],
        series_id="BTC",
        provider="fixture",
        as_of="2026-01-08T00:00:00Z",
        source_item_ids=[],
    )
    assert snapshot["returns"]["3d_observed_pct"] == 60.0
    assert snapshot["returns"]["7d_observed_pct"] == 700.0


def test_time_series_windows_use_calendar_time_not_point_count() -> None:
    snapshot = build_time_series_snapshot(
        [
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 110},
            {"observed_at": "2026-01-06T00:00:00Z", "value": 120},
        ],
        series_id="2330.TW",
        provider="fixture",
        as_of="2026-01-06T00:00:00Z",
        source_item_ids=[],
    )

    # Three calendar days before Jan 6 has Jan 2 as the nearest available
    # observation; using list position would incorrectly start at Jan 1.
    assert snapshot["returns"]["3d_observed_pct"] == 9.090909


def test_equity_volatility_uses_trading_day_annualization_when_requested() -> None:
    snapshot = build_time_series_snapshot(
        [
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 110},
            {"observed_at": "2026-01-05T00:00:00Z", "value": 100},
        ],
        series_id="2330.TW",
        provider="fixture",
        as_of="2026-01-05T00:00:00Z",
        source_item_ids=[],
        annualization_periods=252,
    )

    assert snapshot["volatility_annualized_pct"] == 151.529393


def test_invalid_time_series_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="value must be positive"):
        build_time_series_snapshot(
            [{"observed_at": "2026-01-01T00:00:00Z", "value": 0}],
            series_id="BTC",
            provider="fixture",
            as_of="2026-01-01T00:00:00Z",
            source_item_ids=[],
        )


def test_time_series_rejects_observation_after_as_of() -> None:
    with pytest.raises(ValueError, match="must not be after as_of"):
        build_time_series_snapshot(
            [
                {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
                {"observed_at": "2026-01-04T00:00:00Z", "value": 110},
            ],
            series_id="BTC",
            provider="fixture",
            as_of="2026-01-03T00:00:00Z",
            source_item_ids=[],
        )


def test_event_alignment_uses_nearest_observed_prices_and_stays_non_causal() -> None:
    series = build_time_series_snapshot(
        [
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-03T00:00:00Z", "value": 110},
            {"observed_at": "2026-01-06T00:00:00Z", "value": 121},
        ],
        series_id="2330.TW",
        provider="fixture",
        as_of="2026-01-06T00:00:00Z",
        source_item_ids=[],
    )

    result = build_event_alignment([{
        "item_id": "a" * 64,
        "title": "TSMC demand update",
        "published_at": "2026-01-02T00:00:00Z",
    }], series)

    assert result["status"] == "available"
    assert result["aligned_event_count"] == 1
    assert result["events"][0]["observed_return_pct"] == 10.0
    assert result["events"][0]["causal_status"] == "unresolved"
    assert result["not_causal"] is True


def test_event_alignment_computes_market_adjusted_return_when_benchmark_is_present() -> None:
    series = build_time_series_snapshot(
        [
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-03T00:00:00Z", "value": 110},
            {"observed_at": "2026-01-06T00:00:00Z", "value": 121},
        ],
        series_id="2330.TW",
        provider="fixture",
        as_of="2026-01-06T00:00:00Z",
        source_item_ids=[],
    )
    benchmark = build_time_series_snapshot(
        [
            {"observed_at": "2026-01-01T00:00:00Z", "value": 1000},
            {"observed_at": "2026-01-03T00:00:00Z", "value": 1050},
            {"observed_at": "2026-01-06T00:00:00Z", "value": 1071},
        ],
        series_id="^TWII",
        provider="fixture",
        as_of="2026-01-06T00:00:00Z",
        source_item_ids=[],
    )

    result = build_event_alignment(
        [{
            "item_id": "a" * 64,
            "title": "TSMC demand update",
            "published_at": "2026-01-02T00:00:00Z",
        }],
        series,
        benchmark_time_series=benchmark,
    )

    assert result["event_study_status"] == "available"
    assert result["event_study_event_count"] == 1
    assert result["event_study_sample_status"] == "descriptive_only"
    assert result["event_study_significance_status"] == "not_computed"
    assert result["events"][0]["benchmark_return_pct"] == 5.0
    assert result["events"][0]["abnormal_return_pct"] == 5.0


def test_event_alignment_excludes_right_edge_censored_event_without_marking_unresolved() -> None:
    series = build_time_series_snapshot(
        [
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-03T00:00:00Z", "value": 110},
            {"observed_at": "2026-01-08T00:00:00Z", "value": 120},
        ],
        series_id="2308.TW",
        provider="fixture",
        as_of="2026-01-08T00:00:00Z",
        source_item_ids=[],
    )
    result = build_event_alignment([{
        "item_id": "b" * 64,
        "title": "Right-edge headline",
        "published_at": "2026-01-02T12:00:00Z",
    }], series)

    assert result["aligned_event_count"] == 0
    assert result["unresolved_event_count"] == 0
    assert result["excluded_incomplete_window_event_count"] == 1


def test_market_driver_terms_use_word_boundaries() -> None:
    result = build_market_driver_snapshot(
        target={"kind": "equity", "symbol": "2330.TW"},
        market_snapshot={"instruments": [{"symbol": "2330.TW", "price": 1100}]},
        time_series={"returns": {}},
        evidence=[{
            "item_id": "a" * 64,
            "source_id": "source",
            "title": "Corporate update",
            "summary": "",
        }],
        provider_data={},
    )

    assert result["news_driver_candidates"] == []


def test_market_driver_terms_capture_equity_demand_and_capex_without_claiming_causality() -> None:
    provider_data = {
        "volume": {"status": "available"},
        "etf_flows": {"status": "not_applicable"},
        "derivatives": {"status": "not_applicable"},
        "on_chain": {"status": "not_applicable"},
    }
    result = build_market_driver_snapshot(
        target={"kind": "equity", "symbol": "2330.TW"},
        market_snapshot={"instruments": [{"symbol": "2330.TW", "price": 2410}]},
        time_series={"returns": {"30d_observed_pct": 1.2}},
        evidence=[{
            "item_id": "a" * 64,
            "source_id": "source",
            "title": "TSMC raises capex as AI chip demand grows",
            "summary": "",
        }],
        provider_data=provider_data,
    )

    assert result["status"] == "available"
    assert result["news_driver_candidates"][0]["label"] == "Demand and orders"
    assert result["news_driver_candidates"][0]["causal_status"] == "unresolved"
