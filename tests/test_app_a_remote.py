from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import finance_crawler_poc.app_a_remote as remote
from finance_crawler_poc.app_a_remote import RemoteGateConfig, run_remote_gate


ITEM_ID = "a" * 64


def _config() -> RemoteGateConfig:
    return RemoteGateConfig(
        base_url="https://worker.example/mcp",
        token="test-token",
        target={"kind": "crypto", "symbol": "BTC"},
        question="What are the current drivers and risks for BTC?",
        source_strategy="latest_published",
        include_market_data=True,
        max_sources=12,
        poll_interval_seconds=0,
        timeout_seconds=5,
        idempotency_key="remote-gate-test-20260821",
    )


def _success_sequence() -> list[dict[str, Any]]:
    return [
        {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": name} for name in (
            "resolve_target", "plan_research_sources", "submit_research_job",
            "get_job_status", "retry_research_job", "get_research_pack",
            "get_research_report", "get_evidence_appendix",
        )]}},
        {"jsonrpc": "2.0", "id": 3, "result": {"structuredContent": {
            "source_bundle": {"source_count": 12, "strategy": "reuse", "sufficiency": {"status": "sufficient"}},
        }}},
        {"jsonrpc": "2.0", "id": 4, "result": {"structuredContent": {
            "request_id": "request-1", "job_id": "research_20260821_abc12345", "status": "queued",
        }}},
        {"jsonrpc": "2.0", "id": 5, "result": {"structuredContent": {
            "request_id": "request-1", "job_id": "research_20260821_abc12345", "status": "partial",
        }}},
        {"jsonrpc": "2.0", "id": 6, "result": {"structuredContent": {
            "schema_version": 1, "job_id": "research_20260821_abc12345", "reports": [{
                "report_id": "report_20260821_abc12345", "recommendation_status": "research_only",
            }], "evidence": [{"evidence_id": ITEM_ID}],
        }}},
        {"jsonrpc": "2.0", "id": 7, "result": {"structuredContent": {
            "schema_version": 1, "job_id": "research_20260821_abc12345", "reports": [{
                "report_id": "report_20260821_abc12345", "recommendation_status": "research_only",
            }],
        }}},
        {"jsonrpc": "2.0", "id": 8, "result": {"structuredContent": {
            "schema_version": 1, "job_id": "research_20260821_abc12345", "evidence": [{"evidence_id": ITEM_ID}],
        }}},
    ]


def test_remote_gate_passes_and_never_serializes_token() -> None:
    responses = iter(_success_sequence())
    requests: list[dict[str, Any]] = []

    def request_json(payload: dict[str, Any]) -> dict[str, Any]:
        requests.append(payload)
        return next(responses)

    result = run_remote_gate(_config(), request_json=request_json)

    assert result["remote_status"] == "passed"
    assert result["gate_a_status"] == "passed"
    assert all(value == "passed" for value in result["checks"].values())
    assert "test-token" not in str(result)
    assert [request["method"] for request in requests] == [
        "initialize", "tools/list", "tools/call", "tools/call", "tools/call",
        "tools/call", "tools/call", "tools/call",
    ]


def test_remote_gate_stops_on_blocked_submit_without_reading_private_artifacts() -> None:
    tools = [{"name": name} for name in (
        "resolve_target", "plan_research_sources", "submit_research_job",
        "get_job_status", "retry_research_job", "get_research_pack",
        "get_research_report", "get_evidence_appendix",
    )]
    responses = iter([
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}},
        {"jsonrpc": "2.0", "id": 3, "result": {"structuredContent": {
            "source_bundle": {"source_count": 12, "strategy": "refresh", "sufficiency": {"status": "refresh_required"}},
        }}},
        {"jsonrpc": "2.0", "id": 4, "result": {"structuredContent": {
            "request_id": "request-1", "job_id": "research_20260821_abc12345",
            "status": "blocked", "error_code": "actions_dispatch_not_configured",
        }}},
    ])

    result = run_remote_gate(_config(), request_json=lambda _payload: next(responses))

    assert result["remote_status"] == "blocked"
    assert result["gate_a_status"] == "blocked"
    assert result["checks"]["job_terminal"] == "blocked"
    assert "research_pack" not in result["checks"]
    assert "actions_dispatch_not_configured" in result["blocking_reasons"]


