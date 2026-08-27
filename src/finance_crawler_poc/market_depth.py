from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Iterable, Mapping
from datetime import datetime
from statistics import median
from typing import Any
from urllib.parse import quote

import httpx

from finance_crawler_poc.contracts import build_item_id
from finance_crawler_poc.professional_analysis import (
    build_event_alignment,
    build_market_driver_snapshot,
    build_scenario_analysis,
    build_source_conflict_report,
    build_time_series_snapshot,
    build_valuation_snapshot,
)
from finance_crawler_poc.canonical_evidence import build_canonical_evidence_pack
from finance_crawler_poc.quality_gate import evaluate_quality_gate
from finance_crawler_poc.source_registry import build_registry_for_items


COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
}

EQUITY_PEER_SYMBOLS: dict[str, tuple[str, ...]] = {
    # Frozen selection rule for the first production vertical.  These are
    # large-cap semiconductor peers with public Yahoo fundamentals; the
    # resulting multiple is descriptive and is never presented as a forecast.
    # ASML.AS keeps the price and Yahoo-reported EPS in EUR; using the ADR
    # symbol without an FX conversion would silently mix USD price with EUR
    # earnings and corrupt the peer multiple.
    "2330.TW": ("NVDA", "AMD", "ASML.AS", "QCOM"),
}


