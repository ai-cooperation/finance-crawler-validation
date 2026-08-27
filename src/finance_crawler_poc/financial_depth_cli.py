from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.market_depth import (
    build_financial_depth,
    fetch_market_history,
    fetch_market_provider_bundle,
)


_ORIGINAL_FETCH_MARKET_HISTORY = fetch_market_history


def build_depth_artifact(
    market_snapshot_path: Path,
    raw_items_path: Path,
    *,
    target: dict[str, Any],
    history_days: int,
    fundamentals: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    peer_valuation: dict[str, Any] | None = None
    try:
        market_snapshot = json.loads(market_snapshot_path.read_text(encoding="utf-8"))
        raw_items = json.loads(raw_items_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read financial-depth inputs: {exc}") from exc
    if not isinstance(market_snapshot, dict) or not isinstance(raw_items, list):
        raise ValueError("financial-depth inputs must be a market snapshot object and raw-item array")
    as_of = generated_at or str(market_snapshot.get("as_of") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    try:
        # Preserve the old seam for unit tests and downstream callers that
        # inject a deterministic history provider.  Production uses the full
        # provider bundle, including volume/ETF/derivatives/on-chain data.
        if fetch_market_history is not _ORIGINAL_FETCH_MARKET_HISTORY:
            history_result = fetch_market_history(target, days=history_days)
            if len(history_result) == 3:
                points, provider, history_url = history_result
                history_response_sha256 = None
            else:
                points, provider, history_url, history_response_sha256 = history_result
            provider_data = None
        else:
            bundle = fetch_market_provider_bundle(target, days=history_days)
            points = bundle["points"]
            provider = bundle["provider"]
            history_url = bundle["history_url"]
            history_response_sha256 = bundle["history_response_sha256"]
            if fundamentals is None and isinstance(bundle.get("fundamentals"), dict):
                fundamentals = bundle["fundamentals"]
            if isinstance(bundle.get("peer_valuation"), dict):
                peer_valuation = bundle["peer_valuation"]
            provider_data = bundle.get("provider_data") if isinstance(bundle.get("provider_data"), dict) else None
    except (RuntimeError, ValueError) as exc:
        points, provider, history_response_sha256 = [], "unavailable", None
        history_url = f"https://provider.invalid/history?target={target.get('symbol', '')}"
        failure = str(exc)
        provider_data = None
    else:
        failure = None
    depth = build_financial_depth(
        target=target,
        market_snapshot=market_snapshot,
        history_points=points,
        history_provider=provider,
        history_url=history_url,
        as_of=as_of,
        evidence=raw_items,
        fundamentals=fundamentals,
        peer_valuation=peer_valuation,
        history_response_sha256=history_response_sha256,
        provider_data=provider_data,
    )
    if failure is not None:
        depth["status"] = "research_only"
        depth["time_series"]["status"] = "unavailable"
        depth["time_series"]["missing_reason"] = failure[:500]
    enriched = {**market_snapshot, "financial_depth": depth}
    validate_contract("market-snapshot", enriched)
    return enriched, depth


def write_depth_artifact(
    market_snapshot_path: Path,
    raw_items_path: Path,
    output_directory: Path,
    *,
    target: dict[str, Any],
    history_days: int,
    fundamentals_path: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    fundamentals: dict[str, Any] | None = None
    if fundamentals_path is not None:
        try:
            parsed_fundamentals = json.loads(fundamentals_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read fundamentals input: {exc}") from exc
        if not isinstance(parsed_fundamentals, dict):
            raise ValueError("fundamentals input must be an object")
        fundamentals = parsed_fundamentals
    enriched, depth = build_depth_artifact(
        market_snapshot_path,
        raw_items_path,
        target=target,
        history_days=history_days,
        fundamentals=fundamentals,
        generated_at=generated_at,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    _write_json(market_snapshot_path, enriched)
    envelope_path = market_snapshot_path.parent / "market-alignment-envelope.json"
    if envelope_path.exists():
        try:
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot update market alignment envelope: {exc}") from exc
        if not isinstance(envelope, dict):
            raise ValueError("market alignment envelope must be an object")
        # Keep the alignment request compact. The full depth bundle (which can
        # include hundreds of canonical evidence rows) is sent through its
        # dedicated ingest endpoint below, avoiding a request-body overflow.
        compact_market_snapshot = {
            key: value for key, value in enriched.items() if key != "financial_depth"
        }
        updated_envelope = {**envelope, "market_snapshot": compact_market_snapshot}
        validate_contract("market-alignment-envelope", updated_envelope)
        _write_json(envelope_path, updated_envelope)
        depth_envelope = {
            "schema_version": 1,
            "operation": "upsert_financial_depth",
            "run_id": str(envelope["run_id"]),
            "workflow_run_id": str(envelope.get("workflow_run_id") or os.environ.get("GITHUB_RUN_ID", "0")),
            "commit_sha": str(envelope.get("commit_sha") or os.environ.get("GITHUB_SHA", "0" * 40)),
            "market_snapshot_id": str(enriched["snapshot_id"]),
            "financial_depth": depth,
        }
        _write_json(envelope_path.parent / "financial-depth-envelope.json", depth_envelope)
    _write_json(output_directory / "financial-depth.json", depth)
    summary = {
        "schema_version": 1,
        "status": depth["status"],
        "time_series_status": depth["time_series"]["status"],
        "valuation_status": depth["valuation"]["status"],
        "scenario_status": depth["scenarios"]["status"],
        "conflict_level": depth["source_conflicts"][0]["conflict_level"] if depth["source_conflicts"] else "unknown",
    }
    _write_json(output_directory / "financial-depth-report.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Attach historical market depth and professional-analysis metadata")
    parser.add_argument("--market-snapshot", type=Path, required=True)
    parser.add_argument("--raw-items", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-json", required=True)
    parser.add_argument("--history-days", type=int, default=365)
    parser.add_argument("--fundamentals-json", type=Path, help="Optional provider-normalized fundamentals JSON")
    parser.add_argument("--generated-at")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        target = json.loads(args.target_json)
    except json.JSONDecodeError as exc:
        raise ValueError("--target-json must be valid JSON") from exc
    if not isinstance(target, dict) or not isinstance(target.get("kind"), str):
        raise ValueError("--target-json must be an object with a string kind")
    summary = write_depth_artifact(
        args.market_snapshot,
        args.raw_items,
        args.output,
        target=target,
        history_days=args.history_days,
        fundamentals_path=args.fundamentals_json,
        generated_at=args.generated_at,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
