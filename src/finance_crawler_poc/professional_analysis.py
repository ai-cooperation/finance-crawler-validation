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
from datetime import datetime, timedelta, timezone
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


def build_event_study_statistics(abnormal_returns: Iterable[float]) -> dict[str, Any]:
    """Calculate a deterministic market-adjusted event-study summary.

    This is intentionally a small, dependency-free inference layer.  The
    p-value uses a two-sided normal approximation and is labelled as such;
    callers must not present it as a finite-sample t distribution.  The
    result is only ``computed`` when at least two finite observations exist.
    """

    values = [float(value) for value in abnormal_returns if isinstance(value, (int, float)) and math.isfinite(float(value))]
    if len(values) < 2:
        return {
            "status": "not_computed",
            "sample_count": len(values),
            "mean_abnormal_return_pct": None,
            "standard_error_pct": None,
            "t_stat": None,
            "p_value_two_sided": None,
            "inference": "at_least_two_abnormal_returns_required",
        }
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    standard_deviation = math.sqrt(variance)
    standard_error = standard_deviation / math.sqrt(len(values))
    if standard_error == 0:
        t_stat = None
        p_value = 0.0 if mean != 0 else 1.0
        status = "computed_constant_sample"
    else:
        t_stat = mean / standard_error
        p_value = math.erfc(abs(t_stat) / math.sqrt(2.0))
        status = "computed"
    return {
        "status": status,
        "sample_count": len(values),
        "mean_abnormal_return_pct": round(mean, 6),
        "standard_error_pct": round(standard_error, 6),
        "t_stat": round(t_stat, 6) if t_stat is not None else None,
        "p_value_two_sided": round(p_value, 6),
        "inference": "two_sided_normal_approximation; not_a_finite_sample_t_test",
    }


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
    """Evaluate the frozen reference set used by the release gate.

    The labels are an internal deterministic fixture.  They prove classifier
    reproducibility and unknown handling; they are not an external sentiment
    benchmark and must not be presented as one.
    """

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
        "oracle_type": "frozen_reference_labels",
        "labeling_note": "internal deterministic fixture; not an external sentiment benchmark",
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
    currency: str = "USD",
    annualization_periods: int = 365,
) -> dict[str, Any]:
    """Normalize historical observations and calculate descriptive metrics."""

    if not series_id.strip() or not provider.strip():
        raise ValueError("series_id and provider are required")
    normalized_currency = currency.strip().upper()
    if not normalized_currency:
        raise ValueError("currency is required")
    if annualization_periods <= 0:
        raise ValueError("annualization_periods must be positive")
    as_of_dt = _parse_datetime(as_of, "as_of")
    by_timestamp: dict[str, float] = {}
    for point in points:
        if not isinstance(point, Mapping):
            raise ValueError("time-series point must be an object")
        observed_at = _canonical_datetime(point.get("observed_at"), "observed_at")
        if _parse_datetime(observed_at, "observed_at") > as_of_dt:
            raise ValueError("observed_at must not be after as_of")
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
    volatility = round(
        pstdev(daily_returns) * math.sqrt(annualization_periods) * 100, 6
    ) if len(daily_returns) >= 2 else None
    max_drawdown = _max_drawdown(values)
    response = {
        "schema_version": 1,
        "status": status,
        "series_id": series_id.strip().upper(),
        "provider": provider.strip(),
        "currency": normalized_currency,
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


def build_event_alignment(
    evidence: Iterable[Mapping[str, Any]],
    time_series: Mapping[str, Any],
    *,
    post_window_days: int = 5,
    max_events: int = 50,
    benchmark_time_series: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Align dated headlines with prices and optional benchmark returns.

    ``event_study_status`` is only available when a separate benchmark series
    has matching pre/post observations.  The result is market-adjusted,
    descriptive evidence; it never asserts that the headline caused the
    return.
    """

    if post_window_days < 1 or max_events < 1:
        raise ValueError("post_window_days and max_events must be positive")
    raw_points = time_series.get("points") if isinstance(time_series, Mapping) else None
    points: list[tuple[datetime, Mapping[str, Any]]] = []
    if isinstance(raw_points, list):
        for point in raw_points:
            if not isinstance(point, Mapping):
                continue
            try:
                observed = _parse_datetime(point.get("observed_at"), "observed_at")
            except ValueError:
                continue
            if isinstance(point.get("value"), (int, float)) and not isinstance(point.get("value"), bool):
                points.append((observed, point))
    points.sort(key=lambda pair: pair[0])
    benchmark_points: list[tuple[datetime, Mapping[str, Any]]] = []
    if isinstance(benchmark_time_series, Mapping):
        raw_benchmark_points = benchmark_time_series.get("points")
        if isinstance(raw_benchmark_points, list):
            for point in raw_benchmark_points:
                if not isinstance(point, Mapping):
                    continue
                try:
                    observed = _parse_datetime(point.get("observed_at"), "benchmark_observed_at")
                except ValueError:
                    continue
                if isinstance(point.get("value"), (int, float)) and not isinstance(point.get("value"), bool):
                    benchmark_points.append((observed, point))
            benchmark_points.sort(key=lambda pair: pair[0])
    events: list[dict[str, Any]] = []
    unresolved = 0
    excluded_undated = 0
    excluded_out_of_window = 0
    excluded_incomplete_window = 0
    event_study_event_count = 0
    candidates = []
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        published = item.get("published_at")
        if not isinstance(published, str) or not published.strip():
            excluded_undated += 1
            continue
        candidates.append(item)
    for item in candidates:
        if len(events) >= max_events:
            break
        item_id = item.get("item_id")
        published = item.get("published_at")
        if not isinstance(item_id, str) or not isinstance(published, str):
            unresolved += 1
            continue
        try:
            event_time = _parse_datetime(published, "published_at")
        except ValueError:
            unresolved += 1
            continue
        if points and (
            event_time < points[0][0]
            or event_time > points[-1][0]
            or event_time + timedelta(days=1) > points[-1][0]
        ):
            excluded_out_of_window += 1
            continue
        pre = next((point for observed, point in reversed(points) if observed <= event_time), None)
        post = next(
            (
                point for observed, point in points
                if observed >= event_time + timedelta(days=1)
                and observed <= event_time + timedelta(days=post_window_days)
            ),
            None,
        )
        if pre is None or post is None:
            # A headline at the right edge of the frozen as-of window has no
            # complete post-event observation yet.  It is censored, not a
            # malformed event; exclude it explicitly so one fresh headline
            # cannot downgrade an otherwise valid event-study sample.
            excluded_incomplete_window += 1
            continue
        pre_value = float(pre["value"])
        post_value = float(post["value"])
        event = {
            "event_id": hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16],
            "evidence_id": item_id,
            "published_at": event_time.isoformat().replace("+00:00", "Z"),
            "title": str(item.get("title") or "")[:300],
            "pre_observation": {"observed_at": pre["observed_at"], "value": pre_value},
            "post_observation": {"observed_at": post["observed_at"], "value": post_value},
            "observed_return_pct": round(((post_value / pre_value) - 1) * 100, 6) if pre_value else None,
            "causal_status": "unresolved",
        }
        benchmark_pre = next((point for observed, point in reversed(benchmark_points) if observed <= event_time), None)
        benchmark_post = next(
            (
                point for observed, point in benchmark_points
                if observed >= event_time + timedelta(days=1)
                and observed <= event_time + timedelta(days=post_window_days)
            ),
            None,
        )
        if benchmark_pre is not None and benchmark_post is not None:
            benchmark_pre_value = float(benchmark_pre["value"])
            benchmark_post_value = float(benchmark_post["value"])
            if benchmark_pre_value > 0:
                benchmark_return = ((benchmark_post_value / benchmark_pre_value) - 1) * 100
                event["benchmark_observation"] = {
                    "pre_observed_at": benchmark_pre["observed_at"],
                    "pre_value": benchmark_pre_value,
                    "post_observed_at": benchmark_post["observed_at"],
                    "post_value": benchmark_post_value,
                }
                event["benchmark_return_pct"] = round(benchmark_return, 6)
                event["abnormal_return_pct"] = round(float(event["observed_return_pct"]) - benchmark_return, 6)
                event_study_event_count += 1
        events.append(event)
    abnormal_returns = [
        float(event["abnormal_return_pct"])
        for event in events
        if isinstance(event.get("abnormal_return_pct"), (int, float))
    ]
    event_statistics = build_event_study_statistics(abnormal_returns)
    event_study_status = "available" if event_study_event_count else "insufficient_data"
    event_dates = {str(event.get("published_at") or "")[:10] for event in events if event.get("published_at")}
    significance_status = (
        "computed"
        if event_statistics["status"] in {"computed", "computed_constant_sample"}
        else "not_computed"
    )
    quality_status = (
        "complete"
        if (
            event_study_event_count >= 8
            and len(event_dates) >= 8
            and unresolved == 0
            and significance_status == "computed"
        )
        else "descriptive_only" if event_study_event_count else "insufficient_data"
    )
    return {
        "schema_version": 1,
        "status": "available" if events else "insufficient_data",
        "method": "dated_headline_to_observed_price_alignment_v1",
        "post_window_days": post_window_days,
        "not_causal": True,
        "aligned_event_count": len(events),
        "unresolved_event_count": unresolved,
        "excluded_undated_event_count": excluded_undated,
        "excluded_out_of_window_event_count": excluded_out_of_window,
        "excluded_incomplete_window_event_count": excluded_incomplete_window,
        "eligible_event_count": len(candidates),
        "events": events,
        "event_study_status": event_study_status,
        "event_study_method": "market_adjusted_return_v1" if benchmark_points else None,
        "benchmark_series_id": benchmark_time_series.get("series_id") if isinstance(benchmark_time_series, Mapping) else None,
        "event_study_event_count": event_study_event_count,
        "event_study_sample_status": "descriptive_only" if event_study_event_count < 10 else "screening",
        "event_study_unique_event_date_count": len(event_dates),
        "event_study_significance_status": significance_status,
        "event_study_statistics": event_statistics,
        "event_study_quality_status": quality_status,
        "event_study_missing_reason": None if event_study_status == "available" else "benchmark_pre_and_post_observations_required",
        "missing_reason": None if events else "no_dated_event_with_pre_and_post_observation",
    }


def build_valuation_snapshot(
    target: Mapping[str, Any],
    *,
    fundamentals: Mapping[str, Any] | None,
    market_price: float | None,
    source_item_ids: Iterable[str],
    peer_valuation: Mapping[str, Any] | None = None,
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
        "observed_multiples": {},
        "period_alignment_status": "unresolved",
        "period_alignment_basis": None,
        "target_period_key": None,
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
        observed_multiples = _build_observed_multiples(fundamentals, market_price)
        return {
            **common,
            "status": "insufficient_data",
            "method": "fundamental_multiples",
            "missing_fields": missing,
            "reason": "required_fundamental_fields_missing",
            "observed_multiples": observed_multiples,
        }
    # A P/E-derived implied value is undefined for a non-positive target EPS.
    # Keep the observed market data, but fail closed instead of publishing a
    # negative pseudo-target value that readers could mistake for valuation.
    if not isinstance(fundamentals.get("eps"), (int, float)) or float(fundamentals["eps"]) <= 0:
        # A positive-revenue issuer may still support a period-aligned P/S
        # cross-check when P/E is undefined.  This is an explicit method,
        # backed by at least three comparable peers, not a negative-EPS hack.
        if (
            isinstance(peer_valuation, Mapping)
            and peer_valuation.get("status") == "available"
            and isinstance(peer_valuation.get("median_ps"), (int, float))
            and float(peer_valuation.get("median_ps") or 0) > 0
            and isinstance(fundamentals.get("revenue"), (int, float))
            and float(fundamentals.get("revenue") or 0) > 0
            and isinstance(fundamentals.get("shares"), (int, float))
            and float(fundamentals.get("shares") or 0) > 0
        ):
            return {
                **common,
                "status": "available",
                "method": "price_to_sales",
                "missing_fields": [],
                "reason": "pe_not_applicable_non_positive_eps",
                "period_alignment_status": peer_valuation.get("period_alignment_status", "unresolved"),
                "period_alignment_basis": peer_valuation.get("period_alignment_basis"),
                "target_period_key": peer_valuation.get("target_period_key"),
                "assumptions": {
                    **dict(peer_valuation.get("assumptions") or {}),
                    "peer_median_ps": round(float(peer_valuation["median_ps"]), 6),
                    "target_revenue": float(fundamentals["revenue"]),
                    "target_shares": float(fundamentals["shares"]),
                },
                "observed_multiples": {
                    "price_to_sales": round(float(market_price) * float(fundamentals["shares"]) / float(fundamentals["revenue"]), 6)
                    if isinstance(market_price, (int, float)) and float(market_price) > 0 else None,
                    "not_a_target_value": True,
                },
            }
        book_value_per_share = fundamentals.get("book_value_per_share")
        book_value_as_of = str(fundamentals.get("book_value_as_of") or "").strip()
        if (
            isinstance(book_value_per_share, (int, float))
            and not isinstance(book_value_per_share, bool)
            and math.isfinite(float(book_value_per_share))
            and float(book_value_per_share) > 0
            and isinstance(market_price, (int, float))
            and not isinstance(market_price, bool)
            and math.isfinite(float(market_price))
            and float(market_price) > 0
            and _is_iso_date(book_value_as_of)
        ):
            return {
                **common,
                "status": "available",
                "method": "price_to_book",
                "missing_fields": [],
                "reason": "pe_not_applicable_non_positive_eps",
                "period_alignment_status": "aligned",
                "period_alignment_basis": "official_book_value_per_share",
                "target_period_key": book_value_as_of[:4],
                "assumptions": {
                    "fallback": "price_to_book_for_non_positive_eps",
                    "not_a_forecast": True,
                    "book_value_as_of": book_value_as_of,
                },
                "observed_multiples": {
                    "price_to_book": round(float(market_price) / float(book_value_per_share), 6),
                    "book_value_per_share": round(float(book_value_per_share), 6),
                    "book_value_as_of": book_value_as_of,
                    "not_a_target_value": True,
                },
            }
        return {
            **common,
            "status": "insufficient_data",
            "method": "fundamental_multiples",
            "missing_fields": ["positive_eps_for_pe_valuation"],
            "reason": "target_eps_non_positive",
            "observed_multiples": _build_observed_multiples(fundamentals, market_price),
        }
    if (
        isinstance(peer_valuation, Mapping)
        and peer_valuation.get("status") == "available"
        and not (isinstance(peer_valuation.get("median_pe"), (int, float)) and float(peer_valuation.get("median_pe") or 0) > 0)
        and isinstance(peer_valuation.get("median_ps"), (int, float))
        and math.isfinite(float(peer_valuation["median_ps"]))
        and float(peer_valuation["median_ps"]) > 0
        and isinstance(fundamentals.get("revenue"), (int, float))
        and float(fundamentals.get("revenue") or 0) > 0
        and isinstance(fundamentals.get("shares"), (int, float))
        and float(fundamentals.get("shares") or 0) > 0
    ):
        return {
            **common,
            "status": "available",
            "method": "price_to_sales",
            "missing_fields": [],
            "reason": "peer_ps_crosscheck",
            "period_alignment_status": peer_valuation.get("period_alignment_status", "unresolved"),
            "period_alignment_basis": peer_valuation.get("period_alignment_basis"),
            "target_period_key": peer_valuation.get("target_period_key"),
            "assumptions": {
                **dict(peer_valuation.get("assumptions") or {}),
                "peer_median_ps": round(float(peer_valuation["median_ps"]), 6),
                "target_revenue": float(fundamentals["revenue"]),
                "target_shares": float(fundamentals["shares"]),
            },
            "observed_multiples": {
                "price_to_sales": round(float(market_price) * float(fundamentals["shares"]) / float(fundamentals["revenue"]), 6)
                if isinstance(market_price, (int, float)) and float(market_price) > 0 else None,
                "not_a_target_value": True,
            },
        }
    if (
        isinstance(peer_valuation, Mapping)
        and peer_valuation.get("status") == "available"
        and isinstance(peer_valuation.get("median_pe"), (int, float))
        and not isinstance(peer_valuation.get("median_pe"), bool)
        and math.isfinite(float(peer_valuation["median_pe"]))
        and float(peer_valuation["median_pe"]) > 0
    ):
        implied_value = round(float(fundamentals["eps"]) * float(peer_valuation["median_pe"]), 6)
        return {
            **common,
            "status": "available",
            "method": "fundamental_multiples",
            "missing_fields": [],
            "reason": None,
            "peer_set": list(peer_valuation.get("peer_set") or []),
            "period_alignment_status": peer_valuation.get("period_alignment_status", "unresolved"),
            "period_alignment_basis": peer_valuation.get("period_alignment_basis"),
            "target_period_key": peer_valuation.get("target_period_key"),
            "assumptions": {
                **dict(peer_valuation.get("assumptions") or {}),
                "peer_median_pe": round(float(peer_valuation["median_pe"]), 6),
            },
            "implied_value": {
                "value": implied_value,
                "basis": "annual_diluted_eps_times_peer_median_pe",
                "not_a_forecast": True,
            },
            "observed_multiples": _build_observed_multiples(fundamentals, market_price),
        }
    if isinstance(peer_valuation, Mapping) and peer_valuation.get("dcf_only_fallback_eligible") is True:
        # Peer multiples are an optional cross-check.  When the provider has
        # fewer than three positive, period-aligned peers, keep that absence
        # explicit and let the professional DCF layer proceed.  This is not a
        # fabricated P/E and must remain visibly DCF-only in the audit.
        return {
            **common,
            "status": "available",
            "method": "dcf_only_fallback",
            "missing_fields": [],
            "reason": "peer_multiple_unavailable_dcf_only",
            "period_alignment_status": "not_applicable",
            "period_alignment_basis": "dcf_only_no_peer_multiple",
            "target_period_key": None,
            "dcf_only_fallback_eligible": True,
            "dcf_only_fallback_reason": peer_valuation.get("dcf_only_fallback_reason"),
            "observed_multiples": _build_observed_multiples(fundamentals, market_price),
        }
    # No multiple is invented here.  A peer provider must supply it as an
    # explicit, auditable assumption before an implied value can be published.
    return {
        **common,
        "status": "insufficient_data",
        "method": "fundamental_multiples",
        "missing_fields": ["peer_median_pe"],
        "reason": "peer_multiple_required",
        "observed_multiples": _build_observed_multiples(fundamentals, market_price),
    }


def _is_iso_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _build_observed_multiples(
    fundamentals: Mapping[str, Any] | None,
    market_price: float | None,
) -> dict[str, Any]:
    """Return descriptive multiples only; never convert them into a target value."""

    eps = fundamentals.get("eps") if isinstance(fundamentals, Mapping) else None
    if (
        isinstance(market_price, (int, float))
        and not isinstance(market_price, bool)
        and math.isfinite(float(market_price))
        and float(market_price) > 0
        and isinstance(eps, (int, float))
        and not isinstance(eps, bool)
        and math.isfinite(float(eps))
        and float(eps) > 0
    ):
        return {
            "trailing_pe": round(float(market_price) / float(eps), 6),
            "trailing_pe_basis": "market_price_divided_by_annual_diluted_eps",
            "not_a_target_value": True,
        }
    return {
        "trailing_pe": None,
        "trailing_pe_basis": "market_price_divided_by_annual_diluted_eps",
        "not_a_target_value": True,
        "missing_reason": "positive_market_price_and_eps_required",
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
            "publisher_id": str(item.get("publisher_id") or "").strip() or None,
            "independence_group": (
                str(item.get("independence_group") or "").strip()
                or str(item.get("publisher_id") or "").strip()
                or _source_group_identity(source_id)
            ),
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
        # A unique URL/title cluster is not an independent publisher.  The
        # readiness gate must count source groups, otherwise RSS and search
        # mirrors can inflate apparent corroboration.
        "independent_source_count": len({observation["independence_group"] for observation in observations}),
        "source_group_count": len({observation["independence_group"] for observation in observations}),
        "cluster_count": len({observation["cluster_id"] for observation in observations}),
        "observations": observations,
        "evidence_ids": [observation["evidence_id"] for observation in observations],
        "limitations": [
            "lexical stance is a screening signal, not a sentiment oracle",
            "source independence and coordinated reposts require separate verification",
        ],
    }


def _source_group_identity(source_id: str) -> str:
    """Collapse route-specific aliases that come from one publisher."""

    return re.sub(r"_target_(?:rss|search)$", "", source_id)


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
        ("demand", "Demand and orders"), ("capex", "Capital expenditure"),
        ("capacity", "Capacity and expansion"), ("expansion", "Capacity and expansion"),
        ("earnings", "Earnings"), ("guidance", "Guidance"),
        ("price", "Pricing"), ("investment", "Investment"),
        ("export", "Exports and trade"),
    )
    for item in evidence:
        item_id = item.get("item_id")
        if not isinstance(item_id, str):
            continue
        text = " ".join(str(item.get(field) or "") for field in ("title", "summary")).casefold()
        matched = next(
            (
                (term, label)
                for term, label in driver_terms
                if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)
            ),
            None,
        )
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
    end = _parse_datetime(str(points[-1]["observed_at"]), "observed_at")
    cutoff = end - timedelta(days=days)
    # Use the latest observation at or before the calendar cutoff. This avoids
    # treating a sparse equity trading series as if every point were one day
    # apart and prevents look-ahead from an observation after the cutoff.
    candidates = [
        point for point in points[:-1]
        if _parse_datetime(str(point["observed_at"]), "observed_at") <= cutoff
    ]
    if not candidates:
        return None
    return _pct_change(float(candidates[-1]["value"]), float(points[-1]["value"]))


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
