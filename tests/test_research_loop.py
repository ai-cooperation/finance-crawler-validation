from __future__ import annotations

from copy import deepcopy

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.evidence_gap_broker import build_gap_plan, select_context_for_gap_plan
from finance_crawler_poc.iterative_research import run_context_research_loop, run_iterative_controller
from finance_crawler_poc.research_planner import build_research_plan
from finance_crawler_poc.target_profiles import get_target_profile


def _coverage(ratio: float, statuses: dict[str, str]) -> dict[str, object]:
    requirements = [
        {
            "requirement_id": requirement_id,
            "section": "industry",
            "required": True,
            "status": status,
            "evidence_ids": [],
            "source_groups": [],
            "evidence_roles": [],
            "metric_coverage": [],
            "missing_metrics": ["metric"] if status != "complete" else [],
            "missing_roles": ["industry_statistic"] if status != "complete" else [],
            "geography_scopes": [],
            "required_geography_scopes": ["tw", "asia"],
            "independent_source_count": 0,
            "missing_reasons": ["required_metrics_missing"] if status != "complete" else [],
        }
        for requirement_id, status in statuses.items()
    ]
    return {
        "schema_version": 1,
        "target_id": "tcc",
        "industry_family": "cement",
        "generated_at": "2026-08-26T00:00:00Z",
        "requirements": requirements,
        "summary": {
            "required_count": len(requirements),
            "complete_count": sum(status == "complete" for status in statuses.values()),
            "coverage_ratio": ratio,
            "l3_ready": bool(statuses) and all(status == "complete" for status in statuses.values()),
        },
        "status": "complete" if statuses and all(status == "complete" for status in statuses.values()) else "partial",
    }


def test_planner_freezes_question_and_adds_industry_specific_contracts() -> None:
    profile = get_target_profile("tcc")
    plan = build_research_plan(
        profile,
        question="台泥未來三年的獲利與估值是否具備安全邊際？",
        as_of="2026-08-26T00:00:00Z",
    )

    assert plan["question"] == "台泥未來三年的獲利與估值是否具備安全邊際？"
    assert len(plan["question_sha256"]) == 64
    ids = {item["requirement_id"] for item in plan["requirements"]}
    assert "company.business_model" in ids
    assert "industry.cement_unit_economics" in ids
    cement = next(item for item in plan["requirements"] if item["requirement_id"] == "industry.cement_unit_economics")
    assert cement["parent_question_id"]
    assert cement["decision_materiality"] == "high"
    assert {"sales_volume", "asp", "energy_cost", "carbon_cost"} <= set(cement["required_metrics"])
    assert cement["decision_link"]["financial_lines"]
    validate_contract("research-plan", plan)


def test_each_industry_family_gets_a_distinct_required_data_overlay() -> None:
    expected = {
        "csc": "industry.steel_spread_cycle",
        "formosa": "industry.petrochemical_spread_cycle",
        "yageo": "industry.component_inventory_cycle",
        "asus": "industry.pc_odm_demand_mix",
        "wistron": "industry.pc_odm_demand_mix",
    }

    for target_id, requirement_id in expected.items():
        plan = build_research_plan(get_target_profile(target_id), as_of="2026-08-26T00:00:00Z")
        ids = {item["requirement_id"] for item in plan["requirements"]}
        assert requirement_id in ids


def test_gap_broker_only_plans_material_incomplete_requirements_and_unattempted_routes() -> None:
    profile = get_target_profile("tcc")
    plan = build_research_plan(profile, as_of="2026-08-26T00:00:00Z")
    required_ids = [item["requirement_id"] for item in plan["requirements"]]
    statuses = {requirement_id: "complete" for requirement_id in required_ids}
    statuses["industry.market_demand"] = "partial"
    coverage = _coverage(0.9, statuses)
    for item in coverage["requirements"]:
        if item["requirement_id"] == "industry.market_demand":
            item["missing_metrics"] = ["market_size_or_demand"]

    first = build_gap_plan(
        profile,
        research_plan=plan,
        coverage=coverage,
        round_number=1,
        attempted_route_ids=[],
        generated_at="2026-08-26T00:00:00Z",
    )
    assert {item["requirement_id"] for item in first["gaps"]} == {"industry.market_demand"}
    assert first["routes"]
    assert all(route["requirement_ids"] == ["industry.market_demand"] for route in first["routes"])
    assert first["provider_candidates"]["industry.market_demand"]
    assert all(
        "callable_now" in candidate and "blocked_reasons" in candidate
        for candidate in first["provider_candidates"]["industry.market_demand"]
    )

    attempted = [route["route_id"] for route in first["routes"]]
    second = build_gap_plan(
        profile,
        research_plan=plan,
        coverage=coverage,
        round_number=2,
        attempted_route_ids=attempted,
        generated_at="2026-08-26T00:00:00Z",
    )
    assert not set(attempted) & {route["route_id"] for route in second["routes"]}
    validate_contract("research-gap-plan", first)


