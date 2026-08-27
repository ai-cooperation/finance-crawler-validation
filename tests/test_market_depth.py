from __future__ import annotations

from finance_crawler_poc.contracts import validate_contract
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


def test_financial_depth_preserves_equity_currency_from_market_instrument() -> None:
    depth = build_financial_depth(
        target={"kind": "equity", "symbol": "2330.TW"},
        market_snapshot={
            "instruments": [{
                "symbol": "2330.TW",
                "price": 1100,
                "currency": "TWD",
                "source_item_ids": [],
            }]
        },
        history_points=[
            {"observed_at": "2026-01-01T00:00:00Z", "value": 1000},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 1100},
        ],
        history_provider="yahoo_finance",
        history_url="https://example.com/history",
        as_of="2026-01-02T00:00:00Z",
        evidence=[],
    )

    assert depth["time_series"]["currency"] == "TWD"


def test_financial_depth_links_provider_payloads_and_fiscal_alignment_to_canonical_fields() -> None:
    history_url = "https://example.com/history"
    history_hash = "b" * 64
    benchmark_url = "https://example.com/benchmark"
    benchmark_hash = "c" * 64
    depth = build_financial_depth(
        target={"kind": "equity", "symbol": "2330.TW"},
        market_snapshot={
            "instruments": [{
                "symbol": "2330.TW",
                "price": 1100,
                "currency": "TWD",
                "source_item_ids": [],
            }]
        },
        history_points=[
            {"observed_at": "2026-01-01T00:00:00Z", "value": 1000},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 1100},
        ],
        history_provider="yahoo_finance",
        history_url=history_url,
        history_response_sha256=history_hash,
        benchmark_points=[
            {"observed_at": "2026-01-01T00:00:00Z", "value": 20000},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 20100},
        ],
        benchmark_provider="yahoo_finance",
        benchmark_url=benchmark_url,
        benchmark_response_sha256=benchmark_hash,
        as_of="2026-01-02T00:00:00Z",
        evidence=[],
        fundamentals={
            "status": "available",
            "eps": 50,
            "revenue": 1000,
            "net_debt": -100,
            "as_of": "2025-12-31",
            "source_ref": {
                "url": "https://example.com/fundamentals",
                "response_sha256": "d" * 64,
            },
        },
        peer_valuation={
            "status": "available",
            "median_pe": 20,
            "period_alignment_status": "aligned",
            "period_alignment_basis": "fiscal_year_label",
            "target_period_key": "2025",
            "peer_set": [{"symbol": "NVDA", "trailing_pe": 20}],
        },
    )

    assert depth["time_series"]["source_item_ids"]
    assert depth["time_series"]["source_ref"]["item_id"] == depth["time_series"]["source_item_ids"][0]
    assert depth["benchmark_time_series"]["source_item_ids"]
    assert depth["benchmark_time_series"]["source_ref"]["item_id"] == depth["benchmark_time_series"]["source_item_ids"][0]
    assert depth["fundamentals"]["source_ref"]["item_id"]
    assert depth["valuation"]["source_item_ids"]
    assert depth["valuation"]["period_alignment_basis"] == "fiscal_year_label"
    assert depth["valuation"]["target_period_key"] == "2025"
    validate_contract("time-series-snapshot", depth["time_series"])


def test_financial_depth_fails_closed_when_target_has_no_evidence() -> None:
    depth = build_financial_depth(
        target={"kind": "equity", "symbol": "2330.TW"},
        market_snapshot={
            "instruments": [{
                "symbol": "2330.TW",
                "price": 1100,
                "currency": "TWD",
                "source_item_ids": [],
            }]
        },
        history_points=[
            {"observed_at": "2026-01-01T00:00:00Z", "value": 1000},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 1100},
        ],
        history_provider="yahoo_finance",
        history_url="https://example.com/history",
        as_of="2026-01-02T00:00:00Z",
        evidence=[],
    )

    assert depth["status"] == "research_only"
    assert depth["source_conflicts"][0]["status"] == "insufficient_data"


def test_financial_depth_does_not_call_unknown_stance_evidence_conflict_ready() -> None:
    items = [
        {"item_id": f"{index:064x}", "source_id": f"source_{index}", "title": "TSMC update", "summary": ""}
        for index in (1, 2)
    ]
    provider_data = {key: {"status": "available"} for key in ("volume", "etf_flows", "derivatives", "on_chain")}
    depth = build_financial_depth(
        target={"kind": "crypto", "symbol": "BTC"},
        market_snapshot={"instruments": [{"symbol": "BTC", "price": 110, "source_item_ids": []}]},
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

    assert depth["status"] == "professional_partial"


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
