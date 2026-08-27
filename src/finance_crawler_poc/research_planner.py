"""Layer 1: freeze research questions and derive required-data contracts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from .context_requirements import build_research_requirements, industry_family


_SECTION_QUESTIONS = {
    "company": ("Q-COMPANY", "公司如何賺錢，哪些產品、客戶、區域與分部驅動獲利？"),
    "industry": ("Q-INDUSTRY", "產業量價、產能與競爭循環如何傳導到標的營收與利潤？"),
    "governance": ("Q-GOVERNANCE", "治理與資本配置是否保護長期股東報酬？"),
    "esg": ("Q-ESG", "哪些重大永續議題會影響現金流、資本成本或營運許可？"),
}


_DECISION_LINKS = {
    "company": {"hypothesis": "H-COMPANY-EARNINGS", "financial_lines": ["revenue", "operating_margin", "free_cash_flow"]},
    "industry": {"hypothesis": "H-INDUSTRY-CYCLE", "financial_lines": ["revenue_growth", "gross_margin", "valuation_multiple"]},
    "governance": {"hypothesis": "H-CAPITAL-ALLOCATION", "financial_lines": ["capex", "net_debt", "shareholder_return", "cost_of_capital"]},
    "esg": {"hypothesis": "H-MATERIAL-RISK", "financial_lines": ["operating_cost", "capex", "cost_of_capital", "license_to_operate"]},
}


_INDUSTRY_OVERLAYS: dict[str, tuple[dict[str, Any], ...]] = {
    "cement": ({
        "requirement_id": "industry.cement_unit_economics",
        "section": "industry",
        "required": True,
        "decision_materiality": "high",
        "minimum_independent_groups": 2,
        "required_roles": ["industry_statistic", "company_disclosure"],
        "required_metrics": ["sales_volume", "asp", "capacity_or_utilization", "energy_cost", "carbon_cost", "period", "unit"],
        "period_policy": "latest_available_plus_3y_history",
        "recommended_routes": ["company_annual_report", "regional_cement_statistics", "energy_price_api", "carbon_price_api"],
        "research_question": "區域水泥銷量、ASP、產能利用率、能源與碳成本如何改變單位經濟？",
        "decision_link": {"hypothesis": "H-CEMENT-UNIT-ECONOMICS", "financial_lines": ["revenue", "gross_margin", "capex"]},
    },),
    "steel": ({
        "requirement_id": "industry.steel_spread_cycle",
        "section": "industry",
        "required": True,
        "decision_materiality": "high",
        "minimum_independent_groups": 3,
        "required_roles": ["price_index", "industry_statistic", "company_disclosure"],
        "required_metrics": ["steel_price", "iron_ore_price", "coking_coal_price", "capacity_or_utilization", "period", "unit"],
        "period_policy": "monthly_latest_plus_3y_history",
        "recommended_routes": ["steel_price_api", "raw_material_price_api", "worldsteel_statistics", "company_annual_report"],
        "research_question": "鋼價、鐵礦砂、焦煤與產能利用率如何影響利差與循環位置？",
        "decision_link": {"hypothesis": "H-STEEL-SPREAD", "financial_lines": ["revenue", "gross_margin", "inventory"]},
    },),
    "petrochemical": ({
        "requirement_id": "industry.petrochemical_spread_cycle",
        "section": "industry",
        "required": True,
        "decision_materiality": "high",
        "minimum_independent_groups": 3,
        "required_roles": ["price_index", "industry_statistic", "company_disclosure"],
        "required_metrics": ["product_price", "feedstock_cost", "product_spread", "capacity_or_utilization", "period", "unit"],
        "period_policy": "monthly_latest_plus_3y_history",
        "recommended_routes": ["petrochemical_price_api", "feedstock_price_api", "asia_capacity_statistics", "company_annual_report"],
        "research_question": "產品價格、原料成本、利差與亞洲產能利用率如何改變獲利？",
        "decision_link": {"hypothesis": "H-PETROCHEMICAL-SPREAD", "financial_lines": ["revenue", "gross_margin", "inventory"]},
    },),
    "electronic_components": ({
        "requirement_id": "industry.component_inventory_cycle",
        "section": "industry",
        "required": True,
        "decision_materiality": "high",
        "minimum_independent_groups": 2,
        "required_roles": ["industry_statistic", "company_disclosure"],
        "required_metrics": ["end_market_demand", "inventory_days", "asp_or_product_mix", "capacity_or_utilization", "period", "unit"],
        "period_policy": "quarterly_latest_plus_3y_history",
        "recommended_routes": ["electronics_statistics_api", "company_annual_report", "peer_annual_report"],
        "research_question": "終端需求、庫存、ASP／產品 mix 與產能利用率如何影響被動元件循環？",
        "decision_link": {"hypothesis": "H-COMPONENT-CYCLE", "financial_lines": ["revenue_growth", "gross_margin", "inventory"]},
    },),
    "pc_hardware": ({
        "requirement_id": "industry.pc_odm_demand_mix",
        "section": "industry",
        "required": True,
        "decision_materiality": "high",
        "minimum_independent_groups": 2,
        "required_roles": ["industry_statistic", "company_disclosure"],
        "required_metrics": ["product_mix", "channel_inventory", "pc_or_ai_server_demand", "capacity_or_supply_constraint", "period", "unit"],
        "period_policy": "quarterly_latest_plus_3y_history",
        "recommended_routes": ["pc_market_statistics", "server_market_statistics", "company_annual_report", "peer_annual_report"],
        "research_question": "PC／AI server 需求、產品 mix、通路庫存與關鍵零組件供應如何改變獲利？",
        "decision_link": {"hypothesis": "H-PC-ODM-MIX", "financial_lines": ["revenue_growth", "gross_margin", "working_capital"]},
    },),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _enrich_requirement(requirement: Mapping[str, Any], *, family: str, target: Mapping[str, Any]) -> dict[str, Any]:
    item = deepcopy(dict(requirement))
    section = str(item.get("section") or "industry")
    parent_id, parent_question = _SECTION_QUESTIONS.get(section, _SECTION_QUESTIONS["industry"])
    item.setdefault("industry_family", family)
    item.setdefault("parent_question_id", parent_id)
    item.setdefault("research_question", parent_question)
    item.setdefault("decision_materiality", "high" if item.get("required", True) else "low")
    item.setdefault("geography_policy", list(target.get("region_priority") or [target.get("primary_region") or target.get("domicile_country") or "global"]))
    item.setdefault("freshness_policy", "latest_available_as_of_run")
    item.setdefault("decision_link", deepcopy(_DECISION_LINKS.get(section, _DECISION_LINKS["industry"])))
    return item


def build_research_plan(
    profile: Mapping[str, Any], *, question: str | None = None, as_of: str | None = None, horizon: str = "three_year"
) -> dict[str, Any]:
    """Freeze the user question before any source route is selected."""

    target = deepcopy(dict(profile.get("target") or {}))
    target_id = str(profile.get("target_id") or target.get("symbol") or "target").casefold()
    frozen_question = str(question or profile.get("question") or f"{target_id} investment research").strip()
    if not frozen_question:
        raise ValueError("research question is required")
    frozen_as_of = str(as_of or _now())
    family = industry_family(profile)
    baseline = build_research_requirements(profile, generated_at=frozen_as_of)["requirements"]
    by_id = {
        str(item["requirement_id"]): _enrich_requirement(item, family=family, target=target)
        for item in baseline
        if isinstance(item, Mapping) and item.get("requirement_id")
    }
    for overlay in _INDUSTRY_OVERLAYS.get(family, ()):
        enriched = _enrich_requirement(overlay, family=family, target=target)
        by_id[str(enriched["requirement_id"])] = enriched
    question_sha256 = _canonical_hash({"target_id": target_id, "question": frozen_question, "as_of": frozen_as_of, "horizon": horizon})
    return {
        "schema_version": 1,
        "target_id": target_id,
        "target": target,
        "question": frozen_question,
        "question_sha256": question_sha256,
        "as_of": frozen_as_of,
        "horizon": horizon,
        "industry_family": family,
        "requirements": list(by_id.values()),
    }
