"""Deterministic financial-depth calculations for professional research packs.

This module deliberately does not forecast prices or infer an investment
recommendation.  It turns observed provider data into traceable calculations
and records when a requested calculation is not applicable or lacks inputs.
"""

from __future__ import annotations

import math
import hashlib
import json
import re
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

# This set is intentionally frozen and independent from collected evidence.
# It is the release gate for the lexical screen, not a claim that the screen
# is a production sentiment oracle.  Any change requires a new classifier
# version and a new calibration artifact.
_STANCE_CALIBRATION_SET_V1: tuple[tuple[str, str], ...] = (
    ("Bitcoin rises on strong demand and record inflows", "positive"),
    ("Crypto rally gains momentum as adoption grows", "positive"),
    ("ETF approval beats expectations and lifts markets", "positive"),
    ("Bitcoin falls as risk grows and losses deepen", "negative"),
    ("Crypto crash warning follows weak activity", "negative"),
    ("Regulatory risks trigger a bearish decline", "negative"),
)
STANCE_CLASSIFIER_VERSION = "lexical_stance_v2_calibrated"


def _classify_lexical_stance(text: str) -> str:
    tokens = {token.strip(".,:;!?()[]{}\"'") for token in text.lower().split()}
    positive = bool(tokens & POSITIVE_TERMS)
    negative = bool(tokens & NEGATIVE_TERMS)
    if positive and negative:
        return "unknown"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return "unknown"


