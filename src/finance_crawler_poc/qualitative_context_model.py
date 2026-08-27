"""Build and validate a target-scoped qualitative model request.

The module deliberately separates three concerns:

* raw capture extraction into bounded, source-located excerpts;
* a deterministic input envelope for one model call; and
* fail-closed validation of model evidence references.

It does not decide whether a target is investable and it never treats model
prose as a source of facts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

import httpx


DEFAULT_OPENCODE_MODEL = "opencode/big-pickle"
DEFAULT_OPENCODE_COMMAND = "opencode"
DEFAULT_QWEN_ENDPOINT = "http://ac-4090.taile9e967.ts.net:11434"
DEFAULT_QWEN_MODEL = "qwen3.8-27b-64k:latest"


_SECTION_KEYWORDS = {
    # Put high-signal phrases before generic XBRL/table-of-contents tokens.
    # SEC/annual-report captures begin with long taxonomy blocks where a
    # generic ``business`` match would otherwise consume the whole excerpt.
    "company": (
        "各主要產品表現", "主要產品", "產品組合", "營業內容", "業務內容", "客戶",
        "foundry", "products", "customers", "operating segments", "capacity", "manufactur", "business", "revenue", "營收",
    ),
    "industry": (
        "市場占有率", "市場佔有率", "主要競爭", "產業概況", "市場概況", "伺服器市場", "市場需求",
        "market share", "competition", "market", "demand", "supply", "capacity", "forecast", "growth", "share", "市場", "產能",
    ),
    "governance": ("independent director", "audit and risk committee", "board of directors", "corporate governance", "risk management", "related-party", "compensation", "director"),
    "esg": ("renewable energy", "environmental sustainability", "environmental, safety and health", "carbon", "emission", "climate", "water", "net zero", "supply chain", "environment"),
    "catalysts": ("guidance", "outlook", "expect", "quarter", "capex", "ramp", "demand"),
}


RUNTIME_QUALITATIVE_PROMPT = """你是可稽核投資研究的質化分析器。只使用 USER_PAYLOAD 內的證據，不使用記憶、外部搜尋或常識補值。

硬性規則：
1. 只輸出 JSON，不要 Markdown、不要解釋、不要 code fence。
2. 所有 summary、claim、mechanism、falsifier、blind_spots、missing_evidence、monitoring 與 unresolved_questions 一律使用繁體中文；公司名、產品名、技術名與 URL 可保留英文。
3. 只能引用 USER_PAYLOAD.evidence_bundle 內存在的 evidence_id。
4. 每一個 claim 都要有 evidence_ids、requirement_ids、type（fact/inference/assumption/unresolved）、mechanism、falsifier、confidence 與 decision_link。
5. 官方／監管／公司文件優先；新聞與社群只能作線索。來源衝突必須保留。
6. evidence_bundle 中 citation_eligible=false 或 evidence_role=context_only 的列只能作背景線索，絕對不可放入 claim 的 evidence_ids 或 counterevidence_ids；只有 citation_eligible=true 的列可作可稽核證據。
7. 缺少公司、治理或 ESG 原文時，該 section 必須標為 partial 或 unresolved，不可寫泛化公司描述。
8. USER_PAYLOAD.requirement_coverage 與 context_gap 是研究問題閘門，不是參考資訊：若必要 requirement 未 complete，對應 section 必須保留 partial/unresolved，並把缺口寫入 missing_evidence；不得用更多新聞、較長摘要或語氣自信把 coverage 缺口升級。
9. 不產出 buy、sell、target price 或保證報酬；不要改寫 financial_snapshot 的數字。
10. section 狀態判準：有至少一筆 citation_eligible 的官方／監管／產業原文，且該 section 的每一個 claim 都有直接或交叉證據時，標為 complete；SEC／交易所申報文件本身就是官方原始來源，足以支撐其涵蓋的 ESG、治理或公司事實。仍可在 missing_evidence 與 blind_spots 揭露尚未公開的量化細節；不可因補充互動頁不可存取或列出「可再補的研究問題」就把已有充分證據的 section 降為 partial/unresolved。
11. decision_link 必須逐項回答 driver、target_exposure、kpi、financial_line、scenario_implication；不能只寫產業常識或「有助於競爭力」。
12. 證偽條件必須是 as_of 之後可觀測的 KPI、門檻與期間；已經過去的年度不得再用「若 YYYY」書寫成未來條件。
13. 每個 section 最多 3 個 claims、3 個 blind_spots、5 個 missing_evidence；summary 不超過 700 字。

輸出必須完全符合下列形狀：
{
  "schema_version": "qualitative-context.v1",
  "target_id": "USER_PAYLOAD.target.target_id 或由 symbol 推導的 target id",
  "as_of": "USER_PAYLOAD.as_of 原值",
  "overall_status": "complete|partial|unresolved",
  "sections": {
    "company": {"status":"complete|partial|unresolved","summary":"","claims":[CLAIM],"blind_spots":[""],"missing_evidence":[""]},
    "industry": {"status":"complete|partial|unresolved","summary":"","claims":[CLAIM],"blind_spots":[""],"missing_evidence":[""]},
    "governance": {"status":"complete|partial|unresolved","summary":"","claims":[CLAIM],"blind_spots":[""],"missing_evidence":[""]},
    "esg": {"status":"complete|partial|unresolved","summary":"","claims":[CLAIM],"blind_spots":[""],"missing_evidence":[""]}
  },
  "cross_section_synthesis": {"key_mechanisms":[""],"contradictions":[{"topic":"","supporting_evidence_ids":[""],"opposing_evidence_ids":[""],"resolution":""}],"monitoring":[{"kpi":"","threshold":"","period":"","trigger_action":"","evidence_ids":[""]}],"unresolved_questions":[""]}
}

CLAIM = {"claim_id":"company-001","text":"","type":"fact|inference|assumption|unresolved","evidence_ids":["E001"],"counterevidence_ids":[],"requirement_ids":["company.business_model"],"mechanism":"","falsifier":"","decision_link":{"driver":"","target_exposure":"","kpi":"","financial_line":"","scenario_implication":""},"confidence":"high|medium|low|unresolved","evidence_quality":"direct|corroborated|indirect|insufficient"}