def test_actions_gate_does_not_claim_refresh_when_planner_reuses_snapshot() -> None:
    responses = _success_sequence()
    responses[2]["result"]["structuredContent"]["source_bundle"]["strategy"] = "reuse"
    responses[2]["result"]["structuredContent"]["source_bundle"]["sufficiency"]["status"] = "sufficient"
    iterator = iter(responses[:3])
    config = RemoteGateConfig(**{**_config().__dict__, "source_strategy": "actions"})

    result = run_remote_gate(config, request_json=lambda _payload: next(iterator))

    assert result["remote_status"] == "failed"
    assert result["gate_a_status"] == "blocked"
    assert result["blocking_reasons"] == ["actions_refresh_not_required"]


def test_full_catalog_gate_requires_harness_artifacts() -> None:
    responses = _success_sequence()
    responses[2]["result"]["structuredContent"] = {
        "source_bundle": {
            "source_count": 135,
            "collection_scope": "full_catalog",
            "strategy": "refresh",
            "sufficiency": {"status": "refresh_required"},
        },
    }
    responses[5]["result"]["structuredContent"].update({
        "harness": {"collection_scope": "full_catalog"},
        "source_bundle": {
            "collection_source_group_count": 135,
            "endpoint_attempt_count": 181,
            "normalized_item_count": 200,
        },
        "signals": {"signals": []},
        "action_tasks": [],
        "action_receipts": [],
    })
    config = RemoteGateConfig(**{**_config().__dict__, "collection_scope": "full_catalog"})
    responses_iter = iter(responses)
    result = run_remote_gate(config, request_json=lambda _payload: next(responses_iter))
    assert result["checks"]["h3_mvp_harness"] == "passed"

def test_remote_gate_rejects_an_unauthorized_tool_from_catalog() -> None:
    responses = _success_sequence()
    responses[1]["result"]["tools"].append({"name": "execute_trade"})
    iterator = iter(responses[:2])

    result = run_remote_gate(_config(), request_json=lambda _payload: next(iterator))

    assert result["remote_status"] == "failed"
    assert result["checks"]["mcp_tools_catalog"] == "failed"
    assert result["blocking_reasons"] == ["mcp_tools_catalog_scope_violation"]


def test_remote_gate_fails_closed_when_claim_is_personalized() -> None:
    responses = _success_sequence()
    responses[6]["result"]["structuredContent"]["reports"][0]["recommendation_status"] = "buy_now"
    iterator = iter(responses)

    result = run_remote_gate(_config(), request_json=lambda _payload: next(iterator))

    assert result["remote_status"] == "failed"
    assert result["gate_a_status"] == "blocked"
    assert result["checks"]["no_personalized_recommendation"] == "failed"


def test_remote_gate_rejects_unbounded_timeout_configuration() -> None:
    config = _config()
    config = RemoteGateConfig(**{**config.__dict__, "timeout_seconds": 0})

    try:
        run_remote_gate(config, request_json=lambda _payload: {})
    except ValueError as error:
        assert "timeout_seconds" in str(error)
    else:
        raise AssertionError("expected timeout validation")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("base_url", "http://worker.example/mcp", "https"),
        ("token", "", "token"),
        ("target", {"kind": "unknown"}, "target.kind"),
        ("question", "short", "question"),
        ("source_strategy", "manual", "source_strategy"),
        ("report_profile", "full", "report_profile"),
        ("max_sources", 0, "max_sources"),
        ("poll_interval_seconds", -1, "poll_interval_seconds"),
        ("timeout_seconds", 3601, "timeout_seconds"),
    ],
)
def test_remote_gate_config_validation_boundaries(field: str, value: Any, message: str) -> None:
    config = RemoteGateConfig(**{**_config().__dict__, field: value})
    with pytest.raises(ValueError, match=message):
        config.validate()


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"error": {"message": "bad request"}},
        {"error": "bad request"},
        {"result": {"isError": True, "structuredContent": {"error": "tool failed"}}},
        {"result": {"isError": True}},
    ],
)
def test_structured_rejects_invalid_mcp_responses(response: Any) -> None:
    with pytest.raises(remote.RemoteGateError):
        remote._structured(response)


def test_structured_accepts_result_and_structured_content() -> None:
    assert remote._structured({"result": {"value": 1}}) == {"value": 1}
    assert remote._structured({"result": {"structuredContent": {"value": 2}}}) == {"value": 2}


def test_remote_gate_rejects_incomplete_catalog_and_invalid_planner() -> None:
    incomplete = iter([
        {"result": {}},
        {"result": {"tools": [{"name": "resolve_target"}]}},
    ])
    result = run_remote_gate(_config(), request_json=lambda _payload: next(incomplete))
    assert result["blocking_reasons"] == ["mcp_tools_catalog_incomplete"]

    tools = [{"name": name} for name in remote.REQUIRED_TOOLS]
    invalid_plan = iter([
        {"result": {}},
        {"result": {"tools": tools}},
        {"result": {"structuredContent": {"source_bundle": {"source_count": 1}}}},
    ])
    result = run_remote_gate(_config(), request_json=lambda _payload: next(invalid_plan))
    assert result["blocking_reasons"] == ["planner_source_bundle_invalid"]


