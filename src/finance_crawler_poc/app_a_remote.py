"""Bounded remote verification for the Research Report Generator MVP.

The command intentionally emits metadata and quality checks only.  It never
prints the MCP bearer token or the private raw payload returned by R2-backed
tools.  A non-terminal job is a failed gate, not an implicit success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


Json = dict[str, Any]
RequestJson = Callable[[Json], Json]
Sleep = Callable[[float], None]

REQUIRED_TOOLS = {
    "resolve_target",
    "plan_research_sources",
    "submit_research_job",
    "get_job_status",
    "retry_research_job",
    "get_research_pack",
    "get_research_report",
    "get_evidence_appendix",
}
TERMINAL_STATUSES = {"completed", "partial", "blocked", "failed", "stale"}
SAFE_RECOMMENDATIONS = {"research_only", "monitor", "requires_human_review"}


@dataclass(frozen=True)
class RemoteGateConfig:
    base_url: str
    token: str
    target: Json
    question: str
    source_strategy: str = "actions"
    include_market_data: bool = True
    include_topic_radar: bool = True
    report_profile: str = "detailed_traceable"
    max_sources: int = 12
    collection_scope: str = "legacy_smoke"
    poll_interval_seconds: float = 10.0
    timeout_seconds: float = 300.0
    idempotency_key: str = ""

    def validate(self) -> None:
        if not self.base_url.startswith("https://"):
            raise ValueError("base_url must use https")
        if not self.token:
            raise ValueError("token is required")
        if not isinstance(self.target, dict) or self.target.get("kind") not in {
            "equity", "etf", "crypto", "company", "industry", "topic", "url"
        }:
            raise ValueError("target.kind is invalid")
        if len(self.question.strip()) < 10:
            raise ValueError("question must contain at least 10 characters")
        if self.source_strategy not in {"latest_published", "actions"}:
            raise ValueError("source_strategy is invalid")
        if self.report_profile not in {"detailed_traceable", "compact_traceable"}:
            raise ValueError("report_profile is invalid")
        if self.collection_scope not in {"full_catalog", "legacy_smoke"}:
            raise ValueError("collection_scope is invalid")
        if not 1 <= self.max_sources <= 5000:
            raise ValueError("max_sources must be between 1 and 5000")
        if self.poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be non-negative")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600")


class RemoteGateError(RuntimeError):
    """An expected remote contract failure that should become a blocked gate."""

    def __init__(self, code: str, details: str = "") -> None:
        super().__init__(code if not details else f"{code}: {details}")
        self.code = code
        self.details = details


def _structured(response: Json) -> Any:
    if not isinstance(response, dict):
        raise RemoteGateError("invalid_mcp_response", "response is not an object")
    if "error" in response:
        error = response.get("error")
        if isinstance(error, dict):
            raise RemoteGateError(str(error.get("message", "mcp_error")))
        raise RemoteGateError("mcp_error")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RemoteGateError("invalid_mcp_response", "result is absent")
    if result.get("isError") is True:
        content = result.get("structuredContent")
        if isinstance(content, dict):
            raise RemoteGateError(str(content.get("error", "tool_error")))
        raise RemoteGateError("tool_error")
    return result.get("structuredContent", result)


def _tool(request_json: RequestJson, counter: list[int], name: str, arguments: Json) -> Any:
    counter[0] += 1
    response = request_json({
        "jsonrpc": "2.0",
        "id": counter[0],
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    return _structured(response)


def _base_result(config: RemoteGateConfig) -> Json:
    return {
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": config.base_url,
        "target": config.target,
        "checks": {},
        "blocking_reasons": [],
        "remote_status": "not_checked",
        "gate_a_status": "blocked",
    }


def _mark(result: Json, name: str, status: str) -> None:
    checks = result.setdefault("checks", {})
    if isinstance(checks, dict):
        checks[name] = status


def _reason(result: Json, reason: str) -> None:
    reasons = result.setdefault("blocking_reasons", [])
    if isinstance(reasons, list) and reason not in reasons:
        reasons.append(reason)


def _finish(result: Json) -> Json:
    checks = result.get("checks", {})
    if isinstance(checks, dict) and checks and all(value == "passed" for value in checks.values()):
        result["remote_status"] = "passed"
        result["gate_a_status"] = "passed"
    elif result.get("remote_status") == "not_checked":
        result["remote_status"] = "failed"
    return result


def run_remote_gate(
    config: RemoteGateConfig,
    *,
    request_json: RequestJson,
    sleep_fn: Sleep = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Json:
    """Run one bounded remote App A chain and return a redacted evidence summary."""

    config.validate()
    result = _base_result(config)
    counter = [0]
    try:
        counter[0] += 1
        _structured(request_json({"jsonrpc": "2.0", "id": counter[0], "method": "initialize"}))
        _mark(result, "mcp_initialize", "passed")

        counter[0] += 1
        catalog = _structured(request_json({"jsonrpc": "2.0", "id": counter[0], "method": "tools/list"}))
        tool_names = {item.get("name") for item in catalog.get("tools", [])} if isinstance(catalog, dict) else set()
        if REQUIRED_TOOLS.issubset(tool_names) and tool_names <= REQUIRED_TOOLS:
            _mark(result, "mcp_tools_catalog", "passed")
        else:
            _mark(result, "mcp_tools_catalog", "failed")
            _reason(result, "mcp_tools_catalog_scope_violation" if not tool_names <= REQUIRED_TOOLS else "mcp_tools_catalog_incomplete")
            return _finish(result)

        plan = _tool(request_json, counter, "plan_research_sources", {
            "target": config.target,
            "requirements": {
                "question": config.question,
                "source_strategy": config.source_strategy,
                "include_market_data": config.include_market_data,
                "include_topic_radar": config.include_topic_radar,
                "report_profile": config.report_profile,
                "max_sources": config.max_sources,
                "collection_scope": config.collection_scope,
            },
        })
        bundle = plan.get("source_bundle") if isinstance(plan, dict) else None
        source_count = bundle.get("source_count") if isinstance(bundle, dict) else None
        expected_source_groups = bundle.get("expected_source_group_count") if isinstance(bundle, dict) else None
        expected_endpoint_attempts = bundle.get("expected_endpoint_count") if isinstance(bundle, dict) else None
        sufficiency = bundle.get("sufficiency") if isinstance(bundle, dict) else None
        if (
            isinstance(bundle, dict)
            and isinstance(source_count, int)
            and (
                (
                    config.collection_scope == "full_catalog"
                    and source_count == expected_source_groups
                    and isinstance(expected_source_groups, int)
                    and isinstance(expected_endpoint_attempts, int)
                    and bundle.get("collection_scope") == "full_catalog"
                )
                or (config.collection_scope != "full_catalog" and 12 <= source_count <= 20)
            )
            and isinstance(sufficiency, dict)
            and sufficiency.get("status") in {"sufficient", "refresh_required"}
            and (
                config.source_strategy != "actions"
                or (
                    bundle.get("strategy") == "refresh"
                    and sufficiency.get("status") == "refresh_required"
                )
            )
        ):
            _mark(result, "planner_source_bundle", "passed")
            result["planner"] = {
                "source_count": source_count,
                "expected_source_group_count": expected_source_groups,
                "expected_endpoint_count": expected_endpoint_attempts,
                "strategy": bundle.get("strategy"),
                "sufficiency_status": sufficiency.get("status"),
            }
        else:
            _mark(result, "planner_source_bundle", "failed")
            _reason(
                result,
                "actions_refresh_not_required" if config.source_strategy == "actions" else "planner_source_bundle_invalid",
            )
            return _finish(result)

        idempotency_key = config.idempotency_key or f"remote_gate_{uuid.uuid4().hex}"
        submitted = _tool(request_json, counter, "submit_research_job", {
            "schema_version": 1,
            "operation": "submit_research_job",
            "idempotency_key": idempotency_key,
            "target": config.target,
            "requirements": {
                "question": config.question,
                "source_strategy": config.source_strategy,
                "include_market_data": config.include_market_data,
                "include_topic_radar": config.include_topic_radar,
                "report_profile": config.report_profile,
                "max_sources": config.max_sources,
                "collection_scope": config.collection_scope,
                "requested_outputs": ["detailed_report", "evidence_appendix"]
                if config.report_profile == "detailed_traceable"
                else ["quick_card", "evidence_appendix"],
            },
        })
        request_id = submitted.get("request_id") if isinstance(submitted, dict) else None
        job_id = submitted.get("job_id") if isinstance(submitted, dict) else None
        if not isinstance(request_id, str) or not isinstance(job_id, str):
            _mark(result, "submit_bounded_job", "failed")
            _reason(result, "submit_missing_request_id")
            return _finish(result)
        _mark(result, "submit_bounded_job", "passed")
        result["request_id"] = request_id
        result["job_id"] = job_id

        deadline = monotonic() + config.timeout_seconds
        status: Any = submitted
        while True:
            status_name = status.get("status") if isinstance(status, dict) else None
            if status_name in TERMINAL_STATUSES:
                break
            if monotonic() >= deadline:
                _mark(result, "job_terminal", "failed")
                _reason(result, "job_poll_timeout")
                return _finish(result)
            status = _tool(request_json, counter, "get_job_status", {"request_id": request_id})
            if config.poll_interval_seconds:
                sleep_fn(config.poll_interval_seconds)
        status_name = status.get("status") if isinstance(status, dict) else None
        if status_name in {"completed", "partial"}:
            _mark(result, "job_terminal", "passed")
        elif status_name == "blocked":
            _mark(result, "job_terminal", "blocked")
            _reason(result, str(status.get("error_code", "job_blocked")))
            result["remote_status"] = "blocked"
            return _finish(result)
        else:
            _mark(result, "job_terminal", "failed")
            _reason(result, str(status.get("error_code", "job_failed")))
            return _finish(result)

        pack = _tool(request_json, counter, "get_research_pack", {"job_id": job_id})
        if isinstance(pack, dict) and pack.get("job_id") == job_id and isinstance(pack.get("evidence"), list):
            _mark(result, "research_pack", "passed")
            result["pack"] = {
                "schema_version": pack.get("schema_version"),
                "evidence_count": len(pack["evidence"]),
                "report_count": len(pack.get("reports", [])) if isinstance(pack.get("reports"), list) else None,
                "as_of": pack.get("as_of"),
                "quality": pack.get("quality"),
                "collection_scope": (pack.get("harness") or {}).get("collection_scope") if isinstance(pack.get("harness"), dict) else None,
                "collection_source_group_count": (pack.get("source_bundle") or {}).get("collection_source_group_count") if isinstance(pack.get("source_bundle"), dict) else None,
                "endpoint_attempt_count": (pack.get("source_bundle") or {}).get("endpoint_attempt_count") if isinstance(pack.get("source_bundle"), dict) else None,
                "normalized_item_count": (pack.get("source_bundle") or {}).get("normalized_item_count") if isinstance(pack.get("source_bundle"), dict) else None,
            }
            if config.collection_scope == "full_catalog":
                if (
                    result["pack"]["collection_scope"] == "full_catalog"
                    and result["pack"]["collection_source_group_count"] == result["planner"]["expected_source_group_count"]
                    and result["pack"]["endpoint_attempt_count"] == result["planner"]["expected_endpoint_count"]
                    and isinstance(pack.get("signals"), dict)
                    and isinstance(pack.get("action_tasks"), list)
                    and isinstance(pack.get("action_receipts"), list)
                ):
                    _mark(result, "h3_mvp_harness", "passed")
                else:
                    _mark(result, "h3_mvp_harness", "failed")
                    _reason(result, "full_catalog_harness_artifacts_missing")
        else:
            _mark(result, "research_pack", "failed")
            _reason(result, "research_pack_readback_invalid")
            return _finish(result)

        report = _tool(request_json, counter, "get_research_report", {"job_id": job_id})
        reports = report.get("reports") if isinstance(report, dict) else None
        if isinstance(reports, list) and reports:
            _mark(result, "research_report", "passed")
            safe = all(
                isinstance(item, dict) and item.get("recommendation_status", "research_only") in SAFE_RECOMMENDATIONS
                for item in reports
            )
            _mark(result, "no_personalized_recommendation", "passed" if safe else "failed")
            if not safe:
                _reason(result, "personalized_recommendation_detected")
        else:
            _mark(result, "research_report", "failed")
            _reason(result, "research_report_readback_invalid")

        appendix = _tool(request_json, counter, "get_evidence_appendix", {"job_id": job_id})
        if isinstance(appendix, dict) and isinstance(appendix.get("evidence"), list):
            _mark(result, "evidence_appendix", "passed")
            result["appendix"] = {"evidence_count": len(appendix["evidence"])}
        else:
            _mark(result, "evidence_appendix", "failed")
            _reason(result, "evidence_appendix_readback_invalid")
    except (RemoteGateError, KeyError, TypeError, ValueError) as error:
        _reason(result, error.code if isinstance(error, RemoteGateError) else "remote_contract_error")
        result["remote_status"] = "failed"
    return _finish(result)


def _http_request(base_url: str, token: str, timeout: float) -> RequestJson:
    def request_json(payload: Json) -> Json:
        request = urllib.request.Request(
            base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "finance-research-remote-gate/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(2_000_000)
        except (urllib.error.URLError, TimeoutError) as error:
            raise RemoteGateError("remote_http_error", str(error)) from error
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RemoteGateError("remote_response_not_json") from error
        if not isinstance(parsed, dict):
            raise RemoteGateError("remote_response_not_object")
        return parsed

    return request_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the bounded remote App A Gate A chain.")
    parser.add_argument("--base-url", default=os.environ.get("INGEST_WORKER_URL", ""), help="Worker base URL or /mcp URL")
    parser.add_argument("--token-env", default="FINANCE_RESEARCH_MCP_TOKEN", help="Environment variable containing MCP token")
    parser.add_argument("--kind", default="crypto")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--question", default="What are the current drivers and risks for this target?")
    parser.add_argument("--source-strategy", choices=["latest_published", "actions"], default="actions")
    parser.add_argument("--max-sources", type=int, default=12)
    parser.add_argument("--collection-scope", choices=["full_catalog", "legacy_smoke"], default="full_catalog")
    parser.add_argument("--poll-interval-seconds", type=float, default=10.0)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--idempotency-key", default="")
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")
    if base_url and not base_url.endswith("/mcp"):
        base_url += "/mcp"
    token = os.environ.get(args.token_env, "")
    config = RemoteGateConfig(
        base_url=base_url,
        token=token,
        target={"kind": args.kind, "symbol": args.symbol},
        question=args.question,
        source_strategy=args.source_strategy,
        max_sources=args.max_sources,
        collection_scope=args.collection_scope,
        poll_interval_seconds=args.poll_interval_seconds,
        timeout_seconds=args.timeout_seconds,
        idempotency_key=args.idempotency_key,
    )
    try:
        config.validate()
        request_json = _http_request(config.base_url, config.token, min(config.timeout_seconds, 30.0))
        result = run_remote_gate(config, request_json=request_json)
    except ValueError as error:
        print(json.dumps({"gate_a_status": "blocked", "blocking_reasons": [str(error)]}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("gate_a_status") == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
