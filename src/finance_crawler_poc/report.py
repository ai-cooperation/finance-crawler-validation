from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from finance_crawler_poc.models import ProbeResult


PUBLIC_ACCESS_TIERS = frozenset({"public_api", "public_feed", "public_web"})


@dataclass(frozen=True)
class ReportPaths:
    json_path: Path
    markdown_path: Path


def write_reports(
    results: list[ProbeResult],
    output_dir: Path,
    *,
    generated_at: str,
) -> ReportPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = dict(sorted(Counter(item.outcome.value for item in results).items()))
    payload = {
        "schema_version": 4,
        "generated_at": generated_at,
        "measurement": {
            "observation_unit": "source_path_attempt",
            "first_pass_run_index": 1,
            "repeat_semantics": "burst_repeatability",
        },
        "summary": summary,
        "breakdown": {
            "by_transport": _breakdown(results, "transport"),
            "by_kind": _breakdown(results, "kind"),
            "by_community_type": _breakdown(results, "community_type"),
            "by_region": _breakdown(results, "region"),
            "by_access_tier": _breakdown(results, "access_tier"),
        },
        "acquisition": _acquisition_metrics(results),
        "community_resolution": _community_resolution(results),
        "path_repeatability": _path_repeatability(results),
        "results": [item.to_dict() for item in results],
    }
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(results, generated_at, summary), encoding="utf-8")
    return ReportPaths(json_path=json_path, markdown_path=markdown_path)


def _render_markdown(
    results: list[ProbeResult], generated_at: str, summary: dict[str, int]
) -> str:
    summary_text = ", ".join(f"{key}={value}" for key, value in summary.items())
    lines = [
        "# Finance crawler capability report",
        "",
        f"Generated: {generated_at}",
        "",
        f"Summary: {summary_text}",
        "",
        "## First-pass acquisition",
        "",
        "| view | scope | paths | successes | rate |",
        "|---|---|---:|---:|---:|",
    ]
    acquisition = _acquisition_metrics(results)
    for view in ("direct_first_pass", "resolved_first_pass"):
        for transport, metric in acquisition[view]["by_transport"].items():
            lines.append(
                f"| {view} | {transport} | {metric['paths']} | "
                f"{metric['successes']} | {metric['success_rate']:.1%} |"
            )
    lines.extend(
        [
            "",
            "## Path burst repeatability",
            "",
            "| source | community | region | access | transport | success/runs | outcomes |",
            "|---|---|---|---|---|---:|---|",
        ]
    )
    stability = _path_repeatability(results)
    first_result = {item.source_id: item for item in results}
    for item in stability:
        source = first_result[item["source_id"]]
        outcomes = ", ".join(f"{key}={value}" for key, value in item["outcomes"].items())
        lines.append(
            f"| {item['source_id']} | {source.community_type} | {source.region} | "
            f"{source.access_tier} | {source.transport} | "
            f"{item['successes']}/{item['observations']} | {outcomes} |"
        )
    lines.extend(
        [
            "",
            "## Observations",
            "",
            "| run | source | community | region | access | transport | outcome | HTTP | chars | attempts | ms | error |",
            "|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in results:
        status = str(item.status_code) if item.status_code is not None else "-"
        error = item.error.replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(
            f"| {item.run_index} | {item.source_id} | {item.community_type} | "
            f"{item.region} | {item.access_tier} | {item.transport} | "
            f"{item.outcome.value} | "
            f"{status} | {item.content_chars} | {item.attempts} | {item.elapsed_ms} | {error} |"
        )
    return "\n".join(lines) + "\n"


def _breakdown(results: list[ProbeResult], field: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, Counter[str]] = {}
    for item in results:
        key = str(getattr(item, field))
        grouped.setdefault(key, Counter())[item.outcome.value] += 1
    return {
        key: dict(sorted(counts.items()))
        for key, counts in sorted(grouped.items())
    }


def _path_repeatability(results: list[ProbeResult]) -> list[dict[str, object]]:
    grouped: dict[str, Counter[str]] = {}
    for item in results:
        grouped.setdefault(item.source_id, Counter())[item.outcome.value] += 1
    return [
        {
            "source_id": source_id,
            "observations": sum(outcomes.values()),
            "successes": outcomes.get("success", 0),
            "outcomes": dict(sorted(outcomes.items())),
        }
        for source_id, outcomes in grouped.items()
    ]


def _acquisition_metrics(results: list[ProbeResult]) -> dict[str, object]:
    first_pass = [
        item
        for item in results
        if item.run_index == 1
        and item.outcome.value != "disabled"
        and item.access_tier in PUBLIC_ACCESS_TIERS
    ]

    def direct_outcome(item: ProbeResult) -> str:
        if item.delivery_attempts:
            return item.delivery_attempts[0].outcome.value
        return item.outcome.value

    direct = _availability(first_pass, direct_outcome)
    resolved = _availability(first_pass, lambda item: item.outcome.value)
    fallback_recoveries = sum(
        1
        for item in first_pass
        if len(item.delivery_attempts) > 1
        and item.delivery_attempts[0].outcome.value != "success"
        and item.outcome.value == "success"
    )
    return {
        "direct_first_pass": direct,
        "resolved_first_pass": resolved,
        "fallback_recoveries": fallback_recoveries,
    }


OutcomeSelector = Callable[[ProbeResult], str]


def _availability(
    results: list[ProbeResult], outcome: OutcomeSelector
) -> dict[str, object]:
    return {
        "overall": _availability_bucket(results, outcome),
        "by_transport": {
            key: _availability_bucket(
                [item for item in results if item.transport == key], outcome
            )
            for key in sorted({item.transport for item in results})
        },
        "by_access_tier": {
            key: _availability_bucket(
                [item for item in results if item.access_tier == key], outcome
            )
            for key in sorted({item.access_tier for item in results})
        },
    }


def _availability_bucket(
    results: list[ProbeResult], outcome: OutcomeSelector
) -> dict[str, float | int]:
    paths = len(results)
    successes = sum(1 for item in results if outcome(item) == "success")
    return {
        "paths": paths,
        "successes": successes,
        "success_rate": round(successes / paths, 4) if paths else 0.0,
    }


def _community_resolution(results: list[ProbeResult]) -> list[dict[str, object]]:
    first_pass = [
        item
        for item in results
        if item.run_index == 1
        and item.outcome.value != "disabled"
        and item.access_tier in PUBLIC_ACCESS_TIERS
    ]
    grouped: dict[str, list[ProbeResult]] = {}
    for item in first_pass:
        grouped.setdefault(item.route_group or item.source_id, []).append(item)
    return [
        {
            "route_group": route_group,
            "paths": sorted({item.source_id for item in items}),
            "successful_paths": sorted(
                {item.source_id for item in items if item.outcome.value == "success"}
            ),
            "resolved": any(item.outcome.value == "success" for item in items),
            "transports": sorted({item.transport for item in items}),
        }
        for route_group, items in grouped.items()
    ]
