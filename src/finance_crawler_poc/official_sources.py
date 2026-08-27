"""Fetch auditable regulatory evidence for target research."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from calendar import monthrange
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import httpx

from finance_crawler_poc.contracts import build_item_id


_CIK_PATTERN = re.compile(r"^\d{10}$")
_ACCESSION_PATTERN = re.compile(r"^\d{10}-\d{2}-\d{6}$")

_SEC_CLAIM_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("risk_factors", ("risk factors",)),
    ("financial_statements", ("financial statements",)),
    ("revenue", ("revenue",)),
    ("capital_expenditures", ("capital expenditures", "capital expenditure")),
    ("net_income", ("net income",)),
    ("cash_and_equivalents", ("cash and cash equivalents",)),
    ("advanced_technologies", ("advanced technologies",)),
)


class _VisibleTextParser(HTMLParser):
    """Extract visible HTML text without treating markup as filing facts."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data)


def extract_sec_filing_claims(content: bytes) -> list[dict[str, Any]]:
    """Return deterministic section anchors observed in a SEC HTML filing.

    These are locator-backed coverage markers, not parsed financial facts.  The
    pipeline must not turn a keyword match into a numeric claim without a
    separate table parser and unit validation.
    """

    parser = _VisibleTextParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    normalized_text = " ".join(" ".join(parser.parts).split())
    lowered = normalized_text.casefold()
    claims: list[dict[str, Any]] = []
    for field, terms in _SEC_CLAIM_TERMS:
        match = next((term for term in terms if term.casefold() in lowered), None)
        if match is None:
            continue
        position = lowered.index(match.casefold())
        start = max(0, position - 160)
        end = min(len(normalized_text), position + max(320, len(match) + 160))
        excerpt = normalized_text[start:end]
        claim_id = hashlib.sha256(
            f"sec_filing_claim_v1\0{field}\0{position}\0{excerpt}".encode("utf-8")
        ).hexdigest()
        claims.append({
            "claim_id": claim_id,
            "field": field,
            "status": "observed",
            "matched_term": match,
            "excerpt": excerpt,
            "locator": {
                "type": "normalized_text_offset",
                "offset": position,
            },
        })
    return claims