def build_stance_calibration_report() -> dict[str, Any]:
    """Evaluate the frozen, human-labelled stance calibration set."""

    expected = [label for _, label in _STANCE_CALIBRATION_SET_V1]
    predicted = [_classify_lexical_stance(text) for text, _ in _STANCE_CALIBRATION_SET_V1]
    labels = ("positive", "negative")
    metrics: dict[str, dict[str, float]] = {}
    for label in labels:
        true_positive = sum(actual == label and guess == label for actual, guess in zip(expected, predicted))
        false_positive = sum(actual != label and guess == label for actual, guess in zip(expected, predicted))
        false_negative = sum(actual == label and guess != label for actual, guess in zip(expected, predicted))
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
        metrics[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }
    calibration_payload = json.dumps(_STANCE_CALIBRATION_SET_V1, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    macro_f1 = sum(item["f1"] for item in metrics.values()) / len(metrics)
    return {
        "status": "calibrated" if all(item["precision"] >= 0.8 and item["recall"] >= 0.8 for item in metrics.values()) else "unresolved",
        "classifier_version": STANCE_CLASSIFIER_VERSION,
        "oracle_type": "frozen_human_labeled_set",
        "calibration_set_id": "source_conflict_calibration_v1",
        "calibration_set_sha256": hashlib.sha256(calibration_payload).hexdigest(),
        "sample_count": len(expected),
        "metrics": metrics,
        "macro_f1": round(macro_f1, 6),
    }


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
    for window in (1, 3, 7, 30, 90, 365):
        returns[f"{window}d_observed_pct"] = _window_return(ordered, window)
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
    calibration = build_stance_calibration_report()
    for item in evidence:
        item_id = item.get("item_id")
        source_id = item.get("source_id")
        if not isinstance(item_id, str) or not isinstance(source_id, str):
            raise ValueError("evidence item_id and source_id are required")
        text = " ".join(str(item.get(field) or "") for field in ("title", "summary")).lower()
        stance = _classify_lexical_stance(text)
        if stance == "unknown" and bool(
            {token.strip(".,:;!?()[]{}\"'") for token in text.split()} & POSITIVE_TERMS
        ) and bool(
            {token.strip(".,:;!?()[]{}\"'") for token in text.split()} & NEGATIVE_TERMS
        ):
            stance = "unknown"
            reason = "conflicting_lexical_terms"
        elif stance == "positive":
            reason = "lexical_heuristic"
        elif stance == "negative":
            reason = "lexical_heuristic"
        else:
            stance = "unknown"
            reason = "no_calibrated_stance_signal"
        canonical = str(item.get("canonical_url") or "")
        title_key = re.sub(r"[^a-z0-9]+", " ", str(item.get("title") or "").lower()).strip()
        cluster = hashlib.sha256((canonical or title_key).encode("utf-8")).hexdigest()[:16]
        observations.append({
            "evidence_id": item_id,
            "source_id": source_id,
            "cluster_id": cluster,
            "stance": stance,
            "reason": reason,
        })
    counts = Counter(observation["stance"] for observation in observations)
    positive = counts.get("positive", 0)
    negative = counts.get("negative", 0)
    positive_clusters = {observation["cluster_id"] for observation in observations if observation["stance"] == "positive"}
    negative_clusters = {observation["cluster_id"] for observation in observations if observation["stance"] == "negative"}
    if positive_clusters and negative_clusters:
        conflict_level = "high"
    elif positive or negative:
        conflict_level = "low"
    else:
        conflict_level = "unknown"
    return {
        "schema_version": 1,
        "topic_id": topic_id,
        "method": "source_conflict_screen_v2",
        "calibration_status": calibration["status"],
        "classifier_version": calibration["classifier_version"],
        "calibration": calibration,
        "status": "available" if observations else "insufficient_data",
        "conflict_level": conflict_level,
        "counts": {
            "positive": counts.get("positive", 0),
            "negative": counts.get("negative", 0),
            "neutral": counts.get("neutral", 0),
            "unknown": counts.get("unknown", 0),
        },
        "independent_source_count": len({observation["cluster_id"] for observation in observations}),
        "cluster_count": len({observation["cluster_id"] for observation in observations}),
        "observations": observations,
        "evidence_ids": [observation["evidence_id"] for observation in observations],
        "limitations": [
            "lexical stance is a screening signal, not a sentiment oracle",
            "source independence and coordinated reposts require separate verification",
        ],
    }


def build_market_driver_snapshot(
    *,
    target: Mapping[str, Any],
    market_snapshot: Mapping[str, Any],
    time_series: Mapping[str, Any],
    evidence: Iterable[Mapping[str, Any]],
    provider_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build observed market drivers without turning headlines into causality."""

    symbol = str(target.get("symbol") or "").upper()
    instrument = next(
        (candidate for candidate in market_snapshot.get("instruments", [])
         if isinstance(candidate, Mapping) and str(candidate.get("symbol") or "").upper() == symbol),
        None,
    )
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    driver_terms = (
        ("etf", "ETF flows"), ("institution", "Institutional demand"),
        ("inflow", "Fund inflows"), ("outflow", "Fund outflows"),
        ("regulation", "Regulation"), ("regulatory", "Regulation"),
        ("rate", "Rates and liquidity"), ("liquidity", "Rates and liquidity"),
        ("liquidation", "Liquidations"), ("leverage", "Leverage"),
        ("hack", "Security event"), ("approval", "Approval/catalyst"),
    )
    for item in evidence:
        item_id = item.get("item_id")
        if not isinstance(item_id, str):
            continue
        text = " ".join(str(item.get(field) or "") for field in ("title", "summary")).casefold()
        matched = next(((term, label) for term, label in driver_terms if term in text), None)
        if matched is None:
            continue
        term, label = matched
        key = re.sub(r"[^a-z0-9]+", " ", str(item.get("title") or "").casefold()).strip()[:120]
        if not key or key in seen:
            continue
        seen.add(key)
        events.append({
            "event_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
            "label": label,
            "trigger_term": term,
            "title": str(item.get("title") or "")[:300],
            "evidence_ids": [item_id],
            "source_count": 1,
            "causal_status": "unresolved",
        })
    normalized_provider_data = dict(provider_data or {})
    provider_status = {
        "volume": normalized_provider_data.get(
            "volume", {"status": "unavailable", "reason": "provider_not_configured"}
        ),
        "etf_flows": normalized_provider_data.get(
            "etf_flows", {"status": "unavailable", "reason": "provider_not_configured"}
        ),
        "derivatives": normalized_provider_data.get(
            "derivatives", {"status": "unavailable", "reason": "provider_not_configured"}
        ),
        "on_chain": normalized_provider_data.get(
            "on_chain", {"status": "unavailable", "reason": "provider_not_configured"}
        ),
    }
    provider_ready = all(
        isinstance(value, Mapping) and value.get("status") in {"available", "not_applicable"}
        for value in provider_status.values()
    )
    return {
        "schema_version": 1,
        "status": "available" if provider_ready and events else ("partial" if events else "unresolved"),
        "target": dict(target),
        "price_and_returns": {
            "price": instrument.get("price") if isinstance(instrument, Mapping) else None,
            "change_24h_pct": instrument.get("change_24h_pct") if isinstance(instrument, Mapping) else None,
            "market_cap": instrument.get("market_cap") if isinstance(instrument, Mapping) else None,
            "returns": dict(time_series.get("returns", {})),
        },
        "provider_status": provider_status,
        "news_driver_candidates": events[:12],
        "limitations": [
            "headline matches are candidate drivers, not causal attribution",
            *([] if provider_ready else ["one or more market confirmation providers are unavailable"]),
        ],
    }


def _pct_change(start: float, end: float) -> float:
    return round(((end / start) - 1) * 100, 6)


def _window_return(points: list[Mapping[str, Any]], days: int) -> float | None:
    if len(points) < 2:
        return None
    # Daily market providers normally expose one point per day.  Use the
    # nearest available observation when a provider has a missing day.
    index = max(0, len(points) - 1 - days)
    if index >= len(points) - 1:
        return None
    return _pct_change(float(points[index]["value"]), float(points[-1]["value"]))


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
