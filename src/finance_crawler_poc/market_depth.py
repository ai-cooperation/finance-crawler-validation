from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

from finance_crawler_poc.professional_analysis import (
    build_market_driver_snapshot,
    build_scenario_analysis,
    build_source_conflict_report,
    build_time_series_snapshot,
    build_valuation_snapshot,
)


COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
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
    history_response_sha256: str | None = None,
    provider_data: Mapping[str, Any] | None = None,
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
    time_series = build_time_series_snapshot(
        points,
        series_id=symbol or "TARGET",
        provider=history_provider,
        as_of=as_of,
        source_item_ids=[],
    )
    time_series["source_ref"] = {
        "url": history_url,
        "response_sha256": history_response_sha256 or normalized_hash,
        "hash_kind": "provider_response" if history_response_sha256 else "normalized_points",
    }
    valuation = build_valuation_snapshot(
        target,
        fundamentals=fundamentals,
        market_price=current_price if isinstance(current_price, (int, float)) else None,
        source_item_ids=instrument.get("source_item_ids", []) if isinstance(instrument, Mapping) else [],
    )
    scenarios = build_scenario_analysis(
        time_series,
        current_price=current_price if isinstance(current_price, (int, float)) else None,
        horizon="observed_window",
    )
    evidence_list = list(evidence)
    conflict = build_source_conflict_report(evidence_list, topic_id="target")
    market_drivers = build_market_driver_snapshot(
        target=target,
        market_snapshot=market_snapshot,
        time_series=time_series,
        evidence=evidence_list,
        provider_data=provider_data,
    )
    conflict_ready = bool(
        conflict.get("calibration_status") == "calibrated"
        and conflict.get("status") == "available"
        and int(conflict.get("independent_source_count") or 0) >= 2
    )
    driver_ready = market_drivers.get("status") == "available"
    if (
        time_series["status"] == "available"
        and valuation["status"] in {"available", "not_applicable"}
        and conflict_ready
        and driver_ready
    ):
        status = "professional_ready"
    elif time_series["status"] == "available":
        status = "professional_partial"
    elif time_series["status"] == "insufficient_data":
        status = "research_only"
    else:
        status = "professional_partial"
    return {
        "schema_version": 1,
        "status": status,
        "time_series": time_series,
        "fundamentals": dict(fundamentals) if fundamentals is not None else {"status": "unavailable", "missing_reason": "provider_not_configured"},
        "valuation": valuation,
        "scenarios": scenarios,
        "source_conflicts": [conflict],
        "market_drivers": market_drivers,
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
        "provider_data": {},
    }
    if str(target.get("kind") or "").strip() != "crypto":
        bundle["provider_data"] = {
            "volume": {"status": "unavailable", "reason": "provider_not_configured"},
            "etf_flows": {"status": "not_applicable", "reason": "target_is_not_crypto"},
            "derivatives": {"status": "unavailable", "reason": "provider_not_configured"},
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
    period2 = int(time.time())
    period1 = period2 - days * 24 * 60 * 60
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history"
    )
    try:
        response = httpx.get(url, timeout=timeout_seconds, headers={"accept": "application/json"})
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