def fetch_sec_filing_evidence(
    *,
    cik: str,
    accession: str,
    document: str,
    filing_date: str,
    fiscal_period_end: str,
    timeout_seconds: float = 20.0,
    source_id: str = "sec_tsmc_20f",
    issuer_name: str = "Taiwan Semiconductor Manufacturing Company Limited",
    form_label: str = "Form 20-F",
) -> dict[str, Any]:
    """Fetch one SEC filing and return a normalized regulatory evidence item.

    The response body is intentionally not embedded in the item.  Callers
    must persist the exact bytes separately and use ``content_sha256`` to
    connect this item to that frozen raw payload.
    """

    normalized_cik = str(cik).strip()
    normalized_accession = str(accession).strip()
    normalized_document = str(document).strip().lstrip("/")
    if not _CIK_PATTERN.fullmatch(normalized_cik):
        raise ValueError("SEC CIK must be a ten-digit identifier")
    if not _ACCESSION_PATTERN.fullmatch(normalized_accession):
        raise ValueError("SEC accession must use 0000000000-00-000000 format")
    if not normalized_document or "/" in normalized_document or ".." in normalized_document:
        raise ValueError("SEC document must be a single safe filename")
    filing_dt = _parse_date(filing_date, "filing_date")
    fiscal_dt = _parse_date(fiscal_period_end, "fiscal_period_end")
    accession_path = normalized_accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(normalized_cik)}/{accession_path}/{normalized_document}"
    response = httpx.get(
        url,
        timeout=timeout_seconds,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "finance-crawler-validation/0.1 research@example.com",
        },
    )
    response.raise_for_status()
    content_sha256 = hashlib.sha256(response.content).hexdigest()
    item_id = build_item_id(source_id, url, content_sha256)
    return {
        "item_id": item_id,
        "source_id": source_id,
        "publisher_id": "sec_edgar",
        "source_tier": "regulatory",
        "official_scope": "financial_filing",
        "independence_group": "sec_edgar",
        "transport": "static_html",
        "canonical_url": url,
        "content_sha256": content_sha256,
        "title": f"{issuer_name} {form_label} for fiscal year ended {fiscal_dt.date().isoformat()}",
        "summary": f"SEC filing submitted {filing_dt.date().isoformat()} for {issuer_name}.",
        "published_at": datetime.combine(filing_dt.date(), datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        "fiscal_period_end": fiscal_dt.date().isoformat(),
        "content_type": response.headers.get("content-type", "application/octet-stream"),
        "raw_content_length": len(response.content),
        "source_ref": {"url": url, "response_sha256": content_sha256, "item_id": item_id},
        "official_claims": extract_sec_filing_claims(response.content),
        "raw_bytes": response.content,
    }


def fetch_twse_company_profile_evidence(
    target: Mapping[str, Any],
    *,
    timeout_seconds: float = 20.0,
    source_id: str | None = None,
) -> dict[str, Any]:
    """Fetch one listed-company profile from TWSE's public OpenAPI.

    The profile is an official identity/sector anchor, not a substitute for
    audited financial statements.  Numeric claims must still come from a
    period-aligned fundamentals provider or an annual-report adapter.
    """

    symbol = str(target.get("symbol") or "").strip().upper()
    code = symbol.split(".", 1)[0]
    if not re.fullmatch(r"\d{4,6}", code):
        raise ValueError("TWSE company profile requires a numeric listed-company symbol")
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    response = httpx.get(
        url,
        timeout=timeout_seconds,
        headers={"Accept": "application/json", "User-Agent": "finance-crawler-validation/1.0"},
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("TWSE company profile returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise RuntimeError("TWSE company profile returned a non-array payload")
    row = next(
        (
            candidate
            for candidate in payload
            if isinstance(candidate, Mapping) and str(candidate.get("公司代號") or "").strip() == code
        ),
        None,
    )
    if not isinstance(row, Mapping):
        raise RuntimeError(f"TWSE company profile did not contain listed company {code}")
    content_sha256 = hashlib.sha256(response.content).hexdigest()
    resolved_source_id = source_id or f"twse_{code}_company_profile"
    item_id = build_item_id(resolved_source_id, url, content_sha256)
    issuer_name = str(row.get("公司名稱") or target.get("name") or code).strip()
    observed_date = _twse_profile_date(row.get("出表日期"))
    return {
        "item_id": item_id,
        "source_id": resolved_source_id,
        "publisher_id": "twse_openapi",
        "source_tier": "official",
        "official_scope": "identity_profile",
        "independence_group": "twse_openapi",
        "transport": "json_api",
        "canonical_url": url,
        "content_sha256": content_sha256,
        "title": f"{issuer_name} TWSE listed-company profile",
        "summary": (
            f"Official TWSE profile for {issuer_name} ({code}); "
            f"industry code {str(row.get('產業別') or '').strip() or 'not provided'}."
        ),
        "published_at": observed_date,
        "collected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "content_type": response.headers.get("content-type", "application/json"),
        "raw_content_length": len(response.content),
        "source_ref": {"url": url, "response_sha256": content_sha256, "item_id": item_id},
        "official_data": dict(row),
        "raw_bytes": response.content,
    }


def fetch_twse_financial_statement_evidence(
    target: Mapping[str, Any],
    *,
    timeout_seconds: float = 40.0,
    source_id: str | None = None,
    income_url: str = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
    balance_url: str = "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci",
) -> dict[str, Any]:
    """Fetch the latest listed-company income statement and balance sheet.

    TWSE's OpenAPI exposes the latest quarter as two regulator-hosted JSON
    datasets.  The returned evidence item is a deterministic envelope of the
    two exact response payloads, with both response hashes retained.  It is a
    financial-statement source, unlike the company-profile endpoint.
    """

    symbol = str(target.get("symbol") or "").strip().upper()
    code = symbol.split(".", 1)[0]
    if not re.fullmatch(r"\d{4,6}", code):
        raise ValueError("TWSE financial statement requires a numeric listed-company symbol")
    responses: list[tuple[str, bytes, Any]] = []
    for url in (income_url, balance_url):
        response = httpx.get(
            url,
            timeout=timeout_seconds,
            headers={"Accept": "application/json", "User-Agent": "finance-crawler-validation/1.0"},
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"TWSE financial statement returned invalid JSON: {url}") from exc
        if not isinstance(payload, list):
            raise RuntimeError(f"TWSE financial statement returned a non-array payload: {url}")
        responses.append((url, response.content, payload))

    income_row = _twse_financial_row(responses[0][2], code, "income statement")
    balance_row = _twse_financial_row(responses[1][2], code, "balance sheet")
    year_text = str(income_row.get("年度") or balance_row.get("年度") or "").strip()
    quarter_text = str(income_row.get("季別") or balance_row.get("季別") or "").strip()
    fiscal_period_end = _twse_fiscal_period_end(year_text, quarter_text)
    observed_date = _twse_profile_date(income_row.get("出表日期") or balance_row.get("出表日期"))
    envelope = {
        "income_statement": income_row,
        "balance_sheet": balance_row,
        "response_hashes": {
            responses[0][0]: hashlib.sha256(responses[0][1]).hexdigest(),
            responses[1][0]: hashlib.sha256(responses[1][1]).hexdigest(),
        },
    }
    raw_bytes = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    resolved_source_id = source_id or f"twse_{code}_financial_statement"
    canonical_url = responses[0][0]
    item_id = build_item_id(resolved_source_id, canonical_url, content_sha256)
    issuer_name = str(income_row.get("公司名稱") or target.get("name") or code).strip()
    financial_fields = _twse_financial_fields(income_row, balance_row, fiscal_period_end)
    return {
        "item_id": item_id,
        "source_id": resolved_source_id,
        "publisher_id": "twse_openapi",
        "source_tier": "regulatory",
        "official_scope": "financial_statement",
        "independence_group": "twse_openapi",
        "transport": "json_api",
        "canonical_url": canonical_url,
        "content_sha256": content_sha256,
        "title": f"{issuer_name} TWSE financial statements for period ended {fiscal_period_end}",
        "summary": (
            f"Official TWSE consolidated income statement and balance sheet for {issuer_name}; "
            f"fiscal period ended {fiscal_period_end}."
        ),
        "published_at": observed_date,
        "collected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "fiscal_period_end": fiscal_period_end,
        "content_type": "application/json",
        "raw_content_length": len(raw_bytes),
        "source_ref": {
            "url": canonical_url,
            "response_sha256": content_sha256,
            "item_id": item_id,
            "related_urls": [responses[1][0]],
            "response_hashes": envelope["response_hashes"],
        },
        "official_data": envelope,
        "financial_fields": financial_fields,
        "raw_bytes": raw_bytes,
    }


def _twse_financial_row(payload: list[Any], code: str, label: str) -> Mapping[str, Any]:
    row = next(
        (candidate for candidate in payload if isinstance(candidate, Mapping) and str(candidate.get("公司代號") or "").strip() == code),
        None,
    )
    if not isinstance(row, Mapping):
        raise RuntimeError(f"TWSE {label} did not contain listed company {code}")
    return row


def _twse_fiscal_period_end(year_text: str, quarter_text: str) -> str:
    if not re.fullmatch(r"\d{3,4}", year_text):
        raise RuntimeError("TWSE financial statement missing fiscal year")
    if not re.fullmatch(r"[1-4]", quarter_text):
        raise RuntimeError("TWSE financial statement missing fiscal quarter")
    year_value = int(year_text)
    year = year_value + 1911 if year_value < 1911 else year_value
    month = {"1": 3, "2": 6, "3": 9, "4": 12}[quarter_text]
    return f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"


def _twse_number(row: Mapping[str, Any], field: str) -> float | None:
    value = row.get(field)
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _twse_financial_fields(
    income_row: Mapping[str, Any],
    balance_row: Mapping[str, Any],
    fiscal_period_end: str,
) -> dict[str, Any]:
    book_value_per_share = _twse_number(balance_row, "每股參考淨值")
    official_eps = _twse_number(income_row, "基本每股盈餘（元）")
    revenue = _twse_number(income_row, "營業收入")
    return {
        "book_value_per_share": book_value_per_share,
        "book_value_as_of": fiscal_period_end,
        "official_eps": official_eps,
        "official_revenue": revenue,
        "official_fiscal_period_end": fiscal_period_end,
        "official_fiscal_year": str(income_row.get("年度") or "").strip() or None,
        "official_fiscal_quarter": str(income_row.get("季別") or "").strip() or None,
    }


def _twse_profile_date(value: Any) -> str:
    """Convert TWSE ROC dates (e.g. 1150821) to an ISO UTC timestamp."""

    text = str(value or "").strip()
    if re.fullmatch(r"\d{7,8}", text):
        if len(text) == 7:
            year = int(text[:3]) + 1911
            month = int(text[3:5])
            day = int(text[5:7])
        else:
            year = int(text[:4])
            month = int(text[4:6])
            day = int(text[6:8])
        try:
            return datetime(year, month, day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_date(value: str, field: str) -> datetime:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc
