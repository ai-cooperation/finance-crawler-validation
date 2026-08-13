from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from finance_crawler_poc.adapters import Crawl4AIAdapter, HttpAdapter
from finance_crawler_poc.news_catalog import NewsBrand, NewsCatalogError, load_news_catalog
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
    parser.add_argument(
        "--brand-ids",
        help="Comma-separated unique brand IDs; omit only for an intentional full run",
    )
    parser.add_argument("--max-brands", type=int, default=30)
    return parser


async def run(
    catalog_path: Path,
    executor_path: Path,
    output_dir: Path,
    *,
    current_executor_id: str,
    brand_ids: tuple[str, ...] | None = None,
    max_brands: int | None = None,
) -> list[NewsBrandResult]:
    catalog = load_news_catalog(catalog_path)
    if not catalog.is_complete:
        raise ValueError("news catalog must be complete before a full run")
    selected_brands = _select_brands(catalog.brands, brand_ids, max_brands)
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
            remaining_jobs=max(
                1, sum(len(brand.endpoints) for brand in selected_brands)
            ),
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
        for index, brand in enumerate(selected_brands, start=1):
            print(
                json.dumps(
                    {
                        "event": "news_brand_started",
                        "brand_index": index,
                        "brand_total": len(selected_brands),
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
    report_kwargs: dict[str, object] = {}
    if brand_ids is not None:
        report_kwargs["selected_brand_ids"] = tuple(
            brand.id for brand in selected_brands
        )
    write_news_reports(
        results,
        output_dir,
        generated_at=generated_at,
        target_total=catalog.target.total_brands,
        **report_kwargs,
    )
    return results


def _select_brands(
    brands: tuple[NewsBrand, ...],
    brand_ids: tuple[str, ...] | None,
    max_brands: int | None,
) -> tuple[NewsBrand, ...]:
    if max_brands is not None and max_brands <= 0:
        raise ValueError("max_brands must be positive")
    if brand_ids is None:
        selected = brands
    else:
        if len(brand_ids) != len(set(brand_ids)):
            raise ValueError("brand ids must be unique")
        by_id = {brand.id: brand for brand in brands}
        unknown = sorted(set(brand_ids) - set(by_id))
        if unknown:
            raise ValueError(f"unknown brand ids: {', '.join(unknown)}")
        requested = set(brand_ids)
        selected = tuple(brand for brand in brands if brand.id in requested)
    if max_brands is not None and len(selected) > max_brands:
        raise ValueError(
            f"brand batch exceeds max_brands: {len(selected)} > {max_brands}"
        )
    return selected


def _parse_brand_ids(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    ids = tuple(part.strip() for part in value.split(",") if part.strip())
    if not ids:
        raise ValueError("brand_ids must contain at least one brand id")
    return ids


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(
            run(
                args.catalog,
                args.executors,
                args.output,
                current_executor_id=args.current_executor,
                brand_ids=_parse_brand_ids(args.brand_ids),
                max_brands=args.max_brands,
            )
        )
    except (NewsCatalogError, ExecutorConfigError, ValueError) as exc:
        raise SystemExit(f"invalid news run: {exc}") from exc


if __name__ == "__main__":
    main()
