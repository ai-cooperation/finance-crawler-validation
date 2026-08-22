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


def test_financial_depth_reaches_ready_only_with_calibrated_conflict_and_provider_bundle() -> None:
    items = [
        {"item_id": f"{index:064x}", "source_id": f"source_{index}", "title": title, "summary": ""}
        for index, title in enumerate([
            "Bitcoin rises on ETF inflows",
            "Bitcoin falls as regulation risks grow",
        ], start=1)
    ]
    provider_data = {key: {"status": "available"} for key in ("volume", "etf_flows", "derivatives", "on_chain")}
    depth = build_financial_depth(
        target={"kind": "crypto", "symbol": "BTC"},
        market_snapshot={"instruments": [{"symbol": "BTC", "price": 110, "source_item_ids": [item["item_id"] for item in items]}]},
        history_points=[
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 110},
        ],
        history_provider="fixture",
        history_url="https://example.com/history",
        as_of="2026-01-02T00:00:00Z",
        evidence=items,
        fundamentals={"status": "available"},
        provider_data=provider_data,
    )

    assert depth["status"] == "professional_ready"
    assert depth["market_drivers"]["status"] == "available"
    assert depth["source_conflicts"][0]["calibration_status"] == "calibrated"
