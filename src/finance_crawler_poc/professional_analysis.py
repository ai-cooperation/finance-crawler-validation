"""Deterministic financial-depth calculations for professional research packs.

This module deliberately does not forecast prices or infer an investment
recommendation.  It turns observed provider data into traceable calculations
and records when a requested calculation is not applicable or lacks inputs.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from statistics import pstdev
from typing import Any, Iterable, Mapping


POSITIVE_TERMS = frozenset({
    "rise", "rises", "rally", "rallies", "surge", "surges", "gain", "gains",
    "strong", "growth", "bullish", "positive", "record", "beats", "demand",
})
NEGATIVE_TERMS = frozenset({
    "fall", "falls", "drop", "drops", "decline", "declines", "risk", "risks",
    "bearish", "negative", "loss", "losses", "weak", "warning", "crash",
})


def build_time_series_snapshot(
    points: Iterable[Mapping[str, Any]],
    *,
    series_id: str,
    provider: str,
    as_of: str,
    source_item_ids: Iterable[str],
) -> dict[str, Any]:
    """Normalize historical observations and calculate descriptive metrics."""

    if not series_id.strip() or not provider.strip():
        raise ValueError("series_id and provider are required")
    as_of_dt = _parse_datetime(as_of, "as_of")
    by_timestamp: dict[str, float] = {}
    for point in points:
        if not isinstance(point, Mapping):
            raise ValueError("time-series point must be an object")
        observed_at = _canonical_datetime(point.get("observed_at"), "observed_at")
        value = point.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("time-series value must be finite")
        if float(value) <= 0:
            raise ValueError("time-series value must be positive")
        previous = by_timestamp.get(observed_at)
        if previous is not None and previous != float(value):
            raise ValueError(f"conflicting duplicate observation: {observed_at}")
        by_timestamp[observed_at] = float(value)

    ordered = [
        {"observed_at": timestamp, "value": value}
        for timestamp, value in sorted(by_timestamp.items())
    ]
    values = [point["value"] for point in ordered]
    status = "available" if len(values) >= 2 else "insufficient_data"
    returns: dict[str, float | None] = {
        "observed_pct": _pct_change(values[0], values[-1]) if len(values) >= 2 else None,
    }
    daily_returns = [
        (current / previous) - 1
        for previous, current in zip(values, values[1:])
        if previous > 0
    ]
    volatility = round(pstdev(daily_returns) * math.sqrt(365) * 100, 6) if len(daily_returns) >= 2 else None
    max_drawdown = _max_drawdown(values)
    response = {
        "schema_version": 1,
        "status": status,
        "series_id": series_id.strip().upper(),
        "provider": provider.strip(),
        "currency": "USD",
        "as_of": as_of_dt.isoformat().replace("+00:00", "Z"),
        "window_start": ordered[0]["observed_at"] if ordered else None,
        "window_end": ordered[-1]["observed_at"] if ordered else None,
        "point_count": len(ordered),
        "points": ordered,
        "returns": returns,
        "volatility_annualized_pct": volatility,
        "max_drawdown_pct": max_drawdown,
        "source_item_ids": _unique_ids(source_item_ids),
        "missing_reason": None if status == "available" else "at_least_two_points_required",
    }
    return response


def build_valuation_snapshot(
    target: Mapping[str, Any],
    *,
    fundamentals: Mapping[str, Any] | None,
    market_price: float | None,
    source_item_ids: Iterable[str],
) -> dict[str, Any]:
    """Build a conservative valuation status without silently imputing data."""

    target_kind = str(target.get("kind") or "").strip()
    common = {
        "schema_version": 1,
        "target": dict(target),
        "market_price": float(market_price) if isinstance(market_price, (int, float)) else None,
        "source_item_ids": _unique_ids(source_item_ids),
        "assumptions": {},
        "peer_set": [],
        "implied_value": None,
    }
    if target_kind == "crypto":
        return {
            **common,
            "status": "not_applicable",
            "method": None,
            "missing_fields": [],
            "reason": "intrinsic_valuation_method_not_authorized_for_crypto_without_tokenomics_inputs",
        }
    required = ("eps", "revenue", "net_debt")
    if fundamentals is None:
        missing = list(required)
    else:
        missing = [field for field in required if fundamentals.get(field) is None]
    if missing:
        return {
            **common,
            "status": "insufficient_data",
            "method": "fundamental_multiples",
            "missing_fields": missing,
            "reason": "required_fundamental_fields_missing",
        }
    # No multiple is invented here.  A future peer provider must supply it as
    # an explicit assumption before an implied value can be published.
    return {
        **common,
        "status": "insufficient_data",
        "method": "fundamental_multiples",
        "missing_fields": ["peer_median_pe"],
        "reason": "peer_multiple_required",
    }


def build_scenario_analysis(
    time_series: Mapping[str, Any],
    *,
    current_price: float | None,
    horizon: str,
) -> dict[str, Any]:
    """Return an observed-range scenario table explicitly marked non-forecast."""

    points = time_series.get("points")
    values = [float(point["value"]) for point in points] if isinstance(points, list) else []
    if not values or current_price is None:
        return {
            "schema_version": 1,
            "status": "insufficient_data",
            "horizon": horizon,
            "method": "observed_range",
            "not_a_forecast": True,
            "assumptions": {"reason": "current_price_and_time_series_required"},
            "scenarios": {},
            "evidence_ids": list(time_series.get("source_item_ids", [])),
        }
    low = min(values)
    high = max(values)
    base = float(current_price)
    return {
        "schema_version": 1,
        "status": "available" if len(values) >= 2 else "insufficient_data",
        "horizon": horizon,
        "method": "observed_range",
        "not_a_forecast": True,
        "assumptions": {
            "lookback_start": time_series.get("window_start"),
            "lookback_end": time_series.get("window_end"),
            "source": "observed_provider_points",
        },
        "scenarios": {
            "base": {"price": round(base, 6), "basis": "latest_observation"},
            "bull": {"price": round(high, 6), "basis": "observed_window_high"},
            "bear": {"price": round(low, 6), "basis": "observed_window_low"},
        },
        "evidence_ids": list(time_series.get("source_item_ids", [])),
    }


def build_source_conflict_report(
    evidence: Iterable[Mapping[str, Any]],
    *,
    topic_id: str,
) -> dict[str, Any]:
    """Classify only lexical stance signals and preserve unknowns explicitly."""

    observations: list[dict[str, Any]] = []
    for item in evidence:
        item_id = item.get("item_id")
        source_id = item.get("source_id")
        if not isinstance(item_id, str) or not isinstance(source_id, str):
            raise ValueError("evidence item_id and source_id are required")
        text = " ".join(str(item.get(field) or "") for field in ("title", "summary")).lower()
        tokens = {token.strip(".,:;!?()[]{}\"'") for token in text.split()}
        positive = bool(tokens & POSITIVE_TERMS)
        negative = bool(tokens & NEGATIVE_TERMS)
        if positive and negative:
            stance = "unknown"
            reason = "conflicting_lexical_terms"
        elif positive:
            stance = "positive"
            reason = "lexical_heuristic"
        elif negative:
            stance = "negative"
            reason = "lexical_heuristic"
        else:
            stance = "unknown"
            reason = "no_calibrated_stance_signal"
        observations.append({
            "evidence_id": item_id,
            "source_id": source_id,
            "stance": stance,
            "reason": reason,
        })
    counts = Counter(observation["stance"] for observation in observations)
    positive = counts.get("positive", 0)
    negative = counts.get("negative", 0)
    if positive and negative:
        conflict_level = "high" if min(positive, negative) >= 1 else "medium"
    elif positive or negative:
        conflict_level = "low"
    else:
        conflict_level = "unknown"
    return {
        "schema_version": 1,
        "topic_id": topic_id,
        "method": "lexical_stance_v1",
        "status": "available" if observations else "insufficient_data",
        "conflict_level": conflict_level,
        "counts": {
            "positive": counts.get("positive", 0),
            "negative": counts.get("negative", 0),
            "neutral": counts.get("neutral", 0),
            "unknown": counts.get("unknown", 0),
        },
        "independent_source_count": len({observation["source_id"] for observation in observations}),
        "observations": observations,
        "evidence_ids": [observation["evidence_id"] for observation in observations],
        "limitations": [
            "lexical stance is a screening signal, not a sentiment oracle",
            "source independence and coordinated reposts require separate verification",
        ],
    }


def _pct_change(start: float, end: float) -> float:
    return round(((end / start) - 1) * 100, 6)


def _max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, ((value / peak) - 1) * 100)
    return round(worst, 6)


def _unique_ids(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _parse_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_datetime(value: Any, name: str) -> str:
    return _parse_datetime(value, name).isoformat().replace("+00:00", "Z")
