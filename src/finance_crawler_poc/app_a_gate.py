"""Bounded, fail-closed checks for the Research Report Generator MVP gate.

The detector deliberately distinguishes local contract evidence from the remote
Gate A.  A local green result must never be reported as a deployed Actions →
OIDC → D1/R2 run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_FILES = (
    "20260820-big-pickle-planner-smoke.json",
    "20260820-big-pickle-submit-status-smoke.json",
    "20260820-local-callback-retry-smoke.json",
    "20260820-target-binding-smoke.json",
)


def inspect_local_evidence(evidence_dir: Path) -> dict[str, Any]:
    """Inspect the committed local App A evidence without contacting services."""

    checks: dict[str, str] = {
        "evidence_files": "passed",
        "planner_source_bundle": "passed",
        "client_submit_status": "passed",
        "callback_retry_contract": "passed",
        "target_scope": "passed",
    }
    reasons: list[str] = []
    payloads: dict[str, Any] = {}

    for filename in REQUIRED_FILES:
        path = evidence_dir / filename
        if not path.is_file():
            checks["evidence_files"] = "failed"
            reasons.append(f"missing_evidence:{filename}")
            continue
        try:
            payloads[filename] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            checks["evidence_files"] = "failed"
            reasons.append(f"invalid_evidence:{filename}")

    planner = payloads.get(REQUIRED_FILES[0])
    request = planner.get("request") if isinstance(planner, dict) else None
    planner_result = planner.get("result") if isinstance(planner, dict) else None
    source_count = planner_result.get("source_count") if isinstance(planner_result, dict) else None
    if (
        not isinstance(request, dict)
        or not isinstance(request.get("target"), dict)
        or not isinstance(planner_result, dict)
        or not isinstance(source_count, int)
        or not 12 <= source_count <= 20
        or planner_result.get("sufficiency_status") not in {"sufficient", "refresh_required"}
    ):
        checks["planner_source_bundle"] = "failed"
        reasons.append("planner_source_bundle_failed")

    submit_status = payloads.get(REQUIRED_FILES[1])
    successful_chain = submit_status.get("successful_chain") if isinstance(submit_status, dict) else None
    submitted = successful_chain.get("submit_research_job") if isinstance(successful_chain, dict) else None
    observed_status = successful_chain.get("get_job_status") if isinstance(successful_chain, dict) else None
    if (
        not isinstance(successful_chain, dict)
        or not isinstance(submitted, dict)
        or not isinstance(observed_status, dict)
        or not submitted.get("request_id")
        or not submitted.get("job_id")
        or submitted.get("retryable") is not True
        or submit_status.get("recommendation_generated") is not False
        or submit_status.get("report_generated") is not False
    ):
        checks["client_submit_status"] = "failed"
        reasons.append("client_submit_status_failed")

    callback = payloads.get(REQUIRED_FILES[2])
    callback_checks = callback.get("checks") if isinstance(callback, dict) else None
    test_result = callback.get("test_result") if isinstance(callback, dict) else None
    if (
        not isinstance(callback_checks, dict)
        or not callback_checks
        or any(value != "passed" for value in callback_checks.values())
        or not isinstance(test_result, dict)
        or test_result.get("tests") != test_result.get("passed")
        or not isinstance(test_result.get("tests"), int)
    ):
        checks["callback_retry_contract"] = "failed"
        reasons.append("callback_retry_contract_failed")

    target_scope = payloads.get(REQUIRED_FILES[3])
    target_checks = target_scope.get("checks") if isinstance(target_scope, dict) else None
    target_result = target_scope.get("test_result") if isinstance(target_scope, dict) else None
    if (
        not isinstance(target_checks, dict)
        or not target_checks
        or any(value != "passed" for value in target_checks.values())
        or not isinstance(target_result, dict)
        or target_result.get("worker_tests") != target_result.get("worker_passed")
        or target_result.get("python_tests") != target_result.get("python_passed")
    ):
        checks["target_scope"] = "failed"
        reasons.append("target_scope_failed")

    if all(value == "passed" for value in checks.values()):
        local_status = "passed"
    else:
        local_status = "failed"

    if local_status != "passed":
        gate_a_status = "blocked"
    else:
        gate_a_status = "blocked"
        reasons.append("remote_gate_not_checked")

    return {
        "local_status": local_status,
        "remote_status": "not_checked",
        "gate_a_status": gate_a_status,
        "checks": checks,
        "blocking_reasons": reasons,
        "required_remote_evidence": [
            "deployed_worker_version",
            "mcp_tools_list_with_valid_token",
            "actions_dispatch_run",
            "oidc_callback_read_back",
            "private_r2_d1_research_pack_read_back",
            "report_profile_output_contract",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check local evidence for App A Gate A readiness.")
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("experiments/app-a"),
        help="Directory containing the three App A smoke artifacts.",
    )
    args = parser.parse_args(argv)
    result = inspect_local_evidence(args.evidence_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["local_status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
