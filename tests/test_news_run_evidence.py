import json
import re
from pathlib import Path

from finance_crawler_poc.news_catalog import load_news_catalog


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = (
    REPOSITORY_ROOT / "experiments/news-120/run-31309377786-summary.json"
)
ISOLATED_SUMMARY_PATH = (
    REPOSITORY_ROOT / "experiments/news-120/run-31677822771-summary.json"
)


def test_strict_news_run_summary_matches_the_frozen_catalog() -> None:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    catalog = load_news_catalog(REPOSITORY_ROOT / "news-sources.yaml")
    brand_results = summary["brand_results"]
    failed = summary["failed_brand_outcomes"]
    failed_ids = [brand_id for ids in failed.values() for brand_id in ids]

    assert summary["observation_unit"] == "unique_news_brand"
    assert summary["catalog"]["brands"] == catalog.brand_count == 120
    # This file freezes the 2026-08-09 observation. New fallback endpoints may
    # grow the live catalog without rewriting historical experiment metadata.
    assert summary["catalog"]["declared_endpoints"] == 148
    assert catalog.endpoint_count >= summary["catalog"]["declared_endpoints"]
    assert brand_results["recorded"] == 120
    assert brand_results["successful"] == 99
    assert brand_results["failed"] == len(failed_ids) == 21
    assert len(failed_ids) == len(set(failed_ids))
    assert set(failed_ids) <= {brand.id for brand in catalog.brands}
    assert re.fullmatch(r"[0-9a-f]{64}", summary["news_report_sha256"])


def test_isolated_account_baseline_records_every_unique_brand() -> None:
    summary = json.loads(ISOLATED_SUMMARY_PATH.read_text(encoding="utf-8"))
    catalog = load_news_catalog(REPOSITORY_ROOT / "news-sources.yaml")
    brand_results = summary["brand_results"]
    failed = summary["failed_brand_outcomes"]
    failed_ids = [brand_id for ids in failed.values() for brand_id in ids]

    assert summary["run_id"] == 31677822771
    assert summary["observation_unit"] == "unique_news_brand"
    assert summary["catalog"]["brands"] == catalog.brand_count == 120
    assert summary["catalog"]["declared_endpoints"] == 148
    assert catalog.endpoint_count > summary["catalog"]["declared_endpoints"]
    assert brand_results["recorded"] == 120
    assert brand_results["successful"] == 101
    assert brand_results["failed"] == len(failed_ids) == 19
    assert len(failed_ids) == len(set(failed_ids))
    assert set(failed_ids) <= {brand.id for brand in catalog.brands}
    assert re.fullmatch(r"[0-9a-f]{64}", summary["news_report_sha256"])
