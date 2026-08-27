from __future__ import annotations

from finance_crawler_poc.quality_gate import evaluate_quality_gate
from finance_crawler_poc.professional_analysis import build_event_study_statistics
from finance_crawler_poc.professional_equity import build_valuation_model


def _evidence_pack() -> dict[str, object]:
    return {
        "canonical_story_count": 12,
        "items": [
            {
                "source_tier": "regulatory",
                "official_scope": "financial_statement",
                "independence_group": "sec",
            },
            {"source_tier": "direct_primary", "independence_group": "issuer"},
            {"source_tier": "direct_secondary", "independence_group": "wire"},
        ],
    }


def test_event_study_available_but_descriptive_only_is_blocked() -> None:
    result = evaluate_quality_gate(
        target={"kind": "equity", "symbol": "2330.TW"},
        evidence_pack=_evidence_pack(),
        time_series={"status": "available"},
        fundamentals={"status": "available"},
        valuation={"status": "available", "period_alignment_status": "aligned", "contract_status": "pass"},
        market_drivers={"status": "available"},
        event_alignment={
            "event_study_status": "available",
            "event_study_sample_status": "descriptive_only",
            "event_study_significance_status": "not_computed",
            "event_study_unique_event_date_count": 6,
            "unresolved_event_count": 2,
        },
    )

    assert result["status"] == "professional_partial"
    assert "event_study_not_complete" in result["blocking_reasons"]


def test_valuation_contract_blocks_forward_label_with_trailing_basis() -> None:
    forecast = {
        "status": "available",
        "currency": "TWD",
        "forecast_periods": [
            {"year": 2026, "scenarios": {name: {"eps": 10.0, "free_cash_flow": 100.0} for name in ("bear", "base", "bull")}},
            {"year": 2027, "scenarios": {name: {"eps": 11.0, "free_cash_flow": 110.0} for name in ("bear", "base", "bull")}},
            {"year": 2028, "scenarios": {name: {"eps": 12.0, "free_cash_flow": 120.0} for name in ("bear", "base", "bull")}},
        ],
    }
    result = build_valuation_model(
        forecast,
        market_price=100.0,
        shares_outstanding=100.0,
        net_debt=0.0,
        peer_median_pe=20.0,
        peer_basis="trailing_pe",
        target_eps=8.0,
        target_period_key="2025",
    )

    assert result["status"] == "available"
    assert result["methods"][1]["method"] == "trailing_pe"
    assert result["methods"][1]["scenario_values"] == {"bear": 160.0, "base": 160.0, "bull": 160.0}
    assert result["assumptions"]["peer_multiple_basis"] == "trailing_pe"
    assert result["assumptions"]["peer_target_eps"] == 8.0


def test_event_study_statistics_are_replayable_and_have_inference_fields() -> None:
    values = [0.01, 0.02, -0.01, 0.03, 0.00]
    first = build_event_study_statistics(values)
    second = build_event_study_statistics(values)

    assert first == second
    assert first["sample_count"] == 5
    assert first["status"] == "computed"
    assert "t_stat" in first
    assert "p_value_two_sided" in first
