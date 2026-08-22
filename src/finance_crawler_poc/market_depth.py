from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

from finance_crawler_poc.professional_analysis import (
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
    conflict = build_source_conflict_report(evidence, topic_id="target")
    if (
        time_series["status"] == "available"
        and valuation["status"] in {"available", "not_applicable"}
        and conflict["method"] == "calibrated_stance_v1"
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
        "fundamentals": {"status": "unavailable", "missing_reason": "provider_not_configured"},
        "valuation": valuation,
        "scenarios": scenarios,
        "source_conflicts": [conflict],
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
