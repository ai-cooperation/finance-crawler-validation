from __future__ import annotations

import json
from pathlib import Path

from finance_crawler_poc.app_a_gate import inspect_local_evidence


ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "experiments" / "app-a"


def test_local_evidence_passes_contract_but_does_not_claim_remote_gate() -> None:
    result = inspect_local_evidence(EVIDENCE)

    assert result["local_status"] == "passed"
    assert result["gate_a_status"] == "blocked"
    assert result["remote_status"] == "not_checked"
    assert result["blocking_reasons"] == ["remote_gate_not_checked"]
    assert result["checks"]["planner_source_bundle"] == "passed"
    assert result["checks"]["callback_retry_contract"] == "passed"
    assert result["checks"]["target_scope"] == "passed"


def test_local_evidence_fails_closed_when_callback_check_is_not_passed(tmp_path: Path) -> None:
    for filename in (
        "20260820-big-pickle-planner-smoke.json",
        "20260820-big-pickle-submit-status-smoke.json",
        "20260820-local-callback-retry-smoke.json",
        "20260820-target-binding-smoke.json",
    ):
        (tmp_path / filename).write_text((EVIDENCE / filename).read_text(), encoding="utf-8")
    payload = json.loads((tmp_path / "20260820-local-callback-retry-smoke.json").read_text())
    payload["checks"]["unapproved_run_source_rejected"] = "failed"
    (tmp_path / "20260820-local-callback-retry-smoke.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    result = inspect_local_evidence(tmp_path)

    assert result["local_status"] == "failed"
    assert result["gate_a_status"] == "blocked"
    assert "callback_retry_contract_failed" in result["blocking_reasons"]
