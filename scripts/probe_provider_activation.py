#!/usr/bin/env python3
"""Export provider connector registries and run bounded live probes."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.provider_activation import (
    build_provider_activation_registry,
    build_provider_runtime_registry,
    probe_activation_registry,
)
from finance_crawler_poc.provider_catalog import DEFAULT_CATALOG_PATH, load_provider_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument(
        "--registry-output",
        type=Path,
        default=PROJECT_ROOT / "data" / "provider-activation-registry.json",
    )
    parser.add_argument(
        "--worker-output",
        type=Path,
        default=PROJECT_ROOT / "ingest-worker" / "src" / "generated" / "provider-registry.json",
    )
    parser.add_argument("--probe-output", type=Path)
    parser.add_argument("--skip-probe", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--provider", action="append", default=[])
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    catalog = load_provider_catalog(args.catalog)
    activation = build_provider_activation_registry(catalog)
    validate_contract("provider-activation-registry", activation)
    runtime = build_provider_runtime_registry(catalog, activation)
    _write_json(args.registry_output, activation)
    _write_json(args.worker_output, runtime)
    if args.skip_probe:
        print(json.dumps({"registry": str(args.registry_output), "runtime": str(args.worker_output)}))
        return 0

    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    probe_registry = activation
    if args.provider:
        wanted = set(args.provider)
        selected = [row for row in activation["connections"] if row["provider_id"] in wanted]
        missing = wanted - {row["provider_id"] for row in selected}
        if missing:
            raise SystemExit(f"unknown non-route provider(s): {', '.join(sorted(missing))}")
        probe_registry = {**activation, "connections": selected}
    report = await probe_activation_registry(
        probe_registry,
        checked_at=checked_at,
        concurrency=args.concurrency,
    )
    validate_contract("provider-activation-report", report)
    output = args.probe_output or _default_probe_path(checked_at)
    _write_json(output, report)
    print(json.dumps({"registry": str(args.registry_output), "runtime": str(args.worker_output), "probe": str(output), "summary": report["summary"]}, ensure_ascii=False))
    return 0


def _default_probe_path(checked_at: str) -> Path:
    stamp = checked_at.replace("-", "").replace(":", "").replace(".", "").casefold()
    return PROJECT_ROOT / "experiments" / "provider-activation" / f"provider-activation-{stamp}.json"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