def test_gap_plan_selects_only_approved_urls_and_binds_overlay_requirement() -> None:
    profile = get_target_profile("tcc")
    plan = build_research_plan(profile, as_of="2026-08-26T00:00:00Z")
    coverage = _coverage(0.0, {"industry.cement_unit_economics": "partial"})
    gap_plan = build_gap_plan(
        profile,
        research_plan=plan,
        coverage=coverage,
        round_number=1,
        attempted_route_ids=[],
        generated_at="2026-08-26T00:00:00Z",
    )

    selected = select_context_for_gap_plan(profile, gap_plan)
    selected_urls = {
        source["url"]
        for section in selected.values()
        for source in section.get("sources", [])
    }
    approved_urls = {route["url"] for route in gap_plan["routes"]}
    assert selected_urls == approved_urls
    assert all(
        "industry.cement_unit_economics" in source["requirement_ids"]
        for source in selected["industry"]["sources"]
    )


def test_gap_broker_plans_declared_alternative_route_without_inflating_independence() -> None:
    profile = {
        "target_id": "demo",
        "target": {"symbol": "9999.TW", "industry": "Other", "region_priority": ["TW"]},
        "research_context": {
            "industry": {"sources": [{
                "url": "https://stats.example/primary",
                "alternative_urls": ["https://stats.example/alternate"],
                "group": "stats_group",
                "publisher": "Stats",
                "requirement_ids": ["industry.market_demand"],
                "evidence_role": "industry_statistic",
            }]},
        },
    }
    plan = {
        "target_id": "demo",
        "question_sha256": "e" * 64,
        "industry_family": "generic_equity",
        "requirements": [{
            "requirement_id": "industry.market_demand",
            "parent_question_id": "Q-INDUSTRY",
            "section": "industry",
            "required": True,
            "decision_materiality": "high",
            "required_metrics": ["market_size_or_demand"],
            "required_roles": ["industry_statistic"],
            "geography_policy": ["TW"],
        }],
    }
    gap_plan = build_gap_plan(
        profile,
        research_plan=plan,
        coverage=_coverage(0.0, {"industry.market_demand": "partial"}),
        round_number=1,
        attempted_route_ids=[],
        generated_at="2026-08-26T00:00:00Z",
    )

    assert [route["url"] for route in gap_plan["routes"]] == [
        "https://stats.example/primary",
        "https://stats.example/alternate",
    ]
    assert gap_plan["routes"][1]["fallback_rank"] == 2
    assert gap_plan["routes"][1]["independence_groups"] == ["stats_group"]


def test_same_url_in_two_sections_does_not_cross_contaminate_requirements() -> None:
    profile = {
        "target_id": "demo",
        "target": {"symbol": "9999.TW", "industry": "Other", "region_priority": ["TW"]},
        "research_context": {
            "company": {"sources": [{
                "url": "https://issuer.example/annual-report.pdf",
                "requirement_ids": ["company.business_model"],
                "evidence_role": "annual_report",
                "group": "issuer",
            }]},
            "governance": {"sources": [{
                "url": "https://issuer.example/annual-report.pdf",
                "requirement_ids": ["governance.board_and_ownership"],
                "evidence_role": "annual_report",
                "group": "issuer",
            }]},
        },
    }
    plan = {
        "target_id": "demo",
        "question_sha256": "f" * 64,
        "industry_family": "generic_equity",
        "requirements": [{
            "requirement_id": "company.business_model",
            "parent_question_id": "Q-COMPANY",
            "section": "company",
            "required": True,
            "decision_materiality": "high",
            "required_metrics": ["products_or_services"],
            "required_roles": ["annual_report"],
            "geography_policy": ["TW"],
        }],
    }
    coverage = _coverage(0.0, {"company.business_model": "partial"})

    gap_plan = build_gap_plan(
        profile,
        research_plan=plan,
        coverage=coverage,
        round_number=1,
        attempted_route_ids=[],
        generated_at="2026-08-26T00:00:00Z",
    )
    selected = select_context_for_gap_plan(profile, gap_plan)

    assert {route["section"] for route in gap_plan["routes"]} == {"company"}
    assert "governance" not in selected
    assert selected["company"]["sources"][0]["requirement_ids"] == ["company.business_model"]


def test_controller_round_zero_never_collects_and_releases_immediately_when_complete() -> None:
    calls: list[int] = []
    coverage = _coverage(1.0, {"industry.market_demand": "complete"})

    history = run_iterative_controller(
        research_plan={"question_sha256": "a" * 64},
        initial_coverage=coverage,
        plan_round=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("planner must not run")),
        collect_round=lambda _plan: calls.append(1),
        verify_round=lambda _result: coverage,
        generated_at="2026-08-26T00:00:00Z",
    )

    assert calls == []
    assert history["status"] == "released"
    assert history["stop_reason"] == "release_gate_passed_at_inventory"
    assert [item["round"] for item in history["iterations"]] == [0]
    validate_contract("research-iteration-history", history)


