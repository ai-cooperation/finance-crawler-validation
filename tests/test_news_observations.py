import pytest

from finance_crawler_poc.news_observations import ObservationMergeError, merge_reports


def result(brand_id: str, success: bool) -> dict[str, object]:
    return {
        "brand_id": brand_id,
        "success": success,
        "final_outcome": "success" if success else "blocked",
    }


def report(
    results: list[dict[str, object]], *, selected: list[str] | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "observation_unit": "unique_news_brand",
        "summary": {"target_brands": 3},
        "results": results,
    }
    if selected is not None:
        payload["selection"] = {
            "mode": "explicit_brand_ids",
            "selected_brands": len(selected),
            "target_brands": 3,
            "brand_ids": selected,
        }
    return payload


def test_merge_reports_replaces_only_explicit_batch_brands() -> None:
    baseline = report(
        [result("alpha", True), result("beta", False), result("gamma", False)]
    )
    batch = report([result("beta", True)], selected=["beta"])

    merged = merge_reports(
        baseline,
        [batch],
        expected_brand_ids=("alpha", "beta", "gamma"),
    )

    assert merged["summary"] == {
        "observed_brands": 3,
        "successful_brands": 2,
        "failed_brands": 1,
        "brand_success_rate": 0.6667,
    }
    assert merged["successful_brand_ids"] == ["alpha", "beta"]
    assert merged["failed_brand_ids"] == ["gamma"]
    assert [item["brand_id"] for item in merged["results"]] == [
        "alpha",
        "beta",
        "gamma",
    ]


@pytest.mark.parametrize(
    ("baseline", "batches", "message"),
    [
        (
            report([result("alpha", True), result("beta", False)]),
            [],
            "baseline brand IDs do not match catalog",
        ),
        (
            report(
                [result("alpha", True), result("alpha", False), result("gamma", False)]
            ),
            [],
            "duplicate brand id",
        ),
        (
            report(
                [result("alpha", True), result("beta", False), result("gamma", False)]
            ),
            [report([result("beta", True)], selected=["gamma"])],
            "batch result IDs do not match selection",
        ),
        (
            report(
                [result("alpha", True), result("beta", False), result("gamma", False)]
            ),
            [report([result("beta", True)])],
            "batch must declare explicit selection",
        ),
    ],
)
def test_merge_reports_fails_closed_on_denominator_or_selection_drift(
    baseline: dict[str, object], batches: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ObservationMergeError, match=message):
        merge_reports(
            baseline,
            batches,
            expected_brand_ids=("alpha", "beta", "gamma"),
        )
