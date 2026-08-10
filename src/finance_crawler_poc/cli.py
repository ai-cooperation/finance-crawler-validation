from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from finance_crawler_poc.adapters import Crawl4AIAdapter, HttpAdapter
from finance_crawler_poc.manifest import ManifestError, load_manifest
from finance_crawler_poc.models import ProbeResult
from finance_crawler_poc.probe import probe_source
from finance_crawler_poc.report import write_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe finance crawler source paths")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), default=1)
    return parser


async def run(
    manifest_path: Path, output_dir: Path, *, repetitions: int = 1
) -> list[ProbeResult]:
    if repetitions not in {1, 2, 3}:
        raise ValueError("repetitions must be between 1 and 3")
    manifest = load_manifest(manifest_path)
    http_adapter = HttpAdapter(relay_base_url=os.environ.get("CF_RELAY_BASE_URL"))
    browser_adapter = Crawl4AIAdapter()
    adapters = {
        "json_api": http_adapter,
        "rss": http_adapter,
        "browser": browser_adapter,
    }
    results: list[ProbeResult] = []
    try:
        for run_index in range(1, repetitions + 1):
            for source in manifest.sources:
                print(
                    json.dumps(
                        {
                            "event": "probe_started",
                            "run_index": run_index,
                            "source_id": source.id,
                            "url": source.url,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                result = await probe_source(
                    source, adapters[source.transport], run_index=run_index
                )
                results.append(result)
                print(
                    json.dumps(
                        {
                            "event": "probe_finished",
                            "run_index": run_index,
                            "source_id": source.id,
                            "outcome": result.outcome.value,
                            "status_code": result.status_code,
                            "elapsed_ms": result.elapsed_ms,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if source.enabled:
                    await asyncio.sleep(1)
    finally:
        await browser_adapter.close()
        await http_adapter.close()

    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    write_reports(results, output_dir, generated_at=generated_at)
    return results


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(run(args.manifest, args.output, repetitions=args.repeat))
    except ManifestError as exc:
        raise SystemExit(f"invalid manifest: {exc}") from exc


if __name__ == "__main__":
    main()
