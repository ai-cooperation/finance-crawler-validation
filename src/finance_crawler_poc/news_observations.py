from __future__ import annotations

from typing import Any


class ObservationMergeError(ValueError):
    """Raised when P2 observation evidence would drift from its denominator."""


def merge_reports(
    baseline: dict[str, Any],
    batches: list[dict[str, Any]],
    *,
    expected_brand_ids: tuple[str, ...],
) -> dict[str, object]:
    expected = set(expected_brand_ids)
    if len(expected) != len(expected_brand_ids):
        raise ObservationMergeError("catalog contains duplicate brand id")

    baseline_by_id = _results_by_id(baseline, owner="baseline")
    if set(baseline_by_id) != expected:
        raise ObservationMergeError("baseline brand IDs do not match catalog")

    merged = dict(baseline_by_id)
    for batch in batches:
        selection = batch.get("selection")
        if not isinstance(selection, dict) or selection.get("mode") != "explicit_brand_ids":
            raise ObservationMergeError("batch must declare explicit selection")
        selected_raw = selection.get("brand_ids")
        if not isinstance(selected_raw, list) or not all(
            isinstance(value, str) for value in selected_raw
        ):
            raise ObservationMergeError("batch selection must contain brand IDs")
        selected = set(selected_raw)
        if len(selected) != len(selected_raw) or not selected <= expected:
            raise ObservationMergeError("batch selection contains invalid brand IDs")
        batch_by_id = _results_by_id(batch, owner="batch")
        if set(batch_by_id) != selected:
            raise ObservationMergeError("batch result IDs do not match selection")
        merged = {**merged, **batch_by_id}

    ordered_results = [merged[brand_id] for brand_id in expected_brand_ids]
    successful_ids = [
        str(item["brand_id"]) for item in ordered_results if item.get("success") is True
    ]
    failed_ids = [
        str(item["brand_id"]) for item in ordered_results if item.get("success") is not True
    ]
    total = len(ordered_results)
    successes = len(successful_ids)
    return {
        "schema_version": 1,
        "observation_unit": "unique_news_brand_latest",
        "summary": {
            "observed_brands": total,
            "successful_brands": successes,
            "failed_brands": total - successes,
            "brand_success_rate": round(successes / total, 4) if total else 0.0,
        },
        "successful_brand_ids": successful_ids,
        "failed_brand_ids": failed_ids,
        "results": ordered_results,
    }


def _results_by_id(
    report: dict[str, Any], *, owner: str
) -> dict[str, dict[str, Any]]:
    results = report.get("results")
    if not isinstance(results, list):
        raise ObservationMergeError(f"{owner} results must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("brand_id"), str):
            raise ObservationMergeError(f"{owner} result must contain brand id")
        brand_id = item["brand_id"]
        if brand_id in by_id:
            raise ObservationMergeError(f"{owner} duplicate brand id: {brand_id}")
        by_id[brand_id] = item
    return by_id