def test_controller_stops_after_two_low_gain_rounds_without_critical_completion() -> None:
    coverages = iter([
        _coverage(0.04, {"industry.market_demand": "partial"}),
        _coverage(0.09, {"industry.market_demand": "partial"}),
    ])
    collected: list[int] = []

    def plan_round(_coverage_value: dict[str, object], round_number: int, _attempted: list[str]) -> dict[str, object]:
        return {
            "round": round_number,
            "routes": [{"route_id": f"route-{round_number}"}],
            "gaps": [{"requirement_id": "industry.market_demand", "decision_materiality": "high"}],
        }

    history = run_iterative_controller(
        research_plan={"question_sha256": "b" * 64},
        initial_coverage=_coverage(0.0, {"industry.market_demand": "partial"}),
        plan_round=plan_round,
        collect_round=lambda gap_plan: collected.append(int(gap_plan["round"])) or gap_plan,
        verify_round=lambda _result: next(coverages),
        generated_at="2026-08-26T00:00:00Z",
    )

    assert collected == [1, 2]
    assert history["status"] == "stopped_partial"
    assert history["stop_reason"] == "two_consecutive_low_gain_rounds"
    assert history["iterations"][-1]["coverage_delta"] == 0.05


def test_controller_never_exceeds_round_four() -> None:
    collected: list[int] = []
    partial = _coverage(0.0, {"industry.market_demand": "partial"})

    history = run_iterative_controller(
        research_plan={"question_sha256": "c" * 64},
        initial_coverage=partial,
        plan_round=lambda _coverage_value, round_number, _attempted: {
            "round": round_number,
            "routes": [{"route_id": f"route-{round_number}"}],
            "gaps": [{"requirement_id": "industry.market_demand", "decision_materiality": "high"}],
        },
        collect_round=lambda gap_plan: collected.append(int(gap_plan["round"])) or gap_plan,
        verify_round=lambda _result: deepcopy(partial),
        generated_at="2026-08-26T00:00:00Z",
        low_gain_threshold=-1.0,
    )

    assert collected == [1, 2, 3, 4]
    assert history["status"] == "stopped_partial"
    assert history["stop_reason"] == "maximum_rounds_reached"
    assert history["question_sha256"] == "c" * 64


def test_context_loop_plans_before_collection_and_returns_auditable_artifacts() -> None:
    profile = {
        "target_id": "demo",
        "target": {
            "kind": "equity",
            "symbol": "9999.TW",
            "name": "Demo Issuer",
            "industry": "Other",
            "primary_region": "TW",
            "region_priority": ["TW", "Asia", "global"],
        },
        "question": "Demo Issuer 如何賺錢？",
        "research_requirements": [{
            "requirement_id": "company.business_model",
            "section": "company",
            "required": True,
            "minimum_independent_groups": 1,
            "required_roles": ["official"],
            "required_metrics": ["products_or_services", "customers_or_regions"],
            "period_policy": "latest_available",
            "recommended_routes": ["official_ir"],
        }],
        "research_context": {
            "company": {
                "sources": [{
                    "url": "https://issuer.example/profile",
                    "publisher": "Demo Issuer",
                    "group": "demo_official",
                    "independence_group": "demo_official",
                    "evidence_role": "official",
                    "requirement_ids": ["company.business_model"],
                    "geography_scope": ["TW"],
                }]
            }
        },
    }
    calls: list[dict[str, object]] = []

    def collector(context: dict[str, object], *, as_of: str, timeout_seconds: float) -> dict[str, object]:
        calls.append({"context": deepcopy(context), "as_of": as_of, "timeout": timeout_seconds})
        enriched = deepcopy(context)
        source = enriched["company"]["sources"][0]
        source.update({
            "fetch_status": "success",
            "response_sha256": "d" * 64,
            "content_type": "application/json",
            "metric_attestations": [
                {"metric": "products_or_services", "value": "hardware", "period": "2026", "unit": "text", "geography_scope": "TW", "source_response_sha256": "d" * 64, "target_id": "demo", "requirement_ids": ["company.business_model"]},
                {"metric": "customers_or_regions", "value": "Taiwan", "period": "2026", "unit": "text", "geography_scope": "TW", "source_response_sha256": "d" * 64, "target_id": "demo", "requirement_ids": ["company.business_model"]},
            ],
        })
        return {"context": enriched, "raw_captures": [], "fetch_failures": []}

    result = run_context_research_loop(
        profile,
        as_of="2026-08-26T00:00:00Z",
        history={},
        valuation={},
        collector=collector,
        timeout_seconds=3.0,
    )

    assert len(calls) == 1
    assert result["research_plan"]["question_sha256"] == result["iteration_history"]["question_sha256"]
    assert result["iteration_history"]["status"] == "released"
    assert result["coverage"]["summary"]["l3_ready"] is True
    assert result["round_plans"][0]["routes"][0]["url"] == "https://issuer.example/profile"