USER_PAYLOAD 會提供 target、as_of、financial_snapshot、market_snapshot、evidence_bundle 與 source_conflicts。先逐一核對證據，再產出四個 section；不要新增第五個 section。"""


class _VisibleTextParser(HTMLParser):
    """Extract visible HTML text without adding another runtime dependency."""

    _ignored = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in self._ignored:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._ignored and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._depth and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return _normalize_text(" ".join(self._parts))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\x00", " ")).strip()


def _read_capture(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    if path.suffix.casefold() == ".pdf":
        try:
            completed = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                check=True,
                capture_output=True,
                timeout=15,
            )
            return _normalize_text(completed.stdout.decode("utf-8", errors="ignore"))
        except (OSError, subprocess.SubprocessError):
            return ""
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="ignore")
    if "<html" in text.casefold() or "<body" in text.casefold() or "<div" in text.casefold():
        parser = _VisibleTextParser()
        try:
            parser.feed(text)
            return parser.text()
        except Exception:  # pragma: no cover - malformed third-party HTML fallback
            return _normalize_text(text)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _normalize_text(text)
    return _normalize_text(json.dumps(parsed, ensure_ascii=False, sort_keys=True))


def _bounded_excerpt(text: str, section: str, *, max_chars: int = 4500) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    keywords = _SECTION_KEYWORDS.get(section, ())
    windows: list[str] = []
    lower = normalized.casefold()
    # Take the first hit of several high-signal phrases instead of allowing a
    # generic word (for example ``market`` in a bond table) to consume all six
    # windows.  Keyword order is deliberate and begins with issuer/industry
    # disclosures commonly used in Taiwan annual reports.
    for keyword in keywords:
        if len(windows) >= 6:
            break
        position = lower.find(keyword.casefold())
        if position < 0:
            continue
        left = max(0, position - 480)
        right = min(len(normalized), position + 900)
        windows.append(normalized[left:right])
    if windows:
        excerpt = " … ".join(dict.fromkeys(windows))
    else:
        excerpt = normalized[:max_chars]
    return excerpt[:max_chars].rstrip()


def _target_scoped_source_text(text: str, target: Mapping[str, Any], source: Mapping[str, Any]) -> str:
    """Scope bulk JSON APIs to the requested issuer before model prompting.

    TWSE OpenAPI endpoints return every listed company in one payload.  Feeding
    the first 1,600 characters to the model can therefore surface another
    issuer and make a valid target look unresolved.  Keep HTML/PDF untouched;
    for JSON lists, retain only rows whose company code/name matches the target
    and leave an explicit no-match marker when the source has no target row.
    """

    url = str(source.get("url") or "").casefold()
    group = str(source.get("group") or "").casefold()
    if "openapi.twse.com.tw" not in url and not group.startswith("twse"):
        return text
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text
    symbol = str(target.get("symbol") or "").split(".", 1)[0].strip()
    aliases = [
        str(value).strip().casefold()
        for value in target.get("aliases", [])
        if isinstance(value, str) and len(str(value).strip()) >= 2
    ]
    aliases.extend([symbol.casefold()] if symbol else [])

    def row_matches(row: Mapping[str, Any]) -> bool:
        values = {str(value).strip().casefold() for value in row.values() if value is not None}
        code_values = {
            value
            for key, value in ((str(key), str(value).strip()) for key, value in row.items())
            if any(token in key for token in ("代號", "code", "symbol"))
        }
        if code_values:
            # Bulk exchange APIs expose a structured issuer code.  Treat it
            # as authoritative; alias substring fallback would otherwise make
            # 1303 南亞 also retain 2408 南亞科技.
            return bool(symbol and symbol in code_values)
        joined = " ".join(values)
        return any(alias and alias in joined for alias in aliases)

    rows: list[Any] = []
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, Mapping) and row_matches(row)]
    elif isinstance(payload, Mapping):
        for key in ("data", "results", "records", "rows"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows = [row for row in candidate if isinstance(row, Mapping) and row_matches(row)]
                break
        if not rows and row_matches(payload):
            rows = [payload]
    if rows:
        return _normalize_text(json.dumps(rows, ensure_ascii=False, sort_keys=True))
    return "[target_scope_match: none]"


def _chapter(report: Mapping[str, Any], chapter_id: str) -> Mapping[str, Any]:
    chapters = report.get("chapters") if isinstance(report.get("chapters"), list) else []
    for chapter in chapters:
        if isinstance(chapter, Mapping) and str(chapter.get("id")) == chapter_id:
            content = chapter.get("content")
            return content if isinstance(content, Mapping) else {}
    return {}


def _path_from_capture(raw_path: Any, project_root: Path) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else project_root / path


def _source_id(section: str, source: Mapping[str, Any]) -> str:
    digest = str(source.get("response_sha256") or source.get("url") or "missing")
    digest = hashlib.sha256(digest.encode("utf-8")).hexdigest()[:16]
    return f"Q_{section}_{digest}"


def _compact_periods(values: Any, *, forecast: bool = False) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    if not forecast:
        return [dict(item) for item in values if isinstance(item, Mapping)]
    compact: list[dict[str, Any]] = []
    keep = ("revenue", "operating_margin", "eps", "free_cash_flow", "capital_expenditure", "operating_cash_flow")
    for period in values:
        if not isinstance(period, Mapping):
            continue
        row: dict[str, Any] = {"year": period.get("year")}
        scenarios = period.get("scenarios") if isinstance(period.get("scenarios"), Mapping) else {}
        row["scenarios"] = {
            str(name): {key: scenario.get(key) for key in keep if key in scenario}
            for name, scenario in scenarios.items()
            if isinstance(scenario, Mapping)
        }
        compact.append(row)
    return compact


def _compact_market_snapshot(report: Mapping[str, Any]) -> dict[str, Any]:
    time_series = _chapter(report, "9").get("time_series")
    time_series = dict(time_series) if isinstance(time_series, Mapping) else {}
    # The deterministic chapters retain the complete market/valuation/event
    # payload.  The qualitative model only needs a small orientation view;
    # sending every return statistic and provider field adds latency without
    # improving a company/industry/governance/ESG claim.
    market = {
        key: time_series.get(key)
        for key in ("as_of", "window_end", "point_count", "returns", "status")
        if key in time_series
    }
    valuation = _chapter(report, "8")
    valuation_summary = {
        key: valuation.get(key)
        for key in ("discount_rate", "peer_median_pe", "peer_target_eps", "peer_target_period_key", "target_range")
        if key in valuation
    }
    valuation_summary["methods"] = [
        {key: method.get(key) for key in ("method", "scenario_values", "status", "weight") if key in method}
        for method in valuation.get("methods", [])
        if isinstance(method, Mapping)
    ] if isinstance(valuation.get("methods"), list) else []
    event = _chapter(report, "10").get("historical_event_alignment")
    event = dict(event) if isinstance(event, Mapping) else {}
    event_summary = {
        key: event.get(key)
        for key in ("aligned_event_count", "event_study_quality_status", "event_study_significance_status", "not_causal")
        if key in event
    }
    return {"time_series": market, "valuation": valuation_summary, "event_alignment": event_summary}


def _compact_conflicts(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    compact: list[dict[str, Any]] = []
    for value in values[:4]:
        if not isinstance(value, Mapping):
            continue
        row = {
            key: value.get(key)
            for key in ("conflict_level", "counts", "independent_source_count", "method", "calibration_status")
            if key in value
        }
        if isinstance(value.get("evidence_ids"), list):
            row["evidence_ids"] = list(value["evidence_ids"][:6])
        if isinstance(value.get("limitations"), list):
            row["limitations"] = [str(item) for item in value["limitations"][:2]]
        compact.append(row)
    return compact


def build_qualitative_evidence_bundle(report: Mapping[str, Any], *, project_root: Path, target_id: str | None = None) -> dict[str, Any]:
    """Build bounded excerpts and deterministic metadata for one target."""

    target = dict(report.get("target") or {})
    evidence = report.get("appendix", {}).get("evidence", {}) if isinstance(report.get("appendix"), Mapping) else {}
    evidence = evidence if isinstance(evidence, Mapping) else {}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in evidence.get("items", []) if isinstance(evidence.get("items"), list) else []:
        if not isinstance(item, Mapping) or not item.get("item_id"):
            continue
        item_id = str(item["item_id"])
        if item_id in seen:
            continue
        seen.add(item_id)
        items.append(
            {
                "evidence_id": item_id,
                "section": "news_social",
                "source_tier": item.get("source_tier"),
                "publisher": item.get("publisher_id"),
                "published_at": item.get("published_at"),
                "locator": item.get("canonical_url"),
                "excerpt": _bounded_excerpt(str(item.get("content") or item.get("title") or ""), "industry", max_chars=350),
                "url": item.get("canonical_url"),
                "content_sha256": item.get("content_sha256"),
                "quality_flags": ["metadata_only"] if int((item.get("rights") or {}).get("public_excerpt_chars") or 0) == 0 else [],
            }
        )

    qualitative = evidence.get("qualitative_sources") if isinstance(evidence.get("qualitative_sources"), Mapping) else {}
    if not qualitative:
        existing_context = report.get("appendix", {}).get("qualitative_context") if isinstance(report.get("appendix"), Mapping) else {}
        existing_bundle = existing_context.get("evidence_bundle") if isinstance(existing_context, Mapping) else []
        if isinstance(existing_bundle, list):
            for existing in existing_bundle:
                if not isinstance(existing, Mapping) or not existing.get("evidence_id") or str(existing.get("evidence_id")) in seen:
                    continue
                items.append(dict(existing))
                seen.add(str(existing["evidence_id"]))
    # The same official filing is often intentionally assigned to more than
    # one research question (for example company facts and governance).  The
    # model input must retain a section-scoped row for each assignment; a
    # global URL/hash deduplication would silently make the second section
    # disappear from the bounded prompt.
    qualitative_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for section, values in qualitative.items():
        if not isinstance(values, list):
            continue
        for source in values:
            if not isinstance(source, Mapping) or not source.get("url"):
                continue
            source_key = str(source.get("response_sha256") or source.get("url"))
            section_key = (str(section), source_key)
            existing = qualitative_by_key.get(section_key)
            capture_path = _path_from_capture(source.get("raw_capture_path"), project_root)
            source_text = _read_capture(capture_path) if capture_path else ""
            source_text = _target_scoped_source_text(source_text, target, source)
            if existing is not None:
                existing["section_tags"] = sorted(set(existing.get("section_tags", [])) | {str(section)})
                existing.setdefault("section_excerpts", {})[str(section)] = _bounded_excerpt(source_text, str(section), max_chars=1600)
                continue
            evidence_id = _source_id(str(section), source)
            excerpt = _bounded_excerpt(source_text, str(section), max_chars=1600)
            flags: list[str] = []
            if not excerpt:
                flags.append("excerpt_unavailable")
            if not source.get("response_sha256"):
                flags.append("response_hash_missing")
            source_group = str(source.get("group") or "").casefold()
            source_publisher = str(source.get("publisher") or "").casefold()
            official_context = (
                source_group in {"sec_edgar", "twse", "twse_profile", "twse_financial", "regulator", "official"}
                or source_group.endswith(("_official", "_ir", "_governance", "_esg"))
                or "official" in source_publisher
                or "investor relations" in source_publisher
            )
            record = {
                    "evidence_id": evidence_id,
                    "section": str(section),
                    "section_tags": [str(section)],
                    "section_excerpts": {str(section): excerpt},
                    "source_tier": "official" if official_context else "industry_or_secondary",
                    "publisher": source.get("publisher") or source.get("group"),
                    "published_at": source.get("published_at") or source.get("collected_at"),
                    "locator": source.get("locator") or source.get("url"),
                    "excerpt": excerpt,
                    "url": source.get("citation_url") or source.get("url"),
                    "response_sha256": source.get("response_sha256"),
                    "raw_capture_path": source.get("raw_capture_path"),
                    "requirement_ids": [str(item) for item in source.get("requirement_ids", []) if str(item)] if isinstance(source.get("requirement_ids"), list) else [],
                    "metric_attestations": [dict(item) for item in source.get("metric_attestations", []) if isinstance(item, Mapping)] if isinstance(source.get("metric_attestations"), list) else [],
                    "geography_scope": source.get("geography_scope"),
                    "quality_flags": flags,
                }
            items.append(record)
            qualitative_by_key[section_key] = record

    time_series = _chapter(report, "9").get("time_series")
    time_series = dict(time_series) if isinstance(time_series, Mapping) else {}
    forecast_assumptions = _chapter(report, "7").get("assumptions", {})
    if isinstance(forecast_assumptions, Mapping):
        forecast_assumptions = {
            key: forecast_assumptions.get(key)
            for key in ("revenue_growth", "operating_margin", "tax_rate", "capital_intensity", "cash_conversion", "guidance_lineage")
            if key in forecast_assumptions
        }
        if isinstance(forecast_assumptions.get("guidance_lineage"), list):
            forecast_assumptions["guidance_lineage"] = [
                {key: item.get(key) for key in ("publisher", "url", "response_sha256", "locator") if key in item}
                for item in forecast_assumptions["guidance_lineage"][:4]
                if isinstance(item, Mapping)
            ]
    return {
        "schema_version": "qualitative-context-input.v1",
        "target_id": target_id or report.get("target_id") or str(target.get("symbol") or "").split(".", 1)[0].casefold(),
        "target": target,
        "as_of": report.get("generated_at"),
        "research_window": {
            "start": time_series.get("window_start"),
            "end": time_series.get("window_end"),
            "currency": target.get("currency"),
        },
        "financial_snapshot": {
            "annual_periods": _compact_periods(_chapter(report, "6").get("annual_periods", [])),
            "quarterly_periods": _compact_periods(_chapter(report, "6").get("quarterly_periods", [])),
            "forecast_periods": _compact_periods(_chapter(report, "7").get("forecast_periods", []), forecast=True),
            "forecast_assumptions": forecast_assumptions,
        },
        "market_snapshot": _compact_market_snapshot(report),
        "evidence_bundle": items,
        "requirement_coverage": deepcopy(dict(report.get("appendix", {}).get("context_coverage") or {})) if isinstance(report.get("appendix"), Mapping) and isinstance(report.get("appendix", {}).get("context_coverage"), Mapping) else {},
        "context_gap": deepcopy(dict(report.get("appendix", {}).get("context_gap") or {})) if isinstance(report.get("appendix"), Mapping) and isinstance(report.get("appendix", {}).get("context_gap"), Mapping) else {},
        "context_packs": deepcopy(dict(report.get("appendix", {}).get("context_packs") or {})) if isinstance(report.get("appendix"), Mapping) and isinstance(report.get("appendix", {}).get("context_packs"), Mapping) else {},
        "source_conflicts": _compact_conflicts(_chapter(report, "11").get("source_conflicts", [])),
        "generation_constraints": {
            "max_claims_per_section": 3,
            "max_summary_chars": 700,
            "max_blind_spots_per_section": 3,
            "max_missing_evidence_per_section": 5,
            "json_only": True,
        },
        "provenance": {
            "report_id": report.get("report_id"),
            "report_schema_version": report.get("schema_version"),
            "input_report_sha256": hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        },
    }


def load_prompt_template(path: Path) -> str:
    """Read the executable system prompt from the documented fenced block."""

    text = path.read_text(encoding="utf-8")
    match = re.search(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"prompt template has no executable text block: {path}")
    return match.group(1)


def build_qualitative_model_input(bundle: Mapping[str, Any], *, qualitative_only: bool = False) -> dict[str, Any]:
    """Create the bounded model payload while retaining the full bundle separately."""

    evidence_rows = [item for item in bundle.get("evidence_bundle", []) if isinstance(item, Mapping)]
    qualitative_rows = [item for item in evidence_rows if str(item.get("section") or "") != "news_social"]
    news_rows = [item for item in evidence_rows if str(item.get("section") or "") == "news_social"]
    news_rows = sorted(news_rows, key=lambda item: str(item.get("published_at") or ""), reverse=True)
    # The full bundle is the audit source of truth.  The model receives a
    # bounded view: every full-text qualitative extract plus the latest 60
    # metadata/news rows.  This prevents a three-year target run from
    # overflowing a 64K context window while preserving all raw evidence.
    selected_evidence: list[dict[str, Any]] = []
    for item in [*qualitative_rows, *news_rows[:60]]:
        section = str(item.get("section") or "")
        section_excerpts = item.get("section_excerpts") if isinstance(item.get("section_excerpts"), Mapping) else {}
        if section_excerpts:
            # Keep enough contiguous source text for a section-level claim:
            # governance and ESG PDFs commonly place the quantitative KPI
            # after the navigation/contents pages.  The full bundle remains
            # the audit source of truth; this bounded view is still kept
            # below the local model context budget (news rows are capped at
            # 60 and each qualitative source at 3,600 chars).
            excerpt = "\n".join(
                f"[{tag}] {str(text)[:1100]}"
                for tag, text in section_excerpts.items()
                if str(text or "").strip()
            )
        else:
            excerpt = None
        section_tags = item.get("section_tags") if isinstance(item.get("section_tags"), list) else [section]
        quality_flags = [str(flag) for flag in (item.get("quality_flags") or []) if str(flag)]
        citation_eligible = bool(item.get("excerpt") or excerpt) and "metadata_only" not in quality_flags and "excerpt_unavailable" not in quality_flags
        selected_evidence.append({
            key: value
            for key, value in {
                "evidence_id": item.get("evidence_id"),
                "section": section,
                "section_tags": [str(tag) for tag in section_tags if str(tag)],
                "source_tier": item.get("source_tier"),
                "publisher": item.get("publisher"),
                "published_at": item.get("published_at"),
                "locator": item.get("locator"),
                "excerpt": str(excerpt or item.get("excerpt") or "")[:6000 if section != "news_social" else 350],
                "url": item.get("url"),
                "response_sha256": item.get("response_sha256") or item.get("content_sha256"),
                "quality_flags": quality_flags,
                "requirement_ids": [str(value) for value in item.get("requirement_ids", []) if str(value)] if isinstance(item.get("requirement_ids"), list) else [],
                "metric_attestations": [dict(value) for value in item.get("metric_attestations", []) if isinstance(value, Mapping)][:12] if isinstance(item.get("metric_attestations"), list) else [],
                "geography_scope": item.get("geography_scope"),
                "citation_eligible": citation_eligible,
                "evidence_role": "citation" if citation_eligible else "context_only",
            }.items()
            if value not in (None, "", [])
        })
    # The full bundle remains the audit source of truth, but a target with
    # many peer/IR routes can exceed the local model's 64K context before it
    # has produced a single token.  Apply a deterministic bounded view only
    # when needed: preserve up to two high-signal citation-eligible qualitative sources
    # per section, then recent news, and trim excerpts without changing IDs.
    # Qwen's local endpoint has materially higher latency once the prompt
    # exceeds roughly 10K characters (even with a small completion).  Keep the
    # model view intentionally narrow; all omitted evidence remains in the
    # full bundle and is available to the deterministic audit layer.
    model_budget_chars = 3_000
    def evidence_priority(item: Mapping[str, Any], section: str) -> int:
        """Prefer substantive section documents over generic landing/API rows."""

        text = " ".join(str(item.get(key) or "").casefold() for key in ("publisher", "url", "section"))
        score = 0
        if section == "company":
            score += 100 if any(token in text for token in ("annual", "年報", "filing", "report")) else 0
            score += 80 if "official" in text or "investor" in text else 0
        elif section == "governance":
            score += 120 if any(token in text for token in ("governance", "董事", "board", "committee")) else 0
            score += 90 if any(token in text for token in ("annual", "年報", "filing", "report")) else 0
        elif section == "esg":
            score += 130 if any(token in text for token in ("esg", "sustain", "永續", "csr", "climate")) else 0
            score += 80 if any(token in text for token in ("annual", "年報", "filing", "report")) else 0
        elif section == "industry":
            score += 120 if any(token in text for token in ("eia", "wsts", "oecd", "usgs", "worldsteel", "statistics", "industry")) else 0
            score += 80 if any(token in text for token in ("annual", "年報", "filing", "report")) else 0
        elif section == "peer":
            score += 100 if any(token in text for token in ("fundamentals", "financial", "yahoo")) else 0
        score += 20 if item.get("citation_eligible") else 0
        return score

    def prioritized_rows(rows: list[dict[str, Any]], section: str, limit: int) -> list[dict[str, Any]]:
        eligible = [item for item in rows if item.get("citation_eligible")]
        pool = eligible if eligible else rows
        ordered = sorted(pool, key=lambda item: evidence_priority(item, section), reverse=True)
        specialized_tokens = {
            "governance": ("governance", "董事", "board", "committee"),
            "esg": ("esg", "sustain", "永續", "csr", "climate"),
            "industry": ("eia", "wsts", "oecd", "usgs", "worldsteel", "statistics", "industry"),
        }.get(section, ())
        if specialized_tokens:
            specialized = [
                item
                for item in ordered
                if any(token in " ".join(str(item.get(key) or "").casefold() for key in ("publisher", "url")) for token in specialized_tokens)
            ]
            if specialized:
                first = specialized[0]
                ordered = [first, *[item for item in ordered if item is not first]]
        return ordered[:limit]

    if bundle.get("requirement_coverage") and len(json.dumps(selected_evidence, ensure_ascii=False, separators=(",", ":"))) > model_budget_chars:
        bounded: list[dict[str, Any]] = []
        for section in ("company", "industry", "governance", "esg", "peer"):
            rows = [item for item in selected_evidence if str(item.get("section") or "") == section]
            chosen = prioritized_rows(rows, section, 2)
            bounded.extend({**dict(item), "excerpt": str(item.get("excerpt") or "")[:800]} for item in chosen)
        news_rows = sorted(
            [item for item in selected_evidence if str(item.get("section") or "") == "news_social"],
            key=lambda item: str(item.get("published_at") or ""),
            reverse=True,
        )
        bounded.extend({**dict(item), "excerpt": str(item.get("excerpt") or "")[:120]} for item in news_rows[:1])
        selected_evidence = bounded
    financial = bundle.get("financial_snapshot") if isinstance(bundle.get("financial_snapshot"), Mapping) else {}
    keep = ("year", "period", "revenue", "eps", "gross_margin", "operating_margin", "net_margin", "free_cash_flow", "capital_expenditure")
    annual = [{key: row.get(key) for key in keep if key in row} for row in (financial.get("annual_periods") or [])[-1:] if isinstance(row, Mapping)]
    quarterly = [{key: row.get(key) for key in keep if key in row} for row in (financial.get("quarterly_periods") or [])[-2:] if isinstance(row, Mapping)]
    forecast: list[dict[str, Any]] = []
    for period in (financial.get("forecast_periods") or [])[:1]:
        if not isinstance(period, Mapping):
            continue
        scenarios = period.get("scenarios") if isinstance(period.get("scenarios"), Mapping) else {}
        forecast.append({
            "year": period.get("year"),
            "scenarios": {
                str(name): {key: values.get(key) for key in ("revenue", "operating_margin", "eps", "free_cash_flow") if key in values}
                for name, values in scenarios.items()
                if isinstance(values, Mapping)
            },
        })
    # Keep a compact target identity in the model request.  The full target
    # profile remains in the canonical report and audit bundle.
    target = bundle.get("target") if isinstance(bundle.get("target"), Mapping) else {}
    compact_target = {
        key: target.get(key)
        for key in ("target_id", "symbol", "name", "aliases", "market", "currency", "industry")
        if key in target
    }
    # Once requirement coverage is available, this is the production path:
    # retain up to two high-signal citation-eligible rows per research section.  A
    # single row was too lossy: it could select a blocked landing page while
    # dropping an available annual report, or select a company page while
    # dropping the same filing's governance assignment.  If a section has no
    # citation-eligible row, keep one context-only row so the model must
    # disclose the gap.  The complete rows stay in bundle-full.json.
    if bundle.get("requirement_coverage"):
        bounded: list[dict[str, Any]] = []
        for section in ("company", "industry", "governance", "esg", "peer"):
            rows = [item for item in selected_evidence if str(item.get("section") or "") == section]
            bounded.extend(prioritized_rows(rows, section, 2))
        news_rows = sorted(
            [item for item in selected_evidence if str(item.get("section") or "") == "news_social"],
            key=lambda item: str(item.get("published_at") or ""),
            reverse=True,
        )
        bounded.extend(news_rows[:1])
        selected_evidence = [
            {
                key: value
                for key, value in {
                    "evidence_id": item.get("evidence_id"),
                    "section": item.get("section"),
                    "publisher": item.get("publisher"),
                    "published_at": item.get("published_at"),
                    "excerpt": str(item.get("excerpt") or "")[:1200],
                    "url": item.get("url"),
                    "requirement_ids": item.get("requirement_ids"),
                    "metric_attestations": item.get("metric_attestations"),
                    "geography_scope": item.get("geography_scope"),
                    "citation_eligible": bool(item.get("citation_eligible")),
                    "evidence_role": item.get("evidence_role"),
                }.items()
                if value not in (None, "", [])
            }
            for item in bounded
        ]
    result = {
        **dict(bundle),
        "target": compact_target,
        "evidence_bundle": selected_evidence,
        "model_selection": {
            "full_evidence_count": len(evidence_rows),
            "included_evidence_count": len(selected_evidence),
            "excluded_evidence_count": max(0, len(evidence_rows) - len(selected_evidence)),
            "policy": "up to two section-prioritized citation sources per section plus latest 1 news/social row when coverage metadata is present; full bundle retained for audit",
            "citation_eligible_evidence_count": sum(1 for item in selected_evidence if item.get("citation_eligible")),
            "context_only_evidence_count": sum(1 for item in selected_evidence if not item.get("citation_eligible")),
        },
        "financial_snapshot": {
            "annual_periods": annual,
            "quarterly_periods": quarterly,
            "forecast_periods": forecast,
            "forecast_assumptions": {},
        },
    }
    # Coverage is authoritative for the gate, but the model only needs the
    # requirement IDs/statuses and a short list of missing routes.  Keeping
    # the full coverage/pack objects in the on-disk bundle avoids spending
    # model context on repeated source hashes and provenance fields.
    coverage = bundle.get("requirement_coverage") if isinstance(bundle.get("requirement_coverage"), Mapping) else {}
    if coverage:
        result["requirement_coverage"] = {
            "summary": deepcopy(dict(coverage.get("summary") or {})),
            "requirements": [
                {
                    key: item.get(key)
                    for key in ("requirement_id", "section", "status", "missing_reasons", "missing_metrics", "missing_roles", "geography_scopes", "required_geography_scopes")
                    if key in item
                }
                for item in coverage.get("requirements", [])
                if isinstance(item, Mapping)
            ],
        }
    gap = bundle.get("context_gap") if isinstance(bundle.get("context_gap"), Mapping) else {}
    if gap:
        result["context_gap"] = {
            "status": gap.get("status"),
            "next_action": gap.get("next_action"),
            "missing_requirements": [
                {
                    key: item.get(key)
                    for key in ("requirement_id", "section", "reason", "recommended_routes")
                    if key in item
                }
                for item in gap.get("missing_requirements", [])
                if isinstance(item, Mapping)
            ],
        }
    packs = bundle.get("context_packs") if isinstance(bundle.get("context_packs"), Mapping) else {}
    if packs:
        result["context_packs"] = {
            str(name): {
                key: pack.get(key)
                for key in ("status", "evidence_count", "independent_source_count", "metric_count", "missing_reasons")
                if key in pack
            }
            for name, pack in packs.items()
            if isinstance(pack, Mapping)
        }
    if qualitative_only:
        result["evidence_bundle"] = [item for item in result.get("evidence_bundle", []) if item.get("section") != "news_social"]
        result["financial_snapshot"] = {
            "annual_periods": annual[-1:],
            "quarterly_periods": quarterly[-1:],
            "forecast_periods": forecast[:1],
            "forecast_assumptions": financial.get("forecast_assumptions", {}),
        }
        result["market_snapshot"] = {}
        result["source_conflicts"] = []
    return result


def run_qualitative_context_model(
    report: Mapping[str, Any], *, project_root: Path, target_id: str, endpoint: str, model: str,
    timeout_seconds: float = 600.0, qualitative_only: bool = False, prompt: str | None = None,
    provider: str = "opencode", opencode_model: str = DEFAULT_OPENCODE_MODEL,
    opencode_command: str = DEFAULT_OPENCODE_COMMAND, fallback_qwen: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Run and validate one model call for an in-memory canonical report.

    Local runs prefer OpenCode/Big Pickle.  Qwen remains an explicit fallback
    because OpenCode is a CLI provider and may fail independently of the
    evidence pipeline.  ``provider='qwen'`` keeps the old direct path for
    deterministic replays and debugging.
    """

    full_bundle = build_qualitative_evidence_bundle(report, project_root=project_root, target_id=target_id)
    bundle = build_qualitative_model_input(full_bundle, qualitative_only=qualitative_only)
    runtime_prompt = prompt or RUNTIME_QUALITATIVE_PROMPT
    input_payload = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    attempts: list[dict[str, str]] = []
    result: dict[str, Any] | None = None
    parsed: dict[str, Any] | None = None
    actual_provider = ""
    actual_model = model
    actual_endpoint = endpoint
    if provider == "qwen":
        provider_order = ["qwen"]
    elif provider == "opencode":
        provider_order = ["opencode", "qwen"] if fallback_qwen else ["opencode"]
    else:
        provider_order = ["opencode", "qwen"]
    for candidate_provider in provider_order:
        candidate_model = opencode_model if candidate_provider == "opencode" else model
        try:
            if candidate_provider == "opencode":
                candidate_result = invoke_opencode_model(
                    model=candidate_model,
                    system_prompt=runtime_prompt,
                    user_payload=bundle,
                    timeout_seconds=timeout_seconds,
                    command=opencode_command,
                )
                candidate_endpoint = "opencode://cli"
            else:
                candidate_result = invoke_qualitative_model(
                    endpoint=endpoint, model=candidate_model, system_prompt=runtime_prompt, user_payload=bundle,
                    # The bounded evidence view is intentionally small enough
                    # for the local worker.  The larger budget prevents valid
                    # four-section JSON from being cut off mid-object.
                    timeout_seconds=timeout_seconds, max_tokens=5000 if not qualitative_only else 3500,
                    native_ollama=endpoint.rstrip("/").endswith(":11434"),
                )
                candidate_endpoint = endpoint
            candidate_parsed = parse_model_json(str(candidate_result.get("content") or ""))
            result = candidate_result
            parsed = candidate_parsed
            actual_provider = candidate_provider
            actual_model = candidate_model
            actual_endpoint = candidate_endpoint
            break
        except Exception as exc:
            attempts.append({"provider": candidate_provider, "model": candidate_model, "error": f"{type(exc).__name__}: {exc}"[:500]})
    if result is None or parsed is None:
        detail = "; ".join(f"{item['provider']}: {item['error']}" for item in attempts)
        raise RuntimeError(f"all qualitative model providers failed: {detail}")
    valid_ids = {str(item.get("evidence_id")) for item in bundle.get("evidence_bundle", []) if isinstance(item, Mapping) and item.get("evidence_id")}
    evidence_metadata = {str(item.get("evidence_id")): item for item in bundle.get("evidence_bundle", []) if isinstance(item, Mapping) and item.get("evidence_id")}
    checked = validate_qualitative_model_output(
        parsed, target_id=target_id, as_of=str(bundle.get("as_of") or ""),
        valid_evidence_ids=valid_ids, evidence_metadata=evidence_metadata,
        requirement_coverage=bundle.get("requirement_coverage") if isinstance(bundle.get("requirement_coverage"), Mapping) else None,
    )
    raw_response = json.dumps(result.get("response"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope = {
        "schema_version": "qualitative-context-model-run.v1",
        "run_id": f"qualitative_{target_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "target_id": target_id,
        "model": actual_model,
        "endpoint": actual_endpoint,
        "prompt_template": "runtime-compact",
        "prompt_template_sha256": hashlib.sha256(runtime_prompt.encode("utf-8")).hexdigest(),
        "input_sha256": hashlib.sha256(input_payload).hexdigest(),
        "raw_response_sha256": hashlib.sha256(raw_response).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input_summary": {
            "evidence_count": len(bundle.get("evidence_bundle", [])),
            "full_evidence_count": (bundle.get("model_selection") or {}).get("full_evidence_count") if isinstance(bundle.get("model_selection"), Mapping) else len(full_bundle.get("evidence_bundle", [])),
            "excluded_evidence_count": (bundle.get("model_selection") or {}).get("excluded_evidence_count") if isinstance(bundle.get("model_selection"), Mapping) else 0,
            "news_social_count": sum(1 for item in bundle.get("evidence_bundle", []) if item.get("section") == "news_social"),
            "qualitative_extract_count": sum(1 for item in bundle.get("evidence_bundle", []) if item.get("section") != "news_social"),
            "citation_eligible_evidence_count": sum(1 for item in bundle.get("evidence_bundle", []) if item.get("citation_eligible")),
            "context_only_evidence_count": sum(1 for item in bundle.get("evidence_bundle", []) if not item.get("citation_eligible")),
            "source_conflict_count": len(bundle.get("source_conflicts") or []),
            "qualitative_only": qualitative_only,
            "provider": actual_provider,
            "provider_attempts": attempts,
        },
        "usage": result.get("usage"),
        "validation": checked.get("validation"),
        "result": checked,
    }
    return envelope, full_bundle, raw_response


def parse_model_json(content: str) -> dict[str, Any]:
    """Parse strict JSON while tolerating one accidental Markdown fence."""

    cleaned = str(content or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


def validate_qualitative_model_output(
    output: Mapping[str, Any], *, target_id: str, as_of: str, valid_evidence_ids: set[str],
    evidence_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    requirement_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach recomputed quality facts and fail closed on dangling IDs."""

    checked = deepcopy(dict(output))
    dangling: set[str] = set()
    claim_count = 0
    claims_with_evidence = 0
    evidence_quality_violations: list[dict[str, Any]] = []
    decision_quality_violations: list[dict[str, Any]] = []
    sections = checked.get("sections") if isinstance(checked.get("sections"), Mapping) else {}
    coverage_by_id = {
        str(item.get("requirement_id")): item
        for item in (requirement_coverage or {}).get("requirements", [])
        if isinstance(item, Mapping) and item.get("requirement_id")
    } if isinstance(requirement_coverage, Mapping) else {}
    coverage_incomplete_sections = {
        str(item.get("section"))
        for item in coverage_by_id.values()
        if str(item.get("status") or "") not in {"complete", "not_applicable"}
    }
    try:
        as_of_year = datetime.fromisoformat(str(as_of).replace("Z", "+00:00")).year
    except ValueError:
        year_match = re.search(r"(20\d{2})", str(as_of))
        as_of_year = int(year_match.group(1)) if year_match else None
    output_limit_adjustments: list[dict[str, Any]] = []
    field_limits = {"claims": 3, "blind_spots": 3, "missing_evidence": 5}
    for section_name in ("company", "industry", "governance", "esg"):
        section_values = sections.get(section_name) if isinstance(sections, Mapping) else None
        if not isinstance(section_values, dict):
            continue
        for field, limit in field_limits.items():
            values = section_values.get(field)
            if not isinstance(values, list) or len(values) <= limit:
                continue
            output_limit_adjustments.append({
                "section": section_name,
                "field": field,
                "original_count": len(values),
                "retained_count": limit,
            })
            # The unmodified provider response is retained separately and
            # hashed in the model envelope.  The validated contract keeps the
            # first N items exactly as returned so a harmless over-generation
            # does not discard an otherwise valid four-section research run.
            section_values[field] = values[:limit]
    for section in ("company", "industry", "governance", "esg"):
        values = sections.get(section) if isinstance(sections, Mapping) else {}
        claims = values.get("claims") if isinstance(values, Mapping) and isinstance(values.get("claims"), list) else []
        for claim in claims:
            if not isinstance(claim, Mapping):
                continue
            claim_count += 1
            evidence_ids = [str(item) for item in claim.get("evidence_ids", []) if str(item)] if isinstance(claim.get("evidence_ids"), list) else []
            counter_ids = [str(item) for item in claim.get("counterevidence_ids", []) if str(item)] if isinstance(claim.get("counterevidence_ids"), list) else []
            unknown = set(evidence_ids + counter_ids) - valid_evidence_ids
            dangling.update(unknown)
            if evidence_ids and not unknown:
                claims_with_evidence += 1
            metadata_flags = [
                str(flag)
                for evidence_id in evidence_ids
                for flag in (evidence_metadata.get(evidence_id, {}).get("quality_flags", []) if evidence_metadata else [])
            ]
            ineligible_ids = [
                evidence_id
                for evidence_id in evidence_ids
                if evidence_metadata
                and evidence_id in evidence_metadata
                and evidence_metadata[evidence_id].get("citation_eligible") is False
            ]
            if "metadata_only" in metadata_flags or ineligible_ids:
                original_confidence = claim.get("confidence")
                claim["model_confidence"] = original_confidence
                claim["confidence"] = "unresolved"
                claim["evidence_quality"] = "insufficient" if all(flag == "metadata_only" for flag in metadata_flags) else "indirect"
                evidence_quality_violations.append({
                    "claim_id": claim.get("claim_id"),
                    "confidence": original_confidence,
                    "evidence_ids": evidence_ids,
                    "reason": "metadata_only_source" if "metadata_only" in metadata_flags else "context_only_source",
                })
                if isinstance(values, dict) and str(values.get("status") or "") == "complete":
                    values["status"] = "partial"
            if not evidence_ids:
                claim["confidence"] = "unresolved"
                claim["evidence_quality"] = "insufficient"
                if isinstance(values, dict) and str(values.get("status") or "") == "complete":
                    values["status"] = "partial"
            if coverage_by_id:
                requirement_ids = [str(item) for item in claim.get("requirement_ids", []) if str(item)] if isinstance(claim.get("requirement_ids"), list) else []
                if not requirement_ids:
                    decision_quality_violations.append({"section": section, "claim_id": claim.get("claim_id"), "reason": "requirement_ids_missing"})
                incomplete = [req_id for req_id in requirement_ids if str(coverage_by_id.get(req_id, {}).get("status") or "unresolved") not in {"complete", "not_applicable"}]
                if incomplete:
                    decision_quality_violations.append({"section": section, "claim_id": claim.get("claim_id"), "reason": "requirement_incomplete", "requirement_ids": incomplete})
                unsupported = [
                    evidence_id
                    for evidence_id in evidence_ids
                    if evidence_metadata
                    and isinstance(evidence_metadata.get(evidence_id), Mapping)
                    and isinstance(evidence_metadata[evidence_id].get("requirement_ids"), list)
                    and evidence_metadata[evidence_id].get("requirement_ids")
                    and not set(requirement_ids) & {str(item) for item in evidence_metadata[evidence_id].get("requirement_ids", [])}
                ]
                if unsupported:
                    decision_quality_violations.append({"section": section, "claim_id": claim.get("claim_id"), "reason": "evidence_requirement_mismatch", "evidence_ids": unsupported})
                decision_link = claim.get("decision_link") if isinstance(claim.get("decision_link"), Mapping) else {}
                required_link_fields = ("driver", "target_exposure", "kpi", "financial_line", "scenario_implication")
                missing_link_fields = [field for field in required_link_fields if not str(decision_link.get(field) or "").strip()]
                if missing_link_fields:
                    decision_quality_violations.append({"section": section, "claim_id": claim.get("claim_id"), "reason": "decision_link_incomplete", "missing_fields": missing_link_fields})
                falsifier = str(claim.get("falsifier") or "")
                stale_years = [int(year) for year in re.findall(r"若\s*(20\d{2})", falsifier) if as_of_year is not None and int(year) < as_of_year]
                if stale_years:
                    decision_quality_violations.append({"section": section, "claim_id": claim.get("claim_id"), "reason": "stale_falsifier", "years": sorted(set(stale_years)), "as_of_year": as_of_year})
    for section_name in coverage_incomplete_sections:
        section_values = sections.get(section_name) if isinstance(sections, Mapping) else None
        if isinstance(section_values, dict) and str(section_values.get("status") or "") == "complete":
            section_values["status"] = "partial"
    for violation in decision_quality_violations:
        section_values = sections.get(str(violation.get("section") or "")) if isinstance(sections, Mapping) else None
        if isinstance(section_values, dict) and str(section_values.get("status") or "") == "complete":
            section_values["status"] = "partial"
    for section_name in ("company", "industry", "governance", "esg"):
        section_values = sections.get(section_name) if isinstance(sections, Mapping) else None
        claims = section_values.get("claims") if isinstance(section_values, Mapping) and isinstance(section_values.get("claims"), list) else []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            claim_violations = [
                dict(item)
                for item in decision_quality_violations
                if str(item.get("section") or "") == section_name and item.get("claim_id") == claim.get("claim_id")
            ]
            claim["decision_quality_status"] = "partial" if claim_violations else "complete"
            claim["decision_quality_violations"] = claim_violations
    target_ok = str(checked.get("target_id") or "") == str(target_id)
    as_of_ok = str(checked.get("as_of") or "") == str(as_of)
    required_sections_ok = isinstance(sections, Mapping) and all(isinstance(sections.get(key), Mapping) for key in ("company", "industry", "governance", "esg"))
    status = "pass" if target_ok and as_of_ok and required_sections_ok and not dangling else "fail"
    quality = dict(checked.get("quality") or {}) if isinstance(checked.get("quality"), Mapping) else {}
    high_metadata_claims = [
        item
        for item in evidence_quality_violations
        if str(item.get("confidence") or "").casefold() == "high"
        and str(item.get("reason") or "") == "metadata_only_source"
    ]
    quality.update(
        {
            "claim_count": claim_count,
            "claims_with_evidence": claims_with_evidence,
            "evidence_coverage_ratio": round(claims_with_evidence / claim_count, 6) if claim_count else 0.0,
            "dangling_evidence_ids": sorted(dangling),
            "unresolved_count": sum(1 for section in sections.values() if isinstance(section, Mapping) and str(section.get("status") or "") != "complete") if isinstance(sections, Mapping) else 4,
            "metadata_only_claim_count": len(evidence_quality_violations),
            "evidence_quality": (
                "insufficient"
                if high_metadata_claims or (claim_count and claims_with_evidence < claim_count)
                else "partial"
                if evidence_quality_violations
                or any(
                    isinstance(section, Mapping) and str(section.get("status") or "") != "complete"
                    for section in sections.values()
                )
                else "complete"
            ),
            "decision_quality": "partial" if decision_quality_violations or coverage_incomplete_sections else "complete",
        }
    )
    checked["quality"] = quality
    if not str(checked.get("summary") or "").strip():
        section_summaries = [
            str(section.get("summary") or "").strip()
            for section in sections.values()
            if isinstance(section, Mapping) and str(section.get("summary") or "").strip()
        ] if isinstance(sections, Mapping) else []
        checked["summary"] = "；".join(section_summaries)[:700] or "各質化章節尚未提供可稽核摘要。"
        quality["summary_source"] = "deterministic_section_fallback"
        checked["quality"] = quality
    if evidence_quality_violations or decision_quality_violations or coverage_incomplete_sections or any(
        isinstance(section, Mapping) and str(section.get("status") or "") != "complete"
        for section in sections.values()
    ):
        checked["overall_status"] = "partial"
    checked["validation"] = {
        "status": status,
        "target_match": target_ok,
        "as_of_match": as_of_ok,
        "required_sections": required_sections_ok,
        "dangling_evidence_ids": sorted(dangling),
        "claim_count": claim_count,
        "claims_with_evidence": claims_with_evidence,
        "evidence_coverage_ratio": quality["evidence_coverage_ratio"],
        "evidence_quality_violations": evidence_quality_violations,
        "decision_quality_violations": decision_quality_violations,
        "coverage_incomplete_sections": sorted(coverage_incomplete_sections),
        "output_limit_adjustments": output_limit_adjustments,
    }
    return checked


def render_qualitative_human_report(envelope: Mapping[str, Any], bundle: Mapping[str, Any]) -> str:
    """Render validated model context with clickable evidence references."""

    result = envelope.get("result") if isinstance(envelope.get("result"), Mapping) else envelope
    target = bundle.get("target") if isinstance(bundle.get("target"), Mapping) else {}
    evidence = {
        str(item.get("evidence_id")): item
        for item in bundle.get("evidence_bundle", [])
        if isinstance(item, Mapping) and item.get("evidence_id")
    }
    labels = {"company": "公司與商業模式", "industry": "產業與競爭定位", "governance": "治理與資本配置", "esg": "ESG、法規與地緣政治"}

    def refs(ids: Any) -> str:
        values = ids if isinstance(ids, list) else []
        rendered: list[str] = []
        for evidence_id in values:
            item = evidence.get(str(evidence_id))
            url = item.get("url") if isinstance(item, Mapping) else None
            rendered.append(f"[{evidence_id}]({url})" if url else f"`{evidence_id}`")
        return ", ".join(rendered) or "未提供"

    lines = [
        f"# {target.get('name') or target.get('symbol') or '標的'}｜質化 Research Context",
        "",
        f"> as-of：{result.get('as_of') or bundle.get('as_of')}｜模型：{envelope.get('model') or '未提供'}｜整體狀態：{result.get('overall_status') or '未提供'}",
        "",
        "## 研究摘要",
        "",
        str(result.get("summary") or "本次模型沒有提供總結。"),
        "",
    ]
    sections = result.get("sections") if isinstance(result.get("sections"), Mapping) else {}
    for key in ("company", "industry", "governance", "esg"):
        section = sections.get(key) if isinstance(sections.get(key), Mapping) else {}
        lines.extend([f"## {labels[key]}", "", f"**狀態：** {section.get('status') or 'unresolved'}", "", str(section.get("summary") or "證據不足，無法形成摘要。"), "", "### 可驗證主張", ""])
        claims = section.get("claims") if isinstance(section.get("claims"), list) else []
        if not claims:
            lines.append("- 尚未形成可驗證主張。")
        for claim in claims:
            if not isinstance(claim, Mapping):
                continue
            lines.extend([
                f"- **{claim.get('claim_id') or 'claim'}：** {claim.get('text') or claim.get('claim') or ''}",
                f"  - 類型：{claim.get('type') or 'unresolved'}；信心：{claim.get('confidence') or 'unresolved'}；證據：{refs(claim.get('evidence_ids'))}",
                f"  - 傳導機制：{claim.get('mechanism') or '未提供'}",
                f"  - 證偽條件：{claim.get('falsifier') or '未提供'}",
            ])
        lines.extend(["", "### 盲點", ""])
        lines.extend([f"- {value}" for value in section.get("blind_spots", []) if value] or ["- 未提供。"])
        lines.extend(["", "### 尚缺資料", ""])
        lines.extend([f"- {value}" for value in section.get("missing_evidence", []) if value] or ["- 未提供。"])
        lines.append("")

    synthesis = result.get("cross_section_synthesis") if isinstance(result.get("cross_section_synthesis"), Mapping) else {}
    lines.extend(["## 跨章節傳導與追蹤", ""])
    lines.extend([f"- {value}" for value in synthesis.get("key_mechanisms", []) if value] or ["- 未提供。"])
    lines.extend(["", "### 監測指標", ""])
    for item in synthesis.get("monitoring", []) if isinstance(synthesis.get("monitoring"), list) else []:
        if isinstance(item, Mapping):
            lines.append(f"- **{item.get('kpi')}**｜門檻：{item.get('threshold')}｜期間：{item.get('period')}｜動作：{item.get('trigger_action')}｜證據：{refs(item.get('evidence_ids'))}")
    quality = result.get("quality") if isinstance(result.get("quality"), Mapping) else {}
    lines.extend(["", "## 可稽核品質", "", f"- 主張：{quality.get('claim_count', 0)}；有證據主張：{quality.get('claims_with_evidence', 0)}；證據覆蓋率：{quality.get('evidence_coverage_ratio', 0)}", f"- dangling evidence IDs：{', '.join(quality.get('dangling_evidence_ids', [])) or '無'}", f"- 完整 bundle：`{envelope.get('input_sha256') or '未提供'}`"])
    return "\n".join(lines) + "\n"


def invoke_qualitative_model(
    *,
    endpoint: str,
    model: str,
    system_prompt: str,
    user_payload: Mapping[str, Any],
    timeout_seconds: float = 600.0,
    max_tokens: int = 12000,
    native_ollama: bool = False,
    post: Any = httpx.post,
) -> dict[str, Any]:
    """Call an OpenAI-compatible endpoint once and return the raw envelope."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "USER_PAYLOAD:\n" + json.dumps(user_payload, ensure_ascii=False, sort_keys=True)},
    ]
    if native_ollama:
        request_url = endpoint.rstrip("/") + "/api/chat"
        request_json = {"model": model, "stream": False, "think": False, "options": {"temperature": 0.1, "num_predict": max_tokens}, "messages": messages}
    else:
        request_url = endpoint.rstrip("/") + "/v1/chat/completions"
        request_json = {"model": model, "temperature": 0.1, "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False}, "messages": messages}
    response = post(request_url, json=request_json, timeout=timeout_seconds)
    if response.is_error:
        detail = response.text[:1000]
        raise RuntimeError(f"model endpoint returned HTTP {response.status_code}: {detail}")
    envelope = response.json()
    if not isinstance(envelope, Mapping):
        raise ValueError("model response is not a JSON object")
    if native_ollama:
        message = envelope.get("message") if isinstance(envelope.get("message"), Mapping) else {}
    else:
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ValueError("model response contains no choices")
        message = choices[0].get("message") if isinstance(choices[0].get("message"), Mapping) else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model response contains no text content")
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), Mapping) else {
        "prompt_tokens": envelope.get("prompt_eval_count"),
        "completion_tokens": envelope.get("eval_count"),
        "total_tokens": (int(envelope.get("prompt_eval_count") or 0) + int(envelope.get("eval_count") or 0)) if envelope.get("prompt_eval_count") is not None or envelope.get("eval_count") is not None else None,
        "total_duration_ns": envelope.get("total_duration"),
    }
    return {"response": dict(envelope), "content": content, "usage": usage}


def invoke_opencode_model(
    *,
    model: str,
    system_prompt: str,
    user_payload: Mapping[str, Any],
    timeout_seconds: float = 120.0,
    command: str = DEFAULT_OPENCODE_COMMAND,
    run: Any = subprocess.run,
) -> dict[str, Any]:
    """Call OpenCode's non-interactive CLI and extract its text events.

    OpenCode is treated as a local provider, not as an HTTP endpoint.  Its
    ``--format json`` stream contains progress events and one or more text
    parts; only text parts are sent to the strict JSON parser.  Any CLI error
    or malformed stream is raised so the caller can use the Qwen fallback.
    """

    prompt = (
        f"{system_prompt}\n\n"
        "USER_PAYLOAD:\n"
        + json.dumps(user_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n\n只輸出符合要求的 JSON，不要 Markdown。"
    )
    # OpenCode 1.15.x can inherit a stale user database whose
    # ``session_message.seq`` column rejects the first prompt.  The
    # qualitative overlay is stateless, so use an ephemeral XDG data/state
    # directory and keep the user's desktop history untouched.  ``--pure``
    # also prevents an unrelated optional plugin from changing this batch
    # call's result (the configured morph plugin may be unavailable).
    runtime_root = Path(tempfile.mkdtemp(prefix="finance-crawler-opencode-"))
    env = os.environ.copy()
    env.update(
        {
            "XDG_DATA_HOME": str(runtime_root / "data"),
            "XDG_STATE_HOME": str(runtime_root / "state"),
            "XDG_CACHE_HOME": str(runtime_root / "cache"),
        }
    )
    try:
        completed = run(
            [command, "run", "--model", model, "--format", "json", "--pure", prompt],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
    finally:
        shutil.rmtree(runtime_root, ignore_errors=True)
    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "")
    return_code = int(getattr(completed, "returncode", 0) or 0)
    events: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(event, Mapping):
            continue
        event_dict = dict(event)
        events.append(event_dict)
        if str(event_dict.get("type") or "").casefold() == "error":
            raise RuntimeError(f"OpenCode returned an error event: {event_dict.get('error') or event_dict.get('message') or event_dict}")
        part = event_dict.get("part") if isinstance(event_dict.get("part"), Mapping) else {}
        message = event_dict.get("message") if isinstance(event_dict.get("message"), Mapping) else {}
        for candidate in (event_dict.get("text"), part.get("text"), message.get("content"), event_dict.get("content")):
            if isinstance(candidate, str) and candidate.strip():
                text_parts.append(candidate)
                break
    if return_code != 0:
        detail = stderr.strip() or stdout.strip()[-1000:]
        raise RuntimeError(f"OpenCode exited with code {return_code}: {detail}")
    content = "\n".join(text_parts).strip()
    if not content:
        # A provider may ignore --format json and return plain text.  Let the
        # strict parser decide whether that text is a valid JSON response.
        content = stdout.strip()
    if not content:
        raise ValueError("OpenCode returned no text content")
    return {
        "response": {"events": events, "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest()},
        "content": content,
        "usage": {"provider": "opencode", "event_count": len(events)},
    }
