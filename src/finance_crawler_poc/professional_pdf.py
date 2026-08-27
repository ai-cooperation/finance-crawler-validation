"""Human-readable equity report renderer.

This is the ReportLab implementation of the approved BTC sample layout.  The
machine report remains canonical; this renderer only changes presentation and
keeps URLs, evidence IDs and unresolved limitations visible to a reader.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


NAVY = colors.HexColor("#17324D")
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#607080")
GOLD = colors.HexColor("#C89211")
PALE_BLUE = colors.HexColor("#EEF5F8")
PALE_GOLD = colors.HexColor("#FFF7E3")
PALE_RED = colors.HexColor("#FFF1F0")
GREEN = colors.HexColor("#1B6E4B")
RED = colors.HexColor("#A33B32")

_FONT_REGULAR = "/Users/user/Library/Fonts/TaipeiSansTCBeta-Regular.ttf"
_FONT_BOLD = "/Users/user/Library/Fonts/TaipeiSansTCBeta-Bold.ttf"


def _register_fonts() -> tuple[str, str]:
    regular, bold = "FinanceTaipei", "FinanceTaipeiBold"
    if regular not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular, _FONT_REGULAR))
    if bold not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold, _FONT_BOLD))
    return regular, bold


def _esc(value: Any) -> str:
    return escape(str(value if value is not None else ""))


def _num(value: Any, digits: int = 2) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "未提供"
    return f"{float(value):,.{digits}f}"


def _pct(value: Any, digits: int = 2) -> str:
    return "未提供" if value is None else f"{_num(value, digits)}％"


def _style(font: str, bold: str) -> dict[str, ParagraphStyle]:
    return {
        "cover_kicker": ParagraphStyle("cover_kicker", fontName=font, fontSize=10, leading=14, textColor=MUTED, alignment=TA_CENTER, spaceAfter=7),
        "cover_title": ParagraphStyle("cover_title", fontName=bold, fontSize=25, leading=32, textColor=NAVY, alignment=TA_CENTER, spaceAfter=8),
        "cover_subtitle": ParagraphStyle("cover_subtitle", fontName=font, fontSize=12, leading=18, textColor=INK, alignment=TA_CENTER),
        "cover_meta": ParagraphStyle("cover_meta", fontName=font, fontSize=8.5, leading=14, textColor=MUTED, alignment=TA_CENTER),
        "heading": ParagraphStyle("heading", fontName=bold, fontSize=17, leading=23, textColor=NAVY, spaceBefore=3 * mm, spaceAfter=3 * mm),
        "subheading": ParagraphStyle("subheading", fontName=bold, fontSize=11.5, leading=16, textColor=NAVY, spaceBefore=2 * mm, spaceAfter=2 * mm),
        "body": ParagraphStyle("body", fontName=font, fontSize=8.8, leading=14, textColor=INK, spaceAfter=2 * mm),
        "small": ParagraphStyle("small", fontName=font, fontSize=7.2, leading=10, textColor=MUTED, spaceAfter=1 * mm),
        "metric": ParagraphStyle("metric", fontName=font, fontSize=7.5, leading=11, textColor=INK),
        "table": ParagraphStyle("table", fontName=font, fontSize=7.1, leading=9.5, textColor=INK),
        "table_head": ParagraphStyle("table_head", fontName=bold, fontSize=7.2, leading=10, textColor=colors.white),
        "claim": ParagraphStyle("claim", fontName=font, fontSize=8.3, leading=13, textColor=INK),
    }


def _footer(font: str):
    def draw(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D6DEE5"))
        canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
        canvas.setFont(font, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm, "標的研究｜證據型專業報告｜research_only")
        canvas.drawRightString(192 * mm, 9 * mm, f"第 {doc.page} 頁")
        canvas.restoreState()
    return draw


def _p(text: Any, styles: Mapping[str, ParagraphStyle], name: str = "body") -> Paragraph:
    return Paragraph(_esc(text), styles[name])


def _metric(label: str, value: str, note: str, styles: Mapping[str, ParagraphStyle]) -> Paragraph:
    return Paragraph(f"<font color='{MUTED.hexval()}' size='7'>{_esc(label)}</font><br/><font name='FinanceTaipeiBold' size='13'>{_esc(value)}</font><br/><font color='{MUTED.hexval()}' size='6.5'>{_esc(note)}</font>", styles["metric"])


def _boxed(title: str, values: list[str], background: colors.Color, styles: Mapping[str, ParagraphStyle]) -> Table:
    rows = [[Paragraph(f"<font name='FinanceTaipeiBold'>{_esc(title)}</font>", styles["claim"])]]
    rows.extend([[Paragraph(f"• {_esc(value)}", styles["claim"])] for value in values[:5]])
    table = Table(rows, colWidths=[168 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6DEE5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _chapter(report: Mapping[str, Any], chapter_id: str) -> Mapping[str, Any]:
    return next((row.get("content", {}) for row in report.get("chapters", []) if str(row.get("id")) == chapter_id), {})


def _sources(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(item.get("url")) for item in values if isinstance(item, Mapping) and item.get("url")]


def _chapter_summary(chapter_id: str, content: Mapping[str, Any]) -> list[str]:
    if chapter_id == "6":
        rows = content.get("annual_periods") or []
        return [f"{row.get('year')} 年營收 {_num(row.get('revenue'))}，營業利益率 {_pct(float(row.get('operating_margin') or 0) * 100)}，自由現金流 {_num(row.get('free_cash_flow'))}。" for row in rows[-5:] if isinstance(row, Mapping)]
    if chapter_id == "7":
        first = (content.get("forecast_periods") or [{}])[0]
        return [f"{name}情境：收入 {_num((first.get('scenarios') or {}).get(name, {}).get('revenue'))}，營業利益率 {_pct(float((first.get('scenarios') or {}).get(name, {}).get('operating_margin') or 0) * 100)}" for name in ("bear", "base", "bull")]
    if chapter_id == "8":
        return [f"{row.get('method')}：保守 {_num((row.get('scenario_values') or {}).get('bear'))}、基準 {_num((row.get('scenario_values') or {}).get('base'))}、樂觀 {_num((row.get('scenario_values') or {}).get('bull'))}" for row in content.get("methods", []) if isinstance(row, Mapping)]
    if chapter_id == "11":
        rows = []
        for row in content.get("social_candidates", []):
            if not isinstance(row, Mapping):
                continue
            rows.append(f"社群原文：{row.get('title')}（{row.get('source_id')}；points {row.get('score') if row.get('score') is not None else '未提供'}；comments {row.get('comments') if row.get('comments') is not None else '未提供'}；原文網址 {row.get('url') or '未提供'}）")
            for comment in row.get("comment_excerpts", [])[:2] if isinstance(row.get("comment_excerpts"), list) else []:
                if isinstance(comment, Mapping) and comment.get("text"):
                    rows.append(f"　留言原文（{comment.get('author') or '匿名'}）：{comment.get('text')}")
        return rows or ["本批次尚未取得可回溯的社群原文；新聞與社群背離維持未判定。"]
    result: list[str] = []
    for key in ("summary", "position", "cycle", "capacity", "governance", "ownership", "policy", "limitations"):
        value = content.get(key)
        if isinstance(value, str) and value:
            result.append(value)
    for key in ("theses", "catalysts", "risks", "monitoring"):
        value = content.get(key)
        if isinstance(value, list):
            for row in value[:5]:
                if isinstance(row, Mapping):
                    result.append("｜".join(str(row.get(field)) for field in ("title", "claim", "event", "risk", "kpi") if row.get(field)))
    if not result:
        result = ["本章內容已保存於機器可讀研究包；此頁只顯示人讀摘要。"]
    return result[:7]


def render_equity_report_pdf(report: Mapping[str, Any], output_path: Path) -> Path:
    """Render a chapter-rich human PDF from one validated report object."""

    regular, bold = _register_fonts()
    styles = _style(regular, bold)
    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    card = report.get("decision_card") if isinstance(report.get("decision_card"), Mapping) else {}
    target_range = card.get("target_range") if isinstance(card.get("target_range"), Mapping) else {}
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    local_name = next((str(alias) for alias in aliases if any("一" <= char <= "鿿" for char in str(alias))), str(target.get("name") or target.get("symbol") or "標的"))
    evidence = report.get("appendix", {}).get("evidence", {}) if isinstance(report.get("appendix"), Mapping) else {}
    evidence_items = evidence.get("items", []) if isinstance(evidence, Mapping) else []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(output_path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=17 * mm, bottomMargin=19 * mm, title=f"{local_name} 專業標的研究報告", author="Evidence Research Pipeline")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=_footer(regular))])
    story: list[Any] = []

    story.extend([Spacer(1, 25 * mm), _p("EVIDENCE RESEARCH REPORT", styles, "cover_kicker"), HRFlowable(width="18%", thickness=2, color=GOLD, spaceBefore=1 * mm, spaceAfter=8 * mm, hAlign="CENTER"), _p(f"{local_name}（{target.get('symbol')}）專業標的研究報告", styles, "cover_title"), _p("基於可稽核 Research Pack 的人讀摘要", styles, "cover_subtitle"), Spacer(1, 9 * mm)])
    cover = Table([[Paragraph("研究定位", styles["table_head"]), _p("research_only｜不是個人化投資建議", styles, "table")], [Paragraph("報告版本", styles["table_head"]), _p(f"{report.get('report_id')}｜{report.get('generated_at')}", styles, "table")]], colWidths=[33 * mm, 135 * mm])
    cover.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), NAVY), ("BACKGROUND", (1, 0), (1, -1), PALE_BLUE), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C7D3DC")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.extend([cover, Spacer(1, 12 * mm), _p(f"標的　{target.get('name')}\n市場　{target.get('market')}｜幣別　{target.get('currency')}\n研究期間　{card.get('horizon', '12 months')}\n研究觀點　{card.get('rating')}｜信心　{card.get('confidence')}", styles, "cover_meta"), PageBreak()])

    story.extend([_p("一頁結論", styles, "heading"), _p(f"本報告對 {local_name} 的研究觀點為「{card.get('rating')}」。末筆市場價格 {_num(card.get('market_price'))} {target.get('currency')}，12 個月加權估值區間 {_num(target_range.get('low'))}–{_num(target_range.get('high'))}，基準值 {_num(target_range.get('base'))}；方法包絡 {_num(target_range.get('method_envelope_low'))}–{_num(target_range.get('method_envelope_high'))}，因此信心列為 {card.get('confidence')}。這是描述與情境分析，不是買進、賣出或持有指示。", styles), Spacer(1, 2 * mm)])
    metrics = Table([[_metric("末筆市場價格", f"{_num(card.get('market_price'))} {target.get('currency')}", str(card.get('market_price_as_of') or "未提供"), styles), _metric("12 個月基準", f"{_num(target_range.get('base'))} {target.get('currency')}", "加權情境，不是統計信賴區間", styles), _metric("潛在空間", _pct(card.get("upside_downside_pct")), "相對末筆市場價格", styles)], [_metric("估值包絡低點", _num(target_range.get('method_envelope_low')), "方法與情境全包絡", styles), _metric("估值包絡高點", _num(target_range.get('method_envelope_high')), "方法分歧需人工覆核", styles), _metric("證據筆數", str(evidence.get("item_count") or len(evidence_items)), "canonical evidence pack", styles)]], colWidths=[56 * mm, 56 * mm, 56 * mm], rowHeights=[21 * mm, 21 * mm])
    metrics.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C7D3DC")), ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.white), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    story.extend([metrics, Spacer(1, 5 * mm), _boxed("決策邊界", ["可支援議題發現、基本面假設、風險盤點與後續研究排序。", "不把社群討論或新聞標題直接升格為事實或因果證據。", "估值加權區間不是保證價格；方法分歧與未解決限制必須一併閱讀。"], PALE_GOLD, styles), PageBreak()])

    exec_summary = report.get("executive_summary") if isinstance(report.get("executive_summary"), Mapping) else {}
    theses = exec_summary.get("theses") if isinstance(exec_summary.get("theses"), list) else []
    catalysts = exec_summary.get("catalysts") if isinstance(exec_summary.get("catalysts"), list) else []
    risks = exec_summary.get("risks") if isinstance(exec_summary.get("risks"), list) else []
    story.extend([_p("研究論證", styles, "heading"), _boxed("看多論證", [str(row.get("claim") or row.get("title")) for row in theses], PALE_BLUE, styles), Spacer(1, 3 * mm), _boxed("候選催化劑", [str(row.get("event") or row.get("mechanism")) for row in catalysts], PALE_BLUE, styles), Spacer(1, 3 * mm), _boxed("主要風險", [str(row.get("risk") or row.get("thesis_link")) for row in risks], PALE_RED, styles), PageBreak()])

    for row in report.get("chapters", []):
        chapter_id = str(row.get("id"))
        title = str(row.get("title") or f"研究章節 {chapter_id}")
        content = row.get("content") if isinstance(row.get("content"), Mapping) else {}
        story.extend([_p(f"{chapter_id}｜{title}", styles, "heading")])
        for line in _chapter_summary(chapter_id, content):
            story.append(_p(line, styles))
        urls = _sources(content.get("sources"))
        if urls:
            story.append(_p("可驗證來源：" + "；".join(urls[:3]), styles, "small"))
        if chapter_id in {"4", "8", "11", "12"}:
            story.append(Spacer(1, 2 * mm))
        if chapter_id in {"5", "9", "10", "11"}:
            story.append(PageBreak())

    story.extend([_p("證據附錄與稽核收據", styles, "heading"), _p("下表把人讀結論對回可回溯的證據項目。完整原始回應、response hash、計算輸入與程式版本保留在同批機器可讀附件。", styles)])
    rows = [[Paragraph("Evidence ID", styles["table_head"]), Paragraph("來源", styles["table_head"]), Paragraph("標題／用途", styles["table_head"]), Paragraph("URL", styles["table_head"])]]
    for item in evidence_items[:80]:
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("canonical_url") or "")
        rows.append([_p(str(item.get("item_id") or "")[:12], styles, "table"), _p(str(item.get("source_id") or item.get("publisher_id") or "provider"), styles, "table"), _p(str(item.get("title") or item.get("summary") or "")[:160], styles, "table"), _p(url, styles, "table")])
    table = Table(rows, colWidths=[23 * mm, 32 * mm, 70 * mm, 43 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C7D3DC")), ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E0E6EA")), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE_BLUE]), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    gate_labels = {"identity": "標的身分", "financial_model": "財務模型", "valuation": "估值", "evidence": "證據覆蓋", "audit": "稽核軌跡", "qualitative_research": "質化研究"}
    gate_status = {"pass": "合格", "partial": "需補強", "fail": "不合格"}
    gates = report.get("quality_gates") if isinstance(report.get("quality_gates"), Mapping) else {}
    gate_summary = "、".join(f"{gate_labels.get(str(key), str(key))}{gate_status.get(str(value), str(value))}" for key, value in gates.items()) or "未提供"
    unresolved = report.get("appendix", {}).get("unresolved", []) if isinstance(report.get("appendix"), Mapping) else []
    unresolved_text = "、".join("未取得可驗證的社群原文" if str(value) == "social_narrative_source_unavailable" else str(value) for value in unresolved) or "無"
    story.extend([table, Spacer(1, 4 * mm), _p(f"品質閘：{gate_summary}；未解決限制：{unresolved_text}", styles, "small")])
    doc.build(story)
    return output_path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    report_path = root / "output/targets/tsmc/tsmc-2330tw-research-report.json"
    output_path = root / "output/targets/tsmc/tsmc-2330tw-professional-report.pdf"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(render_equity_report_pdf(report, output_path))


if __name__ == "__main__":
    main()
