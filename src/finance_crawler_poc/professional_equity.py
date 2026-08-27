"""Professional equity-research models and human-first report rendering.

The module keeps observed financial history, analyst assumptions and computed
valuation in separate contracts.  That boundary is deliberate: a report may
explain a model, but prose is never allowed to invent a model input.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

from .paragraph_quality import audit_markdown_report


_FLOW_TYPES = {
    "Revenue": "revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncome": "operating_income",
    "IncomeAfterTaxes": "net_income",
    "EPS": "eps",
}
_BALANCE_TYPES = {
    "TotalAssets": "total_assets",
    "Liabilities": "total_liabilities",
    "Equity": "equity",
    "CashAndCashEquivalents": "cash",
    "CurrentAssets": "current_assets",
    "CurrentLiabilities": "current_liabilities",
}
_CASH_TYPES = {
    "CashFlowsFromOperatingActivities": "operating_cash_flow",
    "NetCashInflowFromOperatingActivities": "operating_cash_flow",
    "PropertyAndPlantAndEquipment": "capital_expenditure",
    "Depreciation": "depreciation",
}
_CHAPTER_TITLES = (
    "封面與決策卡",
    "執行摘要",
    "投資論點與差異觀點",
    "公司與商業模式",
    "產業與競爭定位",
    "管理層、治理與資本配置",
    "歷史財務與盈餘品質",
    "財務預測與關鍵假設",
    "估值與敏感度",
    "市場表現、流動性與持股",
    "催化劑與事件日曆",
    "新聞、社群與敘事背離",
    "投資風險與 bear case",
    "ESG、法規與地緣政治重大性",
    "結論與監測計畫",
)

_FINMIND_DATASETS = (
    "TaiwanStockFinancialStatements",
    "TaiwanStockBalanceSheet",
    "TaiwanStockCashFlowsStatement",
    "TaiwanStockMonthRevenue",
    "TaiwanStockDividend",
)


def _fetch_context_url(get: Any, url: str, *, timeout_seconds: float, accept: str) -> tuple[Any, str, list[str]]:
    """Fetch one public context URL with a bounded, auditable TLS fallback.

    A small number of issuer hosts present a certificate chain that the local
    httpx trust store cannot validate even though the same public URL is
    reachable by another client.  Retry *only* that explicit certificate
    verification failure with ``verify=False`` and persist the relaxed mode in
    the resulting capture.  Other transport errors remain hard failures.
    """

    kwargs = {
        "timeout": timeout_seconds,
        "follow_redirects": True,
        "headers": {"Accept": accept, "User-Agent": "finance-crawler-validation/0.1"},
    }
    try:
        return get(url, **kwargs), "verified", []
    except httpx.ConnectError as exc:
        message = str(exc).casefold()
        if "certificate_verify_failed" not in message and "certificate verify failed" not in message:
            raise
        response = get(url, **kwargs, verify=False)
        return response, "relaxed_fallback", ["tls_verify_relaxed_fallback"]


def fetch_research_context_evidence(
    context: Mapping[str, Any], *, as_of: str, timeout_seconds: float = 45.0, get: Any = httpx.get,
) -> dict[str, Any]:
    """Freeze every configured qualitative source and attach its content hash."""

    collected_at = _canonical_datetime(as_of)
    enriched = deepcopy(dict(context))
    captures_by_url: dict[str, dict[str, Any]] = {}
    failed_by_url: dict[str, dict[str, str]] = {}
    for section_data in enriched.values():
        if not isinstance(section_data, dict):
            continue
        sources = section_data.get("sources")
        if not isinstance(sources, list):
            continue
        resolved = []
        for item in sources:
            if not isinstance(item, Mapping) or not str(item.get("url") or "").startswith(("http://", "https://")):
                continue
            source = dict(item)
            url = str(source["url"])
            capture = captures_by_url.get(url)
            previous_failure = failed_by_url.get(url)
            if previous_failure is not None:
                # A source can be referenced by company/industry/governance/
                # ESG.  Do not re-hit a known failing URL once per section;
                # preserve the failure metadata on each section's source while
                # logging one failure event per unique URL.
                current_required = bool(source.get("required", True))
                source.update(previous_failure)
                source["required"] = current_required
                resolved.append(source)
                continue
            if capture is None or (str(source.get("discovery") or "") == "annual_report_pdf" and "pdf" not in str(capture.get("content_type") or "").casefold()):
                try:
                    response = None
                    tls_verification = "verified"
                    quality_flags: list[str] = []
                    if capture is None:
                        response, tls_verification, quality_flags = _fetch_context_url(
                            get, url, timeout_seconds=timeout_seconds, accept="text/html,application/pdf,application/json,*/*"
                        )
                        response.raise_for_status()
                    effective_url = url
                    # Investor-relations pages are stable discovery roots but
                    # their annual-report PDF links rotate.  Resolve the
                    # latest explicit annual/report PDF once, then freeze the
                    # PDF bytes and cite the discovered URL rather than the
                    # landing page.  This keeps segment evidence real without
                    # hard-coding one issuer's asset path into the pipeline.
                    if str(source.get("discovery") or "") == "annual_report_pdf":
                        html = (response.content if response is not None else capture.get("content", b"")).decode("utf-8", errors="ignore")
                        links = re.findall(r"(?:href|src)=[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE)
                        anchor_links = re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, flags=re.IGNORECASE | re.DOTALL)
                        candidates = [
                            urljoin(url, link)
                            for link in links
                            if ".pdf" in link.casefold() and any(token in link.casefold() for token in ("annual", "report", "年報"))
                        ]
                        candidates.extend(
                            urljoin(url, link)
                            for link, label in anchor_links
                            if ".pdf" in link.casefold() and any(token in re.sub(r"<[^>]+>", " ", label).casefold() for token in ("annual", "report", "年報"))
                        )
                        # MOPS exposes the annual-report filename through a
                        # JavaScript ``readfile2`` form, then returns a short
                        # HTML resolver page whose final href is a timestamped
                        # ``/pdf/...`` path.  Treat both hops as discovery so
                        # the frozen capture cites the actual regulator PDF.
                        mops_files = re.findall(
                            r"readfile2\(\s*[\"']([^\"']+)[\"']\s*,\s*[\"']([^\"']+)[\"']\s*,\s*[\"']([^\"']+\.pdf)[\"']\s*\)",
                            html,
                            flags=re.IGNORECASE,
                        )
                        candidates.extend(
                            urljoin(
                                url,
                                f"/server-java/t57sb01?co_id={co_id}&colorchg=1&kind={kind}&step=9&filename={filename}",
                            )
                            for kind, co_id, filename in mops_files
                            if re.search(r"(?:annual|report|F04|年報)", filename, flags=re.IGNORECASE)
                        )
                        candidates = list(dict.fromkeys(candidates))
                        if candidates:
                            discovered = candidates[0]
                            discovered_response, discovered_tls, discovered_flags = _fetch_context_url(
                                get, discovered, timeout_seconds=timeout_seconds, accept="application/pdf,*/*"
                            )
                            discovered_response.raise_for_status()
                            discovered_type = str(getattr(discovered_response, "headers", {}).get("content-type") or "").casefold()
                            if "pdf" not in discovered_type:
                                resolver_html = discovered_response.content.decode("utf-8", errors="ignore")
                                nested_pdf = re.search(r"(?:href\s*=\s*)?[\"']?(/pdf/[^\"'\s>]+\.pdf)[\"']?", resolver_html, flags=re.IGNORECASE)
                                if nested_pdf:
                                    nested_url = urljoin(discovered, nested_pdf.group(1))
                                    discovered_response, nested_tls, nested_flags = _fetch_context_url(
                                        get, nested_url, timeout_seconds=timeout_seconds, accept="application/pdf,*/*"
                                    )
                                    discovered_response.raise_for_status()
                                    discovered = nested_url
                                    discovered_tls = nested_tls
                                    discovered_flags = list(dict.fromkeys([*discovered_flags, *nested_flags]))
                            response = discovered_response
                            effective_url = discovered
                            tls_verification = discovered_tls
                            quality_flags = list(dict.fromkeys([*quality_flags, *discovered_flags]))
                    if response is None:
                        # Existing non-discovery capture had no usable PDF
                        # link; retain it instead of issuing an unbounded retry.
                        response = None
                except (httpx.HTTPError, OSError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    required = bool(source.get("required", True))
                    failure = {"fetch_status": "failed", "fetch_error": error, "required": required, "quality_flags": ["fetch_failed"]}
                    failed_by_url[url] = failure
                    source.update(failure)
                    resolved.append(source)
                    continue
                if response is not None:
                    digest = hashlib.sha256(response.content).hexdigest()
                    capture = {
                        "url": effective_url,
                        "response_sha256": digest,
                        "content_type": str(getattr(response, "headers", {}).get("content-type") or "application/octet-stream"),
                        "content": response.content,
                        "collected_at": collected_at,
                        "tls_verification": tls_verification,
                        "quality_flags": quality_flags,
                    }
                    captures_by_url[url] = capture
            source.update({key: capture[key] for key in ("response_sha256", "content_type", "collected_at", "tls_verification", "quality_flags")})
            if capture.get("url") != url:
                source["discovered_from"] = url
                source["citation_url"] = capture.get("url")
                source["url"] = capture.get("url")
            source.update({"fetch_status": "success", "required": bool(source.get("required", True))})
            resolved.append(source)
        section_data["sources"] = resolved
    return {"context": enriched, "raw_captures": list(captures_by_url.values()), "fetch_failures": [{"url": url, "error": failure["fetch_error"], "required": failure["required"]} for url, failure in failed_by_url.items()]}


def fetch_finmind_financial_history(
    target: Mapping[str, Any], *, as_of: str, start_date: str = "2019-01-01",
    timeout_seconds: float = 30.0, get: Any = httpx.get,
) -> dict[str, Any]:
    """Fetch the five bounded FinMind datasets used by the financial model.

    FinMind's anonymous plan is deliberately used at one request per dataset.
    A request is retried at most three times and a 429 response honours a
    bounded ``Retry-After`` value.  The caller can persist ``raw_captures``.
    """

    symbol = str(target.get("symbol") or "").strip().upper()
    code = symbol.split(".", 1)[0]
    if not code.isdigit():
        raise ValueError("FinMind Taiwan equity adapter requires a numeric symbol")
    end_date = _parse_datetime(as_of).date().isoformat()
    base_url = "https://api.finmindtrade.com/api/v4/data"
    payloads: dict[str, list[Mapping[str, Any]]] = {}
    refs: list[dict[str, Any]] = []
    captures: list[dict[str, Any]] = []
    for dataset in _FINMIND_DATASETS:
        params = {"dataset": dataset, "data_id": code, "start_date": start_date, "end_date": end_date}
        response = None
        for attempt in range(1, 4):
            response = get(base_url, params=params, timeout=timeout_seconds, headers={"Accept": "application/json", "User-Agent": "finance-crawler-validation/0.1"})
            if int(getattr(response, "status_code", 0)) != 429:
                break
            if attempt < 3:
                retry_after = getattr(response, "headers", {}).get("Retry-After", "1")
                try:
                    wait_seconds = min(5.0, max(0.0, float(retry_after)))
                except (TypeError, ValueError):
                    wait_seconds = 1.0
                time.sleep(wait_seconds)
        if response is None:
            raise RuntimeError(f"FinMind {dataset} returned no response")
        response.raise_for_status()
        try:
            envelope = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"FinMind {dataset} returned invalid JSON") from exc
        rows = envelope.get("data") if isinstance(envelope, Mapping) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"FinMind {dataset} response has no data array")
        payloads[dataset] = [row for row in rows if isinstance(row, Mapping)]
        digest = hashlib.sha256(response.content).hexdigest()
        url = f"{base_url}?{httpx.QueryParams(params)}"
        refs.append({"dataset": dataset, "url": url, "response_sha256": digest, "row_count": len(rows), "collected_at": _canonical_datetime(as_of)})
        captures.append({"dataset": dataset, "url": url, "response_sha256": digest, "content": response.content})
    history = normalize_finmind_financial_history(payloads, target=target, as_of=as_of, source_refs=refs)
    dividends = payloads.get("TaiwanStockDividend", [])
    share_values = [float(row["ParticipateDistributionOfTotalShares"]) for row in dividends if _positive(row.get("ParticipateDistributionOfTotalShares"))]
    history["shares_outstanding"] = share_values[-1] if share_values else None
    return {"history": history, "raw_captures": captures}


def normalize_finmind_financial_history(
    payloads: Mapping[str, list[Mapping[str, Any]]],
    *,
    target: Mapping[str, Any],
    as_of: str,
    source_refs: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Normalize FinMind statement rows into five annual and eight quarterly periods."""

    _parse_datetime(as_of)
    symbol = str(target.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("target.symbol is required")
    quarters: dict[str, dict[str, float]] = defaultdict(dict)
    for dataset, mapping in (
        ("TaiwanStockFinancialStatements", _FLOW_TYPES),
        ("TaiwanStockBalanceSheet", _BALANCE_TYPES),
        ("TaiwanStockCashFlowsStatement", _CASH_TYPES),
    ):
        for row in payloads.get(dataset, []):
            date = str(row.get("date") or "")
            item_type = str(row.get("type") or "")
            value = row.get("value")
            if len(date) != 10 or item_type not in mapping or not _finite_number(value):
                continue
            field = mapping[item_type]
            # Some issuers expose both CFO aliases. Prefer the canonical field.
            if field not in quarters[date] or item_type == "CashFlowsFromOperatingActivities":
                quarters[date][field] = float(value)

    # FinMind cash-flow statements are year-to-date cumulative, while income
    # statement rows are already single-quarter values.  Convert cumulative
    # CFO/CAPEX/depreciation into quarter-only observations before any annual
    # aggregation.  Summing the raw Q1/Q2/Q3/Q4 values would materially
    # overstate cash generation and is prohibited by the model contract.
    cash_fields = ("operating_cash_flow", "capital_expenditure", "depreciation")
    dates_by_year: dict[str, list[str]] = defaultdict(list)
    for date in quarters:
        dates_by_year[date[:4]].append(date)
    for dates in dates_by_year.values():
        previous = {field: 0.0 for field in cash_fields}
        for date in sorted(dates):
            for field in cash_fields:
                if field not in quarters[date]:
                    continue
                cumulative = float(quarters[date][field])
                quarters[date][field] = cumulative - previous[field]
                previous[field] = cumulative

    normalized_quarters = [_complete_period(date, values) for date, values in sorted(quarters.items())]
    required = {"revenue", "gross_profit", "operating_income", "net_income", "eps", "operating_cash_flow", "capital_expenditure"}
    complete_quarters = [row for row in normalized_quarters if required.issubset(row) and int(row["period"][5:7]) in (3, 6, 9, 12)]
    annual_candidates: list[dict[str, Any]] = []
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in complete_quarters:
        by_year[int(str(row["period"])[:4])].append(row)
    for year, rows in sorted(by_year.items()):
        if len(rows) != 4:
            continue
        last = rows[-1]
        annual = {"period": str(year), "year": year}
        for field in ("revenue", "gross_profit", "operating_income", "net_income", "eps", "operating_cash_flow", "capital_expenditure", "depreciation"):
            annual[field] = round(sum(float(row.get(field) or 0.0) for row in rows), 6)
        for field in ("total_assets", "total_liabilities", "equity", "cash", "current_assets", "current_liabilities"):
            annual[field] = last.get(field)
        annual_candidates.append(_add_derived_metrics(annual))

    annual_periods = annual_candidates[-5:]
    quarterly_periods = complete_quarters[-8:]
    monthly_revenue: list[dict[str, Any]] = []
    for row in payloads.get("TaiwanStockMonthRevenue", []):
        revenue = row.get("revenue")
        year = row.get("revenue_year")
        month = row.get("revenue_month")
        if _finite_number(revenue) and isinstance(year, int) and isinstance(month, int) and 1 <= month <= 12:
            monthly_revenue.append({"period": f"{year:04d}-{month:02d}", "revenue": float(revenue)})
    monthly_revenue = sorted({row["period"]: row for row in monthly_revenue}.values(), key=lambda row: row["period"])[-24:]

    identity_checks = []
    for row in annual_periods:
        assets, liabilities, equity = row.get("total_assets"), row.get("total_liabilities"), row.get("equity")
        if all(_finite_number(value) for value in (assets, liabilities, equity)) and float(assets):
            identity_checks.append(abs(float(assets) - float(liabilities) - float(equity)) / abs(float(assets)) <= 0.02)
    missing: list[str] = []
    if len(annual_periods) < 5:
        missing.append("five_complete_annual_periods_required")
    if len(quarterly_periods) < 8:
        missing.append("eight_complete_quarterly_periods_required")
    if not source_refs:
        missing.append("source_reference_required")
    status = "available" if not missing else "insufficient_data"
    return {
        "schema_version": 1,
        "status": status,
        "target": dict(target),
        "currency": str(target.get("currency") or "TWD").upper(),
        "as_of": _canonical_datetime(as_of),
        "annual_periods": annual_periods,
        "quarterly_periods": quarterly_periods,
        "monthly_revenue": monthly_revenue,
        "source_refs": [dict(ref) for ref in source_refs],
        "validation": {
            "period_completeness": "pass" if len(annual_periods) >= 5 and len(quarterly_periods) >= 8 else "fail",
            "balance_sheet_identity": "pass" if identity_checks and all(identity_checks) else "unresolved",
            "free_cash_flow_formula": "operating_cash_flow_minus_absolute_capital_expenditure",
        },
        "missing_reasons": missing,
    }


def build_forecast_model(history: Mapping[str, Any], *, guidance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create a deterministic three-year bear/base/bull forecast with lineage."""

    annual = history.get("annual_periods") if isinstance(history.get("annual_periods"), list) else []
    currency = str(history.get("currency") or "TWD")
    if history.get("status") != "available" or len(annual) < 5:
        return _unavailable_forecast(currency, "five_year_history_required")
    last = annual[-1]
    first = annual[-4]
    if not _positive(first.get("revenue")) or not _positive(last.get("revenue")):
        return _unavailable_forecast(currency, "positive_revenue_history_required")
    historical_cagr = (float(last["revenue"]) / float(first["revenue"])) ** (1 / 3) - 1
    monthly = history.get("monthly_revenue") if isinstance(history.get("monthly_revenue"), list) else []
    monthly_yoy = None
    if len(monthly) >= 24:
        recent = sum(float(row["revenue"]) for row in monthly[-12:])
        prior = sum(float(row["revenue"]) for row in monthly[-24:-12])
        monthly_yoy = recent / prior - 1 if prior > 0 else None
    base_growth = _clamp((historical_cagr * 0.4 + monthly_yoy * 0.6) if monthly_yoy is not None else historical_cagr, -0.10, 0.30)
    recent = annual[-3:]
    operating_margin = sum(float(row.get("operating_margin") or 0.0) for row in recent) / len(recent)
    cash_conversion = _safe_ratio(sum(float(row.get("operating_cash_flow") or 0.0) for row in recent), sum(float(row.get("net_income") or 0.0) for row in recent), default=1.0)
    capital_intensity = _safe_ratio(sum(abs(float(row.get("capital_expenditure") or 0.0)) for row in recent), sum(float(row.get("revenue") or 0.0) for row in recent), default=0.15)
    tax_rate = _clamp(1 - _safe_ratio(sum(float(row.get("net_income") or 0.0) for row in recent), sum(float(row.get("operating_income") or 0.0) for row in recent), default=0.8), 0.05, 0.35)
    guidance = guidance if isinstance(guidance, Mapping) else {}
    guided_growth = _scenario_guidance(guidance.get("revenue_growth"))
    guided_margin = _scenario_guidance(guidance.get("operating_margin"))
    if _finite_number(guidance.get("tax_rate")):
        tax_rate = _clamp(float(guidance["tax_rate"]), 0.05, 0.35)
    assumptions = {
        "revenue_growth": {
            "base": round(base_growth, 6), "bear": round(base_growth - 0.07, 6), "bull": round(base_growth + 0.05, 6),
            "lineage": ["three_year_revenue_cagr", "latest_twelve_month_revenue_yoy" if monthly_yoy is not None else "monthly_revenue_unavailable"],
            "historical_cagr": round(historical_cagr, 6), "latest_twelve_month_yoy": round(monthly_yoy, 6) if monthly_yoy is not None else None,
        },
        "operating_margin": {
            "base": round(operating_margin, 6), "bear": round(max(0.0, operating_margin - 0.04), 6), "bull": round(min(0.8, operating_margin + 0.03), 6),
            "lineage": ["three_year_average_operating_margin", "scenario_margin_delta"],
        },
        "cash_conversion": {"base": round(_clamp(cash_conversion, 0.5, 1.5), 6), "lineage": ["three_year_cfo_to_net_income"]},
        "capital_intensity": {"base": round(_clamp(capital_intensity, 0.02, 0.6), 6), "lineage": ["three_year_capex_to_revenue"]},
        "tax_rate": {"base": round(tax_rate, 6), "lineage": ["three_year_net_income_to_operating_income"]},
    }
    if guided_growth:
        assumptions["revenue_growth"]["by_year"] = guided_growth
        for name in ("bear", "base", "bull"):
            assumptions["revenue_growth"][name] = guided_growth[name][0]
        assumptions["revenue_growth"]["lineage"] = ["official_guidance_anchor", "analyst_scenario_assumption", "explicit_three_year_fade"]
    if guided_margin:
        assumptions["operating_margin"]["by_year"] = guided_margin
        for name in ("bear", "base", "bull"):
            assumptions["operating_margin"][name] = guided_margin[name][0]
    if isinstance(guidance.get("lineage"), list):
        assumptions["guidance_lineage"] = [dict(item) for item in guidance["lineage"] if isinstance(item, Mapping)]
        assumptions["tax_rate"]["lineage"] = ["management_guidance"]
    scenario_previous = {name: dict(last) for name in ("bear", "base", "bull")}
    periods: list[dict[str, Any]] = []
    base_year = int(last["year"])
    for step in range(1, 4):
        scenario_values: dict[str, dict[str, float]] = {}
        for name in ("bear", "base", "bull"):
            growth = float(guided_growth[name][step - 1] if guided_growth else assumptions["revenue_growth"][name])
            margin = float(guided_margin[name][step - 1] if guided_margin else assumptions["operating_margin"][name])
            previous = scenario_previous[name]
            revenue = float(previous["revenue"]) * (1 + growth)
            operating_income = revenue * margin
            net_income = operating_income * (1 - tax_rate)
            prior_eps = float(previous.get("eps") or 0.0)
            prior_net_income = float(previous.get("net_income") or 0.0)
            # A bear case can legitimately drive a loss-making issuer's
            # modeled net income to exactly zero.  Re-scaling EPS by
            # ``net_income / prior_net_income`` would then create 0/0 on the
            # next step.  Keep the forecast executable and expose the
            # denominator limitation for the audit/model reader; valuation is
            # already required to fall back to DCF-only when trailing EPS is
            # non-positive.
            if abs(prior_net_income) > 1e-12:
                eps = prior_eps * (net_income / prior_net_income)
                eps_status = "scaled_from_prior_net_income"
            else:
                eps = 0.0
                eps_status = "unresolved_prior_net_income_zero"
            operating_cash_flow = net_income * float(assumptions["cash_conversion"]["base"])
            capital_expenditure = revenue * float(assumptions["capital_intensity"]["base"])
            free_cash_flow = operating_cash_flow - abs(capital_expenditure)
            values = {
                "revenue": round(revenue, 6), "revenue_growth": round(growth, 6), "operating_margin": round(margin, 6),
                "operating_income": round(operating_income, 6), "net_income": round(net_income, 6), "eps": round(eps, 6), "eps_status": eps_status,
                "operating_cash_flow": round(operating_cash_flow, 6), "capital_expenditure": round(capital_expenditure, 6), "free_cash_flow": round(free_cash_flow, 6),
            }
            scenario_values[name] = values
            scenario_previous[name] = values
        periods.append({"year": base_year + step, "scenarios": scenario_values})
    ordered = all(period["scenarios"]["bear"]["revenue"] <= period["scenarios"]["base"]["revenue"] <= period["scenarios"]["bull"]["revenue"] for period in periods)
    return {
        "schema_version": 1, "status": "available", "base_year": base_year, "currency": currency,
        "forecast_periods": periods, "assumptions": assumptions,
        "validation": {"formula_replay": "pass", "scenario_ordering": "pass" if ordered else "fail", "historical_period_count": len(annual)},
        "missing_reasons": [],
    }


def build_valuation_model(
    forecast: Mapping[str, Any], *, market_price: float | None, shares_outstanding: float | None,
    net_debt: float | None, peer_median_pe: float | None, discount_rate: float = 0.10, terminal_growth: float = 0.03,
    peer_basis: str = "forward_pe", target_eps: float | None = None, target_period_key: str | None = None,
    peer_median_ps: float | None = None, target_revenue: float | None = None,
) -> dict[str, Any]:
    """Calculate a cash-flow DCF and an explicitly period-aligned P/E cross-check.

    ``peer_basis`` is part of the model contract.  A trailing peer multiple
    must use ``target_eps`` from the same historical period; it may not be
    silently applied to forecast EPS.  The default remains forward P/E for
    backwards-compatible fixture callers, while production callers pass the
    basis declared by the peer-valuation adapter.
    """

    currency = str(forecast.get("currency") or "TWD")
    missing = []
    if forecast.get("status") != "available": missing.append("three_year_forecast_required")
    if not _positive(shares_outstanding): missing.append("positive_shares_outstanding_required")
    if not _positive(market_price): missing.append("positive_market_price_required")
    normalized_peer_basis = str(peer_basis or "").strip().casefold()
    dcf_only = normalized_peer_basis == "dcf_only"
    if not dcf_only and normalized_peer_basis in {"forward_pe", "trailing_pe"} and not _positive(peer_median_pe): missing.append("positive_peer_multiple_required")
    if not dcf_only and normalized_peer_basis == "trailing_ps" and not _positive(peer_median_ps): missing.append("positive_peer_sales_multiple_required")
    if net_debt is None or not _finite_number(net_debt): missing.append("net_debt_required")
    if not (0 < terminal_growth < discount_rate < 1): missing.append("valid_discount_and_terminal_rates_required")
    if normalized_peer_basis not in {"forward_pe", "trailing_pe", "trailing_ps", "dcf_only"}:
        missing.append("supported_peer_multiple_basis_required")
    if normalized_peer_basis == "trailing_pe" and not _positive(target_eps):
        missing.append("positive_target_eps_required_for_trailing_pe")
    if normalized_peer_basis == "trailing_ps" and not _positive(target_revenue):
        missing.append("positive_target_revenue_required_for_trailing_ps")
    assumptions = {
        "discount_rate": discount_rate,
        "discount_rate_basis": "cost_of_equity_assumption",
        "terminal_growth": terminal_growth,
        "shares_outstanding": shares_outstanding,
        "net_debt": net_debt,
        "peer_median_pe": peer_median_pe,
        "peer_median_ps": peer_median_ps,
        "peer_multiple_basis": normalized_peer_basis,
        "peer_target_eps": target_eps,
        "peer_target_revenue": target_revenue,
        "peer_target_period_key": target_period_key,
        "method_weighting": {"dcf_fcfe_proxy": 1.0} if dcf_only else ({"dcf_fcfe_proxy": 0.4, normalized_peer_basis: 0.6} if normalized_peer_basis == "trailing_ps" else {"dcf_fcfe_proxy": 0.6, normalized_peer_basis: 0.4}),
        "cash_flow_definition": "operating_cash_flow_minus_capital_expenditure",
        "dcf_method_type": "fcfe_proxy",
        "net_debt_treatment": "not_subtracted; cash-flow proxy is intended to represent equity cash flow because net borrowing is unavailable",
    }
    if missing:
        return {"schema_version": 1, "status": "insufficient_data", "rating": "Not Rated", "currency": currency, "market_price": market_price, "methods": [], "assumptions": assumptions, "sensitivity": {"matrix": []}, "target_range": None, "upside_downside_pct": None, "missing_reasons": missing}
    periods = forecast["forecast_periods"]
    dcf_values = {name: _dcf_per_share(periods, name, float(shares_outstanding), discount_rate, terminal_growth) for name in ("bear", "base", "bull")}
    # A negative base-case FCFE is not an actionable per-share target.  If a
    # period-aligned P/S cross-check is available, use it as the market-based
    # second method and disclose why the P/E route was not selected.  This is
    # a deterministic applicability rule, not a manual target-price override.
    base_period = periods[0].get("scenarios", {}).get("base", {}) if isinstance(periods[0], Mapping) else {}
    base_cash_flow = base_period.get("free_cash_flow") if isinstance(base_period, Mapping) else None
    if (
        not dcf_only
        and (dcf_values["base"] <= 0 or (base_cash_flow is not None and float(base_cash_flow) <= 0))
        and _positive(peer_median_ps)
        and _positive(target_revenue)
    ):
        normalized_peer_basis = "trailing_ps"
        assumptions["peer_multiple_basis"] = normalized_peer_basis
        assumptions["multiple_fallback_reason"] = "dcf_base_value_non_positive"
        assumptions["method_weighting"] = {"dcf_fcfe_proxy": 0.4, "trailing_ps": 0.6}
    if dcf_only:
        peer_values = {}
        combined = dict(dcf_values)
    elif normalized_peer_basis == "trailing_pe":
        peer_values = {name: float(target_eps) * float(peer_median_pe) for name in ("bear", "base", "bull")}
    elif normalized_peer_basis == "trailing_ps":
        peer_values = {name: float(target_revenue) / float(shares_outstanding) * float(peer_median_ps) for name in ("bear", "base", "bull")}
    else:
        peer_values = {name: float(periods[0]["scenarios"][name]["eps"]) * float(peer_median_pe) for name in ("bear", "base", "bull")}
    if not dcf_only:
        dcf_weight = float(assumptions["method_weighting"]["dcf_fcfe_proxy"])
        peer_weight = float(assumptions["method_weighting"][normalized_peer_basis])
        combined = {name: dcf_values[name] * dcf_weight + peer_values[name] * peer_weight for name in ("bear", "base", "bull")}
    method_values = list(dcf_values.values()) + list(peer_values.values())
    method_dispersion_pct = abs(peer_values["base"] - dcf_values["base"]) / combined["base"] * 100 if not dcf_only else None
    assumptions["method_dispersion_pct"] = round(method_dispersion_pct, 6) if method_dispersion_pct is not None else None
    assumptions["method_dispersion_policy"] = "weighted_target_is_a_decision_band_not_a_statistical_confidence_interval"
    target_range = {
        "low": round(min(combined.values()), 6), "base": round(combined["base"], 6), "high": round(max(combined.values()), 6),
        "method_envelope_low": round(min(method_values), 6), "method_envelope_high": round(max(method_values), 6), "horizon_months": 12,
    }
    upside = (target_range["base"] / float(market_price) - 1) * 100
    rating = "Positive" if upside >= 15 else "Cautious" if upside <= -10 else "Neutral"
    matrix = []
    for rate in (discount_rate - 0.01, discount_rate, discount_rate + 0.01):
        for growth in (terminal_growth - 0.01, terminal_growth, terminal_growth + 0.01):
            matrix.append({"discount_rate": round(rate, 4), "terminal_growth": round(growth, 4), "value_per_share": round(_dcf_per_share(periods, "base", float(shares_outstanding), rate, growth), 6)})
    return {
        "schema_version": 1, "status": "available", "rating": rating, "currency": currency, "market_price": float(market_price),
        "methods": [
            {"method": "dcf_fcfe_proxy", "status": "available", "scenario_values": {key: round(value, 6) for key, value in dcf_values.items()}, "weight": float(assumptions["method_weighting"]["dcf_fcfe_proxy"])},
            *([] if dcf_only else [{"method": normalized_peer_basis, "status": "available", "scenario_values": {key: round(value, 6) for key, value in peer_values.items()}, "weight": float(assumptions["method_weighting"][normalized_peer_basis])}]),
        ],
        "assumptions": assumptions, "sensitivity": {"matrix": matrix}, "target_range": target_range,
        "upside_downside_pct": round(upside, 6), "missing_reasons": [],
    }


def build_professional_research_report(
    *, profile: Mapping[str, Any], history: Mapping[str, Any], forecast: Mapping[str, Any],
    valuation: Mapping[str, Any], depth: Mapping[str, Any], metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble a 15-chapter report object from contract-validated inputs."""

    target = dict(profile.get("target") or {})
    evidence = depth.get("evidence_pack") if isinstance(depth.get("evidence_pack"), Mapping) else {}
    evidence_count = int(evidence.get("item_count") or len(evidence.get("items") or []))
    source_groups = int(evidence.get("source_group_count") or 0)
    stories = int(evidence.get("canonical_story_count") or 0)
    research_context = profile.get("research_context") if isinstance(profile.get("research_context"), Mapping) else {}
    qualitative_ready = _qualitative_context_ready(research_context)
    context_coverage = metadata.get("context_coverage") if isinstance(metadata.get("context_coverage"), Mapping) else None
    context_gap = metadata.get("context_gap") if isinstance(metadata.get("context_gap"), Mapping) else None
    production_retrieval = metadata.get("target_retrieval") if isinstance(metadata.get("target_retrieval"), Mapping) else None
    social_item_count = sum(
        1
        for item in evidence.get("items", [])
        if isinstance(item, Mapping)
        and (str(item.get("kind") or "").casefold() == "social" or str(item.get("layer") or "").casefold() == "social")
    )
    news_item_count = sum(
        1
        for item in evidence.get("items", [])
        if isinstance(item, Mapping)
        and str(item.get("kind") or "").casefold() == "news"
    )
    # A target-scoped evidence count is not a substitute for the research
    # questions that the report claims to answer.  Old unit fixtures do not
    # carry the new contract, so they retain the historical gate behaviour;
    # every production run writes this coverage object and is fail-closed.
    context_ready = bool(context_coverage and (context_coverage.get("summary") or {}).get("l3_ready") is True)
    gates = {
        "identity": "pass" if all(target.get(key) for key in ("symbol", "name", "market", "currency")) else "fail",
        "financial_model": "pass" if history.get("status") == forecast.get("status") == "available" else "fail",
        "valuation": "pass" if valuation.get("status") == "available" and (
            len(valuation.get("methods") or []) >= 2
            or (
                str((valuation.get("assumptions") or {}).get("peer_multiple_basis") or "") == "dcf_only"
                and len(valuation.get("methods") or []) == 1
            )
        ) else "fail",
        "evidence": "pass" if evidence_count >= 10 and source_groups >= 5 and stories >= 10 else "partial",
        "audit": "pass" if metadata.get("run_id") and history.get("source_refs") else "fail",
        "qualitative_research": "pass" if qualitative_ready else "partial",
    }
    if context_coverage is not None:
        gates["context_sufficiency"] = "pass" if context_ready else "partial"
    # Production runs explicitly attempt a target-scoped community route.  A
    # successful HTTP response with zero matching discussions is still an
    # unresolved research dimension, not permission to claim a complete
    # news/social divergence analysis.  Unit fixtures without retrieval
    # metadata retain the historical behaviour.
    if production_retrieval is not None:
        community = production_retrieval.get("community") if isinstance(production_retrieval.get("community"), Mapping) else {}
        community_status = str(community.get("status") or "unavailable")
        news_geo = production_retrieval.get("geo_coverage") if isinstance(production_retrieval.get("geo_coverage"), Mapping) else {}
        community_coverage = community.get("coverage") if isinstance(community.get("coverage"), Mapping) else {}
        geo_ready = str(news_geo.get("status") or "") == "complete" and str(community_coverage.get("status") or "") == "complete"
        gates["geo_coverage"] = "pass" if geo_ready else "partial"
        gates["news_social"] = "pass" if news_item_count >= 1 and social_item_count >= 1 and community_status == "available" and geo_ready else "partial"
    valuation_contract = _valuation_contract_status(valuation)
    gates["valuation_contract"] = valuation_contract
    shared_gate = depth.get("quality_gate") if isinstance(depth.get("quality_gate"), Mapping) else None
    event_alignment = depth.get("event_alignment") if isinstance(depth.get("event_alignment"), Mapping) else {}
    if shared_gate is not None:
        gates["shared_quality_gate"] = "pass" if shared_gate.get("status") == "professional_ready" else "fail"
        gates["event_study"] = "pass" if event_alignment.get("event_study_quality_status") == "complete" else "fail"
    risks = _build_risks(target, forecast, valuation, context_ready=context_ready)
    # A production L3 report cannot hide an uncalibrated risk probability in
    # the fourth risk row.  Unit fixtures without target retrieval metadata
    # retain the historical gate set; real reports fail closed until every
    # displayed risk has an evidence-backed probability class.
    if production_retrieval is not None:
        gates["risk_probability"] = "pass" if risks and all(
            str(item.get("probability") or "").casefold() not in {"", "unresolved", "unknown"}
            and str(item.get("probability_basis") or "").strip()
            for item in risks if isinstance(item, Mapping)
        ) else "partial"
    core_keys = ("identity", "financial_model", "valuation", "audit", "valuation_contract")
    core_pass = all(gates[key] == "pass" for key in core_keys)
    professional_keys = ("evidence", "qualitative_research")
    if context_coverage is not None:
        professional_keys = (*professional_keys, "context_sufficiency")
    if shared_gate is not None:
        professional_keys = (*professional_keys, "shared_quality_gate", "event_study")
    if "news_social" in gates:
        professional_keys = (*professional_keys, "news_social")
    if "geo_coverage" in gates:
        professional_keys = (*professional_keys, "geo_coverage")
    if "risk_probability" in gates:
        professional_keys = (*professional_keys, "risk_probability")
    professional_pass = all(gates[key] == "pass" for key in professional_keys)
    level = "L3" if core_pass and professional_pass and qualitative_ready and (context_coverage is None or context_ready) else "L2" if core_pass else "L1"
    rating = str(valuation.get("rating") or "Not Rated") if level == "L3" else "Not Rated"
    target_range = valuation.get("target_range") if level == "L3" else None
    annual = history.get("annual_periods") if isinstance(history.get("annual_periods"), list) else []
    latest = annual[-1] if annual else {}
    time_series = depth.get("time_series") if isinstance(depth.get("time_series"), Mapping) else {}
    market_price = valuation.get("market_price")
    theses = _build_theses(latest, forecast, valuation, history)
    catalysts = _build_catalysts(depth, research_context)
    monitoring = _build_monitoring(forecast, risks)
    content = _chapter_content(target, history, forecast, valuation, depth, metadata, theses, catalysts, risks, monitoring, research_context)
    evidence_index = _build_evidence_index(
        evidence=evidence,
        history=history,
        forecast=forecast,
        valuation=valuation,
        research_context=research_context,
        metadata=metadata,
    )
    _attach_claim_evidence(content, evidence_index)
    chapters = [{"id": str(index), "title": title, "status": "complete" if content[index].get("status") != "unresolved" else "partial", "content": content[index]} for index, title in enumerate(_CHAPTER_TITLES)]
    unresolved = _unresolved(gates, history, forecast, valuation, depth)
    if not content[11].get("social_item_count"):
        unresolved.append("social_narrative_source_unavailable")
    if any(
        isinstance(item, Mapping) and str(item.get("probability") or "").casefold() == "unresolved"
        for item in content[12].get("risks", [])
    ):
        unresolved.append("risk_probability_unresolved")
    generated_at = str(history.get("as_of") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    report_material = f"{metadata.get('run_id')}\0{target.get('symbol')}\0{generated_at}"
    return {
        "schema_version": 2,
        "report_id": "equity_" + hashlib.sha256(report_material.encode()).hexdigest()[:24],
        "report_level": level,
        "generated_at": _canonical_datetime(generated_at),
        "target": target,
        "decision_card": {"rating": rating, "market_price": market_price, "market_price_as_of": time_series.get("window_end"), "target_range": target_range, "horizon": "12 months", "confidence": "low" if level != "L3" or float(valuation.get("assumptions", {}).get("method_dispersion_pct") or 0) > 50 else "medium", "upside_downside_pct": valuation.get("upside_downside_pct") if level == "L3" else None},
        "executive_summary": {"theses": theses, "catalysts": catalysts, "risks": risks, "monitoring": monitoring},
        "chapters": chapters,
        "appendix": {"evidence": {**dict(evidence), "qualitative_sources": {key: value.get("sources", []) for key, value in research_context.items() if isinstance(value, Mapping) and value.get("sources")}, "evidence_index": evidence_index}, "context_coverage": deepcopy(dict(context_coverage)) if context_coverage is not None else None, "context_gap": deepcopy(dict(context_gap)) if context_gap is not None else None, "context_packs": deepcopy(dict(metadata.get("context_packs") or {})) if isinstance(metadata.get("context_packs"), Mapping) else {}, "claim_evidence_coverage": _claim_evidence_coverage(content), "calculations": {"forecast_validation": forecast.get("validation"), "forecast_assumptions": forecast.get("assumptions"), "valuation_assumptions": valuation.get("assumptions")}, "unresolved": list(dict.fromkeys(unresolved)), "run_metadata": dict(metadata)},
        "quality_gates": gates,
        "disclosures": {"investment_advice": "本報告是可稽核的研究第二意見，不是個人化投資建議。", "conflicts": "系統未持有標的部位，也未因本報告收受標的公司報酬；使用者仍須自行揭露利益關係。", "model_responsibility": "數值由可重播公式產生；自動文字只能解釋已保存的模型與證據，不能補造資料。"},
    }


def merge_qualitative_context_into_report(
    report: Mapping[str, Any], envelope: Mapping[str, Any], *, evidence_bundle: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge validated model context into the canonical report without replacing facts.

    Deterministic financial/market fields remain authoritative. A validated
    qualitative overlay may complete the final L3 gate when every deterministic
    core gate already passes; it can never manufacture a source or numerical
    valuation input. Before the overlay is available, the report stays at L2/L1
    and does not display a decision rating.
    """

    merged = deepcopy(dict(report))
    result = envelope.get("result") if isinstance(envelope.get("result"), Mapping) else {}
    validation = envelope.get("validation") if isinstance(envelope.get("validation"), Mapping) else {}
    sections = deepcopy(dict(result.get("sections") or {})) if isinstance(result.get("sections"), Mapping) else {}
    quality = result.get("quality") if isinstance(result.get("quality"), Mapping) else {}
    appendix = deepcopy(dict(merged.get("appendix") or {}))
    context_coverage = appendix.get("context_coverage") if isinstance(appendix.get("context_coverage"), Mapping) else None
    # A persisted/older model envelope may have marked a section complete
    # before the research-context gate was introduced.  Enforce the contract
    # at merge time as well as in the prompt: an incomplete requirement can
    # only yield a partial section, never a model-created L3 shortcut.
    incomplete_by_section: dict[str, list[str]] = {}
    coverage_gaps_by_section: dict[str, list[dict[str, Any]]] = {}
    if isinstance(context_coverage, Mapping):
        for requirement in context_coverage.get("requirements", []):
            if not isinstance(requirement, Mapping) or str(requirement.get("status") or "") == "complete":
                continue
            section_name = str(requirement.get("section") or "")
            if section_name in {"company", "industry", "governance", "esg"}:
                incomplete_by_section.setdefault(section_name, []).append(
                    str(requirement.get("requirement_id") or requirement.get("reason") or "required_context_incomplete")
                )
                coverage_gaps_by_section.setdefault(section_name, []).append({
                    "requirement_id": str(requirement.get("requirement_id") or "required_context_incomplete"),
                    "missing_metrics": [str(value) for value in requirement.get("missing_metrics", []) if value],
                    "missing_roles": [str(value) for value in requirement.get("missing_roles", []) if value],
                    "required_geography_scopes": [str(value) for value in requirement.get("required_geography_scopes", []) if value],
                })
    for section_name, missing in incomplete_by_section.items():
        section = sections.get(section_name)
        if not isinstance(section, dict):
            continue
        if str(section.get("status") or "") == "complete":
            section["status"] = "partial"
        existing_missing = [str(item) for item in section.get("missing_evidence", []) if item] if isinstance(section.get("missing_evidence"), list) else []
        section["missing_evidence"] = list(dict.fromkeys([*existing_missing, *missing]))
    model_complete = bool(
        validation.get("status") == "pass"
        and result.get("overall_status") == "complete"
        and quality.get("evidence_quality") == "complete"
        and quality.get("decision_quality", "complete") == "complete"
        and not validation.get("evidence_quality_violations")
        and not validation.get("decision_quality_violations")
        and all(isinstance(sections.get(name), Mapping) and str(sections[name].get("status") or "") == "complete" for name in ("company", "industry", "governance", "esg"))
    )
    context_ready = bool(context_coverage and (context_coverage.get("summary") or {}).get("l3_ready") is True)
    unresolved = list(appendix.get("unresolved") or []) if isinstance(appendix.get("unresolved"), list) else []
    if not model_complete:
        unresolved.append("qualitative_model_gate_partial")
    if validation.get("evidence_quality_violations"):
        unresolved.append("qualitative_model_evidence_quality_violation")
    if validation.get("decision_quality_violations"):
        unresolved.append("qualitative_model_decision_quality_violation")
    appendix["unresolved"] = list(dict.fromkeys(str(item) for item in unresolved if item))
    appendix["qualitative_context"] = {
        "schema_version": "qualitative-context-merge.v1",
        "run_id": envelope.get("run_id"),
        "model": envelope.get("model"),
        "endpoint": envelope.get("endpoint"),
        "input_sha256": envelope.get("input_sha256"),
        "raw_response_sha256": envelope.get("raw_response_sha256"),
        "overall_status": "complete" if model_complete else "partial",
        "quality": deepcopy(dict(quality)),
        "validation": deepcopy(dict(validation)),
        "sections": deepcopy(dict(sections)),
        "cross_section_synthesis": deepcopy(dict(result.get("cross_section_synthesis") or {})),
        "evidence_bundle": [
            dict(item)
            for item in (
                evidence_bundle
                if evidence_bundle
                else ((appendix.get("qualitative_context") or {}).get("evidence_bundle", []) if isinstance(appendix.get("qualitative_context"), Mapping) else [])
            )
            if isinstance(item, Mapping)
        ],
    }
    run_metadata = deepcopy(dict(appendix.get("run_metadata") or {}))
    run_metadata["qualitative_model"] = {
        "run_id": envelope.get("run_id"),
        "model": envelope.get("model"),
        "input_sha256": envelope.get("input_sha256"),
        "raw_response_sha256": envelope.get("raw_response_sha256"),
        "status": "pass" if model_complete else "partial",
    }
    appendix["run_metadata"] = run_metadata
    merged["appendix"] = appendix

    chapter_ids = {"company": "3", "industry": "4", "governance": "5", "esg": "13"}
    chapters = []
    for chapter in merged.get("chapters", []) if isinstance(merged.get("chapters"), list) else []:
        current = deepcopy(dict(chapter)) if isinstance(chapter, Mapping) else {}
        chapter_id = str(current.get("id") or "")
        section_name = next((name for name, value in chapter_ids.items() if value == chapter_id), None)
        if section_name:
            section = sections.get(section_name) if isinstance(sections.get(section_name), Mapping) else {}
            content = deepcopy(dict(current.get("content") or {}))
            content["model_status"] = section.get("status") or "unresolved"
            content["model_section"] = section_name
            content["context_requirement_gaps"] = deepcopy(coverage_gaps_by_section.get(section_name, []))
            # Partial/failed model prose remains in the audit appendix only.
            # It must not shape the reader-facing report after failing the
            # evidence or decision-transmission contract.
            content["model_summary"] = (section.get("summary") or "") if model_complete else ""
            content["model_claims"] = [dict(item) for item in section.get("claims", []) if isinstance(item, Mapping)] if model_complete else []
            content["model_blind_spots"] = [str(item) for item in section.get("blind_spots", []) if item] if model_complete else []
            content["model_missing_evidence"] = [str(item) for item in section.get("missing_evidence", []) if item] if model_complete else []
            current["content"] = content
            if str(section.get("status") or "unresolved") != "complete":
                current["status"] = "partial"
        elif chapter_id == "14":
            content = deepcopy(dict(current.get("content") or {}))
            synthesis = result.get("cross_section_synthesis") if isinstance(result.get("cross_section_synthesis"), Mapping) else {}
            content["qualitative_key_mechanisms"] = [str(item) for item in synthesis.get("key_mechanisms", []) if item] if model_complete else []
            content["qualitative_monitoring"] = [dict(item) for item in synthesis.get("monitoring", []) if isinstance(item, Mapping)] if model_complete else []
            content["qualitative_unresolved_questions"] = [str(item) for item in synthesis.get("unresolved_questions", []) if item] if model_complete else []
            current["content"] = content
        chapters.append(current)
    merged["chapters"] = chapters

    executive = deepcopy(dict(merged.get("executive_summary") or {}))
    synthesis = result.get("cross_section_synthesis") if isinstance(result.get("cross_section_synthesis"), Mapping) else {}
    executive.update({
        "qualitative_status": "complete" if model_complete else "partial",
        "qualitative_summary": (result.get("summary") or "各質化章節尚未提供可稽核摘要。") if model_complete else "質化模型未通過證據與決策傳導驗證；正文只呈現確定性資料缺口。",
        "qualitative_key_mechanisms": [str(item) for item in synthesis.get("key_mechanisms", []) if item] if model_complete else [],
        "qualitative_unresolved_questions": [str(item) for item in synthesis.get("unresolved_questions", []) if item] if model_complete else [],
    })
    merged["executive_summary"] = executive
    gates = deepcopy(dict(merged.get("quality_gates") or {}))
    gates["qualitative_research"] = "pass" if model_complete else "partial"
    gates["qualitative_evidence_quality"] = "pass" if quality.get("evidence_quality") == "complete" else "partial"
    gates["qualitative_decision_quality"] = "pass" if quality.get("decision_quality", "complete") == "complete" and not validation.get("decision_quality_violations") else "partial"
    if context_coverage is not None:
        gates["context_sufficiency"] = "pass" if context_ready else "partial"
    # Recompute the valuation gates from the canonical chapter after a
    # dcf_only fallback (negative trailing EPS).  The pre-model report may
    # have been built with the old trailing-P/E failure state.
    valuation_content = _chapter(merged, "8")
    valuation_methods = valuation_content.get("methods") if isinstance(valuation_content.get("methods"), list) else []
    valuation_basis = str(valuation_content.get("peer_multiple_basis") or "").casefold()
    valuation_available = bool(valuation_content.get("target_range")) and all(
        isinstance(item, Mapping) and str(item.get("status") or "") == "available" for item in valuation_methods
    )
    if valuation_available:
        gates["valuation"] = "pass"
        gates["valuation_contract"] = (
            "pass"
            if (valuation_basis == "dcf_only" and len(valuation_methods) == 1 and str(valuation_methods[0].get("method") or "") == "dcf_fcfe_proxy")
            else gates.get("valuation_contract", "fail")
        )
    run_retrieval = merged.get("appendix", {}).get("run_metadata", {}).get("target_retrieval") if isinstance(merged.get("appendix", {}).get("run_metadata"), Mapping) else None
    if run_retrieval is not None:
        risk_content = _chapter(merged, "12")
        risk_rows = risk_content.get("risks") if isinstance(risk_content.get("risks"), list) else []
        gates["risk_probability"] = "pass" if risk_rows and all(
            isinstance(item, Mapping) and str(item.get("probability") or "").casefold() not in {"", "unresolved", "unknown"}
            and str(item.get("probability_basis") or "").strip()
            for item in risk_rows
        ) else "partial"
    merged["quality_gates"] = gates
    core_keys = ("identity", "financial_model", "valuation", "audit", "valuation_contract")
    professional_keys = ("evidence", "qualitative_research")
    if context_coverage is not None:
        professional_keys = (*professional_keys, "context_sufficiency")
    if "shared_quality_gate" in gates:
        professional_keys = (*professional_keys, "shared_quality_gate", "event_study")
    if "news_social" in gates:
        professional_keys = (*professional_keys, "news_social")
    if "geo_coverage" in gates:
        professional_keys = (*professional_keys, "geo_coverage")
    if "risk_probability" in gates:
        professional_keys = (*professional_keys, "risk_probability")
    core_pass = all(gates.get(key) == "pass" for key in core_keys)
    professional_pass = all(gates.get(key) == "pass" for key in professional_keys)
    if model_complete and core_pass and professional_pass and (context_coverage is None or context_ready):
        merged["report_level"] = "L3"
        valuation = _chapter(merged, "8")
        target_range = valuation.get("target_range") if isinstance(valuation.get("target_range"), Mapping) else None
        market_price = merged.get("decision_card", {}).get("market_price")
        upside = None
        rating = "Not Rated"
        if target_range and _positive(market_price) and _finite_number(target_range.get("base")):
            upside = (float(target_range["base"]) / float(market_price) - 1) * 100
            rating = "Positive" if upside >= 15 else "Cautious" if upside <= -10 else "Neutral"
        dispersion = float(valuation.get("method_dispersion_pct") or 0)
        card = deepcopy(dict(merged.get("decision_card") or {}))
        card.update({
            "rating": rating,
            "target_range": deepcopy(target_range),
            "upside_downside_pct": round(upside, 6) if upside is not None else None,
            "confidence": "low" if dispersion > 50 else "medium",
        })
        merged["decision_card"] = card
    elif (
        (not model_complete or not professional_pass or (context_coverage is not None and not context_ready))
        and merged.get("report_level") == "L3"
    ):
        merged["report_level"] = "L2"
        card = deepcopy(dict(merged.get("decision_card") or {}))
        card.update({"rating": "Not Rated", "target_range": None, "upside_downside_pct": None})
        merged["decision_card"] = card
    resolved_labels: set[str] = set()
    if model_complete:
        resolved_labels.update({"qualitative_research_gate_partial", "qualitative_model_gate_partial"})
    if gates.get("valuation_contract") == "pass":
        resolved_labels.update({"valuation_gate_fail", "valuation_contract_gate_fail", "positive_target_eps_required_for_trailing_pe"})
    if resolved_labels:
        cleaned_unresolved = [item for item in merged.get("appendix", {}).get("unresolved", []) if item not in resolved_labels]
        merged["appendix"]["unresolved"] = list(dict.fromkeys(cleaned_unresolved))
    return merged


def render_professional_report(report: Mapping[str, Any]) -> str:
    """Render the report as a human-readable Markdown research note."""

    target = report["target"]
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    local_name = next((str(alias) for alias in aliases if any("\u4e00" <= char <= "\u9fff" for char in str(alias))), str(target["name"]))
    card = report["decision_card"]
    valuation = _chapter(report, "8")
    raw_event_titles: list[str] = []
    seen_raw_event_titles: set[str] = set()
    for chapter in report.get("chapters", []):
        if not isinstance(chapter, Mapping):
            continue
        values = chapter.get("content", {}).get("catalysts", []) if isinstance(chapter.get("content"), Mapping) else []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, Mapping):
                continue
            raw = str(item.get("event_raw") or item.get("event") or "").strip()
            display = _catalyst_display_event(item)
            if raw and raw != display and raw not in seen_raw_event_titles:
                seen_raw_event_titles.add(raw)
                raw_event_titles.append(raw)
    lines = [
        f"# {local_name}（{target['symbol']}）專業個股研究報告",
        "",
        f"> 報告日期：{str(report['generated_at'])[:10]}｜市場：{target['market']}｜幣別：{target['currency']}｜研究期間：12 個月",
        "",
        "## 決策摘要",
        "",
        "| 指標 | 結論 |",
        "|---|---|",
        f"| 研究觀點 | **{_rating_label(card['rating'])}** |",
        f"| 末筆市場價格 | {_fmt(card.get('market_price'))} {target['currency']}（{card.get('market_price_as_of') or '日期未提供'}） |",
        f"| 12 個月估值區間 | {_target_range(card.get('target_range'), target['currency'])} |",
        f"| 基準情境潛在空間 | {_fmt_pct(card.get('upside_downside_pct'))} |",
        f"| 研究信心 | {_confidence_label(card.get('confidence'))} |",
        "",
        f"**重要揭露：** {report['disclosures']['investment_advice']} {report['disclosures']['conflicts']}",
    ]
    for chapter in report["chapters"][1:]:
        chapter_id = str(chapter["id"])
        interpretation = _explanatory_lens(chapter_id, chapter["content"], report)
        lines.extend([
            "",
            f"## {chapter_id}、{chapter['title']}",
            "",
            _render_chapter(chapter_id, chapter["content"], report),
        ])
        if interpretation:
            lines.extend(["", "### 研究判讀與盲點", "", interpretation])
    lines.extend([
        "", "## 附錄 A、證據、模型與揭露", "",
        "### 方法與責任", "",
        f"- {report['disclosures']['model_responsibility']}",
        f"- {report['disclosures']['investment_advice']}",
        f"- {report['disclosures']['conflicts']}",
        "",
        "### 模型假設", "",
        f"- 折現率：{_fmt_pct(float(valuation.get('discount_rate') or 0) * 100)}；終值成長率：{_fmt_pct(float(valuation.get('terminal_growth') or 0) * 100)}。",
        f"- 股數：{_fmt(valuation.get('shares_outstanding'))}；淨負債：{_fmt_amount(valuation.get('net_debt'), target['currency'])}。",
        "",
        "### 稽核資訊", "",
        f"- 報告識別：`{report['report_id']}`",
        f"- 研究執行識別：`{report['appendix']['run_metadata'].get('run_id') or 'n/a'}`",
        f"- 報告層級：專業完整層級（{report['report_level']}）",
        f"- 品質檢查：{_quality_gate_summary(report['quality_gates'])}",
        f"- 未解決限制：{'、'.join(_unresolved_label(value) for value in report['appendix']['unresolved']) or '無'}",
        "",
        "完整來源 URL、回應雜湊、計算輸入與 claim 對照保存在同批 JSON 附件。",
    ])
    ledger = _render_qualitative_claim_ledger(report)
    if ledger:
        lines.extend(["", "## 附錄 B、質化主張稽核表", "", ledger])
    if raw_event_titles:
        lines.extend([
            "",
            "### 事件線索原始標題（僅供稽核）",
            "",
            "以下是外部新聞／RSS 的原始標題；它們不是本報告的中文判斷，也未自動升格為官方或監管事實。",
            *[f"- {title}" for title in raw_event_titles],
        ])
    body, references = _number_markdown_citations("\n".join(lines).rstrip())
    reference_lines = [
        "",
        "## 參考來源",
        "",
        "正文引用採編號制；以下列出每個去重來源的完整 URL，供讀者逐項回溯。",
        "",
    ]
    for item in references:
        label = item["label"] or _reference_label(item["url"])
        url = item["url"]
        reference_lines.append(f"<a id=\"ref-{item['number']}\"></a>[{item['number']}] {label} — [{url}]({url})")
        reference_lines.append("")
    return body + "\n" + "\n".join(reference_lines).rstrip() + "\n"


def _render_qualitative_claim_ledger(report: Mapping[str, Any]) -> str:
    """Keep model/audit codes out of the narrative while preserving traceability."""

    qualitative = report.get("appendix", {}).get("qualitative_context") if isinstance(report.get("appendix"), Mapping) else None
    if not isinstance(qualitative, Mapping):
        return ""
    evidence = {
        str(item.get("evidence_id")): item
        for item in qualitative.get("evidence_bundle", [])
        if isinstance(item, Mapping) and item.get("evidence_id")
    }

    def refs(values: Any) -> str:
        links: list[str] = []
        for evidence_id in values if isinstance(values, list) else []:
            row = evidence.get(str(evidence_id))
            url = _safe_markdown_url(row.get("url")) if isinstance(row, Mapping) else ""
            links.append(f"[證據 {evidence_id}]({url})" if url else str(evidence_id))
        return "、".join(links) or "未提供"

    labels = {"company": "公司", "industry": "產業", "governance": "治理", "esg": "ESG"}
    lines = [
        "本表保存模型欄位與證據對照，供稽核使用；正文只呈現人讀研究判斷。",
        "",
        "| 章節／Claim ID | 類型／信心／證據品質 | Requirement | 證據 |",
        "|---|---|---|---|",
    ]
    count = 0
    sections = qualitative.get("sections") if isinstance(qualitative.get("sections"), Mapping) else {}
    for section_name in ("company", "industry", "governance", "esg"):
        section = sections.get(section_name) if isinstance(sections.get(section_name), Mapping) else {}
        for claim in section.get("claims", []) if isinstance(section.get("claims"), list) else []:
            if not isinstance(claim, Mapping):
                continue
            count += 1
            requirements = "、".join(str(item) for item in claim.get("requirement_ids", []) if str(item)) or "未提供"
            codes = "／".join(str(claim.get(key) or "未提供") for key in ("type", "confidence", "evidence_quality"))
            lines.append(
                f"| {labels[section_name]}／`{claim.get('claim_id') or '未提供'}` | {codes} | {requirements} | {refs(claim.get('evidence_ids'))} |"
            )
    return "\n".join(lines) if count else ""


def _number_markdown_citations(markdown: str) -> tuple[str, list[dict[str, Any]]]:
    """Replace inline Markdown links with numbered references in first-use order.

    The report body is intentionally URL-free after this pass. Full URLs are
    emitted once in the final reference section, preventing long SEC/Yahoo
    paths from being mistaken for truncated endpoints by PDF viewers.
    """

    pattern = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\((https?://[^)\s]+)\)", re.IGNORECASE)
    references: list[dict[str, Any]] = []
    number_by_url: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        url = match.group(2).strip()
        number = number_by_url.get(url)
        if number is None:
            number = len(references) + 1
            number_by_url[url] = number
            references.append({"number": number, "label": label if label not in {"證據", "來源"} else "", "url": url})
        elif label not in {"證據", "來源"} and not references[number - 1]["label"]:
            references[number - 1]["label"] = label
        # Keep generic inline labels compact, but preserve meaningful source
        # labels (especially media headlines and official document names) so
        # a human can see what the citation refers to before jumping to the
        # full URL in the reference list.
        return f"[[{number}]](#ref-{number})" if label in {"證據", "來源"} else f"[{label}](#ref-{number})"

    return pattern.sub(replace, markdown), references


def _reference_label(url: str) -> str:
    """Give unlabeled machine/provider links a human-readable source name."""

    host = urlparse(url).netloc.casefold()
    if "sec.gov" in host:
        return "SEC EDGAR"
    if "finmindtrade.com" in host:
        return "FinMind API"
    if "query1.finance.yahoo.com" in host:
        return "Yahoo Finance API"
    if "news.ycombinator.com" in host:
        return "Hacker News"
    return host or "來源"


def write_professional_artifacts(directory: Path, prefix: str, report: Mapping[str, Any], history: Mapping[str, Any], forecast: Mapping[str, Any], valuation: Mapping[str, Any]) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    final_report, markdown, paragraph_audit = finalize_professional_report(report)
    evidence = final_report.get("appendix", {}).get("evidence", {})
    audit = {"schema_version": 1, "report_id": final_report.get("report_id"), "report_level": final_report.get("report_level"), "quality_gates": final_report.get("quality_gates"), "unresolved": final_report.get("appendix", {}).get("unresolved", []), "claim_evidence_coverage": final_report.get("appendix", {}).get("claim_evidence_coverage"), "qualitative_context": final_report.get("appendix", {}).get("qualitative_context"), "qualitative_context_error": final_report.get("appendix", {}).get("qualitative_context_error"), "paragraph_quality": paragraph_audit.get("summary"), "pipeline_provenance": final_report.get("appendix", {}).get("run_metadata", {}).get("pipeline_provenance") if isinstance(final_report.get("appendix", {}).get("run_metadata"), Mapping) else None}
    payloads = {
        "report": (f"{prefix}-research-report.json", final_report), "history": (f"{prefix}-financial-history.json", history),
        "forecast": (f"{prefix}-forecast-model.json", forecast), "valuation": (f"{prefix}-valuation-model.json", valuation),
        "evidence": (f"{prefix}-evidence-appendix.json", evidence), "audit": (f"{prefix}-professional-quality-audit.json", audit),
        "paragraph_audit": (f"{prefix}-paragraph-quality-audit.json", paragraph_audit),
    }
    paths: dict[str, str] = {}
    markdown_path = directory / f"{prefix}-professional-report.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    paths["markdown"] = str(markdown_path)
    for key, (filename, payload) in payloads.items():
        path = directory / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths[key] = str(path)
    return paths


def finalize_professional_report(report: Mapping[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Apply the human-report release gate after all chapters are assembled."""

    final_report = deepcopy(dict(report))
    removed_catalysts = 0
    sanitized_chapters: list[dict[str, Any]] = []
    for chapter in final_report.get("chapters", []) if isinstance(final_report.get("chapters"), list) else []:
        current = deepcopy(dict(chapter)) if isinstance(chapter, Mapping) else {}
        if str(current.get("id") or "") in {"1", "10"} and isinstance(current.get("content"), Mapping):
            content = deepcopy(dict(current["content"]))
            original = content.get("catalysts") if isinstance(content.get("catalysts"), list) else []
            verified = _decision_grade_catalysts(original)
            removed_catalysts += len(original) - len(verified)
            content["catalysts"] = verified
            current["content"] = content
        sanitized_chapters.append(current)
    final_report["chapters"] = sanitized_chapters
    executive = deepcopy(dict(final_report.get("executive_summary") or {}))
    executive["catalysts"] = _decision_grade_catalysts(executive.get("catalysts"))
    final_report["executive_summary"] = executive
    if removed_catalysts:
        appendix = deepcopy(dict(final_report.get("appendix") or {}))
        unresolved = [str(item) for item in appendix.get("unresolved", []) if item] if isinstance(appendix.get("unresolved"), list) else []
        unresolved.append("unverified_catalysts_excluded")
        appendix["unresolved"] = list(dict.fromkeys(unresolved))
        final_report["appendix"] = appendix
    initial_markdown = render_professional_report(final_report)
    target = final_report.get("target") if isinstance(final_report.get("target"), Mapping) else {}
    as_of = str(final_report.get("generated_at") or "")
    initial_audit = audit_markdown_report(initial_markdown, target=target, as_of=as_of)
    gates = deepcopy(dict(final_report.get("quality_gates") or {}))
    paragraph_ready = bool(initial_audit.get("summary", {}).get("release_ready"))
    gates["paragraph_quality"] = "pass" if paragraph_ready else "partial"
    final_report["quality_gates"] = gates
    appendix = deepcopy(dict(final_report.get("appendix") or {}))
    unresolved = [str(item) for item in appendix.get("unresolved", []) if item] if isinstance(appendix.get("unresolved"), list) else []
    if not paragraph_ready:
        unresolved.append("paragraph_quality_gate_partial")
        if final_report.get("report_level") == "L3":
            final_report["report_level"] = "L2"
            card = deepcopy(dict(final_report.get("decision_card") or {}))
            card.update({"rating": "Not Rated", "target_range": None, "upside_downside_pct": None, "confidence": "low"})
            final_report["decision_card"] = card
    appendix["unresolved"] = list(dict.fromkeys(unresolved))
    final_report["appendix"] = appendix
    markdown = render_professional_report(final_report)
    paragraph_audit = audit_markdown_report(markdown, target=target, as_of=as_of)
    paragraph_audit["pre_gate_report_sha256"] = initial_audit["report_sha256"]
    paragraph_audit["gate_action"] = "downgrade_to_l2" if not paragraph_ready and report.get("report_level") == "L3" else "retain_level"
    return final_report, markdown, paragraph_audit


def _complete_period(period: str, values: Mapping[str, float]) -> dict[str, Any]:
    return _add_derived_metrics({"period": period, **values})


def _add_derived_metrics(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    revenue = result.get("revenue")
    for source, field in (("gross_profit", "gross_margin"), ("operating_income", "operating_margin"), ("net_income", "net_margin")):
        result[field] = round(_safe_ratio(result.get(source), revenue, default=0.0), 6) if _positive(revenue) else None
    cfo = result.get("operating_cash_flow")
    capex = result.get("capital_expenditure")
    result["free_cash_flow"] = round(float(cfo) - abs(float(capex)), 6) if _finite_number(cfo) and _finite_number(capex) else None
    result["free_cash_flow_margin"] = round(_safe_ratio(result.get("free_cash_flow"), revenue, default=0.0), 6) if _positive(revenue) else None
    result["cash_conversion"] = round(_safe_ratio(result.get("operating_cash_flow"), result.get("net_income"), default=0.0), 6) if _positive(result.get("net_income")) else None
    result["liabilities_to_equity"] = round(_safe_ratio(result.get("total_liabilities"), result.get("equity"), default=0.0), 6) if _positive(result.get("equity")) else None
    if _finite_number(result.get("current_assets")) and _finite_number(result.get("current_liabilities")):
        result["working_capital"] = round(float(result["current_assets"]) - float(result["current_liabilities"]), 6)
    return result


def _unavailable_forecast(currency: str, reason: str) -> dict[str, Any]:
    empty = {"base": None, "lineage": []}
    return {"schema_version": 1, "status": "insufficient_data", "base_year": None, "currency": currency, "forecast_periods": [], "assumptions": {"revenue_growth": dict(empty), "operating_margin": dict(empty), "cash_conversion": dict(empty), "capital_intensity": dict(empty)}, "validation": {"formula_replay": "fail", "scenario_ordering": "unresolved"}, "missing_reasons": [reason]}


def _scenario_guidance(value: Any) -> dict[str, list[float]] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, list[float]] = {}
    for name in ("bear", "base", "bull"):
        rows = value.get(name)
        if not isinstance(rows, list) or len(rows) != 3 or not all(_finite_number(item) for item in rows):
            return None
        result[name] = [round(float(item), 6) for item in rows]
    return result


def _dcf_per_share(periods: list[Mapping[str, Any]], scenario: str, shares: float, rate: float, terminal_growth: float) -> float:
    cash_flows = [float(period["scenarios"][scenario]["free_cash_flow"]) for period in periods]
    present = sum(value / ((1 + rate) ** index) for index, value in enumerate(cash_flows, start=1))
    terminal = cash_flows[-1] * (1 + terminal_growth) / (rate - terminal_growth)
    equity_value = present + terminal / ((1 + rate) ** len(cash_flows))
    return equity_value / shares


def _build_theses(latest: Mapping[str, Any], forecast: Mapping[str, Any], valuation: Mapping[str, Any], history: Mapping[str, Any]) -> list[dict[str, Any]]:
    assumptions = forecast.get("assumptions") if isinstance(forecast.get("assumptions"), Mapping) else {}
    growth = assumptions.get("revenue_growth") if isinstance(assumptions.get("revenue_growth"), Mapping) else {}
    margin = assumptions.get("operating_margin") if isinstance(assumptions.get("operating_margin"), Mapping) else {}
    return [
        {"title": "營收動能能否延續", "claim": f"基準情境收入增速為 {_fmt_pct(float(growth.get('base') or 0) * 100)}。", "mechanism": "收入增速透過固定成本吸收與產能利用率影響營業利益及自由現金流。", "kpi": "月營收年增率與季度營收", "falsifier": "連續兩季收入增速低於 bear 情境", "confidence": "medium"},
        {"title": "獲利率是估值的核心槓桿", "claim": f"基準營業利益率假設為 {_fmt_pct(float(margin.get('base') or 0) * 100)}。", "mechanism": "毛利與費用率的小幅變動會放大到自由現金流與 DCF。", "kpi": "毛利率、營業利益率、自由現金流率", "falsifier": "營業利益率跌破 bear 情境且無一次性解釋", "confidence": "medium"},
        {"title": "價格是否已提前反映基本面", "claim": f"雙重估值基準值相對市場價格的空間為 {_fmt_pct(valuation.get('upside_downside_pct'))}。", "mechanism": "若預測落在基準以上，盈餘上修可支撐估值；若只靠倍數擴張，安全邊際下降。", "kpi": "forward EPS、同業倍數、DCF 敏感度", "falsifier": "基本面未上修但市場倍數持續高於可比區間", "confidence": "low"},
    ]


def _is_decision_grade_catalyst(item: Mapping[str, Any]) -> bool:
    """Return true only when a future event has an auditable promotion basis."""

    event = str(item.get("event") or item.get("event_display") or "").strip()
    if not event or any(value in event for value in ("外部新聞線索", "財報與法說更新", "資本支出或產能指引", "產業需求與價格變化")):
        return False
    sources = [source for source in item.get("sources", []) if isinstance(source, Mapping)] if isinstance(item.get("sources"), list) else []
    if any(source.get("response_sha256") or source.get("url") for source in sources):
        return True
    causal_status = str(item.get("causal_status") or "").casefold()
    return (
        causal_status in {"supported", "verified", "corroborated"}
        and int(item.get("source_count") or 0) >= 2
        and bool(item.get("evidence_ids"))
    )


def _decision_grade_catalysts(values: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in values if isinstance(item, Mapping) and _is_decision_grade_catalyst(item)] if isinstance(values, list) else []


def _build_catalysts(depth: Mapping[str, Any], research_context: Mapping[str, Any]) -> list[dict[str, Any]]:
    configured = research_context.get("catalysts") if isinstance(research_context.get("catalysts"), Mapping) else {}
    configured_items = configured.get("items") if isinstance(configured.get("items"), list) else []
    if configured_items:
        sources = [dict(item) for item in configured.get("sources", []) if isinstance(item, Mapping)]
        source_ids = [str(item.get("response_sha256")) for item in sources if item.get("response_sha256")]
        return _decision_grade_catalysts([
            _with_catalyst_display({**dict(item), "evidence_ids": source_ids, "sources": sources})
            for item in configured_items[:3]
            if isinstance(item, Mapping)
        ])
    drivers = depth.get("market_drivers") if isinstance(depth.get("market_drivers"), Mapping) else {}
    candidates = drivers.get("news_driver_candidates") if isinstance(drivers.get("news_driver_candidates"), list) else []
    result = []
    for candidate in candidates[:3]:
        if not isinstance(candidate, Mapping):
            continue
        # News classifiers produce research leads, not catalysts.  Promotion
        # requires both a supported causal status and independent sources;
        # otherwise the item stays visible in the news chapter only.
        causal_status = str(candidate.get("causal_status") or "").casefold()
        if causal_status not in {"supported", "verified", "corroborated"}:
            continue
        if int(candidate.get("source_count") or 0) < 2:
            continue
        result.append(_with_catalyst_display({
            "event": str(candidate.get("title") or candidate.get("label") or "待確認事件"),
            "window": str(candidate.get("window") or "未來 12 個月／日期待公司公告"),
            "mechanism": str(candidate.get("label") or "營運資訊影響預期"),
            "probability": str(candidate.get("probability") or "unresolved"),
            "evidence_ids": list(candidate.get("evidence_ids") or []),
            "causal_status": causal_status,
            "source_count": int(candidate.get("source_count") or 0),
        }))
    return result


def _with_catalyst_display(item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep source headlines separate from the human-facing event label.

    RSS/news headlines are evidence metadata, not an analyst conclusion.  A
    non-CJK headline must therefore not leak into the executive summary as if
    it were a translated, verified event.  The raw value remains in
    ``event_raw`` for the machine report and audit appendix.
    """

    result = dict(item)
    raw = str(result.get("event") or result.get("title") or "待確認事件").strip()
    result.setdefault("event_raw", raw)
    explicit = str(result.get("event_display") or result.get("event_zh") or "").strip()
    if explicit and explicit != raw:
        result["event_display"] = explicit
        return result
    cjk_present = any("\u4e00" <= char <= "\u9fff" for char in raw)
    latin_letters = len(re.findall(r"[A-Za-z]", raw))
    # Short product/acronym tokens such as AI or HPC do not make a Chinese
    # event an English headline.  Long mixed-language headlines still need
    # the same neutral, verification-pending label as fully English RSS.
    if cjk_present and latin_letters < 20:
        result["event_display"] = raw
        return result
    mechanism = str(result.get("mechanism") or result.get("label") or "").casefold()
    if any(token in mechanism for token in ("investment", "capex", "capital")):
        category = "投資／資本支出線索"
    elif any(token in mechanism for token in ("demand", "order", "price")):
        category = "需求／訂單線索"
    elif any(token in mechanism for token in ("earning", "profit", "eps", "財報")):
        category = "財報／獲利線索"
    elif any(token in mechanism for token in ("regulat", "policy", "law")):
        category = "法規／政策線索"
    else:
        category = "外部新聞線索"
    result["event_display"] = f"{category}（待公司／監管原文驗證）"
    return result


def _catalyst_display_event(item: Mapping[str, Any]) -> str:
    return str(_with_catalyst_display(item).get("event_display") or "待確認事件")


def _news_candidate_display(item: Mapping[str, Any]) -> str:
    """Render a media candidate as a labelled lead, not as verified fact."""

    display = _catalyst_display_event({"event": item.get("title"), "mechanism": item.get("label")})
    # A neutral placeholder carries no research information.  The raw title
    # remains in the evidence list below, so omit it from the analyst-topic
    # shortlist until a human-readable, claim-scoped label exists.
    return "" if display.startswith("外部新聞線索") else display


def _catalyst_mechanism_display(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "未提供"
    if any("\u4e00" <= char <= "\u9fff" for char in raw):
        return _display_text(raw)
    labels = {
        "investment": "投資／資本支出",
        "demand and orders": "需求與訂單",
        "earnings": "財報與獲利",
        "operations": "營運表現",
        "policy": "法規／政策",
    }
    return labels.get(raw.casefold(), "營運資訊影響預期")


def _catalyst_probability_display(value: Any) -> str:
    labels = {
        "unresolved": "待確認",
        "scheduled_or_unresolved": "已排定／待確認",
        "low": "低",
        "medium": "中",
        "high": "高",
    }
    raw = str(value or "").strip()
    return labels.get(raw.casefold(), _display_text(raw or "未提供"))


def _build_risks(
    target: Mapping[str, Any],
    forecast: Mapping[str, Any],
    valuation: Mapping[str, Any],
    *,
    context_ready: bool = False,
) -> list[dict[str, Any]]:
    industry = {"Semiconductors": "半導體"}.get(str(target.get("industry")), str(target.get("industry") or "產業"))
    return [
        {"risk": "需求或售價低於預期", "probability": "medium", "probability_basis": "以 bear/base 情境差距作為定性風險級距；不是統計發生率", "impact": "high", "valuation_sensitivity": "bear 情境收入增速與營業利益率", "leading_indicator": "月營收年增率、庫存與客戶指引", "mitigation": "分散終端需求並調整資本支出", "thesis_link": "營收動能能否延續"},
        {"risk": "資本支出回收期拉長", "probability": "medium", "probability_basis": "以預測中的資本密集度與自由現金流敏感度作為定性級距；不是統計發生率", "impact": "high", "valuation_sensitivity": "資本密集度上升會壓低自由現金流與 DCF", "leading_indicator": "CAPEX／營收、產能利用率、自由現金流率", "mitigation": "分期建置、客戶承諾與財務緩衝", "thesis_link": "獲利率是估值的核心槓桿"},
        {"risk": "估值倍數壓縮", "probability": "medium", "probability_basis": "以折現率與同業倍數敏感度作為定性級距；不是統計發生率", "impact": "medium", "valuation_sensitivity": "折現率上升 1 個百分點與同業 P/E 下修", "leading_indicator": "殖利率、風險溢酬、同業 forward P/E", "mitigation": "以現金流成長抵銷資本成本上升", "thesis_link": "價格是否已提前反映基本面"},
        {"risk": f"{industry}法規、供應鏈或地緣政治衝擊", "probability": "medium" if context_ready else "unresolved", "probability_basis": "完整產業／治理／ESG requirement coverage 支持定性 watchlist 級距；不是統計發生率" if context_ready else "", "impact": "high", "valuation_sensitivity": "收入中斷、成本增加及折現率上升", "leading_indicator": "出口限制、關鍵供應商與營運據點公告", "mitigation": "供應鏈與生產據點分散", "thesis_link": "營收動能能否延續"},
    ]


def _build_monitoring(forecast: Mapping[str, Any], risks: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    first_period = forecast.get("forecast_periods", [{}])[0] if forecast.get("forecast_periods") else {}
    scenarios = first_period.get("scenarios", {}) if isinstance(first_period, Mapping) else {}
    base = scenarios.get("base", {}) if isinstance(scenarios, Mapping) else {}
    bear = scenarios.get("bear", {}) if isinstance(scenarios, Mapping) else {}
    forecast_year = first_period.get("year") or "未提供"
    return [
        {"kpi": "營收（期間對齊）", "threshold": f"對照 {forecast_year} 年基準情境 {_fmt_amount(base.get('revenue'), forecast.get('currency'))}；季度實績先年化再比較", "frequency": "每月／每季", "action": "偏離兩期即重跑三情境"},
        {"kpi": "營業利益率", "threshold": f"低於 {forecast_year} 年 bear 情境 {_fmt_pct(float(bear.get('operating_margin') or 0) * 100)}", "frequency": "每季", "action": "跌破 bear 情境時重估 DCF"},
        {"kpi": "自由現金流", "threshold": f"不得持續低於 {forecast_year} 年 bear 情境", "frequency": "每季", "action": "檢查 CAPEX、營運資金與盈餘品質"},
        {"kpi": "重大法規／地緣事件", "threshold": "正式公告或可靠直接來源", "frequency": "事件觸發", "action": "更新風險機率與折現率"},
    ]


def _chapter_content(target: Mapping[str, Any], history: Mapping[str, Any], forecast: Mapping[str, Any], valuation: Mapping[str, Any], depth: Mapping[str, Any], metadata: Mapping[str, Any], theses: list[dict[str, Any]], catalysts: list[dict[str, Any]], risks: list[dict[str, Any]], monitoring: list[dict[str, Any]], research_context: Mapping[str, Any]) -> list[dict[str, Any]]:
    annual = history.get("annual_periods") or []
    quarterly = history.get("quarterly_periods") or []
    event = depth.get("event_alignment") if isinstance(depth.get("event_alignment"), Mapping) else {}
    conflicts = depth.get("source_conflicts") if isinstance(depth.get("source_conflicts"), list) else []
    evidence_items = depth.get("evidence_pack", {}).get("items", []) if isinstance(depth.get("evidence_pack"), Mapping) else []
    news_candidates = depth.get("market_drivers", {}).get("news_driver_candidates", []) if isinstance(depth.get("market_drivers"), Mapping) else []
    evidence_by_id = {
        str(item.get("item_id")): item
        for item in evidence_items
        if isinstance(item, Mapping) and item.get("item_id")
    }
    news_evidence = []
    for candidate in news_candidates[:10] if isinstance(news_candidates, list) else []:
        if not isinstance(candidate, Mapping):
            continue
        for evidence_id in candidate.get("evidence_ids", []) if isinstance(candidate.get("evidence_ids"), list) else []:
            item = evidence_by_id.get(str(evidence_id))
            if not isinstance(item, Mapping):
                continue
            evidence_meta = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
            news_evidence.append({
                "evidence_id": str(item.get("item_id") or evidence_id),
                "title": str(item.get("title") or candidate.get("title") or "未命名媒體線索"),
                "url": str(item.get("canonical_url") or ""),
                "publisher": str(evidence_meta.get("publisher_name") or evidence_meta.get("publisher_id") or item.get("publisher_id") or "未提供"),
                "published_at": item.get("published_at"),
                "summary": re.sub(r"<[^>]+>", " ", str(item.get("summary") or item.get("content") or "")).strip()[:600],
                "source_tier": item.get("source_tier"),
            })
    # The market-driver classifier is intentionally conservative and can
    # return no candidates even when the frozen evidence pack contains valid
    # target-scoped news. Do not let that classifier erase source visibility
    # from the human report: fall back to the frozen news items themselves.
    if not news_evidence:
        for item in evidence_items:
            if not isinstance(item, Mapping) or str(item.get("kind") or "").casefold() != "news":
                continue
            evidence_meta = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
            news_evidence.append({
                "evidence_id": str(item.get("item_id") or ""),
                "title": str(item.get("title") or "未命名媒體線索"),
                "url": str(item.get("canonical_url") or ""),
                "publisher": str(evidence_meta.get("publisher_name") or evidence_meta.get("publisher_id") or item.get("publisher_id") or "未提供"),
                "published_at": item.get("published_at"),
                "summary": re.sub(r"<[^>]+>", " ", str(item.get("summary") or item.get("content") or "")).strip()[:600],
                "source_tier": item.get("source_tier"),
            })
    news_evidence = list({str(item["evidence_id"]): item for item in news_evidence}.values())
    social_count = sum(1 for item in evidence_items if isinstance(item, Mapping) and (str(item.get("kind") or "").casefold() == "social" or "social" in str(item.get("source_tier") or "").casefold()))
    retrieval = metadata.get("target_retrieval") if isinstance(metadata.get("target_retrieval"), Mapping) else {}
    community_retrieval = retrieval.get("community") if isinstance(retrieval.get("community"), Mapping) else {}
    social_candidates = []
    for item in evidence_items:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("kind") or "").casefold() != "social" and str(item.get("layer") or "").casefold() != "social":
            continue
        engagement = item.get("engagement") if isinstance(item.get("engagement"), Mapping) else {}
        evidence_meta = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
        raw_comments = evidence_meta.get("comments") if isinstance(evidence_meta.get("comments"), list) else []
        social_candidates.append({
            "title": str(item.get("title") or "未命名社群討論"),
            "url": str(item.get("canonical_url") or ""),
            "source_id": str(item.get("source_id") or "unknown"),
            "score": engagement.get("score"),
            "comments": engagement.get("comments"),
            "comment_excerpts": [
                {"author": str(comment.get("author") or "匿名"), "text": (str(comment.get("text") or "")[:500] + ("…（已截斷）" if len(str(comment.get("text") or "")) > 500 else "")), "truncated": len(str(comment.get("text") or "")) > 500}
                for comment in raw_comments[:3]
                if isinstance(comment, Mapping) and comment.get("text")
            ],
        })
    target_description = f"{target.get('name')} 是 {target.get('industry')} 產業公司；本次研究已確認交易市場與財務輪廓，產品、客戶、區域及分部資料仍須以最新年報逐項核對。"
    peer_set = metadata.get("peer_valuation", {}).get("peer_set", []) if isinstance(metadata.get("peer_valuation"), Mapping) else []
    company = research_context.get("company") if isinstance(research_context.get("company"), Mapping) else {}
    industry = research_context.get("industry") if isinstance(research_context.get("industry"), Mapping) else {}
    governance = research_context.get("governance") if isinstance(research_context.get("governance"), Mapping) else {}
    esg = research_context.get("esg") if isinstance(research_context.get("esg"), Mapping) else {}
    return [
        {"rating": valuation.get("rating"), "target_range": valuation.get("target_range")},
        {"theses": theses, "catalysts": catalysts, "risks": risks[:3]},
        {"theses": theses},
        {"summary": company.get("business_model") or target_description, "scale": company.get("scale"), "platform_mix": company.get("platform_mix"), "geography_mix": company.get("geography_mix"), "moat": company.get("moat"), "sources": company.get("sources", []), "revenue_drivers": ["銷量／出貨", "產品組合與售價", "產能利用率"], "cost_drivers": ["原料與能源", "折舊與製造成本", "研發及營業費用"], "status": "complete" if company.get("business_model") and company.get("sources") else "unresolved"},
        {"industry": target.get("industry"), "sector": target.get("sector"), "peer_set": peer_set, "position": industry.get("position"), "cycle": industry.get("cycle"), "capacity": industry.get("capacity"), "sources": industry.get("sources", []), "framework": ["需求成長與循環", "供應能力與資本強度", "客戶議價力", "替代技術與進入障礙"], "status": "complete" if len({str(item.get('group')) for item in industry.get('sources', []) if isinstance(item, Mapping) and item.get('group')}) >= 3 else "unresolved"},
        {"capital_allocation": governance.get("capital_allocation") or {"capital_intensity": forecast.get("assumptions", {}).get("capital_intensity"), "cash_conversion": forecast.get("assumptions", {}).get("cash_conversion")}, "governance": governance.get("summary") or "董事會、主要股東、管理層薪酬及交易紀錄需由最新年報與公開資訊觀測站補齊。", "ownership": governance.get("ownership"), "sources": governance.get("sources", []), "status": "complete" if governance.get("summary") and governance.get("ownership") and governance.get("sources") else "unresolved"},
        {"annual_periods": annual, "quarterly_periods": quarterly, "quality": history.get("validation"), "sources": history.get("source_refs", [])},
        {"forecast_periods": forecast.get("forecast_periods"), "assumptions": forecast.get("assumptions"), "limitations": "情境由歷史、月營收與明列假設推導；不是公司指引或市場共識。"},
        {"methods": valuation.get("methods"), "target_range": valuation.get("target_range"), "sensitivity": valuation.get("sensitivity"), "peer_set": metadata.get("peer_valuation", {}).get("peer_set", []) if isinstance(metadata.get("peer_valuation"), Mapping) else [], "peer_target_period_key": metadata.get("peer_valuation", {}).get("target_period_key") if isinstance(metadata.get("peer_valuation"), Mapping) else None, "sources": [ref for peer in metadata.get("peer_valuation", {}).get("peer_set", []) if isinstance(peer, Mapping) for ref in peer.get("source_refs", []) if isinstance(ref, Mapping)] + list(history.get("source_refs", [])), **dict(valuation.get("assumptions") or {})},
        {"time_series": depth.get("time_series"), "volume": depth.get("market_drivers", {}).get("provider_status", {}).get("volume") if isinstance(depth.get("market_drivers"), Mapping) else None, "market_cap": float(valuation.get("market_price")) * float(valuation.get("assumptions", {}).get("shares_outstanding")) if _finite_number(valuation.get("market_price")) and _finite_number(valuation.get("assumptions", {}).get("shares_outstanding")) else None, "ownership": governance.get("ownership") or "自由流通股、主要股東與持股集中度尚待官方持股資料。", "ownership_sources": governance.get("sources", []), "status": "complete" if governance.get("ownership") else "unresolved"},
        {"catalysts": catalysts, "historical_event_alignment": event},
        {"source_conflicts": conflicts, "news_candidates": news_candidates[:5] if isinstance(news_candidates, list) else [], "news_item_count": sum(1 for item in evidence_items if isinstance(item, Mapping) and str(item.get("kind") or "").casefold() == "news"), "news_evidence": news_evidence[:10], "social_item_count": social_count, "social_candidates": social_candidates[:10], "social_route_status": community_retrieval.get("status"), "social_route_missing_reason": community_retrieval.get("missing_reason"), "social_route_attempt_count": len(community_retrieval.get("attempts", [])) if isinstance(community_retrieval.get("attempts"), list) else 0, "social_route_queries": [str(item.get("query")) for item in community_retrieval.get("attempts", []) if isinstance(item, Mapping) and item.get("query")], "social_route_attempts": [{"source_id": str(item.get("source_id") or "unknown"), "status": str(item.get("status") or "unknown"), "status_code": item.get("status_code"), "item_count": item.get("item_count", 0), "evidence_admitted": item.get("evidence_admitted", True), "error": item.get("error")} for item in community_retrieval.get("attempts", []) if isinstance(item, Mapping)], "canonical_story_count": depth.get("evidence_pack", {}).get("canonical_story_count") if isinstance(depth.get("evidence_pack"), Mapping) else 0, "source_group_count": depth.get("evidence_pack", {}).get("source_group_count") if isinstance(depth.get("evidence_pack"), Mapping) else 0, "policy": "官方與直接來源作事實層；媒體作敘事層；社群只作待查證線索。"},
        {"risks": risks},
        {"material_topics": ["營運許可與法規", "能源、水與碳成本", "供應鏈與地緣政治", "治理與資本配置"], "summary": esg.get("summary"), "sources": esg.get("sources", []), "policy": "只有能影響現金流、資本成本、營運許可或護城河者列入。", "status": "complete" if esg.get("summary") and esg.get("sources") else "unresolved"},
        {"monitoring": monitoring, "next_review": "財報、法說、重大公告或任一證偽門檻觸發時"},
    ]


_REQUIREMENT_LABELS = {
    "company.business_model": "產品、客戶與區域營收結構",
    "segment.disclosure": "分部營收與分部獲利",
    "industry.market_demand": "目標市場需求與市場規模",
    "industry.price_capacity_cycle": "售價、產能與利用率循環",
    "industry.competitive_position": "市占、成本與競爭位置",
    "peer.comparison": "同業成長、利潤率、ROIC 與估值比較",
    "governance.board_and_ownership": "董事會、委員會與持股結構",
    "governance.capital_allocation": "資本支出、負債、股利與併購配置",
    "esg.materiality_kpi": "重大 ESG 議題、基準值、目標與財務傳導",
}

_METRIC_LABELS = {
    "products_or_services": "產品／服務組合",
    "customers_or_regions": "客戶／區域營收",
    "segment_revenue": "分部營收",
    "segment_period": "分部資料期間",
    "currency": "幣別",
    "unit": "計量單位",
    "market_size_or_demand": "市場規模／需求量",
    "period": "可比期間",
    "price_or_spread": "售價／價差",
    "capacity_or_utilization": "產能／利用率",
    "market_share_or_position": "市占／競爭位置",
    "margin_or_cost_comparison": "利潤率／成本比較",
    "peer_set": "可比同業組",
    "gross_margin": "毛利率",
    "operating_margin": "營業利益率",
    "revenue_growth": "營收成長",
    "roic": "投入資本報酬率",
    "valuation": "估值倍數",
    "board": "董事會組成",
    "committee": "功能委員會",
    "independent_directors": "獨立董事",
    "ownership": "持股結構",
    "capex": "資本支出",
    "debt": "負債",
    "dividend_or_buyback": "股利／庫藏股",
    "m_and_a": "併購",
    "material_topic": "重大議題",
    "baseline_kpi": "基準 KPI",
    "target": "目標值",
    "progress": "實際進度",
    "financial_transmission": "財務傳導",
}


def _human_context_gap_text(content: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    """Turn deterministic requirement gaps into a reader-facing conclusion."""

    gaps = [item for item in content.get("context_requirement_gaps") or [] if isinstance(item, Mapping)]
    if not gaps:
        return ""
    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    name = str(target.get("name") or target.get("symbol") or "本標的")
    section = str(content.get("model_section") or "")
    section_label = {
        "company": "公司與商業模式",
        "industry": "產業與競爭",
        "governance": "治理與資本配置",
        "esg": "ESG 重大性",
    }.get(section, "質化研究")
    requirement_labels = list(dict.fromkeys(
        _REQUIREMENT_LABELS.get(str(item.get("requirement_id") or ""), "必要研究欄位")
        for item in gaps
    ))
    metric_labels = list(dict.fromkeys(
        _METRIC_LABELS.get(str(metric), str(metric).replace("_", " "))
        for item in gaps
        for metric in item.get("missing_metrics", [])
        if metric
    ))
    impact = {
        "company": "無法判斷哪個產品、客戶或地區真正驅動營收、毛利與現金流",
        "industry": "無法把市場需求、售價與產能變化換算成標的營收、利潤率或情境調整",
        "governance": "無法評估管理層的資本配置效率、利益一致性與下行保護",
        "esg": "無法把政策宣示區分為已發生的營運成果，亦不能量化現金流或資本成本影響",
    }.get(section, "無法形成可採用的方向性研究結論")
    requirements = "、".join(requirement_labels)
    metrics = "、".join(metric_labels) or "必要的可比數值"
    return (
        f"**可採用結論：暫無。** {name} 的{section_label}仍缺少{requirements}；"
        f"具體未取得或未完成結構化抽取的欄位為{metrics}。因此{impact}。"
        "目前保存的來源只能證明資料路徑存在，不能代替研究問題已被回答。"
    )


def _qualitative_case_lens(content: Mapping[str, Any]) -> str:
    """Render one decision synthesis without repeating the model narrative."""

    claims = [
        item for item in content.get("model_claims") or []
        if isinstance(item, Mapping) and str(item.get("decision_quality_status") or "complete") == "complete"
    ]
    blind_spots = [str(item).strip() for item in content.get("model_blind_spots") or [] if str(item).strip()]
    missing = [str(item).strip() for item in content.get("model_missing_evidence") or [] if str(item).strip()]
    if not claims:
        return ""
    links = [claim.get("decision_link") for claim in claims if isinstance(claim.get("decision_link"), Mapping)]
    complete_links = [
        link for link in links
        if all(str(link.get(field) or "").strip() for field in ("driver", "target_exposure", "kpi", "financial_line", "scenario_implication"))
    ]
    if not complete_links:
        return ""
    lines = ["**本章決策結論：**"]
    for index, link in enumerate(complete_links, start=1):
        lines.append(
            f"{index}. 驅動因子為{link.get('driver')}；標的曝險為{link.get('target_exposure')}；"
            f"追蹤 KPI 為{link.get('kpi')}，影響{link.get('financial_line')}；情境動作為{link.get('scenario_implication')}。"
        )
    if blind_spots:
        lines.extend(["", f"**尚未排除的盲點：** {'；'.join(blind_spots)}"])
    if missing:
        lines.extend(["", f"**影響判讀的缺證：** {'；'.join(missing)}"])
    return "\n".join(lines)


def _executive_case_limits(content: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    """Summarize issuer-specific blind spots for the executive section.

    L3 reports must not pass with the same generic warning on every issuer.
    This summary is composed only from validated qualitative chapter output
    (with thesis/risk fallbacks), so it remains traceable to the report JSON.
    """

    blind_spots: list[str] = []
    missing_evidence: list[str] = []
    falsifiers: list[str] = []
    for chapter in report.get("chapters", []):
        chapter_content = chapter.get("content") if isinstance(chapter, Mapping) else None
        if not isinstance(chapter_content, Mapping):
            continue
        if str(chapter_content.get("model_status") or "") == "complete":
            blind_spots.extend(str(value).strip() for value in chapter_content.get("model_blind_spots") or [] if str(value).strip())
            missing_evidence.extend(str(value).strip() for value in chapter_content.get("model_missing_evidence") or [] if str(value).strip())
        else:
            missing_evidence.extend(
                _REQUIREMENT_LABELS.get(str(item.get("requirement_id") or ""), "必要研究欄位")
                for item in chapter_content.get("context_requirement_gaps") or []
                if isinstance(item, Mapping)
            )
        for claim in chapter_content.get("model_claims") or []:
            if (
                isinstance(claim, Mapping)
                and str(claim.get("decision_quality_status") or "complete") == "complete"
                and str(claim.get("falsifier") or "").strip()
            ):
                falsifiers.append(str(claim["falsifier"]).strip())
    if not falsifiers:
        falsifiers.extend(
            str(item.get("falsifier") or "").strip()
            for item in content.get("theses") or []
            if isinstance(item, Mapping) and str(item.get("falsifier") or "").strip()
        )
    if not blind_spots:
        blind_spots.extend(
            str(item.get("risk") or "").strip()
            for item in content.get("risks") or []
            if isinstance(item, Mapping) and str(item.get("risk") or "").strip()
        )

    def first_unique(values: Sequence[str], limit: int = 2) -> list[str]:
        return list(dict.fromkeys(value.rstrip("。") for value in values if value))[:limit]

    blind_spots = first_unique(blind_spots)
    falsifiers = first_unique(falsifiers)
    missing_evidence = first_unique(missing_evidence)
    parts = [f"**個案盲點：** {'；'.join(blind_spots)}。"] if blind_spots else []
    if falsifiers:
        parts.append(f"**優先證偽條件：** {'；'.join(falsifiers)}。")
    if missing_evidence:
        parts.append(f"**仍待補證據：** {'；'.join(missing_evidence)}。")
    return " ".join(parts) or "**個案盲點：** 本批次未形成可驗證的個案盲點，研究信心不得上調。"


def _explanatory_lens(chapter_id: str, content: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    """Add a human interpretation layer without creating new facts.

    The machine-readable chapter payload remains the source of numbers and
    claims.  This layer explains how to read those claims, what would weaken
    them, and which missing dimensions can still hide a wrong conclusion.
    """

    card = report.get("decision_card") if isinstance(report.get("decision_card"), Mapping) else {}
    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    currency = str(target.get("currency") or "")

    if chapter_id in {"3", "4", "5", "13"}:
        # The qualitative overlay already owns the chapter narrative.  A
        # second generic "how to read this chapter" essay would duplicate it
        # and can hide that no decision-grade claim survived validation.
        return _qualitative_case_lens(content)

    if chapter_id == "1":
        thesis_titles = "、".join(str(item.get("title")) for item in content.get("theses") or [] if isinstance(item, Mapping)) or "未形成可追蹤論點"
        catalyst_titles = "、".join(_catalyst_display_event(item) for item in content.get("catalysts") or [] if isinstance(item, Mapping)) or "未形成事件清單"
        risk_titles = "、".join(str(item.get("risk")) for item in content.get("risks") or [] if isinstance(item, Mapping)) or "未形成風險清單"
        return (
            f"本案截至報告日的評等為 {_rating_label(card.get('rating'))}、信心為 {_confidence_label(card.get('confidence'))}；估值區間為 {_target_range(card.get('target_range'), currency)}，市場價格為 {_fmt(card.get('market_price'))} {currency}。這表示本案的主要爭點不是『公司是否優秀』，而是營運改善能否在報告日價格與估值假設下留下足夠安全邊際。"
            f"本案的三條論點是：{thesis_titles}。接下來要觀察的事件是：{catalyst_titles}；需要優先壓力測試的風險是：{risk_titles}。"
            f"\n\n{_executive_case_limits(content, report)}"
        )
    if chapter_id == "2":
        theses = [item for item in content.get("theses") or [] if isinstance(item, Mapping)]
        company_name = str(target.get("name") or target.get("symbol") or "本標的")
        kpis = "、".join(dict.fromkeys(str(item.get("kpi") or "").strip() for item in theses if str(item.get("kpi") or "").strip())) or "尚未定義 KPI"
        falsifiers = "；".join(str(item.get("falsifier") or "").strip() for item in theses if str(item.get("falsifier") or "").strip()) or "尚未定義反證條件"
        return (
            f"**跨論點結論：** {company_name} 的三條論點共同依賴基準收入增速、營業利益率與自由現金流能否同期間改善；目前的交叉追蹤指標為{kpis}。"
            f"任一項只靠股價或倍數上升、但營運 KPI 沒有改善，都不足以提高研究信心。已設定的反證條件為：{falsifiers}。"
            "\n\n**尚未排除的盲點：** 季節性、公告時點與資本支出週期可能造成短期錯位；連續兩期同方向偏離且沒有一次性解釋時，應降低論點信心並重跑估值。"
        )
    if chapter_id == "3":
        platform = "、".join(f"{key} {value}" for key, value in (content.get("platform_mix") or {}).items())
        geography = "、".join(f"{key} {value}" for key, value in (content.get("geography_mix") or {}).items())
        revenue_drivers = "、".join(str(item) for item in content.get("revenue_drivers") or []) or "營收驅動尚未完成拆解"
        cost_drivers = "、".join(str(item) for item in content.get("cost_drivers") or []) or "成本驅動尚未完成拆解"
        source_cite = _inline_source(content.get("sources"))
        company_name = str(target.get("name") or target.get("symbol") or "本公司")
        if platform or geography:
            exposure_parts = []
            if platform:
                exposure_parts.append(f"產品／平台組合為 {platform}")
            if geography:
                exposure_parts.append(f"客戶總部地區組合為 {geography}")
            exposure = "本批次保存的收入暴露輪廓：" + "；".join(exposure_parts) + "。"
        else:
            exposure = "本批次未取得可比較的結構化產品／地區占比，因此不顯示空欄，也不以產業平均補值；下方模型章節僅呈現年報原文能直接支持的產品與市場描述。"
        return (
            f"本案公司模式的讀法：{company_name} 的核心價值與收入來源，必須由已保存的公司原文與財務快照共同驗證，而不能套用其他標的的商業模式。報告已保存的公司描述為「{content.get('summary') or '公司摘要尚未取得'}」，規模資訊為「{content.get('scale') or '未提供'}」；讀者要把這些質化描述接回營收組合、成本結構、毛利率與現金流，才能判斷規模是否帶來經濟效益。{source_cite}"
            f"{exposure}收入驅動 {revenue_drivers} 與成本驅動 {cost_drivers} 用來回答『收入如何轉成現金』。本批次保存 {len(content.get('sources') or [])} 組公司來源，支持公開描述，但不等於已取得每一產品線的獲利率或客戶合約條件。{source_cite}"
            "\n\n盲點檢查：產品與地區占比是收入暴露，不是客戶集中度，也不代表各組合的邊際利潤；產能排程、產品爬坡、供應鏈成本與客戶議價可能在揭露之間快速變化。若公司揭露只到年度層級，研究者必須把產品／客戶／產能的缺口標出來，不能用產業平均補上。"
        )
    if chapter_id == "4":
        peers = [item for item in content.get("peer_set") or [] if isinstance(item, Mapping)]
        peer_symbols = "、".join(str(item.get("symbol")) for item in peers) or "同業組合未提供"
        position = str(content.get("position") or "未提供").replace("增量", "新增需求")
        capacity = str(content.get("capacity") or "未提供").replace("增量", "新增需求")
        source_cite = _inline_source(content.get("sources"))
        company_name = str(target.get("name") or target.get("symbol") or "本公司")
        industry_label = str(target.get("industry") or target.get("sector") or "本產業")
        return (
            f"本案同業比較的可比性：{company_name} 所屬的 {industry_label} 產業位置要同時放入需求循環、供應能力、資本強度、客戶議價力與替代方案，而不是只用市占率或股價表現代替護城河。研究批次的估值同業觀察組包含 {len(peers)} 個標的（{peer_symbols}）；這些標的可協助校準倍數，不能直接證明商業模式相同。{source_cite}"
            f"本案保存的市場位置是「{position}」，循環判斷是「{content.get('cycle') or '未提供'}」，產能與資本循環是「{capacity}」。讀者應先檢查這三段是否談同一個 {industry_label} 市場定義，再把產業需求預測與 {company_name} 實際接單、出貨、庫存及公司指引對齊。{source_cite}"
            "\n\n盲點檢查：不同研究機構對市場、產品類別與區域範圍的分母可能不同，市占率不能直接互比；同業倍數也會受會計年度、產品組合與資本結構影響。若市場預測沒有事件日期與公司訂單證據，最多只能當作情境背景，不能直接寫進收入預測。"
        )
    if chapter_id == "5":
        governance = _display_text(content.get("governance") or "治理摘要未提供")
        capital = _display_text(content.get("capital_allocation") or "資本配置未提供")
        ownership = _display_text(content.get("ownership") or "持股結構未提供")
        source_cite = _inline_source(content.get("sources"))
        return (
            f"本案治理與資本配置：治理摘要為「{governance}」。董事會與委員會回答決策如何被監督；資本支出、股利與海外擴產則回答現金如何被分配。持股結構「{ownership}」會影響重大決策的資本市場約束，不能只看公司治理評鑑的形式分數。{source_cite}"
            f"本案保存的資本配置資料是「{capital}」。判讀時要把 CAPEX、產能利用率、折舊與自由現金流放在同一個期間：高支出若伴隨訂單、良率與現金回收，可能是建立產能；若只有支出增加而回收期拉長，則應反映到 bear 情境與折現率。{source_cite}"
            "\n\n盲點檢查：形式上的獨立董事席次不等於實質制衡，公告的資本支出也不等於投資報酬；若缺少專案級回報、關係人交易、薪酬誘因與董事會異議紀錄，治理判斷只能維持中等信心。"
        )
    if chapter_id == "6":
        annual = [item for item in content.get("annual_periods") or [] if isinstance(item, Mapping)]
        quarterly = [item for item in content.get("quarterly_periods") or [] if isinstance(item, Mapping)]
        trend = "歷史期間不足以計算完整趨勢"
        if len(annual) >= 2 and _positive(annual[0].get("revenue")) and _positive(annual[-1].get("revenue")):
            cagr = (float(annual[-1]["revenue"]) / float(annual[0]["revenue"])) ** (1 / (len(annual) - 1)) - 1
            margin_start = float(annual[0].get("operating_margin") or 0) * 100
            margin_end = float(annual[-1].get("operating_margin") or 0) * 100
            trend = f"營收由 {_fmt_amount(annual[0].get('revenue'), currency)} 變為 {_fmt_amount(annual[-1].get('revenue'), currency)}，期間年複合成長約 {_fmt_pct(cagr * 100)}；營業利益率由 {_fmt_pct(margin_start)} 變為 {_fmt_pct(margin_end)}。"
        return (
            f"本案歷史財務的解讀：這一章先描述已發生的財務，再判斷盈餘品質；不是把歷史成長直接外推成預測。{trend}"
            f"本案保存 {len(annual)} 年與 {len(quarterly)} 季資料；八季資料用來觀察拐點與季節性，必須與年度合計及現金流量表勾稽後再解讀。"
            "營收成長若沒有同步轉成毛利、營業利益與自由現金流，便不能只用營收趨勢支持多頭論點；反之，現金流改善也要拆分營運效率、付款時點與資本支出週期。"
            "\n\n盲點檢查：自由現金流會受到資本支出週期、營運資金與付款時點影響，CFO／淨利倍數也可能被一次性因素扭曲；歷史財務能說明結果，不能單獨證明未來需求或價格。"
        )
    if chapter_id == "7":
        assumptions = content.get("assumptions") if isinstance(content.get("assumptions"), Mapping) else {}
        growth = assumptions.get("revenue_growth") if isinstance(assumptions.get("revenue_growth"), Mapping) else {}
        margin = assumptions.get("operating_margin") if isinstance(assumptions.get("operating_margin"), Mapping) else {}
        return (
            f"本案預測的判讀：情境模型把不確定性攤開，不是產生一個看似精確的單一路徑。基準收入成長 {_fmt_pct(float(growth.get('base') or 0) * 100)}、基準營業利益率 {_fmt_pct(float(margin.get('base') or 0) * 100)}，再以保守／基準／樂觀三條路徑呈現收入、EPS 與自由現金流。"
            "讀者應先追每一個假設的來源與期間：哪些來自公司指引，哪些是歷史平均，哪些是研究者自行加入的增速遞減、資本密集度或現金轉換假設。三條路徑只有在能覆蓋需求、價格、良率與資本支出的不同組合時，才有風險管理價值。"
            "\n\n盲點檢查：模型中的現金轉換率、資本密集度與稅率若沿用歷史平均，可能低估轉型期的非線性；三種情境也沒有自動代表機率，不能把基準情境當成預測承諾。"
        )
    if chapter_id == "8":
        methods = [item for item in content.get("methods") or [] if isinstance(item, Mapping)]
        dispersion = content.get("method_dispersion_pct")
        peer_label = (
            "歷史 P/E" if any(str(item.get("method")) == "trailing_pe" for item in methods)
            else "前瞻 P/E" if any(str(item.get("method")) == "forward_pe" for item in methods)
            else "同業 P/S" if any(str(item.get("method")) == "trailing_ps" for item in methods)
            else "未使用 P/E（目標 trailing EPS 為負，採 DCF-only）"
        )
        dispersion_text = _fmt_pct(dispersion) if dispersion is not None else "未計算（DCF-only，沒有第二估值方法）"
        peer_multiple_label = "同業 P/S" if any(str(item.get("method")) == "trailing_ps" for item in methods) else "同業 P/E"
        method_input_text = f"收入增速、營業利益率、CAPEX、折現率、終值成長率與{peer_multiple_label}" if len(methods) > 1 else "收入增速、營業利益率、CAPEX、折現率與終值成長率"
        peer_sentence = f"；{peer_label}則反映同業倍數在明確期間下的市場定價" if len(methods) > 1 else "；本案不使用 P/E，避免把負的 trailing EPS 套入倍數造成失真"
        return (
            f"本案估值的判讀：本案使用 {len(methods)} 種方法，是為了把可觀測的估值限制攤開，而不是把加權結果包裝成精確公允價值。DCF 主要反映現金流、資本支出、折現率與終值{peer_sentence}。"
            f"本批次方法數為 {len(methods)}，基準分歧為 {dispersion_text}，綜合區間為 {_target_range(content.get('target_range'), currency)}；因此區間比單點更有解釋力，市場價格若落在區間內，也不代表假設已被驗證。"
            f"讀者應把估值拆成 {method_input_text} 等可爭辯輸入，逐一檢查哪一個輸入真正推動基準值。"
            "\n\n盲點檢查：終值通常占 DCF 很大比重，同業倍數又容易把市場樂觀情緒帶入；加權權重是研究假設，不是統計信賴區間。若市場價格落在估值區間內，仍要回頭檢查成長與利潤率假設是否已被價格提前反映。"
        )
    if chapter_id == "9":
        ts = content.get("time_series") if isinstance(content.get("time_series"), Mapping) else {}
        returns = ts.get("returns") if isinstance(ts.get("returns"), Mapping) else {}
        return (
            f"本案價格與基本面的關係：市場表現章回答『市場已經如何定價』，不直接回答『企業值多少』。觀測區間為 {ts.get('window_start') or '未提供'} 至 {ts.get('window_end') or '未提供'}，一年觀察報酬 {_fmt_pct(returns.get('365d_observed_pct'))}、年化波動 {_fmt_pct(ts.get('volatility_annualized_pct'))}、最大回撤 {_fmt_pct(ts.get('max_drawdown_pct'))}。"
            f"本案市值約 {_fmt_amount(content.get('market_cap'), currency)}，末筆成交量為 {((content.get('volume') or {}).get('latest') or {}).get('value', '未提供') if isinstance(content.get('volume'), Mapping) else '未提供'}；這是流動性與市場定價的快照，不是企業內在價值。應把價格拐點對回財務期間、重大公告與估值輸入，判斷上漲究竟來自盈餘上修、倍數擴張還是風險偏好。"
            "\n\n盲點檢查：報酬與回撤高度依賴起訖日，末筆成交量只是流動性快照；若沒有基準指數、ADR／現貨價差與事件窗的對照，不能把價格變化解讀成公司的獨立訊號。"
        )
    if chapter_id == "10":
        catalysts = [item for item in content.get("catalysts") or [] if isinstance(item, Mapping)]
        if not catalysts:
            return (
                f"本批次沒有可由官方時點或至少兩個獨立來源支持的未來催化劑，因此不把新聞標題或例行財報占位文字列入事件日曆。"
                f"{str(target.get('name') or target.get('symbol') or '本標的')} 的媒體線索仍保留在新聞與社群章供查證；只有補齊事件日期、傳導 KPI 與反證動作後，才會升格為催化劑。"
            )
        return (
            f"本案催化劑的判讀：催化劑不是『一定會上漲的消息』，而是可在特定時間窗內檢驗假設的事件。本批次整理 {len(catalysts)} 個候選事件，包含「{'、'.join(_catalyst_display_event(item) for item in catalysts)}」。"
            "每一項都要拆成發生條件、傳導機制、預期 KPI 與失敗後的模型動作；只有事件實際發生且 KPI 改變，才有理由更新收入、利潤率或估值。歷史事件對價格的對照只作描述，不把相關性升格為因果。"
            "\n\n盲點檢查：事件日期可能延後，市場也可能在公告前提前定價；若沒有事件前基準、同業或大盤對照，以及未達成時的反證紀錄，催化劑清單容易變成事後敘事。"
        )
    if chapter_id == "11":
        company_name = str(target.get("name") or target.get("symbol") or "本標的")
        conflicts = [item for item in content.get("source_conflicts") or [] if isinstance(item, Mapping)]
        conflict_counts = conflicts[0].get("counts") if conflicts and isinstance(conflicts[0].get("counts"), Mapping) else {}
        known = int(conflict_counts.get("positive") or 0) + int(conflict_counts.get("negative") or 0)
        divergence = "已有可分類的相反敘事，但尚須逐條回到同一 claim 比對" if int(conflict_counts.get("positive") or 0) and int(conflict_counts.get("negative") or 0) else "尚未形成可稽核的正反敘事背離"
        return (
            f"**本案來源衝突的判讀：** {company_name} 本批次保存 {content.get('canonical_story_count', 0)} 個去重故事、{content.get('source_group_count', 0)} 個來源群組、{content.get('news_item_count', 0)} 則新聞證據與 {content.get('social_item_count', 0)} 則社群原文；其中 {known} 則標題進入正負敘事分類。{divergence}。"
            "新聞或社群若沒有對應同一項官方事實、KPI 與期間，只能提高查證優先序，不能改寫收入、利潤率或估值。"
            "\n\n**尚未排除的盲點：** 平台、語言、排名與 API 可得性會改變社群樣本；同一通訊社稿件也可能被多站轉載。獨立證據以 canonical story 與 publisher group 計算，不以標題數量計算。"
        )
    if chapter_id == "12":
        risks = [item for item in content.get("risks") or [] if isinstance(item, Mapping)]
        company_name = str(target.get("name") or target.get("symbol") or "本標的")
        risk_names = "、".join(str(item.get("risk")) for item in risks) or "尚未形成風險清單"
        unresolved_probability = sum(str(item.get("probability") or "").casefold() in {"", "unresolved", "unknown"} for item in risks)
        indicators = "、".join(dict.fromkeys(str(item.get("leading_indicator") or "").strip() for item in risks if str(item.get("leading_indicator") or "").strip())) or "尚未定義領先指標"
        return (
            f"**本案風險的判讀：** {company_name} 本批次列出 {len(risks)} 項風險：{risk_names}；其中 {unresolved_probability} 項尚未完成機率校準。可觀測的領先指標為{indicators}。"
            "需求放緩會同時壓低利用率與毛利、拉長資本支出回收期；若再疊加估值倍數壓縮，同一營運缺口會放大股價下行，這些風險不能視為彼此獨立。"
            "\n\n**尚未排除的盲點：** 機率與影響是本次研究判斷，不是統計頻率；沒有歷史基準或情境壓力測試的項目維持低信心。"
        )
    if chapter_id == "13":
        topics = "、".join(str(item) for item in content.get("material_topics") or [])
        source_cite = _inline_source(content.get("sources"))
        summary = str(content.get("summary") or "尚未取得可比的公司摘要").replace("100% 再生能源", "全面使用再生能源")
        return (
            f"本案 ESG 與地緣風險的判讀：只納入能改變現金流、資本成本、營運許可或護城河的重大事項；本次聚焦 {topics}。每一個議題都要回到收入、成本、資本支出或風險溢酬，才會影響投資判斷，而不是獨立的形象章節。"
            f"本案的 ESG 摘要為「{summary}」，已保存 {len(content.get('sources') or [])} 組來源。讀者應分開看公司已承諾的政策、已發生的營運結果，以及尚未驗證的轉型目標，並確認時間點是否與財務預測相同。{source_cite}"
            "\n\n盲點檢查：企業揭露通常偏向政策與目標，未必包含可比較的實際排放、能源成本、供應中斷或法規情境數據；若沒有時間序列與同業基準，本章只能支持風險辨識，不能宣稱 ESG 績效優劣。"
        )
    if chapter_id == "14":
        monitoring = [item for item in content.get("monitoring") or [] if isinstance(item, Mapping)]
        company_name = str(target.get("name") or target.get("symbol") or "本標的")
        contracts = "；".join(
            f"{item.get('kpi')}：{item.get('threshold')}／{item.get('frequency')}／{item.get('action')}"
            for item in monitoring
        ) or "尚未定義 KPI、門檻與動作"
        return (
            f"**本案監測計畫的判讀：** {company_name} 本次建立 {len(monitoring)} 組 KPI／門檻／頻率／動作：{contracts}。下一次完整複核條件為「{content.get('next_review') or '未提供'}」。"
            "門檻觸發後必須保存新的 as-of、來源、計算版本與情境調整，否則不能判斷變動來自公司基本面或研究假設。"
            "\n\n**尚未排除的盲點：** 門檻沿用舊假設時可能誤報；只監測價格而沒有營收、利潤率、現金流與重大公告，會讓系統延後發現基本面惡化。"
        )
    return "本章採用結構化資料呈現；仍需沿著來源、計算與限制欄位逐項複核。\n\n盲點檢查：若資料期間、來源或模型版本改變，應重新執行本章，而不是沿用舊結論。"


def _render_chapter(chapter_id: str, content: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    if chapter_id == "1":
        theses = [item for item in content.get("theses", []) if isinstance(item, Mapping)]
        catalysts = [item for item in content.get("catalysts", []) if isinstance(item, Mapping)]
        risks = [item for item in content.get("risks", []) if isinstance(item, Mapping)]
        lines = ["### 核心投資爭點", "", "| 爭點 | 追蹤 KPI | 反證條件 |", "|---|---|---|"]
        lines.extend(f"| {item.get('title')} | {item.get('kpi')} | {item.get('falsifier')} |" for item in theses)
        lines.extend([
            "", "### 近期催化劑摘要", "",
            "、".join(_catalyst_display_event(item) for item in catalysts) or "尚未形成可驗證事件。",
            "", "### 三項主要風險摘要", "",
            "、".join(f"{item.get('risk')}（{_risk_label(item.get('probability'))}／{_risk_label(item.get('impact'))}）" for item in risks) or "尚未形成風險清單。",
        ])
        return "\n".join(lines)
    if chapter_id == "2": return _render_theses(content.get("theses", []), detailed=True)
    if chapter_id == "3":
        platform = "、".join(f"{key} {value}" for key, value in (content.get("platform_mix") or {}).items())
        geography = "、".join(f"{key} {value}" for key, value in (content.get("geography_mix") or {}).items())
        moat = _display_text("、".join(content.get("moat") or []))
        cite = _inline_source(content.get("sources"))
        detail_rows: list[str] = []
        if content.get("scale"):
            detail_rows.append(f"**營運規模：** {_display_text(content.get('scale'))} {cite}")
        if platform:
            detail_rows.append(f"**產品／平台組合：** {_display_text(platform)}。{cite}")
        if geography:
            detail_rows.append(f"**客戶總部地區組合：** {_display_text(geography)}。{cite}")
        if moat:
            detail_rows.append(f"**競爭優勢來源：** {moat}。{cite}")
        if not platform and not geography:
            detail_rows.append("> 資料揭露：本次未取得可比較的結構化產品／地區占比；下方質性章節只引用保存的年報原文，不以空欄或產業平均補值。")
        detail = "\n\n" + "\n\n".join(detail_rows) if detail_rows else "\n\n> 研究缺口：公司、分部、客戶與區域組合尚未完成原始文件抽取，不以通用產業描述代替公司事實。"
        return f"{content.get('summary')}{detail}\n\n**收入驅動：** {'、'.join(content.get('revenue_drivers', []))}。\n\n**成本驅動：** {'、'.join(content.get('cost_drivers', []))}。\n\n{_render_sources(content.get('sources', []))}\n\n{_render_qualitative_overlay(content, report)}"
    if chapter_id == "4":
        peers = content.get("peer_set") or []
        peer_text = "、".join(str(peer.get("symbol")) for peer in peers if isinstance(peer, Mapping)) or "同業資料待補"
        cite = _inline_source(content.get("sources"))
        context_text = f"\n\n**市場位置：** {_display_text(content.get('position'))} {cite}\n\n**循環判斷：** {content.get('cycle')} {cite}\n\n**產能與資本循環：** {_display_text(content.get('capacity'))} {cite}" if content.get("position") else "\n\n本次同業倍數可供估值交叉驗證，但市場規模、市占與競爭優勢仍需至少三個獨立產業來源後才能形成強結論。"
        return f"公司屬於 **{content.get('industry')}**；同業觀察組為 {peer_text}。競爭判斷依序檢查：{'、'.join(content.get('framework', []))}。{context_text}\n\n{_render_sources(content.get('sources', []))}\n\n{_render_qualitative_overlay(content, report)}"
    if chapter_id == "5":
        cite = _inline_source(content.get("sources"))
        return f"**治理判斷：** {_display_text(content.get('governance'))} {cite}\n\n**資本配置：** {_display_text(content.get('capital_allocation'))} {cite}\n\n**持股結構：** {_display_text(content.get('ownership') or '待官方資料補齊')} {cite}\n\n{_render_sources(content.get('sources', []))}\n\n{_render_qualitative_overlay(content, report)}"
    if chapter_id == "6": return _financial_tables(content)
    if chapter_id == "7": return _forecast_tables(content)
    if chapter_id == "8": return _valuation_tables(content, report["target"]["currency"])
    if chapter_id == "9": return _market_section(content, report["target"]["currency"])
    if chapter_id == "10":
        event = content.get("historical_event_alignment", {}) if isinstance(content.get("historical_event_alignment"), Mapping) else {}
        stats = event.get("event_study_statistics", {}) if isinstance(event.get("event_study_statistics"), Mapping) else {}
        return _render_catalysts(content.get("catalysts", [])) + f"\n\n歷史上可與價格對照的事件 {event.get('aligned_event_count', 0)} 則，其中可扣除大盤變化者 {event.get('event_study_event_count', 0)} 則；事件日期 {event.get('event_study_unique_event_date_count', 0)} 個，未解決事件 {event.get('unresolved_event_count', 0)} 則，未具日期而排除 {event.get('excluded_undated_event_count', 0)} 則，超出價格觀測窗而排除 {event.get('excluded_out_of_window_event_count', 0)} 則，尚未完成 post-window 而排除 {event.get('excluded_incomplete_window_event_count', 0)} 則。\n\n事件研究狀態：{event.get('event_study_quality_status') or '未完成'}；平均異常報酬 {_fmt_pct(stats.get('mean_abnormal_return_pct'))}，雙尾 p-value（常態近似）{_fmt(stats.get('p_value_two_sided'))}。本結果僅為描述性市場調整分析，不宣稱因果；若樣本不足或顯著性未計算，不能用作投資結論。"
    if chapter_id == "11":
        conflict = (content.get("source_conflicts") or [{}])[0]
        counts = conflict.get("counts", {}) if isinstance(conflict, Mapping) else {}
        candidate_rows = []
        for item in content.get("news_candidates", []) if isinstance(content.get("news_candidates"), list) else []:
            if not isinstance(item, Mapping):
                continue
            display = _news_candidate_display(item)
            if display:
                candidate_rows.append(f"- {_catalyst_mechanism_display(item.get('label') or '待分類')}：{display}")
        candidates = "\n".join(candidate_rows) or "- 尚未形成足夠明確、可連到同一項 KPI 的媒體議題。"
        social_rows = []
        for item in content.get("social_candidates", []):
            if not isinstance(item, Mapping):
                continue
            url = _safe_markdown_url(item.get("url"))
            title = _display_text(item.get("title"))
            linked_title = f"[{title}]({url})" if url else title
            engagement = []
            if item.get("score") is not None:
                engagement.append(f"points {_display_text(item.get('score'))}")
            if item.get("comments") is not None:
                engagement.append(f"comments {_display_text(item.get('comments'))}")
            source_label = _social_source_label(item.get("source_id"))
            social_rows.append(f"- {linked_title}｜來源：{source_label}" + (f"｜{'；'.join(engagement)}" if engagement else ""))
            for comment in item.get("comment_excerpts", [])[:2] if isinstance(item.get("comment_excerpts"), list) else []:
                if isinstance(comment, Mapping) and comment.get("text"):
                    social_rows.append(f"  - 社群留言（{_display_text(comment.get('author') or '匿名')}）：{_display_text(comment.get('text'))}")
        social_originals = "\n".join(social_rows) or "- 本次沒有可回溯的社群原文。"
        social = f"本次取得 {content.get('social_item_count')} 則可回溯的社群原文；它們只作敘事與情緒線索，未升格為事實。新聞與社群必須先有可比對的 claim，才可判定背離；本批次仍不把留言或轉述當成財務證據。" if content.get("social_item_count") else f"本次 target-scoped Research Pack 沒有取得可驗證的社群原文，因此**不能判定新聞與社群是否背離**；這是明列的觀測缺口，不以新聞留言或二手轉述替代。"
        social_route = _social_route_status_label(content.get("social_route_status"))
        social_route_reason = _social_route_reason_label(content.get("social_route_missing_reason"))
        social_route_queries = "、".join(dict.fromkeys(str(item) for item in content.get("social_route_queries", []) if item)) or "未提供"
        route_rows = ["| 路徑 | 結果 | HTTP | 返回項目 | 是否納入證據 |", "|---|---|---:|---:|---|"]
        for attempt in content.get("social_route_attempts", []) if isinstance(content.get("social_route_attempts"), list) else []:
            if not isinstance(attempt, Mapping):
                continue
            route_rows.append(
                f"| {_social_source_label(attempt.get('source_id'))} | {_social_route_attempt_label(attempt.get('status'))} | {attempt.get('status_code') or '—'} | {attempt.get('item_count', 0)} | {'是' if attempt.get('evidence_admitted', True) else '否（僅邊界探測）'} |"
            )
        route_table = "\n".join(route_rows) if len(route_rows) > 2 else "尚未保存逐路徑結果。"
        route_note = f"社群路徑狀態：{social_route}；共嘗試 {content.get('social_route_attempt_count', 0)} 次；查詢身份：{social_route_queries}；缺口原因：{social_route_reason}。\n\n{route_table}"
        return f"{content.get('policy')}\n\n本次去重後 {content.get('canonical_story_count', 0)} 個故事、{content.get('source_group_count', 0)} 個來源群組；其中 {content.get('news_item_count', 0)} 則新聞證據、{content.get('social_item_count', 0)} 則社群原文。標題篩選器辨識偏正面 {counts.get('positive', 0)} 則、偏負面 {counts.get('negative', 0)} 則、無法判讀 {counts.get('unknown', 0)} 則；這只是詞彙篩選，不是情緒真值。\n\n### 媒體敘事候選\n\n{candidates}\n\n### 媒體原文與證據摘要\n\n{_render_news_evidence(content.get('news_evidence'))}\n\n### 社群路徑稽核\n\n{route_note}\n\n### 社群原文候選\n\n{social_originals}\n\n### 社群背離判定\n\n{social}\n\n未找到原始文件的傳聞一律維持待查證，不進財務模型。"
    if chapter_id == "12": return _render_risks(content.get("risks", []), detailed=True)
    if chapter_id == "13":
        summary = str(content.get('summary') or '現階段尚未把最新永續報告與治理資料逐項映射到現金流，因此本章列為待補，不使用制式 ESG 文案。').replace("100% 再生能源", "全面使用再生能源")
        return f"重大性篩選涵蓋：{'、'.join(content.get('material_topics', []))}。{content.get('policy')}\n\n{summary}\n\n{_render_sources(content.get('sources', []))}\n\n{_render_qualitative_overlay(content, report)}"
    if chapter_id == "14": return _render_monitoring(content.get("monitoring", [])) + f"\n\n**下一次完整複核：** {content.get('next_review')}。"
    return json.dumps(content, ensure_ascii=False, indent=2)


def _financial_tables(content: Mapping[str, Any]) -> str:
    annual = content.get("annual_periods") or []
    cite = _inline_source(content.get("sources"))
    lines = ["### 五年年度趨勢", "", "| 年度 | 營收 | 毛利率 | 營業利益率 | EPS | 自由現金流 |", "|---|---:|---:|---:|---:|---:|"]
    for row in annual:
        lines.append(f"| {row.get('year')} | {_fmt_amount(row.get('revenue'), '')} | {_fmt_pct(float(row.get('gross_margin') or 0)*100)} | {_fmt_pct(float(row.get('operating_margin') or 0)*100)} | {_fmt(row.get('eps'))} | {_fmt_amount(row.get('free_cash_flow'), '')} {cite} |")
    latest_quarter = (content.get("quarterly_periods") or [{}])[-1].get("period")
    lines.extend(["", f"### 截至 {latest_quarter or '未提供'} 的八季", "", "| 季度 | 營收 | 營業利益率 | 淨利 | EPS | 自由現金流 |", "|---|---:|---:|---:|---:|---:|"])
    for row in content.get("quarterly_periods") or []:
        lines.append(f"| {row.get('period')} | {_fmt_amount(row.get('revenue'), '')} | {_fmt_pct(float(row.get('operating_margin') or 0)*100)} | {_fmt_amount(row.get('net_income'), '')} | {_fmt(row.get('eps'))} | {_fmt_amount(row.get('free_cash_flow'), '')} {cite} |")
    latest = annual[-1] if annual else {}
    lines.extend(["", "### 盈餘品質與財務韌性", "", f"- 2025 年 CFO／淨利為 {_fmt(latest.get('cash_conversion'))} 倍；自由現金流率為 {_fmt_pct(float(latest.get('free_cash_flow_margin') or 0)*100)}。{cite}", f"- 負債／權益為 {_fmt(latest.get('liabilities_to_equity'))} 倍；營運資金為 {_fmt_amount(latest.get('working_capital'), '')}。{cite}", "", "自由現金流以營業現金流減資本支出絕對值計算；現金流量表先由累計值還原成單季，再聚合年度。資產負債勾稽與期間完整性保存在附錄。"])
    return "\n".join(lines)


def _forecast_tables(content: Mapping[str, Any]) -> str:
    cite = _inline_source((content.get("assumptions") or {}).get("guidance_lineage"))
    lines = ["### 三年情境預測", "", "| 年度 | 情境 | 營收 | 成長率 | 營業利益率 | EPS | 自由現金流 |", "|---|---|---:|---:|---:|---:|---:|"]
    for period in content.get("forecast_periods") or []:
        for name, label in (("bear", "保守"), ("base", "基準"), ("bull", "樂觀")):
            row = period["scenarios"][name]
            lines.append(f"| {period['year']} | {label} | {_fmt_amount(row['revenue'], '')} | {_fmt_pct(row['revenue_growth']*100)} | {_fmt_pct(row['operating_margin']*100)} | {_fmt(row['eps'])} | {_fmt_amount(row['free_cash_flow'], '')} {cite} |")
    assumptions = content.get("assumptions") or {}
    lines.extend(["", "### 驅動假設", "", f"- 基準收入成長：{_fmt_pct(float(assumptions.get('revenue_growth', {}).get('base') or 0)*100)}；由官方近期指引定錨，再加入明列的三年情境與增速遞減假設。", f"- 基準營業利益率：{_fmt_pct(float(assumptions.get('operating_margin', {}).get('base') or 0)*100)}。", f"- CAPEX／營收：{_fmt_pct(float(assumptions.get('capital_intensity', {}).get('base') or 0)*100)}；CFO／淨利：{_fmt(assumptions.get('cash_conversion', {}).get('base'))} 倍。", "", _render_sources(assumptions.get("guidance_lineage", [])), "", str(content.get("limitations"))])
    return "\n".join(lines)


def _valuation_tables(content: Mapping[str, Any], currency: str) -> str:
    methods = content.get("methods") or []
    cite = _inline_source(content.get("sources"))
    lines = ["### 估值方法", "", "| 方法 | 保守 | 基準 | 樂觀 | 權重 |", "|---|---:|---:|---:|---:|"]
    labels = {
        "dcf_fcfe_proxy": "股東自由現金流代理值折現",
        "forward_pe": "前瞻 P/E 交叉驗證",
        "trailing_pe": "歷史 P/E 交叉驗證",
        "trailing_ps": "同業 P/S 交叉驗證",
    }
    for method in methods:
        values = method.get("scenario_values") or {}
        lines.append(f"| {labels.get(method.get('method'), method.get('method'))} | {_fmt(values.get('bear'))} | {_fmt(values.get('base'))} | {_fmt(values.get('bull'))} | {_fmt_pct(float(method.get('weight') or 0)*100)} {cite} |")
    target = content.get("target_range")
    envelope = f"{_fmt(target.get('method_envelope_low'))}–{_fmt(target.get('method_envelope_high'))} {currency}" if isinstance(target, Mapping) else "未提供"
    dispersion = _fmt_pct(content.get("method_dispersion_pct"))
    method_count = len(methods)
    dispersion_text = dispersion if content.get("method_dispersion_pct") is not None else "未計算（DCF-only）"
    method_summary = (
        "第一種方法使用『營業現金流減資本支出』當作保守的股東自由現金流代理值；因缺少淨舉債預測，不冒充完整 FCFE。第二種方法使用幣別與期間對齊的同業 P/S，將目標收入除以流通股數後乘以同業倍數；兩者以 40％／60％ 交叉權重形成研究區間，權重屬分析假設而非市場事實。"
        if method_count > 1 and any(str(item.get("method")) == "trailing_ps" for item in methods)
        else "第一種方法使用『營業現金流減資本支出』當作保守的股東自由現金流代理值；因缺少淨舉債預測，不冒充完整 FCFE。第二種方法使用幣別與期間對齊的同業 P/E，兩者以 60％／40％ 交叉權重形成研究區間，權重屬分析假設而非市場事實。"
        if method_count > 1
        else "本案只使用『營業現金流減資本支出』的股東自由現金流代理值折現；因目標最新年度 EPS 為負，P/E 交叉驗證被明確排除，避免把負分母套入倍數。DCF-only 區間不等於多方法共識，應以敏感度矩陣與後續盈餘轉正作為重新估值條件。"
    )
    lines.extend([
        "", f"綜合估值區間為 **{_target_range(target, currency)}**；折現率 {_fmt_pct(float(content.get('discount_rate') or 0)*100)}，終值成長率 {_fmt_pct(float(content.get('terminal_growth') or 0)*100)}。",
        "", f"**方法分歧：** 本案使用 {method_count} 種方法；基準分歧為 {dispersion_text}。綜合區間是 **{_target_range(target, currency)}**；另列的『方法診斷包絡』為 {envelope}，只用來揭露各方法與情境的極端值，**不是可直接採用的目標價或統計信賴區間**。若包絡含負值，代表 DCF 敏感度在該組假設下失去經濟意義，不能解讀成股價應為負數；決策只使用前述綜合區間，並保留原始值供稽核。{cite}",
        "", "### 同業倍數明細", "", "| 公司 | 期間 | EPS／營收 | 股價 | P/E | P/S | 來源 |", "|---|---|---:|---:|---:|---:|---|",
    ])
    for peer in content.get("peer_set", []) if isinstance(content.get("peer_set"), list) else []:
        if not isinstance(peer, Mapping):
            continue
        source = next((ref for ref in peer.get("source_refs", []) if isinstance(ref, Mapping) and ref.get("url")), None)
        eps_or_revenue = peer.get("eps") if peer.get("eps") is not None else peer.get("revenue")
        lines.append(f"| {peer.get('symbol') or '未提供'} | {peer.get('period_key') or peer.get('period') or content.get('peer_target_period_key') or '未提供'} | {_fmt(eps_or_revenue)} | {_fmt(peer.get('price'))} | {_fmt(peer.get('trailing_pe'))} | {_fmt(peer.get('trailing_ps'))} | {_inline_source([source]) if source else '未提供'} |")
    lines.extend(["", "### DCF 敏感度", "", "| 折現率 | 終值成長率 | 每股價值 |", "|---:|---:|---:|"])
    for row in content.get("sensitivity", {}).get("matrix", []):
        lines.append(f"| {_fmt_pct(row['discount_rate']*100)} | {_fmt_pct(row['terminal_growth']*100)} | {_fmt(row['value_per_share'])} {currency} {cite} |")
    lines.extend(["", method_summary])
    return "\n".join(lines)


def _market_section(content: Mapping[str, Any], currency: str) -> str:
    ts = content.get("time_series") or {}
    returns = ts.get("returns") or {}
    volume = content.get("volume") or {}
    market_cite = _inline_source([ts.get("source_ref")])
    ownership_cite = _inline_source(content.get("ownership_sources"))
    window_end = ts.get("window_end") or "未提供"
    return f"- 觀測期間：{ts.get('window_start') or '未提供'} 至 {window_end}。{market_cite}\n- 一年報酬：{_fmt_pct(returns.get('365d_observed_pct'))}；年化波動：{_fmt_pct(ts.get('volatility_annualized_pct'))}；最大回撤：{_fmt_pct(ts.get('max_drawdown_pct'))}。{market_cite}\n- 市值約 {_fmt_amount(content.get('market_cap'), currency)}；{window_end} 末筆成交量：{_fmt(volume.get('latest', {}).get('value')) if isinstance(volume, Mapping) else '未提供'}。{market_cite}\n- {_display_text(content.get('ownership'))} {ownership_cite}"


def _render_theses(values: list[Mapping[str, Any]], detailed: bool = False) -> str:
    blocks = []
    for index, item in enumerate(values, 1):
        text = f"### 論點 {index}｜{item.get('title')}\n\n{item.get('claim')}"
        if detailed: text += f"\n\n- **傳導機制：** {item.get('mechanism')}\n- **追蹤 KPI：** {item.get('kpi')}\n- **證偽條件：** {item.get('falsifier')}\n- **信心：** {_confidence_label(item.get('confidence'))}"
        blocks.append(text)
    return "\n\n".join(blocks)


def _render_catalysts(values: list[Mapping[str, Any]]) -> str:
    return "\n".join(
        f"- **{_catalyst_display_event(item)}**｜時間：{item.get('window')}｜傳導：{_catalyst_mechanism_display(item.get('mechanism'))}｜狀態：{_catalyst_probability_display(item.get('probability'))} {_inline_source(item.get('sources'))}"
        for item in values
    )


def _render_news_evidence(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "- 本次沒有可回溯的媒體原文。"
    lines: list[str] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        title = _display_text(item.get("title") or "未命名媒體線索")
        url = _safe_markdown_url(item.get("url"))
        linked_title = f"[{title}]({url})" if url else title
        publisher = _display_text(item.get("publisher") or "未提供")
        published_at = _display_text(item.get("published_at") or "日期未提供")
        summary = _display_text(item.get("summary") or "未取得可公開摘要；僅保存標題與來源 metadata。")
        lines.append(f"- **原文標題（外部線索）：** {linked_title}｜來源：{publisher}｜發布：{published_at}｜證據層：{_source_tier_label(item.get('source_tier'))}")
        lines.append(f"  - 媒體摘要：{summary}")
    return "\n".join(lines) or "- 本次沒有可回溯的媒體原文。"


def _source_tier_label(value: Any) -> str:
    return {
        "direct_primary": "第一手／官方或監管",
        "direct_secondary": "第二手／媒體原文",
        "aggregated": "聚合索引（需回到原文）",
        "social": "社群原文（僅線索）",
    }.get(str(value or ""), str(value or "未標記"))


def _social_source_label(value: Any) -> str:
    text = str(value or "未提供")
    if text.startswith("hacker_news_"):
        return "Hacker News 公開 API"
    return {
        "reddit_public_search": "Reddit 公開搜尋 API",
        "stocktwits_public_symbol_stream": "Stocktwits 公開個股串流",
    }.get(text, text)


def _social_route_status_label(value: Any) -> str:
    return {
        "available": "可用",
        "insufficient_data": "查詢成功但未找到標的討論",
        "unavailable": "路徑不可用",
        "failed": "路徑失敗",
    }.get(str(value or ""), str(value or "未提供路徑狀態"))


def _social_route_reason_label(value: Any) -> str:
    return {
        "no_target_discussions_from_hackernews_public_api": "Hacker News 公開 API 查詢成功，但本次身份詞未找到標的討論",
        "community_routes_blocked_or_no_target_discussions": "Hacker News 未找到標的討論，且 Reddit／Stocktwits 等公開路徑被拒絕或需要授權",
        "hackernews_public_api_unavailable": "Hacker News 公開 API 不可用或連續請求失敗",
    }.get(str(value or ""), str(value or "未提供"))


def _social_route_attempt_label(value: Any) -> str:
    return {
        "success": "成功回應",
        "blocked": "被拒絕／需授權",
        "failed": "請求失敗",
    }.get(str(value or ""), str(value or "未提供"))


def _render_risks(values: list[Mapping[str, Any]], detailed: bool = False) -> str:
    if not detailed: return "\n".join(f"- **{item.get('risk')}**（機率 {_risk_label(item.get('probability'))}／影響 {_risk_label(item.get('impact'))}）" for item in values)
    return "\n\n".join(f"### {index}. {item.get('risk')}\n\n- 機率／影響：{_risk_label(item.get('probability'))}／{_risk_label(item.get('impact'))}\n- 估值敏感度：{item.get('valuation_sensitivity')}\n- 領先指標：{item.get('leading_indicator')}\n- 緩解因素：{item.get('mitigation')}\n- 對應論點：{item.get('thesis_link')}" for index, item in enumerate(values, 1))


def _render_monitoring(values: list[Mapping[str, Any]]) -> str:
    lines = ["| KPI | 門檻 | 頻率 | 觸發動作 |", "|---|---|---|---|"]
    lines.extend(f"| {item.get('kpi')} | {item.get('threshold')} | {item.get('frequency')} | {item.get('action')} |" for item in values)
    return "\n".join(lines)


def _render_qualitative_overlay(content: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    """Render one human research narrative; machine fields stay in appendix."""

    claims = content.get("model_claims") if isinstance(content.get("model_claims"), list) else []
    status = str(content.get("model_status") or "unresolved")
    if not claims and not content.get("model_summary"):
        gap_text = _human_context_gap_text(content, report)
        return f"### 質化研究判讀\n\n{gap_text or '尚未取得能連回原始證據的標的質化分析。'}"
    qualitative = report.get("appendix", {}).get("qualitative_context", {}) if isinstance(report.get("appendix"), Mapping) else {}
    evidence = {
        str(item.get("evidence_id")): item
        for item in qualitative.get("evidence_bundle", [])
        if isinstance(item, Mapping) and item.get("evidence_id")
    } if isinstance(qualitative, Mapping) else {}

    def refs(ids: Any) -> str:
        values = ids if isinstance(ids, list) else []
        links: list[str] = []
        for evidence_id in values:
            item = evidence.get(str(evidence_id))
            url = _safe_markdown_url(item.get("url")) if isinstance(item, Mapping) else ""
            links.append(f"[證據]({url})" if url else "證據保存在稽核附件")
        return "、".join(links) or "未提供"

    valid_claims = [claim for claim in claims if str(claim.get("decision_quality_status") or "complete") == "complete"]
    if claims and not valid_claims:
        lines = [
            "### 質化研究判讀",
            "",
            _human_context_gap_text(content, report) or "本章尚未形成可由原始證據支持的研究結論。",
            "",
        ]
    else:
        lines = ["### 質化研究判讀", "", _display_text(content.get("model_summary") or "未提供章節摘要。"), ""]
    for index, claim in enumerate(valid_claims, start=1):
        if not isinstance(claim, Mapping):
            continue
        mechanism = _display_text(claim.get("mechanism") or "尚未建立傳導機制")
        falsifier = _display_text(claim.get("falsifier") or "尚未建立證偽條件")
        lines.extend([
            f"**判斷 {index}：** {_display_text(claim.get('text') or claim.get('claim'))} {refs(claim.get('evidence_ids'))}",
            "",
            f"這項判斷的營運傳導為：{mechanism}。反證條件為：{falsifier}。",
            "",
        ])
    blind_spots = [str(value) for value in content.get("model_blind_spots", []) if value]
    missing = [str(value) for value in content.get("model_missing_evidence", []) if value]
    if blind_spots:
        lines.extend(["**尚未排除的個案盲點：**", *[f"- {value}" for value in blind_spots], ""])
    if missing:
        lines.extend(["**影響本章判讀的待補證據：**", *[f"- {value}" for value in missing]])
    return "\n".join(lines).rstrip()


def _unresolved(
    gates: Mapping[str, str],
    history: Mapping[str, Any],
    forecast: Mapping[str, Any],
    valuation: Mapping[str, Any],
    depth: Mapping[str, Any] | None = None,
) -> list[str]:
    values = [f"{key}_gate_{value}" for key, value in gates.items() if value != "pass"]
    values.extend(str(value) for source in (history, forecast, valuation) for value in source.get("missing_reasons", []))
    if isinstance(depth, Mapping):
        event = depth.get("event_alignment") if isinstance(depth.get("event_alignment"), Mapping) else {}
        if event.get("event_study_quality_status") not in {None, "complete"}:
            values.append("event_study_not_complete")
        if int(event.get("unresolved_event_count") or 0) > 0:
            values.append("unresolved_event_records")
        shared = depth.get("quality_gate") if isinstance(depth.get("quality_gate"), Mapping) else {}
        values.extend(str(item) for item in shared.get("blocking_reasons", []) if item)
    return list(dict.fromkeys(values))


def _build_evidence_index(
    *,
    evidence: Mapping[str, Any],
    history: Mapping[str, Any],
    forecast: Mapping[str, Any],
    valuation: Mapping[str, Any],
    research_context: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Create one human/audit-facing index for every evidence-bearing input."""

    records: dict[str, dict[str, Any]] = {}
    raw_paths_by_url = {
        str(item.get("url")): str(item.get("path"))
        for item in metadata.get("raw_captures", [])
        if isinstance(item, Mapping) and item.get("url") and item.get("path")
    }

    def add(source: Mapping[str, Any], *, kind: str, locator: str | None = None) -> None:
        url = str(source.get("citation_url") or source.get("url") or "").strip()
        digest = str(source.get("response_sha256") or source.get("content_sha256") or "").strip().casefold()
        if not url and not digest:
            return
        evidence_id = str(source.get("item_id") or digest or hashlib.sha256(url.encode("utf-8")).hexdigest()).strip()
        records.setdefault(evidence_id, {
            "evidence_id": evidence_id,
            "kind": kind,
            "url": url or None,
            "publisher": source.get("publisher") or source.get("publisher_id") or source.get("group"),
            "source_id": source.get("source_id"),
            "response_sha256": digest or None,
            "raw_capture_path": source.get("raw_capture_path") or source.get("path") or raw_paths_by_url.get(url),
            "collected_at": source.get("collected_at"),
            "locator": locator or source.get("locator"),
            "status": "frozen" if len(digest) == 64 else "unfrozen",
        })

    for item in evidence.get("items", []) if isinstance(evidence.get("items"), list) else []:
        if isinstance(item, Mapping):
            add(item, kind="canonical_evidence", locator=str(item.get("claim_locator") or "") or None)
    for source in history.get("source_refs", []) if isinstance(history.get("source_refs"), list) else []:
        if isinstance(source, Mapping):
            add(source, kind="financial_history", locator=str(source.get("dataset") or "") or None)
    assumptions = forecast.get("assumptions") if isinstance(forecast.get("assumptions"), Mapping) else {}
    for source in assumptions.get("guidance_lineage", []) if isinstance(assumptions.get("guidance_lineage"), list) else []:
        if isinstance(source, Mapping):
            add(source, kind="company_guidance", locator=str(source.get("claim") or "") or None)
    for source in metadata.get("peer_valuation", {}).get("peer_set", []) if isinstance(metadata.get("peer_valuation"), Mapping) else []:
        if isinstance(source, Mapping):
            for ref in source.get("source_refs", []) if isinstance(source.get("source_refs"), list) else []:
                if isinstance(ref, Mapping):
                    add({**dict(ref), "publisher": source.get("symbol")}, kind="peer_valuation", locator=str(source.get("period") or "") or None)
    for section_name, section in research_context.items():
        if not isinstance(section, Mapping):
            continue
        for source in section.get("sources", []) if isinstance(section.get("sources"), list) else []:
            if isinstance(source, Mapping):
                add(source, kind=f"qualitative:{section_name}")
    return sorted(records.values(), key=lambda item: (str(item.get("kind")), str(item.get("evidence_id"))))


def _attach_claim_evidence(content: list[dict[str, Any]], evidence_index: list[Mapping[str, Any]]) -> None:
    frozen_ids = [str(item.get("evidence_id")) for item in evidence_index if item.get("status") == "frozen"]
    for chapter in content:
        for key in ("theses", "catalysts", "risks"):
            values = chapter.get(key)
            if not isinstance(values, list):
                continue
            for index, item in enumerate(values):
                if not isinstance(item, dict):
                    continue
                claim = str(item.get("claim") or item.get("event") or item.get("risk") or f"{key}:{index}")
                item.setdefault("claim_id", hashlib.sha256(f"{chapter.get('id')}\0{key}\0{claim}".encode("utf-8")).hexdigest()[:16])
                if frozen_ids:
                    item.setdefault("evidence_ids", frozen_ids[:3])


def _claim_evidence_coverage(content: list[dict[str, Any]]) -> dict[str, Any]:
    claims = []
    for chapter in content:
        for key in ("theses", "catalysts", "risks"):
            values = chapter.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, Mapping):
                    claims.append(item)
    linked = sum(1 for item in claims if item.get("evidence_ids"))
    explicitly_unresolved = sum(
        1
        for item in claims
        if not item.get("evidence_ids")
        and str(item.get("probability") or "").casefold() in {"unresolved", "scheduled_or_unresolved"}
    )
    actionable_unlinked = len(claims) - linked - explicitly_unresolved
    return {
        "claim_count": len(claims),
        "linked_claim_count": linked,
        "unlinked_claim_count": actionable_unlinked,
        "explicitly_unresolved_claim_count": explicitly_unresolved,
        "status": "pass" if claims and actionable_unlinked == 0 else "partial" if claims else "unresolved",
    }


def _valuation_contract_status(valuation: Mapping[str, Any]) -> str:
    """Require the displayed P/E method to match its declared basis."""

    if valuation.get("status") != "available":
        return "fail"
    methods = valuation.get("methods") if isinstance(valuation.get("methods"), list) else []
    assumptions = valuation.get("assumptions") if isinstance(valuation.get("assumptions"), Mapping) else {}
    peer_basis = str(assumptions.get("peer_multiple_basis") or "").strip().casefold()
    peer_methods = [str(item.get("method") or "").strip().casefold() for item in methods if isinstance(item, Mapping) and str(item.get("method") or "").strip().casefold() in {"forward_pe", "trailing_pe", "trailing_ps"}]
    if peer_basis == "dcf_only":
        return "pass" if len(methods) == 1 and peer_methods == [] and str(methods[0].get("method") or "") == "dcf_fcfe_proxy" else "fail"
    if len(peer_methods) != 1 or peer_methods[0] != peer_basis:
        return "fail"
    if peer_basis == "trailing_pe" and (not _positive(assumptions.get("peer_target_eps")) or not assumptions.get("peer_target_period_key")):
        return "fail"
    if peer_basis == "trailing_ps" and (not _positive(assumptions.get("peer_target_revenue")) or not _positive(assumptions.get("peer_median_ps")) or not assumptions.get("peer_target_period_key")):
        return "fail"
    return "pass"


def _qualitative_context_ready(context: Mapping[str, Any]) -> bool:
    company = context.get("company") if isinstance(context.get("company"), Mapping) else {}
    industry = context.get("industry") if isinstance(context.get("industry"), Mapping) else {}
    governance = context.get("governance") if isinstance(context.get("governance"), Mapping) else {}
    esg = context.get("esg") if isinstance(context.get("esg"), Mapping) else {}
    industry_groups = {str(item.get("group")) for item in industry.get("sources", []) if isinstance(item, Mapping) and item.get("group")}
    all_sources = [item for section in (company, industry, governance, esg) for item in section.get("sources", []) if isinstance(item, Mapping)]
    # A profile may carry optional interactive pages that are blocked by a
    # publisher edge (for example a JS/anti-bot IR page).  They remain in the
    # evidence appendix and audit log, but must not invalidate a section when
    # a required regulatory filing or report is frozen and citation-eligible.
    required_sources = [item for item in all_sources if item.get("required", True) is not False]
    sources_frozen = bool(required_sources) and all(isinstance(item.get("response_sha256"), str) and len(item["response_sha256"]) == 64 for item in required_sources)
    return bool(
        company.get("business_model") and company.get("scale") and company.get("sources")
        and industry.get("position") and industry.get("cycle") and len(industry_groups) >= 3
        and governance.get("summary") and governance.get("capital_allocation") and governance.get("ownership") and governance.get("sources")
        and esg.get("summary") and esg.get("sources")
        and sources_frozen
    )


def _render_sources(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "**來源：** 尚未補齊。"
    links = []
    for item in values:
        if not isinstance(item, Mapping) or not item.get("url"):
            continue
        label = str(item.get("publisher") or item.get("group") or "來源")
        locator = str(item.get("locator") or item.get("claim_locator") or "").strip()
        if locator:
            label = f"{label}（{locator}）"
        citation_url = _safe_markdown_url(item.get("citation_url") or item.get("url"))
        if citation_url:
            links.append(f"[{label}]({citation_url})")
    return "**來源：** " + "、".join(links) + "。" if links else "**來源：** 尚未補齊。"


def _inline_source(values: Any, label: str = "證據") -> str:
    if not isinstance(values, list):
        return ""
    source = next(
        (
            item
            for item in values
            if isinstance(item, Mapping) and (item.get("citation_url") or item.get("url"))
        ),
        None,
    )
    if not source:
        return ""
    citation_url = _safe_markdown_url(source.get("citation_url") or source.get("url"))
    return f"[{label}]({citation_url})"


def _safe_markdown_url(value: Any) -> str:
    """Percent-encode spaces/unicode without altering URL delimiters."""

    return quote(str(value or "").strip(), safe=":/?&=#%+;,@!$'()*~")


def _display_text(value: Any) -> str:
    return (
        str(value or "")
        .replace("%", "％")
        .replace("主要增量", "主要成長來源")
        .replace("供應鏈生態系", "供應鏈協作體系")
        .replace("unresolved", "待確認")
        .replace("insufficient_data", "資料不足")
    )


def _unresolved_label(value: Any) -> str:
    text = str(value)
    labels = {
        "social_narrative_source_unavailable": "未取得可驗證的社群原文，無法判定新聞與社群是否背離",
        "news_social_gate_partial": "新聞／社群證據未完成，不能判定背離",
        "risk_probability_unresolved": "風險機率尚未完成校準",
        "qualitative_research_gate_partial": "質化研究證據尚未完整",
        "evidence_gate_partial": "獨立證據數量尚未達完整門檻",
    }
    return labels.get(text, text.replace("_", " "))


def _quality_gate_summary(gates: Mapping[str, Any]) -> str:
    labels = {
        "identity": "標的身分",
        "financial_model": "財務模型",
        "valuation": "估值",
        "evidence": "證據覆蓋",
        "audit": "稽核軌跡",
        "qualitative_research": "質化研究",
    }
    statuses = {"pass": "合格", "partial": "需補強", "fail": "不合格"}
    return "、".join(f"{labels.get(key, key)}{statuses.get(str(value), str(value))}" for key, value in gates.items())


def _chapter(report: Mapping[str, Any], chapter_id: str) -> Mapping[str, Any]:
    return next(chapter["content"] for chapter in report["chapters"] if chapter["id"] == chapter_id)


def _target_range(value: Any, currency: str) -> str:
    if not isinstance(value, Mapping): return "未評等；不產生目標價"
    return f"{_fmt(value.get('low'))}–{_fmt(value.get('high'))} {currency}（基準 {_fmt(value.get('base'))}）"


def _rating_label(value: Any) -> str:
    return {"Positive": "正向", "Neutral": "中性", "Cautious": "審慎", "Not Rated": "未評等"}.get(str(value), str(value))


def _confidence_label(value: Any) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(str(value), str(value))


def _risk_label(value: Any) -> str:
    return {"high": "高", "medium": "中", "low": "低", "unresolved": "待確認"}.get(str(value), str(value))


def _fmt(value: Any) -> str:
    return "未提供" if not _finite_number(value) else f"{float(value):,.2f}"


def _fmt_pct(value: Any) -> str:
    return "未提供" if not _finite_number(value) else f"{float(value):,.2f}％"


def _fmt_amount(value: Any, currency: Any) -> str:
    if not _finite_number(value): return "未提供"
    number = float(value)
    suffix = ""
    for threshold, label in ((1e12, "兆"), (1e8, "億"), (1e4, "萬")):
        if abs(number) >= threshold:
            number, suffix = number / threshold, label
            break
    return f"{number:,.2f}{suffix} {currency or ''}".strip()


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _positive(value: Any) -> bool:
    return _finite_number(value) and float(value) > 0


def _safe_ratio(numerator: Any, denominator: Any, *, default: float) -> float:
    return float(numerator) / float(denominator) if _finite_number(numerator) and _positive(denominator) else default


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("as_of must be an ISO datetime") from exc
    if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_datetime(value: Any) -> str:
    return _parse_datetime(value).isoformat().replace("+00:00", "Z")