def test_remote_gate_handles_submit_and_poll_boundaries() -> None:
    tools = [{"name": name} for name in remote.REQUIRED_TOOLS]
    base = [
        {"result": {}},
        {"result": {"tools": tools}},
        {"result": {"structuredContent": {
            "source_bundle": {"source_count": 12, "strategy": "reuse", "sufficiency": {"status": "sufficient"}},
        }}},
    ]
    missing_submit = iter(base + [{"result": {"structuredContent": {"status": "queued"}}}])
    result = run_remote_gate(_config(), request_json=lambda _payload: next(missing_submit))
    assert result["blocking_reasons"] == ["submit_missing_request_id"]

    failed_job = iter(base + [
        {"result": {"structuredContent": {"request_id": "r", "job_id": "j", "status": "queued"}}},
        {"result": {"structuredContent": {"request_id": "r", "job_id": "j", "status": "failed", "error_code": "source_error"}}},
    ])
    result = run_remote_gate(_config(), request_json=lambda _payload: next(failed_job))
    assert result["blocking_reasons"] == ["source_error"]

    timeout = iter(base + [
        {"result": {"structuredContent": {"request_id": "r", "job_id": "j", "status": "queued"}}},
    ])
    now = iter([0.0, 2.0])
    result = run_remote_gate(
        RemoteGateConfig(**{**_config().__dict__, "timeout_seconds": 1}),
        request_json=lambda _payload: next(timeout),
        monotonic=lambda: next(now),
    )
    assert result["blocking_reasons"] == ["job_poll_timeout"]


def test_remote_gate_handles_readback_contract_failures() -> None:
    tools = [{"name": name} for name in remote.REQUIRED_TOOLS]
    prefix = [
        {"result": {}},
        {"result": {"tools": tools}},
        {"result": {"structuredContent": {
            "source_bundle": {"source_count": 12, "strategy": "reuse", "sufficiency": {"status": "sufficient"}},
        }}},
        {"result": {"structuredContent": {"request_id": "r", "job_id": "j", "status": "completed"}}},
    ]
    bad_pack = iter(prefix + [{"result": {"structuredContent": {"job_id": "other"}}}])
    result = run_remote_gate(_config(), request_json=lambda _payload: next(bad_pack))
    assert result["blocking_reasons"] == ["research_pack_readback_invalid"]

    bad_report = iter(prefix + [
        {"result": {"structuredContent": {"job_id": "j", "evidence": []}}},
        {"result": {"structuredContent": {"reports": []}}},
        {"result": {"structuredContent": {"evidence": []}}},
    ])
    result = run_remote_gate(_config(), request_json=lambda _payload: next(bad_report))
    assert result["checks"]["research_report"] == "failed"
    assert result["checks"]["evidence_appendix"] == "passed"


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def test_http_request_parses_json_and_maps_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []

    def capture_request(request: Any, **_kwargs: Any) -> _Response:
        captured.append(request)
        return _Response(b'{"result": {}}')

    monkeypatch.setattr(remote.urllib.request, "urlopen", capture_request)
    request_json = remote._http_request("https://worker.example/mcp", "token", 1)
    assert request_json({"method": "initialize"}) == {"result": {}}
    assert captured[0].get_header("User-agent") == "finance-research-remote-gate/1.0"

    def raise_url_error(*_args: Any, **_kwargs: Any) -> Any:
        raise remote.urllib.error.URLError("offline")

    monkeypatch.setattr(remote.urllib.request, "urlopen", raise_url_error)
    with pytest.raises(remote.RemoteGateError, match="remote_http_error"):
        request_json({"method": "initialize"})

    monkeypatch.setattr(remote.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(b"not-json"))
    with pytest.raises(remote.RemoteGateError, match="remote_response_not_json"):
        request_json({"method": "initialize"})

    monkeypatch.setattr(remote.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(b"[]"))
    with pytest.raises(remote.RemoteGateError, match="remote_response_not_object"):
        request_json({"method": "initialize"})


def test_cli_main_fails_closed_without_token(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("FINANCE_RESEARCH_MCP_TOKEN", raising=False)
    assert remote.main(["--base-url", "https://worker.example"]) == 2
    assert "token is required" in capsys.readouterr().out
