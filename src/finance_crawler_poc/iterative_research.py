"""Layer 3: bounded state machine for gap-driven research collection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from .context_requirements import build_context_coverage, build_context_gap_report, build_context_packs
from .evidence_gap_broker import build_gap_plan, select_context_for_gap_plan
from .research_planner import build_research_plan


Coverage = Mapping[str, Any]
GapPlanner = Callable[[Coverage, int, list[str]], Mapping[str, Any]]
Collector = Callable[[Mapping[str, Any]], Any]
Verifier = Callable[[Any], Coverage]


def _ratio(coverage: Coverage) -> float:
    summary = coverage.get("summary") if isinstance(coverage.get("summary"), Mapping) else {}
    value = summary.get("coverage_ratio", 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _released(coverage: Coverage) -> bool:
    summary = coverage.get("summary") if isinstance(coverage.get("summary"), Mapping) else {}
    conflicts = coverage.get("unresolved_p0_conflicts")
    return bool(summary.get("l3_ready")) and not conflicts


def _status_by_id(coverage: Coverage) -> dict[str, str]:
    return {
        str(item.get("requirement_id")): str(item.get("status") or "unresolved")
        for item in coverage.get("requirements", [])
        if isinstance(item, Mapping) and item.get("requirement_id")
    }


def run_iterative_controller(
    *, research_plan: Mapping[str, Any], initial_coverage: Coverage, plan_round: GapPlanner,
    collect_round: Collector, verify_round: Verifier, generated_at: str, max_rounds: int = 4,
    low_gain_threshold: float = 0.10,
) -> dict[str, Any]:
    """Execute inventory plus at most four evidence collection rounds.

    The controller knows no URLs or parsers.  Those side effects stay behind
    the broker/collector ports so state transitions remain deterministic.
    """

    if max_rounds < 1 or max_rounds > 4:
        raise ValueError("max_rounds must be between 1 and 4")
    question_sha256 = str(research_plan.get("question_sha256") or "")
    if len(question_sha256) != 64:
        raise ValueError("research_plan.question_sha256 must be a SHA-256 digest")
    current = deepcopy(dict(initial_coverage))
    initial_ratio = _ratio(current)
    iterations: list[dict[str, Any]] = [{
        "round": 0,
        "state": "released" if _released(current) else "inventory_checked",
        "question_sha256": question_sha256,
        "coverage_before": initial_ratio,
        "coverage_after": initial_ratio,
        "coverage_delta": 0.0,
        "planned_route_ids": [],
        "attempted_route_ids": [],
        "new_complete_requirement_ids": [],
        "critical_requirement_ids": [],
        "stop_reason": "release_gate_passed_at_inventory" if _released(current) else None,
    }]
    if _released(current):
        return {
            "schema_version": 1,
            "question_sha256": question_sha256,
            "generated_at": generated_at,
            "status": "released",
            "stop_reason": "release_gate_passed_at_inventory",
            "final_coverage": current,
            "attempted_route_ids": [],
            "iterations": iterations,
        }

    attempted: list[str] = []
    consecutive_low_gain = 0
    stop_reason = "maximum_rounds_reached"
    final_status = "stopped_partial"
    for round_number in range(1, max_rounds + 1):
        gap_plan = dict(plan_round(current, round_number, list(attempted)))
        routes = [item for item in gap_plan.get("routes", []) if isinstance(item, Mapping)]
        planned_route_ids = [str(item.get("route_id")) for item in routes if item.get("route_id")]
        critical_ids = [
            str(item.get("requirement_id"))
            for item in gap_plan.get("gaps", [])
            if isinstance(item, Mapping) and str(item.get("decision_materiality") or "") == "high" and item.get("requirement_id")
        ]
        before = deepcopy(current)
        before_ratio = _ratio(before)
        if not planned_route_ids:
            stop_reason = "no_remaining_routes"
            iterations.append({
                "round": round_number,
                "state": "stopped_partial",
                "question_sha256": question_sha256,
                "coverage_before": before_ratio,
                "coverage_after": before_ratio,
                "coverage_delta": 0.0,
                "planned_route_ids": [],
                "attempted_route_ids": list(attempted),
                "new_complete_requirement_ids": [],
                "critical_requirement_ids": critical_ids,
                "stop_reason": stop_reason,
            })
            break
        collection_result = collect_round(gap_plan)
        attempted.extend(route_id for route_id in planned_route_ids if route_id not in attempted)
        current = deepcopy(dict(verify_round(collection_result)))
        after_ratio = _ratio(current)
        delta = round(after_ratio - before_ratio, 6)
        before_status = _status_by_id(before)
        after_status = _status_by_id(current)
        newly_complete = sorted(
            requirement_id for requirement_id, status in after_status.items()
            if status in {"complete", "not_applicable"} and before_status.get(requirement_id) not in {"complete", "not_applicable"}
        )
        critical_gain = bool(set(newly_complete) & set(critical_ids))
        consecutive_low_gain = consecutive_low_gain + 1 if delta < low_gain_threshold and not critical_gain else 0
        state = "released" if _released(current) else "verified"
        round_stop_reason = "release_gate_passed" if _released(current) else None
        iterations.append({
            "round": round_number,
            "state": state,
            "question_sha256": question_sha256,
            "coverage_before": before_ratio,
            "coverage_after": after_ratio,
            "coverage_delta": delta,
            "planned_route_ids": planned_route_ids,
            "attempted_route_ids": list(attempted),
            "new_complete_requirement_ids": newly_complete,
            "critical_requirement_ids": critical_ids,
            "stop_reason": round_stop_reason,
        })
        if _released(current):
            final_status = "released"
            stop_reason = "release_gate_passed"
            break
        if consecutive_low_gain >= 2:
            stop_reason = "two_consecutive_low_gain_rounds"
            iterations[-1]["state"] = "stopped_partial"
            iterations[-1]["stop_reason"] = stop_reason
            break
    return {
        "schema_version": 1,
        "question_sha256": question_sha256,
        "generated_at": generated_at,
        "status": final_status,
        "stop_reason": stop_reason,
        "final_coverage": current,
        "attempted_route_ids": attempted,
        "iterations": iterations,
    }


def _successful_existing_context(profile: Mapping[str, Any]) -> dict[str, Any]:
    context = profile.get("research_context") if isinstance(profile.get("research_context"), Mapping) else {}
    result: dict[str, Any] = {}
    for section, section_data in context.items():
        if not isinstance(section_data, Mapping):
            continue
        output = {key: deepcopy(value) for key, value in section_data.items() if key != "sources"}
        sources = [
            deepcopy(dict(source))
            for source in section_data.get("sources", [])
            if isinstance(source, Mapping)
            and str(source.get("fetch_status") or "").casefold() == "success"
            and bool(source.get("response_sha256"))
        ]
        output["sources"] = sources
        result[str(section)] = output
    return result


def _merge_context(current: dict[str, Any], incoming: Mapping[str, Any]) -> None:
    for section, section_data in incoming.items():
        if not isinstance(section_data, Mapping):
            continue
        target = current.setdefault(str(section), {})
        for key, value in section_data.items():
            if key != "sources":
                target[key] = deepcopy(value)
        by_url = {
            str(item.get("url") or item.get("citation_url") or ""): deepcopy(dict(item))
            for item in target.get("sources", [])
            if isinstance(item, Mapping)
        }
        for source in section_data.get("sources", []):
            if not isinstance(source, Mapping):
                continue
            url = str(source.get("url") or source.get("citation_url") or "")
            by_url[url] = deepcopy(dict(source))
        target["sources"] = list(by_url.values())


def run_context_research_loop(
    profile: Mapping[str, Any], *, as_of: str, history: Mapping[str, Any], valuation: Mapping[str, Any],
    collector: Callable[..., Mapping[str, Any]], timeout_seconds: float = 45.0,
    metric_extractor: Callable[[Mapping[str, Any], Mapping[str, Any], str], Mapping[str, Any]] | None = None,
    max_rounds: int = 4, research_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Connect the three layers to the existing context-pack verifier."""

    research_plan = deepcopy(dict(research_plan)) if research_plan is not None else build_research_plan(profile, as_of=as_of)
    loop_profile = deepcopy(dict(profile))
    loop_profile["research_requirements"] = deepcopy(research_plan["requirements"])
    current_context = _successful_existing_context(loop_profile)
    captures_by_hash: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    round_plans: list[dict[str, Any]] = []

    def verify_current() -> tuple[dict[str, Any], dict[str, Any]]:
        replay_profile = deepcopy(loop_profile)
        replay_profile["research_context"] = deepcopy(current_context)
        packs = build_context_packs(replay_profile, context=current_context, history=history, valuation=valuation)
        coverage = build_context_coverage(replay_profile, context=current_context, packs=packs, generated_at=as_of)
        return packs, coverage

    initial_packs, initial_coverage = verify_current()
    latest_packs = initial_packs

    def planner(coverage: Coverage, round_number: int, attempted: list[str]) -> Mapping[str, Any]:
        gap_plan = build_gap_plan(
            loop_profile,
            research_plan=research_plan,
            coverage=coverage,
            round_number=round_number,
            attempted_route_ids=attempted,
            generated_at=as_of,
        )
        round_plans.append(deepcopy(gap_plan))
        return gap_plan

    def collect(gap_plan: Mapping[str, Any]) -> Mapping[str, Any]:
        planned_context = select_context_for_gap_plan(loop_profile, gap_plan)
        result = dict(collector(planned_context, as_of=as_of, timeout_seconds=timeout_seconds))
        if metric_extractor is not None:
            result = dict(metric_extractor(result, loop_profile, as_of))
        incoming = result.get("context") if isinstance(result.get("context"), Mapping) else {}
        _merge_context(current_context, incoming)
        for capture in result.get("raw_captures", []):
            if isinstance(capture, Mapping) and capture.get("response_sha256"):
                captures_by_hash[str(capture["response_sha256"])] = deepcopy(dict(capture))
        failures.extend(deepcopy(dict(item)) for item in result.get("fetch_failures", []) if isinstance(item, Mapping))
        return result

    def verify(_collection_result: Any) -> Coverage:
        nonlocal latest_packs
        latest_packs, coverage = verify_current()
        return coverage

    iteration_history = run_iterative_controller(
        research_plan=research_plan,
        initial_coverage=initial_coverage,
        plan_round=planner,
        collect_round=collect,
        verify_round=verify,
        generated_at=as_of,
        max_rounds=max_rounds,
    )
    final_coverage = deepcopy(dict(iteration_history["final_coverage"]))
    final_round = int(iteration_history["iterations"][-1]["round"])
    context_gap = build_context_gap_report(
        loop_profile,
        requirements={
            "target_id": research_plan["target_id"],
            "industry_family": research_plan["industry_family"],
            "generated_at": as_of,
            "requirements": research_plan["requirements"],
        },
        coverage=final_coverage,
        generated_at=as_of,
        round_number=final_round,
    )
    if iteration_history["status"] != "released":
        context_gap["status"] = "stopped_partial"
        context_gap["next_action"] = "stop_partial"
        context_gap["stop_reason"] = str(iteration_history["stop_reason"])
    return {
        "research_plan": research_plan,
        "context": current_context,
        "raw_captures": list(captures_by_hash.values()),
        "fetch_failures": failures,
        "packs": latest_packs,
        "coverage": final_coverage,
        "gap_report": context_gap,
        "iteration_history": iteration_history,
        "round_plans": round_plans,
    }
