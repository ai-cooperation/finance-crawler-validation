"""Deterministic paragraph-level release audit for human research reports.

The audit deliberately checks observable failure modes only.  It does not
pretend that a keyword score can establish investment correctness; instead it
prevents known false-L3 paths such as duplicated model payload, generic reading
instructions, stale falsifiers and target/geography mismatches from being
hidden by a long report or a high source count.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Mapping


_BLOCKING_ISSUES = {
    "duplicate_block",
    "generic_process_prose",
    "machine_payload_in_body",
    "stale_falsifier",
    "target_geography_mismatch",
    "raw_format_payload",
    "invalid_markdown_url",
    "internal_requirement_id_in_body",
    "synthetic_research_placeholder",
}

_MACHINE_PATTERNS = (
    re.compile(r"\b(?:company|industry|governance|esg)-\d{3}\b", re.IGNORECASE),
    re.compile(r"類型\s*[／/]\s*信心\s*[／/]\s*證據品質"),
    re.compile(r"\bQ_(?:company|industry|governance|esg)_[a-f0-9]+\b", re.IGNORECASE),
    re.compile(r"\b(?:fact|inference|assumption)/(?:high|medium|low)/(?:direct|corroborated|indirect)\b", re.IGNORECASE),
)

_PROCESS_PHRASES = (
    "讀者應",
    "本章的判讀",
    "本案公司模式的讀法",
    "閱讀時要",
    "不是提醒清單",
    "應先沿著",
    "不能只",
    "要把",
    "模型敘事未通過",
    "已移至質化主張稽核表",
    "本章不能支持評等升級",
    "尚未建立完整的驅動因子",
)

_INTERNAL_REQUIREMENT_PATTERN = re.compile(
    r"\b(?:company|segment|industry|peer|governance|esg)\.[a-z][a-z0-9_]*\b",
    re.IGNORECASE,
)

_SYNTHETIC_RESEARCH_PLACEHOLDERS = (
    "外部新聞線索（待公司／監管原文驗證）",
    "財報與法說更新",
    "資本支出或產能指引",
    "產業需求與價格變化",
)

_MATERIAL_TERMS = (
    "營收", "收入", "毛利", "營業利益", "現金流", "資本支出", "capex",
    "eps", "估值", "wacc", "成本", "售價", "出貨", "利用率", "市占", "市佔",
)


def _normalize_block(text: str) -> str:
    normalized = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    normalized = re.sub(r"<a\s+[^>]+></a>", "", normalized)
    normalized = re.sub(r"[`*_>#|\-]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _chapter_key(heading: str) -> str:
    match = re.match(r"##\s+([^、\s]+)", heading)
    return str(match.group(1)) if match else "frontmatter"


def _block_type(lines: list[str], *, in_references: bool) -> str:
    if in_references:
        return "reference"
    first = lines[0].lstrip()
    if first.startswith("#"):
        return "heading"
    if all(line.lstrip().startswith("|") for line in lines):
        return "table"
    if first.startswith(("- ", "* ", "+ ")) or re.match(r"\d+[.)]\s", first):
        return "list"
    if first.startswith(">"):
        return "callout"
    return "prose"


def _split_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    chapter = "frontmatter"
    chapter_title = "封面與決策摘要"
    in_references = False
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        start = index + 1
        current = [lines[index].rstrip()]
        if lines[index].lstrip().startswith("|"):
            index += 1
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                current.append(lines[index].rstrip())
                index += 1
        elif lines[index].lstrip().startswith(("- ", "* ", "+ ")):
            index += 1
            while index < len(lines) and (
                lines[index].lstrip().startswith(("- ", "* ", "+ "))
                or (lines[index].startswith(("  ", "\t")) and lines[index].strip())
            ):
                current.append(lines[index].rstrip())
                index += 1
        else:
            index += 1
            while index < len(lines) and lines[index].strip() and not lines[index].startswith("#"):
                if lines[index].lstrip().startswith("|") or lines[index].lstrip().startswith(("- ", "* ", "+ ")):
                    break
                current.append(lines[index].rstrip())
                index += 1
        text = "\n".join(current).strip()
        if text.startswith("## "):
            chapter = _chapter_key(text)
            chapter_title = text.removeprefix("## ").strip()
            in_references = chapter_title.startswith("參考來源")
        blocks.append(
            {
                "line_start": start,
                "line_end": start + len(current) - 1,
                "chapter": chapter,
                "chapter_title": chapter_title,
                "block_type": _block_type(current, in_references=in_references),
                "text": text,
            }
        )
    return blocks


def _target_markers(target: Mapping[str, Any]) -> tuple[str, ...]:
    values = [target.get("name"), target.get("symbol"), *(target.get("aliases") or [])]
    return tuple(str(value).casefold() for value in values if str(value or "").strip())


def _has_specific_fact(text: str, target: Mapping[str, Any]) -> bool:
    lower = text.casefold()
    target_match = any(marker in lower for marker in _target_markers(target))
    numeric_values = re.findall(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?", text)
    numeric = bool(numeric_values)
    citation = bool(re.search(r"\[[^\]]+\]\((?:https?://|#ref-)", text))
    material = any(term in lower for term in _MATERIAL_TERMS)
    quantified_material = len(numeric_values) >= 2 and material
    return (target_match and (numeric or citation or material)) or (numeric and citation) or quantified_material


def _as_of_year(as_of: str) -> int | None:
    try:
        return datetime.fromisoformat(str(as_of).replace("Z", "+00:00")).year
    except ValueError:
        match = re.search(r"(20\d{2})", str(as_of))
        return int(match.group(1)) if match else None


def _issues_for_block(block: Mapping[str, Any], *, target: Mapping[str, Any], as_of_year: int | None) -> list[str]:
    text = str(block["text"])
    lower = text.casefold()
    block_type = str(block["block_type"])
    chapter = str(block["chapter"])
    in_audit_area = chapter in {"附錄", "參考來源"} or str(block["chapter_title"]).startswith(("附錄", "參考來源"))
    issues: list[str] = []

    if not in_audit_area and any(pattern.search(text) for pattern in _MACHINE_PATTERNS):
        issues.append("machine_payload_in_body")
    if not in_audit_area and _INTERNAL_REQUIREMENT_PATTERN.search(text):
        issues.append("internal_requirement_id_in_body")
    if not in_audit_area and ("```yaml" in lower or "\\\n" in text or re.search(r"^```", text, flags=re.MULTILINE)):
        issues.append("raw_format_payload")
    if re.search(r"\]\(https?://[^)\n]*\s+[^)\n]*\)", text, flags=re.IGNORECASE):
        issues.append("invalid_markdown_url")
    if block_type in {"prose", "callout"} and not in_audit_area:
        process_hits = sum(phrase in text for phrase in _PROCESS_PHRASES)
        if process_hits and not _has_specific_fact(text, target):
            issues.append("generic_process_prose")
    if not in_audit_area and any(value in text for value in _SYNTHETIC_RESEARCH_PLACEHOLDERS):
        issues.append("synthetic_research_placeholder")
    if not in_audit_area and as_of_year is not None:
        stale_years = [
            int(year)
            for year in re.findall(r"若\s*(20\d{2})", text)
            if int(year) < as_of_year
        ]
        if stale_years and any(token in text for token in ("證偽", "聲明", "主張", "不成立", "推翻")):
            issues.append("stale_falsifier")
    if chapter == "4" and not in_audit_area:
        us_only = bool(re.search(r"(?:美國|U\.S\.|\bUS\b|United States)", text, flags=re.IGNORECASE))
        target_or_exposure = any(marker in lower for marker in _target_markers(target)) or any(
            term in text for term in ("台灣", "亞洲", "全球", "歐洲", "土耳其", "營收曝險", "營運曝險")
        )
        demand_claim = any(term in lower for term in ("需求", "市場", "demand", "consumption", "shipment"))
        if us_only and demand_claim and not target_or_exposure:
            issues.append("target_geography_mismatch")
    return sorted(set(issues))


def _dimension_assessment(block: Mapping[str, Any], issues: list[str], target: Mapping[str, Any]) -> dict[str, str]:
    """Apply the same observable rubric to every block.

    ``not_applicable`` is explicit: a reference row does not need a financial
    transmission chain, but it is still present in the audit inventory.
    """

    block_type = str(block.get("block_type") or "")
    chapter = str(block.get("chapter") or "")
    text = str(block.get("text") or "")
    lower = text.casefold()
    if block_type in {"heading", "table", "reference"}:
        return {
            "target_specificity": "not_applicable",
            "evidence_linkage": "not_applicable" if block_type != "reference" else "pass",
            "quantification_period_unit": "not_applicable",
            "decision_transmission": "not_applicable",
            "temporal_validity": "not_applicable",
            "reader_presentation": "fail" if "duplicate_block" in issues else "pass",
        }
    citation = bool(re.search(r"\[[^\]]+\]\((?:https?://|#ref-)", text) or re.search(r"\[\[\d+\]\]\(#ref-\d+\)", text))
    has_number = bool(re.search(r"\d", text))
    has_period = bool(re.search(r"20\d{2}|Q[1-4]|第[一二三四]季|年度|季度|月", text, flags=re.IGNORECASE))
    has_unit = any(token in lower for token in ("％", "%", "元", "噸", "億", "兆", "倍", "台", "美元", "twd", "usd", "bps"))
    material = any(term in lower for term in _MATERIAL_TERMS)
    causal = any(term in text for term in ("帶動", "影響", "導致", "壓低", "提高", "下修", "上修", "傳導", "情境"))
    target_specific = _has_specific_fact(text, target)
    quantitative_chapter = chapter in {"3", "4", "5", "6", "7", "8", "9", "10", "12", "13", "14"}
    return {
        "target_specificity": "pass" if target_specific else "fail" if "generic_process_prose" in issues else "partial",
        "evidence_linkage": "pass" if citation else "partial" if material or has_number else "not_applicable",
        "quantification_period_unit": (
            "pass" if has_number and has_period and has_unit
            else "partial" if quantitative_chapter and (material or has_number)
            else "not_applicable"
        ),
        "decision_transmission": (
            "pass" if material and causal and target_specific
            else "partial" if chapter in {"2", "3", "4", "5", "10", "12", "13", "14"}
            else "not_applicable"
        ),
        "temporal_validity": "fail" if "stale_falsifier" in issues else "pass" if has_period else "not_applicable",
        "reader_presentation": "fail" if set(issues) & {"duplicate_block", "machine_payload_in_body", "raw_format_payload"} else "pass",
    }


def audit_markdown_report(markdown: str, *, target: Mapping[str, Any], as_of: str) -> dict[str, Any]:
    """Audit every non-empty Markdown block and return a replayable result."""

    blocks = _split_blocks(markdown)
    fingerprints: dict[str, list[int]] = defaultdict(list)
    for index, block in enumerate(blocks):
        if block["block_type"] in {"prose", "list", "callout"}:
            normalized = _normalize_block(str(block["text"]))
            if len(normalized) >= 20:
                fingerprints[hashlib.sha256(normalized.encode("utf-8")).hexdigest()].append(index)
    duplicate_indexes = {
        index
        for indexes in fingerprints.values()
        if len(indexes) > 1
        for index in indexes
    }
    report_year = _as_of_year(as_of)
    paragraphs: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    dimension_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for index, block in enumerate(blocks, start=1):
        issues = _issues_for_block(block, target=target, as_of_year=report_year)
        if index - 1 in duplicate_indexes:
            issues = sorted(set([*issues, "duplicate_block"]))
        issue_counts.update(issues)
        dimensions = _dimension_assessment(block, issues, target)
        for dimension, value in dimensions.items():
            dimension_counts[dimension][value] += 1
        severity = "P0" if any(issue in {"machine_payload_in_body", "stale_falsifier", "target_geography_mismatch", "raw_format_payload", "invalid_markdown_url", "internal_requirement_id_in_body", "synthetic_research_placeholder"} for issue in issues) else "P1" if issues else "pass"
        paragraphs.append(
            {
                "paragraph_id": f"p-{index:04d}",
                **dict(block),
                "content_sha256": hashlib.sha256(str(block["text"]).encode("utf-8")).hexdigest(),
                "issues": issues,
                "dimensions": dimensions,
                "severity": severity,
                "status": "fail" if issues else "pass",
            }
        )
    blocking_count = sum(count for issue, count in issue_counts.items() if issue in _BLOCKING_ISSUES)
    target_id = str(target.get("target_id") or target.get("symbol") or target.get("name") or "target")
    return {
        "schema_version": "paragraph-quality-audit.v1",
        "target_id": target_id,
        "as_of": as_of,
        "report_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "paragraphs": paragraphs,
        "summary": {
            "audited_block_count": len(paragraphs),
            "failed_block_count": sum(item["status"] == "fail" for item in paragraphs),
            "p0_block_count": sum(item["severity"] == "P0" for item in paragraphs),
            "p1_block_count": sum(item["severity"] == "P1" for item in paragraphs),
            "issue_counts": dict(sorted(issue_counts.items())),
            "dimension_counts": {name: dict(sorted(values.items())) for name, values in sorted(dimension_counts.items())},
            "blocking_issue_count": blocking_count,
            "release_ready": blocking_count == 0,
        },
    }


def aggregate_paragraph_audits(audits: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate issue prevalence without allowing paragraph volume to vote."""

    issue_targets: dict[str, set[str]] = defaultdict(set)
    issue_blocks: Counter[str] = Counter()
    for audit in audits:
        target_id = str(audit.get("target_id") or "target")
        seen: set[str] = set()
        for paragraph in audit.get("paragraphs", []):
            if not isinstance(paragraph, Mapping):
                continue
            for issue in paragraph.get("issues", []):
                issue = str(issue)
                issue_blocks[issue] += 1
                seen.add(issue)
        for issue in seen:
            issue_targets[issue].add(target_id)
    target_count = len(audits)
    systemic_threshold = max(1, (target_count + 1) // 2)
    modes = [
        {
            "issue": issue,
            "target_count": len(issue_targets[issue]),
            "target_ratio": round(len(issue_targets[issue]) / target_count, 6) if target_count else 0.0,
            "affected_targets": sorted(issue_targets[issue]),
            "block_count": issue_blocks[issue],
            "systemic": len(issue_targets[issue]) >= systemic_threshold,
        }
        for issue in sorted(issue_targets)
    ]
    return {
        "schema_version": "paragraph-quality-aggregate.v1",
        "target_count": target_count,
        "systemic_threshold": systemic_threshold,
        "failure_modes": modes,
        "systemic_failure_modes": [item["issue"] for item in modes if item["systemic"]],
        "release_ready": not any(item["systemic"] and item["issue"] in _BLOCKING_ISSUES for item in modes),
    }
