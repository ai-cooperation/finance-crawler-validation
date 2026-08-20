from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from finance_crawler_poc.contracts import validate_contract


_CRYPTO_TOPIC_IDS = frozenset({"digital_assets"})
_SEMICONDUCTOR_SYMBOLS = frozenset({"AMD", "ASML", "AVGO", "INTC", "MU", "NVDA", "QCOM", "TSM"})
_EQUITY_TOPIC_IDS = frozenset({"equities_earnings", "market_risk"})


def build_market_snapshot(
    items: Iterable[Mapping[str, Any]],
    *,
    snapshot_id: str,
    as_of: str,
    provider: str,
) -> dict[str, Any]:
    """Normalize collected market evidence into the OpenBB-facing contract.

    This adapter intentionally accepts the collector's raw market items and does
    not import OpenBB at collection time.  The resulting shape is provider
    neutral and can be populated by an OpenBB provider later without changing
    the topic/agent boundary.
    """

    if not provider.strip():
        raise ValueError("provider is required")
    _parse_datetime(as_of, "as_of")
    instruments_by_symbol: dict[str, dict[str, Any]] = {}
    seen_market_item = False
    for raw_item in items:
        if raw_item.get("kind") != "market_data":
            continue
        seen_market_item = True
        item_id = _sha256(raw_item.get("item_id"), "item_id")
        content = raw_item.get("content")
        if not isinstance(content, str):
            raise ValueError(f"invalid market item {item_id}: content must be JSON text")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid market item {item_id}: content is not JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"invalid market item {item_id}: content must be an object")
        symbol = str(payload.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError(f"invalid market item {item_id}: symbol is required")
        price = _number_or_none(payload.get("current_price"), "current_price", item_id)
        change = _number_or_none(
            payload.get("price_change_percentage_24h"),
            "price_change_percentage_24h",
            item_id,
        )
        market_cap = _number_or_none(payload.get("market_cap"), "market_cap", item_id)
        observed_at = _first_datetime(
            payload.get("last_updated"),
            raw_item.get("published_at"),
            raw_item.get("collected_at"),
        )
        existing = instruments_by_symbol.get(symbol)
        if existing is None:
            instruments_by_symbol[symbol] = {
                "symbol": symbol,
                "asset_type": "crypto",
                "currency": "USD",
                "price": price,
                "observed_at": observed_at,
                "change_24h_pct": change,
                "market_cap": market_cap,
                "source_item_ids": [item_id],
            }
            continue
        existing["source_item_ids"] = sorted({*existing["source_item_ids"], item_id})
        if observed_at > existing["observed_at"]:
            existing.update(
                price=price,
                observed_at=observed_at,
                change_24h_pct=change,
                market_cap=market_cap,
            )
    if not seen_market_item or not instruments_by_symbol:
        raise ValueError("no market data items")
    snapshot = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "as_of": as_of,
        "provider": provider.strip(),
        "instruments": [instruments_by_symbol[key] for key in sorted(instruments_by_symbol)],
    }
    validate_contract("market-snapshot", snapshot)
    return snapshot


def build_topic_market_alignment(
    topic_snapshot: Mapping[str, Any],
    market_snapshot: Mapping[str, Any],
    *,
    alignment_id: str,
    generated_at: str,
) -> dict[str, Any]:
    """Attach market coverage and direction to each ranked topic.

    Direction is descriptive (positive/negative/mixed), not a trading signal.
    No causal or bullish/bearish claim is made because the topic radar does not
    contain a market stance oracle.
    """

    validate_contract("topic-snapshot", dict(topic_snapshot))
    validate_contract("market-snapshot", dict(market_snapshot))
    _parse_datetime(generated_at, "generated_at")
    instruments = list(market_snapshot["instruments"])
    aligned_topics: list[dict[str, Any]] = []
    covered_count = 0
    for topic in topic_snapshot["topics"]:
        topic_id = topic["topic_id"]
        matching = [instrument for instrument in instruments if _matches_topic(topic_id, instrument)]
        if matching:
            covered_count += 1
        changes = [instrument["change_24h_pct"] for instrument in matching if instrument["change_24h_pct"] is not None]
        mean_change = round(sum(changes) / len(changes), 6) if changes else None
        if mean_change is None:
            direction = "not_covered"
        elif mean_change > 0.05 and all(change >= -0.05 for change in changes):
            direction = "positive"
        elif mean_change < -0.05 and all(change <= 0.05 for change in changes):
            direction = "negative"
        else:
            direction = "mixed"
        evidence_ids = set(topic["evidence_ids"])
        for instrument in matching:
            evidence_ids.update(instrument["source_item_ids"])
        aligned_topics.append(
            {
                "topic_id": topic_id,
                "label": topic["label"],
                "topic_score": topic["score"],
                "market_direction": direction,
                "instrument_count": len(matching),
                "symbols": sorted(instrument["symbol"] for instrument in matching),
                "mean_change_24h_pct": mean_change,
                "evidence_ids": sorted(evidence_ids),
            }
        )
    alignment = {
        "schema_version": 1,
        "alignment_id": alignment_id,
        "topic_snapshot_id": topic_snapshot["snapshot_id"],
        "market_snapshot_id": market_snapshot["snapshot_id"],
        "generated_at": generated_at,
        "partial": bool(topic_snapshot["partial"] or covered_count < len(aligned_topics)),
        "coverage_ratio": round(covered_count / len(aligned_topics), 6) if aligned_topics else 0,
        "topics": aligned_topics,
    }
    validate_contract("market-topic-alignment", alignment)
    return alignment


def _matches_topic(topic_id: str, instrument: Mapping[str, Any]) -> bool:
    asset_type = instrument["asset_type"]
    symbol = instrument["symbol"]
    if topic_id in _CRYPTO_TOPIC_IDS:
        return asset_type == "crypto"
    if topic_id == "ai_semiconductors":
        return symbol in _SEMICONDUCTOR_SYMBOLS
    if topic_id in _EQUITY_TOPIC_IDS:
        return asset_type == "equity"
    return False


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"invalid market item: {name} must be a lowercase SHA-256 digest")
    return value


def _number_or_none(value: Any, name: str, item_id: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid market item {item_id}: {name} must be numeric or null")
    return float(value)


def _first_datetime(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            _parse_datetime(value, "observed_at")
            return value
    raise ValueError("invalid market item: observed_at is required")


def _parse_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 datetime")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed
