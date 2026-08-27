from __future__ import annotations

import json

import httpx

from finance_crawler_poc.market_depth import (
    fetch_yahoo_fundamentals,
    fetch_yahoo_history,
    fetch_market_provider_bundle,
    fetch_yahoo_peer_valuation,
    fetch_yahoo_volume,
    parse_yahoo_fundamentals,
)
from finance_crawler_poc.market_depth import fetch_market_provider_bundle


def test_peer_valuation_uses_target_specific_selection_rule(monkeypatch) -> None:
    def fundamentals(target, **kwargs):
        return {
            "status": "available",
            "eps": 10.0,
            "as_of": "2025-12-31",
            "currency": "TWD",
            "source_ref": {"url": f"https://example.test/{target['symbol']}", "response_sha256": "a" * 64},
        }

    def history(target, **kwargs):
        return ([{"observed_at": "2026-08-21T00:00:00Z", "value": 100.0}], "yahoo_finance", "https://example.test/chart", "b" * 64)

    monkeypatch.setattr("finance_crawler_poc.market_depth.fetch_yahoo_fundamentals", fundamentals)
    monkeypatch.setattr("finance_crawler_poc.market_depth.fetch_yahoo_history", history)

    result = fetch_yahoo_peer_valuation(
        {
            "symbol": "2308.TW",
            "peer_symbols": ["2301.TW", "2382.TW", "2395.TW"],
            "peer_selection_rule": "curated_taiwan_electronics_peers_v1",
        },
        target_as_of="2025-12-31",
    )

    assert result["status"] == "available"
    assert result["selection_rule"] == "curated_taiwan_electronics_peers_v1"
    assert result["assumptions"]["selection_rule"] == "curated_taiwan_electronics_peers_v1"


def test_peer_valuation_rejects_price_eps_currency_mismatch(monkeypatch) -> None:
    def fundamentals(target, **kwargs):
        return {
            "status": "available",
            "eps": 1.0,
            "as_of": "2025-12-31",
            "currency": "USD",
            "source_ref": {"url": "https://example.test/fundamentals", "response_sha256": "a" * 64},
        }

    def history(target, **kwargs):
        return ([{"observed_at": "2026-08-21T00:00:00Z", "value": 100.0}], "yahoo_finance", "https://example.test/chart", "b" * 64)

    monkeypatch.setattr("finance_crawler_poc.market_depth.fetch_yahoo_fundamentals", fundamentals)
    monkeypatch.setattr("finance_crawler_poc.market_depth.fetch_yahoo_history", history)
    result = fetch_yahoo_peer_valuation(
        {"symbol": "2330.TW", "peer_symbols": ["0981.HK", "1347.HK", "0005.HK"]},
        target_as_of="2025-12-31",
    )
    assert result["status"] == "insufficient_data"
    assert result["usable_peer_count"] == 0
    assert result["peer_set"][0]["price_currency"] == "HKD"
    assert result["peer_set"][0]["currency_alignment_status"] == "mismatch"


def test_fetch_yahoo_history_uses_range_endpoint_for_equity(monkeypatch) -> None:
    payload = {
        "chart": {
            "result": [{
                "meta": {"currency": "TWD"},
                "timestamp": [1767225600, 1767312000],
                "indicators": {"quote": [{"close": [100.0, 110.0]}]},
            }],
            "error": None,
        }
    }
    calls: list[str] = []

    def fake_get(url: str, **kwargs):
        calls.append(url)
        return httpx.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            request=httpx.Request("GET", url),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr("finance_crawler_poc.market_depth.httpx.get", fake_get)

    points, provider, url, response_hash = fetch_yahoo_history(
        {"kind": "equity", "symbol": "2330.TW"}, days=365
    )

    assert provider == "yahoo_finance"
    assert len(points) == 2
    assert "range=1y" in url
    assert calls == [url]
    assert len(response_hash) == 64


