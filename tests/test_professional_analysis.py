from __future__ import annotations

import pytest

from finance_crawler_poc.professional_analysis import (
    build_scenario_analysis,
    build_source_conflict_report,
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


def test_invalid_time_series_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="value must be positive"):
        build_time_series_snapshot(
            [{"observed_at": "2026-01-01T00:00:00Z", "value": 0}],
            series_id="BTC",
            provider="fixture",
            as_of="2026-01-01T00:00:00Z",
            source_item_ids=[],
        )