def build_financial_depth(
    *,
    target: Mapping[str, Any],
    market_snapshot: Mapping[str, Any],
    history_points: Iterable[Mapping[str, Any]],
    history_provider: str,
    history_url: str,
    as_of: str,
    evidence: Iterable[Mapping[str, Any]],
    fundamentals: Mapping[str, Any] | None = None,
    peer_valuation: Mapping[str, Any] | None = None,
    history_response_sha256: str | None = None,
    benchmark_points: Iterable[Mapping[str, Any]] | None = None,
    benchmark_provider: str | None = None,
    benchmark_url: str | None = None,
    benchmark_response_sha256: str | None = None,
    benchmark_symbol: str = "^TWII",
    provider_data: Mapping[str, Any] | None = None,
    source_registry: Mapping[str, Any] | None = None,
    requirement_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = str(target.get("symbol") or "").strip().upper()
    instrument = next(
        (candidate for candidate in market_snapshot.get("instruments", [])
         if candidate.get("symbol") == symbol),
        None,
    )
    current_price = instrument.get("price") if isinstance(instrument, Mapping) else None
    # Materialize once because callers may pass a generator.
    points = list(history_points)
    normalized_hash = hashlib.sha256(
        json.dumps(points, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    history_source_ref = _provider_source_ref(
        source_id=f"provider_history_{(symbol or 'target').casefold()}",
        url=history_url,
        response_sha256=history_response_sha256 or normalized_hash,
        hash_kind="provider_response" if history_response_sha256 else "normalized_points",
    )
    instrument_source_item_ids = (
        instrument.get("source_item_ids", [])
        if isinstance(instrument, Mapping)
        else []
    )
    time_series = build_time_series_snapshot(
        points,
        series_id=symbol or "TARGET",
        provider=history_provider,
        as_of=as_of,
        source_item_ids=[history_source_ref["item_id"], *instrument_source_item_ids],
        currency=(
            str(instrument.get("currency") or "").strip().upper()
            if isinstance(instrument, Mapping)
            else str(target.get("currency") or "USD").strip().upper()
        ) or "USD",
        annualization_periods=252 if str(target.get("kind") or "").strip() in {"equity", "etf", "company"} else 365,
    )
    time_series["source_ref"] = history_source_ref
    benchmark_time_series: dict[str, Any] | None = None
    if benchmark_points is not None and benchmark_provider and benchmark_url:
        benchmark_values = list(benchmark_points)
        benchmark_normalized_hash = hashlib.sha256(
            json.dumps(benchmark_values, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        normalized_benchmark_symbol = str(benchmark_symbol or "^TWII").strip().upper()
        benchmark_source_ref = _provider_source_ref(
            source_id=f"provider_history_{normalized_benchmark_symbol.casefold()}",
            url=benchmark_url,
            response_sha256=benchmark_response_sha256 or benchmark_normalized_hash,
            hash_kind="provider_response" if benchmark_response_sha256 else "normalized_points",
        )
        benchmark_time_series = build_time_series_snapshot(
            benchmark_values,
            series_id=normalized_benchmark_symbol,
            provider=benchmark_provider,
            as_of=as_of,
            source_item_ids=[benchmark_source_ref["item_id"]],
            currency=time_series["currency"],
            annualization_periods=252,
        )
        benchmark_time_series["source_ref"] = benchmark_source_ref
    normalized_fundamentals = dict(fundamentals) if isinstance(fundamentals, Mapping) else fundamentals
    if isinstance(normalized_fundamentals, dict):
        source_ref = normalized_fundamentals.get("source_ref")
        if isinstance(source_ref, Mapping):
            normalized_ref = dict(source_ref)
            source_url = str(normalized_ref.get("url") or "")
            source_hash = str(normalized_ref.get("response_sha256") or "")
            if source_url and source_hash:
                normalized_ref["item_id"] = build_item_id(
                    f"provider_fundamentals_{symbol.casefold() or 'target'}",
                    source_url,
                    source_hash,
                )
            normalized_fundamentals["source_ref"] = normalized_ref
    fundamental_source_ids = []
    if isinstance(normalized_fundamentals, Mapping):
        fundamental_ref = normalized_fundamentals.get("source_ref")
        if isinstance(fundamental_ref, Mapping) and fundamental_ref.get("item_id"):
            fundamental_source_ids.append(str(fundamental_ref["item_id"]))
    valuation = build_valuation_snapshot(
        target,
        fundamentals=normalized_fundamentals,
        market_price=current_price if isinstance(current_price, (int, float)) else None,
        source_item_ids=[*instrument_source_item_ids, *fundamental_source_ids],
        peer_valuation=peer_valuation,
    )
    scenarios = build_scenario_analysis(
        time_series,
        current_price=current_price if isinstance(current_price, (int, float)) else None,
        horizon="observed_window",
    )
    evidence_list = list(evidence)
    registry = (
        dict(source_registry)
        if isinstance(source_registry, Mapping)
        else build_registry_for_items(evidence_list)
        if evidence_list
        else {"schema_version": 1, "registry_id": "empty_evidence_sources_v1", "sources": []}
    )
    evidence_pack = build_canonical_evidence_pack(evidence_list, registry=registry)
    canonical_evidence = evidence_pack["canonical_items"]
    conflict = build_source_conflict_report(canonical_evidence, topic_id="target")
    event_alignment = build_event_alignment(
        canonical_evidence,
        time_series,
        benchmark_time_series=benchmark_time_series,
    )
    market_drivers = build_market_driver_snapshot(
        target=target,
        market_snapshot=market_snapshot,
        time_series=time_series,
        evidence=canonical_evidence,
        provider_data=provider_data,
    )
    quality_gate = evaluate_quality_gate(
        target=target,
        evidence_pack=evidence_pack,
        time_series=time_series,
        fundamentals=normalized_fundamentals if isinstance(normalized_fundamentals, Mapping) else {"status": "unavailable"},
        valuation=valuation,
        market_drivers=market_drivers,
        event_alignment=event_alignment,
        provider_data=provider_data,
        requirement_coverage=requirement_coverage,
    )
    status = quality_gate["status"]
    return {
        "schema_version": 1,
        "status": status,
        "evidence_pack": evidence_pack,
        "quality_gate": quality_gate,
        "time_series": time_series,
        "benchmark_time_series": benchmark_time_series,
        "fundamentals": normalized_fundamentals if normalized_fundamentals is not None else {"status": "unavailable", "missing_reason": "provider_not_configured"},
        "valuation": valuation,
        "scenarios": scenarios,
        "source_conflicts": [conflict],
        "market_drivers": market_drivers,
        "event_alignment": event_alignment,
    }


def _provider_source_ref(*, source_id: str, url: str, response_sha256: str, hash_kind: str) -> dict[str, str]:
    """Create a deterministic provider evidence reference for a raw payload."""

    # Yahoo symbols such as ^TWII are valid API path values but the literal
    # caret is not a valid URI character under the JSON Schema format checker.
    # Keep the source URL replayable while emitting a standards-compliant URI.
    normalized_url = url.replace("^", "%5E")
    return {
        "url": normalized_url,
        "response_sha256": response_sha256,
        "hash_kind": hash_kind,
        "item_id": build_item_id(source_id, normalized_url, response_sha256),
    }


def parse_coingecko_history(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    prices = payload.get("prices")
    if not isinstance(prices, list):
        raise ValueError("CoinGecko response missing prices")
    points: list[dict[str, Any]] = []
    for row in prices:
        if not isinstance(row, list) or len(row) < 2:
            continue
        timestamp_ms, value = row[0], row[1]
        if not isinstance(timestamp_ms, (int, float)) or not isinstance(value, (int, float)):
            continue
        from datetime import datetime, timezone
        observed_at = datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        points.append({"observed_at": observed_at, "value": float(value)})
    return points


def fetch_coingecko_history(
    target: Mapping[str, Any],
    *,
    days: int,
    timeout_seconds: float = 20.0,
) -> tuple[list[dict[str, Any]], str, str, str]:
    symbol = str(target.get("symbol") or "").strip().upper()
    coin_id = COINGECKO_IDS.get(symbol)
    if coin_id is None:
        raise ValueError(f"unsupported CoinGecko target symbol: {symbol}")
    if days < 2 or days > 3650:
        raise ValueError("days must be between 2 and 3650")
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days={days}&interval=daily"
    try:
        response = httpx.get(url, timeout=timeout_seconds, headers={"accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"market history provider failed: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("market history provider returned a non-object")
    return parse_coingecko_history(payload), "coingecko", url, hashlib.sha256(response.content).hexdigest()


def fetch_market_provider_bundle(
    target: Mapping[str, Any],
    *,
    days: int,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Fetch a bounded, auditable market-depth bundle for one target.

    Every provider is best-effort and records its own failure.  A missing
    provider never becomes a zero or a fabricated neutral value.
    """

    target_kind = str(target.get("kind") or "").strip()
    target_symbol = str(target.get("symbol") or "").strip().upper()
    coin_id = COINGECKO_IDS.get(target_symbol)
    if target_kind == "crypto" and coin_id is not None:
        history_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days={days}&interval=daily"
        chart_payload, history_hash = _fetch_json(history_url, timeout_seconds=timeout_seconds)
        points = parse_coingecko_history(chart_payload)
        provider = "coingecko"
    else:
        points, provider, history_url, history_hash = fetch_market_history(
            target, days=days, timeout_seconds=timeout_seconds
        )
    bundle: dict[str, Any] = {
        "points": points,
        "provider": provider,
        "history_url": history_url,
        "history_response_sha256": history_hash,
        "fundamentals": None,
        "peer_valuation": None,
        "provider_data": {},
    }
    if str(target.get("kind") or "").strip() != "crypto":
        bundle["fundamentals"] = fetch_yahoo_fundamentals(
            target,
            timeout_seconds=timeout_seconds,
        )
        bundle["peer_valuation"] = fetch_yahoo_peer_valuation(
            target,
            timeout_seconds=timeout_seconds,
            target_as_of=bundle["fundamentals"].get("as_of") if isinstance(bundle["fundamentals"], Mapping) else None,
        )
        bundle["provider_data"]["volume"] = fetch_yahoo_volume(
            target,
            days=days,
            timeout_seconds=timeout_seconds,
        )
        bundle["provider_data"] = {
            "volume": bundle["provider_data"]["volume"],
            "etf_flows": {"status": "not_applicable", "reason": "target_is_not_crypto"},
            "derivatives": {"status": "not_applicable", "reason": "target_is_not_crypto"},
            "on_chain": {"status": "not_applicable", "reason": "target_is_not_crypto"},
        }
        return bundle

    # The CoinGecko chart response contains the same price series plus volume
    # and market-cap observations, so fetch it once and retain its hash.
    if coin_id is None:
        raise ValueError(f"unsupported CoinGecko target symbol: {target.get('symbol')}")
    chart_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days={days}&interval=daily"
    try:
        chart, chart_hash = _fetch_json(chart_url, timeout_seconds=timeout_seconds)
        volume_points = _parse_series(chart.get("total_volumes"), scale=1.0)
        market_cap_points = _parse_series(chart.get("market_caps"), scale=1.0)
        latest_volume = volume_points[-1] if volume_points else None
        bundle["provider_data"]["volume"] = {
            "status": "available" if latest_volume else "insufficient_data",
            "provider": "coingecko",
            "latest": latest_volume,
            "point_count": len(volume_points),
            "source_ref": {"url": chart_url, "response_sha256": chart_hash},
        }
        bundle["provider_data"]["market_cap"] = {
            "status": "available" if market_cap_points else "insufficient_data",
            "provider": "coingecko",
            "latest": market_cap_points[-1] if market_cap_points else None,
            "point_count": len(market_cap_points),
            "source_ref": {"url": chart_url, "response_sha256": chart_hash},
        }
    except (RuntimeError, ValueError) as exc:
        bundle["provider_data"]["volume"] = {"status": "unavailable", "reason": str(exc)[:500]}

    details_url = (
        f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        "?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false"
    )
    try:
        details, details_hash = _fetch_json(details_url, timeout_seconds=timeout_seconds)
        market_data = details.get("market_data") if isinstance(details, Mapping) else None
        if not isinstance(market_data, Mapping):
            raise RuntimeError("CoinGecko details missing market_data")
        usd = market_data.get("current_price", {}).get("usd") if isinstance(market_data.get("current_price"), Mapping) else None
        market_cap_usd = market_data.get("market_cap", {}).get("usd") if isinstance(market_data.get("market_cap"), Mapping) else None
        total_volume_usd = market_data.get("total_volume", {}).get("usd") if isinstance(market_data.get("total_volume"), Mapping) else None
        required = {
            "circulating_supply": market_data.get("circulating_supply"),
            "total_supply": market_data.get("total_supply"),
            "max_supply": market_data.get("max_supply"),
        }
        if any(not isinstance(value, (int, float)) for value in required.values()):
            raise RuntimeError("CoinGecko tokenomics fields are incomplete")
        bundle["fundamentals"] = {
            "status": "available",
            "provider": "coingecko",
            "kind": "crypto_market_structure",
            **{key: float(value) for key, value in required.items()},
            "price_usd": float(usd) if isinstance(usd, (int, float)) else None,
            "market_cap_usd": float(market_cap_usd) if isinstance(market_cap_usd, (int, float)) else None,
            "total_volume_usd": float(total_volume_usd) if isinstance(total_volume_usd, (int, float)) else None,
            "source_ref": {"url": details_url, "response_sha256": details_hash},
        }
    except (RuntimeError, ValueError) as exc:
        bundle["fundamentals"] = {"status": "unavailable", "missing_reason": str(exc)[:500]}

    bundle["provider_data"]["derivatives"] = _fetch_binance_derivatives(
        str(target.get("symbol") or "BTC").upper(), timeout_seconds=timeout_seconds
    )
    bundle["provider_data"]["on_chain"] = _fetch_blockchain_transactions(timeout_seconds=timeout_seconds)
    bundle["provider_data"]["etf_flows"] = _fetch_theblock_etf_flows(timeout_seconds=timeout_seconds)
    return bundle


def _fetch_json(url: str, *, timeout_seconds: float) -> tuple[Any, str]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = httpx.get(
                url,
                timeout=timeout_seconds,
                headers={"accept": "application/json", "user-agent": "finance-crawler-validation/1.0"},
            )
            if response.status_code == 429 and attempt < 2:
                retry_after = response.headers.get("retry-after")
                try:
                    delay = min(5.0, max(0.5, float(retry_after or "1")))
                except ValueError:
                    delay = 1.0
                time.sleep(delay)
                continue
            response.raise_for_status()
            payload = response.json()
            return payload, hashlib.sha256(response.content).hexdigest()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2 and not isinstance(exc, httpx.HTTPStatusError):
                time.sleep(0.5 * (attempt + 1))
                continue
            break
    raise RuntimeError(f"provider failed: {last_error}") from last_error


def _parse_series(value: Any, *, scale: float) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    from datetime import datetime, timezone
    parsed: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, list) or len(row) < 2:
            continue
        timestamp, number = row[0], row[1]
        if not isinstance(timestamp, (int, float)) or not isinstance(number, (int, float)):
            continue
        observed_at = datetime.fromtimestamp(float(timestamp) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        parsed.append({"observed_at": observed_at, "value": float(number) * scale})
    return parsed


def _fetch_binance_derivatives(symbol: str, *, timeout_seconds: float) -> dict[str, Any]:
    pair = f"{symbol}USDT"
    funding_url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={pair}&limit=30"
    open_interest_url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={pair}"
    ticker_url = f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={pair}"
    try:
        funding_payload, funding_hash = _fetch_json(funding_url, timeout_seconds=timeout_seconds)
        open_interest_payload, open_interest_hash = _fetch_json(open_interest_url, timeout_seconds=timeout_seconds)
        ticker_payload, ticker_hash = _fetch_json(ticker_url, timeout_seconds=timeout_seconds)
        funding_rows = funding_payload if isinstance(funding_payload, list) else []
        latest_funding = funding_rows[-1] if funding_rows else None
        open_interest = open_interest_payload.get("openInterest")
        quote_volume = ticker_payload.get("quoteVolume")
        if latest_funding is None or not isinstance(open_interest, str) or not isinstance(quote_volume, str):
            raise RuntimeError("Binance derivatives response missing funding/open-interest/volume")
        return {
            "status": "available",
            "provider": "binance_futures_public",
            "symbol": pair,
            "latest_funding_rate": float(latest_funding.get("fundingRate")),
            "funding_observed_at": _epoch_ms_to_iso(latest_funding.get("fundingTime")),
            "funding_point_count": len(funding_rows),
            "open_interest_contracts": float(open_interest),
            "quote_volume_24h_usd": float(quote_volume),
            "source_refs": [
                {"url": funding_url, "response_sha256": funding_hash},
                {"url": open_interest_url, "response_sha256": open_interest_hash},
                {"url": ticker_url, "response_sha256": ticker_hash},
            ],
        }
    except (RuntimeError, ValueError, TypeError) as exc:
        return {"status": "unavailable", "provider": "binance_futures_public", "reason": str(exc)[:500]}


def _fetch_blockchain_transactions(*, timeout_seconds: float) -> dict[str, Any]:
    url = "https://api.blockchain.info/charts/n-transactions?timespan=30days&format=json"
    try:
        payload, response_hash = _fetch_json(url, timeout_seconds=timeout_seconds)
        values = payload.get("values") if isinstance(payload, Mapping) else None
        latest = values[-1] if isinstance(values, list) and values else None
        if not isinstance(latest, Mapping) or not isinstance(latest.get("y"), (int, float)):
            raise RuntimeError("Blockchain.com response missing transaction values")
        return {
            "status": "available",
            "provider": "blockchain_com_charts",
            "metric": "confirmed_transactions_per_day",
            "latest_value": float(latest["y"]),
            "observed_at": _epoch_seconds_to_iso(latest.get("x")),
            "point_count": len(values),
            "source_ref": {"url": url, "response_sha256": response_hash},
        }
    except (RuntimeError, ValueError, TypeError) as exc:
        return {"status": "unavailable", "provider": "blockchain_com_charts", "reason": str(exc)[:500]}


def _fetch_theblock_etf_flows(*, timeout_seconds: float) -> dict[str, Any]:
    url = "https://data.tbstat.com/dashboard/markets_structuredproducts_btcspotetfflows_daily_other.json"
    try:
        payload, response_hash = _fetch_json(url, timeout_seconds=timeout_seconds)
        series = payload.get("Series") if isinstance(payload, Mapping) else None
        if not isinstance(series, Mapping):
            raise RuntimeError("The Block ETF response missing Series")
        by_timestamp: dict[int, float] = {}
        for values in series.values():
            rows = values.get("Data") if isinstance(values, Mapping) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, Mapping) and isinstance(row.get("Timestamp"), (int, float)) and isinstance(row.get("Result"), (int, float)):
                    timestamp = int(row["Timestamp"])
                    by_timestamp[timestamp] = by_timestamp.get(timestamp, 0.0) + float(row["Result"])
        if not by_timestamp:
            raise RuntimeError("The Block ETF response contains no flow observations")
        timestamp = max(by_timestamp)
        return {
            "status": "available",
            "provider": "theblock_tbstat_public_json",
            "latest_net_flow_usd": round(by_timestamp[timestamp], 2),
            "observed_at": _epoch_seconds_to_iso(timestamp),
            "point_count": len(by_timestamp),
            "series_count": len(series),
            "source_ref": {"url": url, "response_sha256": response_hash},
        }
    except (RuntimeError, ValueError, TypeError) as exc:
        return {"status": "unavailable", "provider": "theblock_tbstat_public_json", "reason": str(exc)[:500]}


def _epoch_ms_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch_seconds_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_yahoo_history(
    target: Mapping[str, Any],
    *,
    days: int,
    timeout_seconds: float = 20.0,
) -> tuple[list[dict[str, Any]], str, str, str]:
    symbol = str(target.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("Yahoo target symbol is required")
    if days < 2 or days > 3650:
        raise ValueError("days must be between 2 and 3650")
    # Yahoo's period1/period2 chart route intermittently returns 429 for
    # unauthenticated public clients.  The range route is equivalent for the
    # bounded research windows we support and is materially more reliable.
    if days <= 365:
        range_value = "1y"
    elif days <= 730:
        range_value = "2y"
    elif days <= 1825:
        range_value = "5y"
    else:
        range_value = "10y"
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_value}&interval=1d&events=history"
    )
    try:
        response = httpx.get(
            url,
            timeout=timeout_seconds,
            headers={
                "accept": "application/json",
                "user-agent": "finance-crawler-validation/1.0",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Yahoo market history provider failed: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("Yahoo market history provider returned a non-object")
    results = payload.get("chart", {}).get("result") if isinstance(payload.get("chart"), Mapping) else None
    if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
        raise RuntimeError("Yahoo response missing chart result")
    result = results[0]
    timestamps = result.get("timestamp")
    quote = result.get("indicators", {}).get("quote") if isinstance(result.get("indicators"), Mapping) else None
    closes = quote[0].get("close") if isinstance(quote, list) and quote and isinstance(quote[0], Mapping) else None
    if not isinstance(timestamps, list) or not isinstance(closes, list):
        raise RuntimeError("Yahoo response missing timestamps or close values")
    from datetime import datetime, timezone
    points: list[dict[str, Any]] = []
    for timestamp, close in zip(timestamps, closes):
        if not isinstance(timestamp, (int, float)) or not isinstance(close, (int, float)):
            continue
        points.append({
            "observed_at": datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "value": float(close),
        })
    if not points:
        raise RuntimeError("Yahoo response contained no valid close values")
    return points, "yahoo_finance", url, hashlib.sha256(response.content).hexdigest()


_YAHOO_FUNDAMENTAL_METRICS: dict[str, str] = {
    "eps": "annualDilutedEPS",
    "revenue": "annualTotalRevenue",
    # Optional denominator for a target-scoped price-to-sales cross-check
    # when earnings are negative and P/E is not an applicable method.
    "shares": "annualDilutedAverageShares",
    "total_debt": "annualTotalDebt",
    "cash": "annualCashAndCashEquivalents",
}


def parse_yahoo_fundamentals(
    payload: Mapping[str, Any],
    *,
    symbol: str,
    source_ref: Mapping[str, Any],
    as_of_cutoff: str | None = None,
) -> dict[str, Any]:
    """Normalize Yahoo's public fundamentals-timeseries response.

    Yahoo returns one result object per requested metric and nests the actual
    observations below a metric-specific key.  The parser keeps the latest
    annual observation for each field, records each field's as-of date, and
    computes net debt only when both debt and cash are present.  Missing
    metrics remain explicit; they are never replaced with zero.
    """

    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("Yahoo fundamentals symbol is required")
    timeseries = payload.get("timeseries")
    results = timeseries.get("result") if isinstance(timeseries, Mapping) else None
    if not isinstance(results, list):
        raise ValueError("Yahoo fundamentals response missing timeseries result")
    error = timeseries.get("error") if isinstance(timeseries, Mapping) else None
    if error not in (None, {}):
        raise ValueError(f"Yahoo fundamentals response returned error: {error}")

    cutoff_date = None
    if as_of_cutoff is not None:
        try:
            cutoff_date = datetime.strptime(str(as_of_cutoff).strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("as_of_cutoff must use YYYY-MM-DD") from exc

    latest: dict[str, tuple[str, float, str | None]] = {}
    for field, metric_name in _YAHOO_FUNDAMENTAL_METRICS.items():
        observations: list[tuple[str, float, str | None]] = []
        for result in results:
            if not isinstance(result, Mapping):
                continue
            rows = result.get(metric_name)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                as_of = row.get("asOfDate")
                reported = row.get("reportedValue")
                raw = reported.get("raw") if isinstance(reported, Mapping) else None
                if not isinstance(as_of, str) or not as_of.strip():
                    continue
                if cutoff_date is not None:
                    try:
                        if datetime.strptime(as_of.strip(), "%Y-%m-%d").date() > cutoff_date:
                            continue
                    except ValueError:
                        continue
                if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
                    continue
                currency = row.get("currencyCode")
                observations.append((as_of.strip(), float(raw), str(currency).strip().upper() if currency else None))
        if observations:
            observations.sort(key=lambda item: item[0])
            latest[field] = observations[-1]

    values: dict[str, Any] = {
        "eps": latest.get("eps", (None, None, None))[1],
        "revenue": latest.get("revenue", (None, None, None))[1],
        "shares": latest.get("shares", (None, None, None))[1],
        "total_debt": latest.get("total_debt", (None, None, None))[1],
        "cash": latest.get("cash", (None, None, None))[1],
    }
    if values["total_debt"] is not None and values["cash"] is not None:
        values["net_debt"] = float(values["total_debt"]) - float(values["cash"])
    else:
        values["net_debt"] = None

    required = ("eps", "revenue", "total_debt", "cash", "net_debt")
    missing_fields = [field for field in required if values.get(field) is None]
    as_of_dates = [latest[field][0] for field in latest]
    currencies = [latest[field][2] for field in latest if latest[field][2]]
    currency = currencies[0] if currencies and len(set(currencies)) == 1 else (currencies[0] if currencies else None)
    return {
        "status": "available" if not missing_fields else "insufficient_data",
        "provider": "yahoo_finance_fundamentals",
        "symbol": normalized_symbol,
        "as_of": max(as_of_dates) if as_of_dates else None,
        "currency": currency,
        **values,
        "field_as_of": {field: latest[field][0] for field in latest},
        "missing_fields": missing_fields,
        "missing_reason": None if not missing_fields else "required_fundamental_fields_missing",
        "source_ref": dict(source_ref),
    }


def fetch_yahoo_fundamentals(
    target: Mapping[str, Any],
    *,
    timeout_seconds: float = 20.0,
    as_of_cutoff: str | None = None,
) -> dict[str, Any]:
    """Fetch annual EPS/revenue/debt/cash from Yahoo's public timeseries API."""

    symbol = str(target.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("Yahoo target symbol is required")
    encoded_symbol = quote(symbol, safe=".^=-")
    period2 = int(time.time()) + 86_400
    period1 = period2 - (5 * 365 * 86_400)
    metric_types = ",".join(_YAHOO_FUNDAMENTAL_METRICS.values())
    # Keep the runtime request time-bounded, but publish a deterministic full
    # API URL as the citation target.  The raw request URL remains in
    # ``source_ref.url``; the PDF renderer uses a semantic label for long query
    # URLs so readers never copy a truncated path such as ``fundamentals-time``.
    citation_url = (
        "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
        f"{encoded_symbol}?symbol={encoded_symbol}&type={metric_types}"
    )
    url = (
        "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
        f"{encoded_symbol}?symbol={encoded_symbol}&type={metric_types}"
        f"&period1={period1}&period2={period2}"
    )
    try:
        payload, response_hash = _fetch_json(url, timeout_seconds=timeout_seconds)
        if not isinstance(payload, Mapping):
            raise RuntimeError("Yahoo fundamentals provider returned a non-object")
        return parse_yahoo_fundamentals(
            payload,
            symbol=symbol,
            source_ref={"url": url, "citation_url": citation_url, "response_sha256": response_hash},
            as_of_cutoff=as_of_cutoff,
        )
    except (RuntimeError, ValueError, TypeError) as exc:
        return {
            "status": "unavailable",
            "provider": "yahoo_finance_fundamentals",
            "symbol": symbol,
            "missing_fields": ["eps", "revenue", "total_debt", "cash", "net_debt"],
            "missing_reason": str(exc)[:500],
            "source_ref": {"url": url, "citation_url": citation_url},
        }


def fetch_yahoo_volume(
    target: Mapping[str, Any],
    *,
    days: int,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Fetch the latest Yahoo daily volume observation for non-crypto targets."""

    symbol = str(target.get("symbol") or "").strip().upper()
    if not symbol:
        return {"status": "unavailable", "provider": "yahoo_finance", "reason": "symbol_required"}
    if days < 2 or days > 3650:
        raise ValueError("days must be between 2 and 3650")
    if days <= 365:
        range_value = "1y"
    elif days <= 730:
        range_value = "2y"
    elif days <= 1825:
        range_value = "5y"
    else:
        range_value = "10y"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='.^=-')}?range={range_value}&interval=1d&events=history"
    try:
        payload, response_hash = _fetch_json(url, timeout_seconds=timeout_seconds)
        result = payload.get("chart", {}).get("result") if isinstance(payload, Mapping) and isinstance(payload.get("chart"), Mapping) else None
        if not isinstance(result, list) or not result or not isinstance(result[0], Mapping):
            raise RuntimeError("Yahoo response missing chart result")
        chart = result[0]
        timestamps = chart.get("timestamp")
        quote_data = chart.get("indicators", {}).get("quote") if isinstance(chart.get("indicators"), Mapping) else None
        volumes = quote_data[0].get("volume") if isinstance(quote_data, list) and quote_data and isinstance(quote_data[0], Mapping) else None
        if not isinstance(timestamps, list) or not isinstance(volumes, list):
            raise RuntimeError("Yahoo response missing volume series")
        points: list[dict[str, Any]] = []
        from datetime import datetime, timezone
        for timestamp, volume in zip(timestamps, volumes):
            if isinstance(timestamp, (int, float)) and isinstance(volume, (int, float)) and not isinstance(volume, bool) and math.isfinite(float(volume)) and float(volume) >= 0:
                points.append({
                    "observed_at": datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                    "value": float(volume),
                })
        if not points:
            raise RuntimeError("Yahoo response contained no valid volume observations")
        return {
            "status": "available",
            "provider": "yahoo_finance",
            "symbol": symbol,
            "metric": "daily_volume",
            "latest": points[-1],
            "point_count": len(points),
            "source_ref": {"url": url, "response_sha256": response_hash},
        }
    except (RuntimeError, ValueError, TypeError) as exc:
        return {"status": "unavailable", "provider": "yahoo_finance", "symbol": symbol, "reason": str(exc)[:500], "source_ref": {"url": url}}


def fetch_yahoo_peer_valuation(
    target: Mapping[str, Any],
    *,
    timeout_seconds: float = 20.0,
    target_as_of: str | None = None,
) -> dict[str, Any]:
    """Calculate a transparent peer-median input from a common fiscal-year label."""

    symbol = str(target.get("symbol") or "").strip().upper()
    selection_rule = str(target.get("peer_selection_rule") or "curated_semiconductor_peers_v1")
    configured = target.get("peer_symbols")
    if isinstance(configured, list):
        peer_symbols = tuple(str(value).strip().upper() for value in configured if isinstance(value, str) and value.strip())
    else:
        peer_symbols = EQUITY_PEER_SYMBOLS.get(symbol, ())
    if not peer_symbols:
        return {
            "status": "insufficient_data",
            "provider": "yahoo_finance_peer_multiples",
            "selection_rule": "no_configured_peer_set",
            "peer_set": [],
            "missing_reason": "peer_set_not_configured",
        }
    peer_set: list[dict[str, Any]] = []
    multiples: list[float] = []
    for peer_symbol in peer_symbols:
        fundamentals_kwargs: dict[str, Any] = {
            "timeout_seconds": timeout_seconds,
        }
        if target_as_of is not None:
            fundamentals_kwargs["as_of_cutoff"] = target_as_of
        fundamentals = fetch_yahoo_fundamentals(
            {"kind": "equity", "symbol": peer_symbol},
            **fundamentals_kwargs,
        )
        record: dict[str, Any] = {
            "symbol": peer_symbol,
            "fundamentals_status": fundamentals.get("status"),
            "eps": fundamentals.get("eps"),
            "revenue": fundamentals.get("revenue"),
            "shares": fundamentals.get("shares"),
            "as_of": fundamentals.get("as_of"),
            "period_key": str(fundamentals.get("as_of") or "")[:4] or None,
            "currency": fundamentals.get("currency"),
            "price_currency": _yahoo_symbol_currency(peer_symbol),
            "source_refs": [fundamentals.get("source_ref")] if fundamentals.get("source_ref") else [],
        }
        try:
            points, provider, history_url, history_hash = fetch_yahoo_history(
                {"kind": "equity", "symbol": peer_symbol},
                days=30,
                timeout_seconds=timeout_seconds,
            )
            price = points[-1]["value"] if points else None
            record.update({
                "price": price,
                "price_as_of": points[-1].get("observed_at") if points else None,
                "price_provider": provider,
                "source_refs": [*record["source_refs"], {"url": history_url, "response_sha256": history_hash}],
            })
        except (RuntimeError, ValueError, TypeError) as exc:
            record["price_error"] = str(exc)[:500]
            price = None
        eps = record.get("eps")
        record["currency_alignment_status"] = (
            "aligned"
            if record.get("currency") and record.get("price_currency") == record.get("currency")
            else "mismatch" if record.get("currency") and record.get("price_currency") else "unresolved"
        )
        if (
            isinstance(price, (int, float)) and not isinstance(price, bool)
            and isinstance(eps, (int, float)) and not isinstance(eps, bool)
            and math.isfinite(float(price)) and math.isfinite(float(eps))
            and float(price) > 0 and float(eps) > 0
            and record["currency_alignment_status"] == "aligned"
        ):
            multiple = round(float(price) / float(eps), 6)
            record["trailing_pe"] = multiple
            multiples.append(multiple)
        else:
            record["trailing_pe"] = None
        revenue = record.get("revenue")
        shares = record.get("shares")
        if (
            isinstance(price, (int, float)) and not isinstance(price, bool)
            and isinstance(revenue, (int, float)) and not isinstance(revenue, bool)
            and isinstance(shares, (int, float)) and not isinstance(shares, bool)
            and math.isfinite(float(price)) and math.isfinite(float(revenue)) and math.isfinite(float(shares))
            and float(price) > 0 and float(revenue) > 0 and float(shares) > 0
            and record["currency_alignment_status"] == "aligned"
        ):
            record["trailing_ps"] = round(float(price) * float(shares) / float(revenue), 6)
        else:
            record["trailing_ps"] = None
        peer_set.append(record)
    ps_multiples = [float(row["trailing_ps"]) for row in peer_set if isinstance(row.get("trailing_ps"), (int, float))]
    if len(multiples) < 3 and len(ps_multiples) >= 3:
        target_period_key = str(target_as_of or "")[:4] or None
        usable_ps_rows = [row for row in peer_set if row.get("trailing_ps") is not None]
        period_alignment_status = (
            "aligned"
            if target_period_key and usable_ps_rows and all(row.get("period_key") == target_period_key for row in usable_ps_rows)
            else "unresolved"
        )
        return {
            "status": "available",
            "provider": "yahoo_finance_peer_multiples",
            "selection_rule": selection_rule,
            "peer_set": peer_set,
            "usable_peer_count": len(ps_multiples),
            "usable_ps_count": len(ps_multiples),
            "median_ps": round(float(median(ps_multiples)), 6),
            "multiple_basis": "trailing_ps",
            "period_alignment_status": period_alignment_status,
            "period_alignment_basis": "fiscal_year_label" if target_period_key else "not_provided",
            "target_period_key": target_period_key,
            "assumptions": {
                "selection_rule": selection_rule,
                "multiple": "trailing_ps",
                "minimum_usable_peers": 3,
                "not_a_forecast": True,
                "applicability": "positive_revenue_with_non_positive_or_unavailable_eps",
            },
        }
    if len(multiples) < 3:
        return {
            "status": "insufficient_data",
            "provider": "yahoo_finance_peer_multiples",
            "selection_rule": selection_rule,
            "peer_set": peer_set,
            "usable_peer_count": len(multiples),
            "usable_ps_count": len(ps_multiples),
            "period_alignment_status": "unresolved",
            "missing_reason": "at_least_three_positive_peer_multiples_required",
        }
    target_period_key = str(target_as_of or "")[:4] or None
    usable_peer_rows = [row for row in peer_set if row.get("trailing_pe") is not None]
    period_alignment_status = (
        "aligned"
        if target_period_key and usable_peer_rows and all(row.get("period_key") == target_period_key for row in usable_peer_rows)
        else "unresolved"
    )
    return {
        "status": "available",
        "provider": "yahoo_finance_peer_multiples",
        "selection_rule": selection_rule,
        "peer_set": peer_set,
        "usable_peer_count": len(multiples),
        "median_pe": round(float(median(multiples)), 6),
        "usable_ps_count": len(ps_multiples),
        "median_ps": round(float(median(ps_multiples)), 6) if len(ps_multiples) >= 3 else None,
        "multiple_basis": "trailing_pe",
        "period_alignment_status": period_alignment_status,
        "period_alignment_basis": "fiscal_year_label" if target_period_key else "not_provided",
        "target_period_key": target_period_key,
        "assumptions": {
            "selection_rule": selection_rule,
            "multiple": "trailing_pe",
            "minimum_usable_peers": 3,
            "not_a_forecast": True,
        },
    }


def _yahoo_symbol_currency(symbol: str) -> str | None:
    """Return the quote currency implied by common Yahoo exchange suffixes."""

    normalized = symbol.strip().upper()
    suffixes = {
        ".TW": "TWD", ".TWO": "TWD", ".HK": "HKD", ".KS": "KRW",
        ".KQ": "KRW", ".AS": "EUR", ".DE": "EUR", ".L": "GBP",
        ".T": "JPY", ".SS": "CNY", ".SZ": "CNY",
    }
    for suffix, currency in suffixes.items():
        if normalized.endswith(suffix):
            return currency
    return "USD" if "." not in normalized else None


def fetch_market_history(
    target: Mapping[str, Any],
    *,
    days: int,
    timeout_seconds: float = 20.0,
) -> tuple[list[dict[str, Any]], str, str, str]:
    kind = str(target.get("kind") or "").strip()
    if kind == "crypto":
        return fetch_coingecko_history(target, days=days, timeout_seconds=timeout_seconds)
    if kind in {"equity", "etf", "company"}:
        return fetch_yahoo_history(target, days=days, timeout_seconds=timeout_seconds)
    raise ValueError(f"market history provider does not support target kind: {kind}")