def _fundamentals_payload() -> dict:
    def row(metric: str, as_of: str, value: float) -> dict:
        return {
            "meta": {"symbol": ["2330.TW"], "type": [metric]},
            metric: [{
                "dataId": 1,
                "asOfDate": as_of,
                "periodType": "12M",
                "currencyCode": "TWD",
                "reportedValue": {"raw": value, "fmt": str(value)},
            }],
        }

    return {
        "timeseries": {
            "result": [
                row("annualDilutedEPS", "2025-12-31", 65.47),
                row("annualTotalRevenue", "2025-12-31", 3_809_054_300_000),
                row("annualTotalDebt", "2025-12-31", 1_064_582_700_000),
                row("annualCashAndCashEquivalents", "2025-12-31", 2_767_856_400_000),
            ],
            "error": None,
        }
    }


def test_parse_yahoo_fundamentals_normalizes_financial_fields_and_net_debt() -> None:
    parsed = parse_yahoo_fundamentals(
        _fundamentals_payload(),
        symbol="2330.TW",
        source_ref={"url": "https://example.com/fundamentals", "response_sha256": "a" * 64},
    )

    assert parsed["status"] == "available"
    assert parsed["provider"] == "yahoo_finance_fundamentals"
    assert parsed["symbol"] == "2330.TW"
    assert parsed["as_of"] == "2025-12-31"
    assert parsed["currency"] == "TWD"
    assert parsed["eps"] == 65.47
    assert parsed["revenue"] == 3_809_054_300_000.0
    assert parsed["total_debt"] == 1_064_582_700_000.0
    assert parsed["cash"] == 2_767_856_400_000.0
    assert parsed["net_debt"] == -1_703_273_700_000.0
    assert parsed["source_ref"]["response_sha256"] == "a" * 64
    assert parsed["missing_fields"] == []


def test_parse_yahoo_fundamentals_can_select_latest_observation_before_cutoff() -> None:
    payload = _fundamentals_payload()
    payload["timeseries"]["result"][0]["annualDilutedEPS"] = [
        {"asOfDate": "2025-12-31", "reportedValue": {"raw": 65.0}, "currencyCode": "TWD"},
        {"asOfDate": "2026-12-31", "reportedValue": {"raw": 70.0}, "currencyCode": "TWD"},
    ]

    parsed = parse_yahoo_fundamentals(
        payload,
        symbol="2330.TW",
        source_ref={"url": "https://example.com/fundamentals"},
        as_of_cutoff="2026-01-01",
    )

    assert parsed["eps"] == 65.0
    assert parsed["as_of"] == "2025-12-31"


def test_parse_yahoo_fundamentals_fails_closed_when_a_required_metric_is_missing() -> None:
    payload = _fundamentals_payload()
    payload["timeseries"]["result"] = payload["timeseries"]["result"][:2]

    parsed = parse_yahoo_fundamentals(payload, symbol="2330.TW", source_ref={"url": "https://example.com"})

    assert parsed["status"] == "insufficient_data"
    assert parsed["missing_fields"] == ["total_debt", "cash", "net_debt"]
    assert parsed["net_debt"] is None


def test_fetch_yahoo_fundamentals_uses_public_timeseries_endpoint(monkeypatch) -> None:
    payload = _fundamentals_payload()
    calls: list[str] = []

    def fake_get(url: str, **kwargs):
        calls.append(url)
        return httpx.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            request=httpx.Request("GET", url),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr("finance_crawler_poc.market_depth.httpx.get", fake_get)

    parsed = fetch_yahoo_fundamentals({"kind": "equity", "symbol": "2330.TW"})

    assert parsed["status"] == "available"
    assert len(calls) == 1
    assert "/ws/fundamentals-timeseries/v1/finance/timeseries/2330.TW" in calls[0]
    assert "annualDilutedEPS" in calls[0]
    citation_url = parsed["source_ref"]["citation_url"]
    assert citation_url.startswith("https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/2330.TW?")
    assert "period1=" not in citation_url
    assert "period2=" not in citation_url


