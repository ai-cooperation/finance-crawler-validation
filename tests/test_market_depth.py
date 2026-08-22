from __future__ import annotations

from finance_crawler_poc.market_depth import build_financial_depth, parse_coingecko_history


def test_parse_coingecko_history_normalizes_millisecond_points() -> None:
    points = parse_coingecko_history({"prices": [[1767225600000, 100], [1767312000000, 110]]})

    assert points == [
        {"observed_at": "2026-01-01T00:00:00Z", "value": 100.0},
        {"observed_at": "2026-01-02T00:00:00Z", "value": 110.0},
    ]


def test_financial_depth_contains_time_series_scenarios_and_conflicts() -> None:
    item_id = "a" * 64
    depth = build_financial_depth(
        target={"kind": "crypto", "symbol": "BTC"},
        market_snapshot={"instruments": [{"symbol": "BTC", "price": 110, "source_item_ids": [item_id]}]},
        history_points=[
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 110},
        ],
        history_provider="fixture",
        history_url="https://example.com/history",
        as_of="2026-01-02T00:00:00Z",
        evidence=[{"item_id": item_id, "source_id": "news", "title": "Bitcoin rises", "summary": ""}],
    )

    assert depth["status"] == "professional_partial"
    assert depth["time_series"]["source_ref"]["url"] == "https://example.com/history"
    assert depth["valuation"]["status"] == "not_applicable"
    assert depth["scenarios"]["not_a_forecast"] is True
    assert depth["source_conflicts"][0]["evidence_ids"] == [item_id]
