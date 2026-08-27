from __future__ import annotations

import hashlib
import json

import httpx

from finance_crawler_poc.official_sources import (
    extract_sec_filing_claims,
    fetch_sec_filing_evidence,
    fetch_twse_company_profile_evidence,
    fetch_twse_financial_statement_evidence,
)


def test_extract_sec_filing_claims_returns_only_observed_section_anchors() -> None:
    claims = extract_sec_filing_claims(
        b"<html><body><h1>Risk Factors</h1><p>Revenue and capital expenditures are discussed.</p></body></html>"
    )

    assert [claim["field"] for claim in claims] == ["risk_factors", "revenue", "capital_expenditures"]
    assert all(claim["status"] == "observed" for claim in claims)
    assert all(claim["locator"]["type"] == "normalized_text_offset" for claim in claims)


def test_sec_filing_evidence_preserves_response_hash_and_regulatory_tier(monkeypatch) -> None:
    body = b"<html><title>TSMC Form 20-F</title></html>"

    def fake_get(url: str, **kwargs):
        return httpx.Response(200, content=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.official_sources.httpx.get", fake_get)
    result = fetch_sec_filing_evidence(
        cik="0001046179",
        accession="0001628280-26-025362",
        document="tsm-20251231.htm",
        filing_date="2026-04-16",
        fiscal_period_end="2025-12-31",
    )

    assert result["source_tier"] == "regulatory"
    assert result["official_scope"] == "financial_filing"
    assert result["publisher_id"] == "sec_edgar"
    assert result["content_sha256"] == hashlib.sha256(body).hexdigest()
    assert result["item_id"]
    assert result["canonical_url"].endswith("/tsm-20251231.htm")
    assert result["official_claims"] == []


def test_sec_filing_evidence_uses_configured_issuer_metadata(monkeypatch) -> None:
    body = b"<html><title>Delta filing</title></html>"

    def fake_get(url: str, **kwargs):
        return httpx.Response(200, content=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.official_sources.httpx.get", fake_get)
    result = fetch_sec_filing_evidence(
        cik="0000000001",
        accession="0000000000-00-000001",
        document="delta.htm",
        filing_date="2026-04-16",
        fiscal_period_end="2025-12-31",
        source_id="sec_delta_20f",
        issuer_name="Delta Electronics, Inc.",
        form_label="Form 20-F",
    )

    assert result["source_id"] == "sec_delta_20f"
    assert result["title"].startswith("Delta Electronics, Inc. Form 20-F")
    assert "Taiwan Semiconductor" not in result["summary"]


def test_twse_company_profile_evidence_selects_symbol_and_preserves_payload_hash(monkeypatch) -> None:
    payload = [{"公司代號": "2308", "公司名稱": "台達電子工業股份有限公司", "產業別": "電腦及週邊設備業"}]
    body = __import__("json").dumps(payload, ensure_ascii=False).encode("utf-8")

    def fake_get(url: str, **kwargs):
        return httpx.Response(200, content=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.official_sources.httpx.get", fake_get)
    result = fetch_twse_company_profile_evidence(
        {"symbol": "2308.TW", "name": "Delta Electronics, Inc."}
    )

    assert result["source_tier"] == "official"
    assert result["official_scope"] == "identity_profile"
    assert result["publisher_id"] == "twse_openapi"
    assert result["source_id"] == "twse_2308_company_profile"
    assert result["content_sha256"] == hashlib.sha256(body).hexdigest()
    assert result["official_data"]["公司名稱"] == "台達電子工業股份有限公司"


def test_twse_company_profile_rejects_invalid_symbol_and_payload(monkeypatch) -> None:
    with __import__("pytest").raises(ValueError, match="numeric"):
        fetch_twse_company_profile_evidence({"symbol": "ABC.TW"})

    def fake_get(url: str, **kwargs):
        body = json.dumps({"not": "an array"}).encode("utf-8")
        return httpx.Response(200, content=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.official_sources.httpx.get", fake_get)
    with __import__("pytest").raises(RuntimeError, match="non-array"):
        fetch_twse_company_profile_evidence({"symbol": "2308.TW"})


def test_twse_company_profile_reports_missing_company_and_invalid_roc_date(monkeypatch) -> None:
    body = json.dumps([{"公司代號": "9999", "公司名稱": "Other", "出表日期": "invalid"}], ensure_ascii=False).encode()

    def fake_get(url: str, **kwargs):
        return httpx.Response(200, content=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.official_sources.httpx.get", fake_get)
    with __import__("pytest").raises(RuntimeError, match="did not contain"):
        fetch_twse_company_profile_evidence({"symbol": "2308.TW"})

    body = json.dumps([{"公司代號": "2308", "公司名稱": "Delta", "出表日期": "invalid"}], ensure_ascii=False).encode()
    result = fetch_twse_company_profile_evidence({"symbol": "2308.TW"})
    assert result["published_at"].endswith("Z")


def test_twse_financial_statement_evidence_combines_income_and_balance_rows(monkeypatch) -> None:
    income = [{
        "出表日期": "1150822", "年度": "115", "季別": "2", "公司代號": "2371",
        "公司名稱": "大同", "營業收入": "24501702.00", "基本每股盈餘（元）": "0.91",
    }]
    balance = [{
        "出表日期": "1150822", "年度": "115", "季別": "2", "公司代號": "2371",
        "公司名稱": "大同", "權益─歸屬於母公司業主之權益合計": "48217604.00",
        "每股參考淨值": "23.57",
    }]
    bodies = [json.dumps(income, ensure_ascii=False).encode(), json.dumps(balance, ensure_ascii=False).encode()]
    calls: list[str] = []

    def fake_get(url: str, **kwargs):
        calls.append(url)
        body = bodies[0] if "t187ap06" in url else bodies[1]
        return httpx.Response(200, content=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("finance_crawler_poc.official_sources.httpx.get", fake_get)
    result = fetch_twse_financial_statement_evidence({"symbol": "2371.TW", "name": "Tatung Company"})

    assert result["source_tier"] == "regulatory"
    assert result["official_scope"] == "financial_statement"
    assert result["publisher_id"] == "twse_openapi"
    assert result["fiscal_period_end"] == "2026-06-30"
    assert result["financial_fields"]["book_value_per_share"] == 23.57
    assert result["financial_fields"]["official_eps"] == 0.91
    assert len(calls) == 2
    assert result["content_sha256"] == hashlib.sha256(result["raw_bytes"]).hexdigest()
    assert result["source_ref"]["response_hashes"]
