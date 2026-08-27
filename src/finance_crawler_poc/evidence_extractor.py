"""Deterministic metric attestations from frozen source payloads.

The extractor is deliberately conservative: transport success never becomes
coverage unless the saved bytes expose an exact value/excerpt with period,
unit, target scope and response hash.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


_TEXT_METRICS: dict[str, tuple[str, ...]] = {
    "products_or_services": ("主要經營業務", "主要產品", "產品與服務", "products and services", "principal activities"),
    "customers_or_regions": ("銷售地區", "地區別", "主要客戶", "customers", "geographic", "regions"),
    "board": ("董事會", "board of directors"),
    "independent_directors": ("獨立董事", "independent director"),
    "ownership": ("主要股東", "大股東", "shareholding", "ownership"),
    "committee": ("審計委員會", "薪資報酬委員會", "audit committee", "compensation committee"),
    "m_and_a": ("併購", "收購", "merger", "acquisition"),
    "material_topic": ("重大主題", "重大性", "material topic", "materiality"),
    "financial_transmission": ("營運成本", "資本支出", "資本成本", "operating cost", "capital expenditure", "cost of capital"),
    "peer_set": ("peer", "competitor", "同業", "競爭者"),
    "product_mix": ("product mix", "產品組合"),
    "channel_inventory": ("channel inventory", "通路庫存"),
    "pc_or_ai_server_demand": ("ai server", "server demand", "pc demand", "ai 伺服器", "電腦需求"),
    "capacity_or_supply_constraint": ("supply constraint", "capacity constraint", "供應限制", "產能瓶頸"),
    "end_market_demand": ("end-market demand", "end market demand", "終端需求"),
    "asp_or_product_mix": ("average selling price", "asp", "product mix", "平均售價", "產品組合"),
}


_NUMERIC_METRICS: dict[str, tuple[str, ...]] = {
    "segment_revenue": ("segment revenue", "revenue by segment", "部門營收", "分部營收", "主要產品"),
    "market_size_or_demand": ("market size", "demand", "consumption", "市場規模", "需求", "消費量"),
    "price_or_spread": ("price", "spread", "價格", "利差"),
    "capacity_or_utilization": ("capacity utilization", "utilization", "capacity", "產能利用率", "產能"),
    "market_share_or_position": ("market share", "市佔率"),
    "margin_or_cost_comparison": ("gross margin", "operating margin", "cost", "毛利率", "營業利益率", "成本"),
    "capex": ("capital expenditure", "capex", "資本支出"),
    "dividend_or_buyback": ("dividend", "buyback", "股利", "庫藏股"),
    "debt": ("total debt", "net debt", "負債", "有息負債"),
    "baseline_kpi": ("baseline", "基準年", "基線"),
    "target": ("target", "目標"),
    "progress": ("progress", "進度", "達成率"),
    "sales_volume": ("sales volume", "shipment volume", "銷售量", "出貨量"),
    "asp": ("average selling price", "asp", "平均售價"),
    "energy_cost": ("energy cost", "fuel cost", "electricity cost", "能源成本", "燃料成本", "電力成本"),
    "carbon_cost": ("carbon cost", "carbon price", "carbon fee", "碳成本", "碳價", "碳費"),
    "steel_price": ("steel price", "鋼價"),
    "iron_ore_price": ("iron ore price", "鐵礦砂價格"),
    "coking_coal_price": ("coking coal price", "焦煤價格"),
    "product_price": ("product price", "產品價格"),
    "feedstock_cost": ("feedstock cost", "raw material cost", "原料成本"),
    "product_spread": ("product spread", "產品利差", "價差"),
    "inventory_days": ("inventory days", "days inventory", "存貨天數"),
}


_UNITS = (
    "million tonnes", "million tons", "thousand tonnes", "thousand tons", "tonnes", "tons",
    "twd million", "nt$ million", "ntd million", "usd million", "million", "billion", "%", "percent",
    "新台幣仟元", "新台幣千元", "新台幣百萬元", "仟元", "千元", "百萬元", "公噸", "噸",
)


def _plain_text(content: bytes, content_type: str) -> str:
    if "pdf" in content_type.casefold() or content.startswith(b"%PDF"):
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "capture.pdf"
                path.write_bytes(content)
                completed = subprocess.run(
                    ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, timeout=30
                )
                return re.sub(r"\s+", " ", completed.stdout.decode("utf-8", errors="ignore")).strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    decoded = content.decode("utf-8", errors="ignore")
    decoded = re.sub(r"</(?:p|h[1-6]|div|li|tr)>", ". ", decoded, flags=re.IGNORECASE)
    decoded = html.unescape(re.sub(r"<[^>]+>", " ", decoded))
    return re.sub(r"\s+", " ", decoded).strip()


def _target_json_text(content: bytes, target: Mapping[str, Any]) -> str | None:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    symbol_code = str(target.get("symbol") or "").split(".", 1)[0]
    rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, Mapping) and isinstance(payload.get("data"), list) else None
    if isinstance(rows, list):
        identity_keys = ("公司代號", "證券代號", "stock_id", "symbol", "code")
        scoped = [
            row for row in rows if isinstance(row, Mapping)
            and any(str(row.get(key) or "").split(".", 1)[0] == symbol_code for key in identity_keys)
        ]
        if scoped:
            return json.dumps(scoped, ensure_ascii=False, sort_keys=True)
        if any(isinstance(row, Mapping) and any(key in row for key in identity_keys) for row in rows):
            return ""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _target_tokens(profile: Mapping[str, Any]) -> list[str]:
    target = profile.get("target") if isinstance(profile.get("target"), Mapping) else {}
    values = [target.get("name"), str(target.get("symbol") or "").split(".", 1)[0]]
    values.extend(target.get("aliases", []) if isinstance(target.get("aliases"), list) else [])
    values.extend(target.get("local_names", []) if isinstance(target.get("local_names"), list) else [])
    values.extend(target.get("international_names", []) if isinstance(target.get("international_names"), list) else [])
    return [str(value).casefold() for value in values if len(str(value).strip()) >= 2]


def _source_is_target_scoped(source: Mapping[str, Any], text: str, profile: Mapping[str, Any]) -> bool:
    role = str(source.get("evidence_role") or "")
    if role in {"industry_statistic", "price_index", "capacity_or_utilization", "independent_secondary", "peer_filing", "market_data"}:
        return True
    lowered = text.casefold()
    return any(token in lowered for token in _target_tokens(profile))


def _sentence(text: str, index: int) -> str:
    start = max(text.rfind(".", 0, index), text.rfind("。", 0, index), text.rfind(";", 0, index)) + 1
    candidates = [value for value in (text.find(".", index), text.find("。", index), text.find(";", index)) if value >= 0]
    end = min(candidates) + 1 if candidates else min(len(text), index + 260)
    return text[start:end].strip()[:500]


def _period(excerpt: str, as_of: str) -> str:
    match = re.search(r"(?:19|20)\d{2}", excerpt)
    return match.group(0) if match else str(as_of)[:4]


def _unit(excerpt: str) -> str | None:
    lowered = excerpt.casefold()
    return next((unit for unit in _UNITS if unit.casefold() in lowered), None)


def _text_fact(text: str, tokens: tuple[str, ...], metric: str, as_of: str) -> tuple[str, str, str, str] | None:
    lowered = text.casefold()
    for token in tokens:
        index = lowered.find(token.casefold())
        if index >= 0:
            excerpt = _sentence(text, index)
            if excerpt:
                return excerpt, _period(excerpt, as_of), "text", f"text:{index}"
    return None


def _numeric_fact(text: str, tokens: tuple[str, ...], metric: str, as_of: str) -> tuple[str, str, str, str] | None:
    lowered = text.casefold()
    for token in tokens:
        index = lowered.find(token.casefold())
        if index < 0:
            continue
        excerpt = _sentence(text, index)
        local_index = excerpt.casefold().find(token.casefold())
        after = excerpt[local_index + len(token):] if local_index >= 0 else excerpt
        number = re.search(r"(?<![A-Za-z])(-?\d[\d,]*(?:\.\d+)?)", after)
        unit = _unit(after if number else excerpt)
        if number and unit:
            return number.group(1).replace(",", ""), _period(excerpt, as_of), unit, f"text:{index}"
    return None


def extract_metric_attestations(
    result: Mapping[str, Any], profile: Mapping[str, Any], as_of: str
) -> dict[str, Any]:
    """Attach deterministic attestations to successful context sources."""

    output = deepcopy(dict(result))
    captures = {
        str(item.get("response_sha256")): item
        for item in output.get("raw_captures", [])
        if isinstance(item, Mapping) and item.get("response_sha256") and isinstance(item.get("content"), bytes)
    }
    requirements = {
        str(item.get("requirement_id")): item
        for item in profile.get("research_requirements", [])
        if isinstance(item, Mapping) and item.get("requirement_id")
    }
    target = profile.get("target") if isinstance(profile.get("target"), Mapping) else {}
    target_id = str(profile.get("target_id") or target.get("symbol") or "target").casefold()
    context = output.get("context") if isinstance(output.get("context"), Mapping) else {}
    for section_data in context.values():
        if not isinstance(section_data, dict) or not isinstance(section_data.get("sources"), list):
            continue
        for source in section_data["sources"]:
            if not isinstance(source, dict) or str(source.get("fetch_status") or "") != "success":
                continue
            digest = str(source.get("response_sha256") or "")
            capture = captures.get(digest)
            if capture is None:
                continue
            content = capture["content"]
            content_type = str(capture.get("content_type") or source.get("content_type") or "application/octet-stream")
            json_text = _target_json_text(content, target) if "json" in content_type.casefold() else None
            text = json_text if json_text is not None else _plain_text(content, content_type)
            if not text or not _source_is_target_scoped(source, text, profile):
                continue
            scopes = source.get("geography_scope") if isinstance(source.get("geography_scope"), list) else []
            geography = str(scopes[0] if scopes else target.get("primary_region") or target.get("domicile_country") or "global")
            facts: list[dict[str, Any]] = []
            for requirement_id in source.get("requirement_ids", []):
                requirement = requirements.get(str(requirement_id), {})
                for metric in requirement.get("required_metrics", []):
                    metric = str(metric)
                    extracted = None
                    method = "exact_text_excerpt_v1"
                    if metric in _TEXT_METRICS:
                        extracted = _text_fact(text, _TEXT_METRICS[metric], metric, as_of)
                    elif metric in _NUMERIC_METRICS:
                        extracted = _numeric_fact(text, _NUMERIC_METRICS[metric], metric, as_of)
                        method = "numeric_context_v1"
                    if extracted is None:
                        continue
                    value, period, unit, locator = extracted
                    facts.append({
                        "metric": metric,
                        "value": value,
                        "period": period,
                        "unit": unit,
                        "currency": str(target.get("currency") or "") if unit != "text" else None,
                        "geography_scope": geography,
                        "source_response_sha256": digest,
                        "target_id": target_id,
                        "requirement_ids": [str(requirement_id)],
                        "locator": locator,
                        "extraction_method": method,
                    })
            unique: dict[tuple[str, str], dict[str, Any]] = {}
            for fact in facts:
                unique[(fact["metric"], fact["requirement_ids"][0])] = fact
            source["metric_attestations"] = list(unique.values())
            source["metric_extraction_status"] = "attested" if unique else "no_verified_metric"
    return output
