from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from finance_crawler_poc.news_probe import NewsBrandResult


@dataclass(frozen=True)
class NewsReportPaths:
    json_path: Path
    markdown_path: Path


def write_news_reports(
    results: list[NewsBrandResult],
    output_dir: Path,
    *,
    generated_at: str,
    target_total: int,
) -> NewsReportPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    successes = sum(result.success for result in results)
    endpoint_attempts = [
        attempt for result in results for attempt in result.endpoint_attempts
    ]
    summary: dict[str, int | float] = {
        "catalog_brands": len(results),
        "target_brands": target_total,
        "successful_brands": successes,
        "failed_brands": len(results) - successes,
        "brand_success_rate": round(successes / len(results), 4) if results else 0.0,
        "endpoint_attempts": len(endpoint_attempts),
    }
    payload = {
        "schema_version": 1,
        "observation_unit": "unique_news_brand",
        "generated_at": generated_at,
        "summary": summary,
        "by_brand_class": _brand_class_breakdown(results),
        "by_endpoint_transport": _attempt_breakdown(
            endpoint_attempts, "transport"
        ),
        "by_executor": _attempt_breakdown(endpoint_attempts, "executor_id"),
        "results": [result.to_dict() for result in results],
    }
    json_path = output_dir / "news-report.json"
    markdown_path = output_dir / "news-report.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        _render_markdown(results, generated_at, summary), encoding="utf-8"
    )
    return NewsReportPaths(json_path=json_path, markdown_path=markdown_path)


def _brand_class_breakdown(
    results: list[NewsBrandResult],
) -> dict[str, dict[str, int]]:
    grouped: dict[str, Counter[str]] = {}
    for result in results:
        outcome = "success" if result.success else result.final_outcome
        grouped.setdefault(result.brand_class, Counter())[outcome] += 1
    return {
        key: dict(sorted(counts.items())) for key, counts in sorted(grouped.items())
    }


def _attempt_breakdown(attempts: list[object], field: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, Counter[str]] = {}
    for attempt in attempts:
        key = str(getattr(attempt, field)) or "unassigned"
        grouped.setdefault(key, Counter())[str(getattr(attempt, "outcome"))] += 1
    return {
        key: dict(sorted(counts.items())) for key, counts in sorted(grouped.items())
    }


def _render_markdown(
    results: list[NewsBrandResult],
    generated_at: str,
    summary: dict[str, int | float],
) -> str:
    successes = int(summary["successful_brands"])
    total = int(summary["catalog_brands"])
    rate = float(summary["brand_success_rate"])
    lines = [
        "# 120-brand finance news crawl report",
        "",
        f"Generated: {generated_at}",
        "",
        f"Brand success: {successes}/{total} ({rate:.1%})",
        "",
        f"Endpoint attempts: {summary['endpoint_attempts']}",
        "",
        "| brand | class | region | result | successful endpoint | attempts |",
        "|---|---|---|---|---|---:|",
    ]
    for result in results:
        lines.append(
            f"| {result.brand_id} | {result.brand_class} | {result.region} | "
            f"{result.final_outcome} | {result.successful_endpoint_id or '-'} | "
            f"{len(result.endpoint_attempts)} |"
        )
    return "\n".join(lines) + "\n"
