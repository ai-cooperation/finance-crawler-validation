from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from finance_crawler_poc.adapters import Crawl4AIAdapter, HttpAdapter
from finance_crawler_poc.news_catalog import NewsCatalogError, load_news_catalog
from finance_crawler_poc.news_probe import NewsBrandResult, probe_news_brand
from finance_crawler_poc.news_report import write_news_reports
from finance_crawler_poc.resource_router import (
    ExecutorConfigError,
    ExecutorState,
    load_executors,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe the unique-brand finance news catalog"
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--executors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--current-executor", required=True)
    return parser


async def run(
    catalog_path: Path,
    executor_path: Path,
    output_dir: Path,
    *,
    current_executor_id: str,
) -> list[NewsBrandResult]:
    catalog = load_news_catalog(catalog_path)
    if not catalog.is_complete:
        raise ValueError("news catalog must be complete before a full run")
    executors = load_executors(executor_path)
    current = next(
        (executor for executor in executors if executor.id == current_executor_id),
        None,
    )
    if current is None:
        raise ValueError(f"unknown current executor: {current_executor_id}")
    credential_available = not current.requires_credential or os.environ.get(
        "NEWS_COMMERCIAL_CREDENTIAL_AVAILABLE"
    ) == "1"
    states = {
        current.id: ExecutorState(
            available=True,
            credential_available=credential_available,
            remaining_jobs=max(1, catalog.endpoint_count),
        )
    }

    http_adapter = HttpAdapter()
    browser_adapter = Crawl4AIAdapter()
    adapters = {
        "json_api": http_adapter,
        "rss": http_adapter,
        "static_html": http_adapter,
        "browser": browser_adapter,
    }
    results: list[NewsBrandResult] = []
    try:
        for index, brand in enumerate(catalog.brands, start=1):
            print(
                json.dumps(
                    {
                        "event": "news_brand_started",
                        "brand_index": index,
                        "brand_total": catalog.brand_count,
                        "brand_id": brand.id,
                        "current_executor": current.id,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            result = await probe_news_brand(
                brand,
                executors=executors,
                states=states,
                adapters=adapters,
            )
            results.append(result)
            print(
                json.dumps(
                    {
                        "event": "news_brand_finished",
                        "brand_id": brand.id,
                        "success": result.success,
                        "outcome": result.final_outcome,
                        "endpoint_attempts": len(result.endpoint_attempts),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        await browser_adapter.close()
        await http_adapter.close()

    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    write_news_reports(
        results,
        output_dir,
        generated_at=generated_at,
        target_total=catalog.target.total_brands,
    )
    return results


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(
            run(
                args.catalog,
                args.executors,
                args.output,
                current_executor_id=args.current_executor,
            )
        )
    except (NewsCatalogError, ExecutorConfigError, ValueError) as exc:
        raise SystemExit(f"invalid news run: {exc}") from exc


if __name__ == "__main__":
    main()
