from __future__ import annotations

import hashlib
import json

from finance_crawler_poc.evidence_extractor import extract_metric_attestations


def _capture(content: bytes, content_type: str = "application/json") -> dict[str, object]:
    return {
        "url": "https://source.example/data",
        "response_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": content_type,
        "content": content,
        "collected_at": "2026-08-26T00:00:00Z",
    }


def _profile(requirement: dict[str, object]) -> dict[str, object]:
    return {
        "target_id": "demo",
        "target": {
            "symbol": "9999.TW",
            "name": "Demo Issuer",
            "aliases": ["Demo Issuer", "範例公司"],
            "primary_region": "TW",
            "currency": "TWD",
        },
        "research_requirements": [requirement],
    }


def test_twse_json_extractor_filters_the_target_row_before_attestation() -> None:
    payload = json.dumps([
        {"公司代號": "1111", "公司名稱": "Other Issuer", "主要經營業務": "irrelevant chemicals"},
        {"公司代號": "9999", "公司名稱": "Demo Issuer", "主要經營業務": "industrial hardware and services", "國際證券識別號碼": "TW0009999000"},
    ], ensure_ascii=False).encode("utf-8")
    capture = _capture(payload)
    requirement = {
        "requirement_id": "company.business_model",
        "required_metrics": ["products_or_services", "customers_or_regions"],
    }
    result = {
        "context": {"company": {"sources": [{
            "url": capture["url"],
            "fetch_status": "success",
            "response_sha256": capture["response_sha256"],
            "content_type": capture["content_type"],
            "requirement_ids": ["company.business_model"],
            "evidence_role": "official",
            "geography_scope": ["TW"],
        }]}},
        "raw_captures": [capture],
        "fetch_failures": [],
    }

    extracted = extract_metric_attestations(result, _profile(requirement), "2026-08-26T00:00:00Z")
    facts = extracted["context"]["company"]["sources"][0]["metric_attestations"]

    products = next(item for item in facts if item["metric"] == "products_or_services")
    assert "industrial hardware" in products["value"]
    assert "irrelevant chemicals" not in products["value"]
    assert products["source_response_sha256"] == capture["response_sha256"]
    assert products["target_id"] == "demo"
    assert products["period"] == "2026"


def test_governance_text_extractor_retains_exact_locator_and_provenance() -> None:
    html = """
    <html><body><h1>Demo Issuer Corporate Governance</h1>
    <p>2025 年董事會由 9 名董事組成，包含 4 名獨立董事，並設置審計委員會與薪資報酬委員會。</p>
    </body></html>
    """.encode("utf-8")
    capture = _capture(html, "text/html")
    requirement = {
        "requirement_id": "governance.board_and_ownership",
        "required_metrics": ["board", "independent_directors", "committee"],
    }
    result = {
        "context": {"governance": {"sources": [{
            "url": capture["url"],
            "fetch_status": "success",
            "response_sha256": capture["response_sha256"],
            "content_type": capture["content_type"],
            "requirement_ids": ["governance.board_and_ownership"],
            "evidence_role": "governance_filing",
            "geography_scope": ["TW"],
        }]}},
        "raw_captures": [capture],
        "fetch_failures": [],
    }

    extracted = extract_metric_attestations(result, _profile(requirement), "2026-08-26T00:00:00Z")
    facts = extracted["context"]["governance"]["sources"][0]["metric_attestations"]

    assert {item["metric"] for item in facts} == {"board", "independent_directors", "committee"}
    assert all(item["locator"].startswith("text:") for item in facts)
    assert all(item["value"] in html.decode("utf-8") for item in facts)


def test_industry_numeric_extractor_requires_value_period_and_unit() -> None:
    html = b"<html><body>Global cement statistics: 2025 demand was 123 million tonnes.</body></html>"
    capture = _capture(html, "text/html")
    requirement = {
        "requirement_id": "industry.market_demand",
        "required_metrics": ["market_size_or_demand", "period", "unit"],
    }
    result = {
        "context": {"industry": {"sources": [{
            "url": capture["url"],
            "fetch_status": "success",
            "response_sha256": capture["response_sha256"],
            "content_type": capture["content_type"],
            "requirement_ids": ["industry.market_demand"],
            "evidence_role": "industry_statistic",
            "geography_scope": ["global"],
        }]}},
        "raw_captures": [capture],
        "fetch_failures": [],
    }

    extracted = extract_metric_attestations(result, _profile(requirement), "2026-08-26T00:00:00Z")
    fact = extracted["context"]["industry"]["sources"][0]["metric_attestations"][0]

    assert fact["metric"] == "market_size_or_demand"
    assert fact["value"] == "123"
    assert fact["period"] == "2025"
    assert fact["unit"] == "million tonnes"
    assert fact["geography_scope"] == "global"