def test_market_provider_bundle_wires_equity_fundamentals_without_fabricating_other_feeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "finance_crawler_poc.market_depth.fetch_market_history",
        lambda target, days, timeout_seconds: (
            [{"observed_at": "2026-08-20T00:00:00Z", "value": 2400.0}, {"observed_at": "2026-08-21T00:00:00Z", "value": 2410.0}],
            "yahoo_finance",
            "https://example.com/chart",
            "b" * 64,
        ),
    )
    monkeypatch.setattr(
        "finance_crawler_poc.market_depth.fetch_yahoo_fundamentals",
        lambda target, timeout_seconds: {"status": "available", "eps": 65.47, "revenue": 1000, "net_debt": -200},
    )
    monkeypatch.setattr(
        "finance_crawler_poc.market_depth.fetch_yahoo_volume",
        lambda target, days, timeout_seconds: {"status": "unavailable", "reason": "fixture"},
    )

    bundle = fetch_market_provider_bundle({"kind": "equity", "symbol": "2330.TW"}, days=365)

    assert bundle["fundamentals"]["status"] == "available"
    assert bundle["provider_data"]["volume"]["status"] == "unavailable"
    assert bundle["provider_data"]["derivatives"]["status"] == "not_applicable"


def test_market_provider_bundle_covers_crypto_public_provider_pack(monkeypatch) -> None:
    chart = {
        "prices": [[1_735_689_600_000, 100.0], [1_735_776_000_000, 110.0]],
        "total_volumes": [[1_735_776_000_000, 1000.0]],
        "market_caps": [[1_735_776_000_000, 2_000_000.0]],
    }
    details = {"market_data": {
        "current_price": {"usd": 110.0},
        "market_cap": {"usd": 2_000_000.0},
        "total_volume": {"usd": 1000.0},
        "circulating_supply": 10.0,
        "total_supply": 20.0,
        "max_supply": 21.0,
    }}

    def fake_fetch_json(url: str, **kwargs):
        if "market_chart" in url:
            return chart, "a" * 64
        if "api.coingecko.com/api/v3/coins/bitcoin?" in url:
            return details, "b" * 64
        if "fundingRate" in url:
            return ([{"fundingRate": "0.001", "fundingTime": 1_735_776_000_000}], "c" * 64)
        if "openInterest" in url:
            return ({"openInterest": "123.0"}, "d" * 64)
        if "ticker/24hr" in url:
            return ({"quoteVolume": "456.0"}, "e" * 64)
        if "blockchain.info" in url:
            return ({"values": [{"x": 1_735_776_000, "y": 123.0}]}, "f" * 64)
        if "tbstat.com" in url:
            return ({"Series": {"spot": {"Data": [{"Timestamp": 1_735_776_000, "Result": 12.0}]}}}, "g" * 64)
        raise AssertionError(url)

    monkeypatch.setattr("finance_crawler_poc.market_depth._fetch_json", fake_fetch_json)

    bundle = fetch_market_provider_bundle({"kind": "crypto", "symbol": "BTC"}, days=2)

    assert bundle["provider"] == "coingecko"
    assert bundle["fundamentals"]["status"] == "available"
    assert bundle["provider_data"]["volume"]["status"] == "available"
    assert bundle["provider_data"]["derivatives"]["status"] == "available"
    assert bundle["provider_data"]["on_chain"]["status"] == "available"
    assert bundle["provider_data"]["etf_flows"]["status"] == "available"


def test_fetch_yahoo_volume_normalizes_latest_daily_volume(monkeypatch) -> None:
    payload = {
        "chart": {
            "result": [{
                "timestamp": [1767225600, 1767312000],
                "indicators": {"quote": [{"volume": [1000, 1250]}]},
            }],
            "error": None,
        }
    }

    def fake_get(url: str, **kwargs):
        return httpx.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            request=httpx.Request("GET", url),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr("finance_crawler_poc.market_depth.httpx.get", fake_get)

    result = fetch_yahoo_volume({"kind": "equity", "symbol": "2330.TW"}, days=365)

    assert result["status"] == "available"
    assert result["latest"] == {"observed_at": "2026-01-02T00:00:00Z", "value": 1250.0}
    assert result["point_count"] == 2
