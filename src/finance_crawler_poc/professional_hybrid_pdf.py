"""Full professional equity report renderer.

This is deliberately different from the compact playbook renderer.  It keeps
the complete research narrative and model tables from the Markdown report,
while borrowing the ESG reference report's visual hierarchy: numbered
chapters, summary cards, gap/priority tables, staged actions, and a final
one-page meeting card.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
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

from finance_crawler_poc.research_playbook_pdf import (
    BLUE,
    BEIGE,
    GOLD,
    GREEN,
    INK,
    LINE,
    MUTED,
    PALE_BLUE,
    PALE_GREEN,
    PALE_RED,
    RED,
    TEAL,
    TEAL_DARK,
    _chapter,
    _esc,
    _num,
    _p,
    _register_fonts,
    _social_rows,
    _table,
)


def _styles(font: str, bold: str) -> dict[str, ParagraphStyle]:
    return {
        "cover_kicker": ParagraphStyle("hy_cover_kicker", fontName=font, fontSize=10, leading=14, textColor=MUTED, alignment=TA_CENTER),
        "cover_title": ParagraphStyle("hy_cover_title", fontName=bold, fontSize=24, leading=31, textColor=TEAL_DARK, alignment=TA_CENTER),
        "cover_subtitle": ParagraphStyle("hy_cover_subtitle", fontName=font, fontSize=11.2, leading=17, textColor=INK, alignment=TA_CENTER),
        "cover_meta": ParagraphStyle("hy_cover_meta", fontName=font, fontSize=8.2, leading=13, textColor=MUTED, alignment=TA_CENTER),
        "heading": ParagraphStyle("hy_heading", fontName=bold, fontSize=18, leading=24, textColor=TEAL_DARK, spaceBefore=2 * mm, spaceAfter=3 * mm),
        "subheading": ParagraphStyle("hy_subheading", fontName=bold, fontSize=11.5, leading=16, textColor=TEAL_DARK, spaceBefore=1.5 * mm, spaceAfter=2 * mm),
        "body": ParagraphStyle("hy_body", fontName=font, fontSize=8.5, leading=13.2, textColor=INK, spaceAfter=1.7 * mm),
        "small": ParagraphStyle("hy_small", fontName=font, fontSize=7, leading=9.5, textColor=MUTED, spaceAfter=1 * mm),
        "tiny": ParagraphStyle("hy_tiny", fontName=font, fontSize=6.1, leading=8.1, textColor=INK),
        "tiny_head": ParagraphStyle("hy_tiny_head", fontName=bold, fontSize=6.2, leading=8.2, textColor=colors.white),
        "table": ParagraphStyle("hy_table", fontName=font, fontSize=7, leading=9.2, textColor=INK),
        "table_head": ParagraphStyle("hy_table_head", fontName=bold, fontSize=7, leading=9.2, textColor=colors.white),
        "box_title": ParagraphStyle("hy_box_title", fontName=bold, fontSize=9.5, leading=13, textColor=TEAL_DARK, spaceAfter=1 * mm),
        "box_body": ParagraphStyle("hy_box_body", fontName=font, fontSize=8.1, leading=12, textColor=INK),
        "metric": ParagraphStyle("hy_metric", fontName=font, fontSize=7.2, leading=10.5, textColor=INK),
        "metric_value": ParagraphStyle("hy_metric_value", fontName=bold, fontSize=13, leading=16, textColor=TEAL_DARK),
        "quick": ParagraphStyle("hy_quick", fontName=font, fontSize=7.3, leading=9.4, textColor=INK),
        "quick_head": ParagraphStyle("hy_quick_head", fontName=bold, fontSize=7.4, leading=9.5, textColor=colors.white),
    }


def _rich(text: str, styles: Mapping[str, ParagraphStyle], style: str = "body") -> Paragraph:
    return Paragraph(text, styles[style])


def _chapter_title(number: str, title: str, styles: Mapping[str, ParagraphStyle]) -> Table:
    heading = Paragraph(f"<b>{_esc(number)}、{_esc(title)}</b>", styles["heading"])
    table = Table([[heading]], colWidths=[174 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 3, TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _box(title: str, lines: Sequence[str], background: colors.Color, styles: Mapping[str, ParagraphStyle], accent: colors.Color = TEAL) -> Table:
    rows: list[list[Any]] = [[_p(title, styles, "box_title")]]
    rows.extend([[_rich(f"<font color='{accent.hexval()}'>●</font> {_esc(line)}", styles, "box_body")] for line in lines])
    table = Table(rows, colWidths=[174 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _metric(label: str, value: str, note: str, styles: Mapping[str, ParagraphStyle]) -> Table:
    rows = [[_p(label, styles, "small")], [_p(value, styles, "metric_value")], [_p(note, styles, "small")]]
    table = Table(rows, colWidths=[54 * mm], rowHeights=[6 * mm, 8 * mm, 6 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


def _money(value: Any, digits: int = 2) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        return "未提供"
    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e12:
        return f"{sign}{value / 1e12:,.{digits}f}兆"
    if value >= 1e8:
        return f"{sign}{value / 1e8:,.{digits}f}億"
    if value >= 1e4:
        return f"{sign}{value / 1e4:,.{digits}f}萬"
    return f"{sign}{value:,.{digits}f}"


def _rate(value: Any, digits: int = 2) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        return "未提供"
    return f"{float(value) * 100:,.{digits}f}%"


def _source_label(item: Mapping[str, Any]) -> str:
    publisher = str(item.get("publisher") or item.get("source_id") or item.get("group") or "來源")
    publisher = {"yahoo_finance": "Yahoo Finance", "hacker_news": "Hacker News", "hacker_news_tsmc_api": "Hacker News", "sec_edgar": "SEC EDGAR", "google_news": "Google News"}.get(publisher, publisher)
    url = str(item.get("url") or item.get("canonical_url") or "")
    if publisher == "來源" and url:
        publisher = urlparse(url).netloc or publisher
    return f"{publisher}｜{_short_url(url)}" if url else publisher


def _short_url(url: str, limit: int = 62) -> str:
    """Keep human tables readable without presenting a fake truncated URL."""
    if not url:
        return "未提供"
    parsed = urlparse(url)
    label = parsed.netloc + parsed.path
    if parsed.query:
        return f"{parsed.netloc} API（完整連結）"
    if len(label) <= limit:
        return label
    # Long SEC accession paths are not safe to abbreviate: the visible
    # prefix can be copied as a different, invalid endpoint.  The full URL
    # remains in the machine-readable evidence appendix.
    return f"{parsed.netloc}（完整連結）"


def _human_level(value: Any) -> str:
    return {"high": "高", "medium": "中", "low": "低", "unresolved": "待確認", "unknown": "待確認"}.get(str(value), str(value) if value not in (None, "") else "未提供")


def _source_note(content: Mapping[str, Any], styles: Mapping[str, ParagraphStyle], limit: int = 2) -> Paragraph | None:
    values = content.get("sources")
    if not isinstance(values, list):
        return None
    labels = [_source_label(item) for item in values if isinstance(item, Mapping)]
    if not labels:
        return None
    return _p("來源：" + "；".join(labels[:limit]), styles, "small")


def _bullet_lines(values: Sequence[Any], fields: Sequence[str] = ()) -> list[str]:
    lines: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            text = "｜".join(str(value.get(field)) for field in fields if value.get(field) not in (None, ""))
            lines.append(text or str(value))
        else:
            lines.append(str(value))
    return lines


def _footer(font: str):
    def draw(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
        canvas.setFont(font, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm, "台積電（2330.TW）專業個股研究報告｜研究用途")
        canvas.drawRightString(192 * mm, 9 * mm, f"第 {doc.page} 頁")
        canvas.restoreState()
    return draw


def _thesis_cards(theses: Sequence[Mapping[str, Any]], styles: Mapping[str, ParagraphStyle]) -> list[Any]:
    output: list[Any] = []
    for index, thesis in enumerate(theses, start=1):
        title = str(thesis.get("title") or f"研究論點 {index}")
        lines = [
            str(thesis.get("claim") or ""),
            f"傳導機制：{thesis.get('mechanism') or '未提供'}",
            f"追蹤 KPI：{thesis.get('kpi') or '未提供'}",
            f"證偽條件：{thesis.get('falsifier') or '未提供'}｜信心：{_human_level(thesis.get('confidence'))}",
        ]
        output.extend([_box(f"論點 {index}｜{title}", lines, PALE_BLUE, styles, accent=TEAL), Spacer(1, 2 * mm)])
    return output


def _render_quick_card(report: Mapping[str, Any], styles: Mapping[str, ParagraphStyle]) -> Table:
    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    card = report.get("decision_card") if isinstance(report.get("decision_card"), Mapping) else {}
    rng = card.get("target_range") if isinstance(card.get("target_range"), Mapping) else {}
    questions = [
        "先定義研究目的與目前觀點", "確認標的、市場、幣別與 as-of", "檢查財務趨勢與盈餘品質",
        "拆解公司與產業位置", "用官方／監管來源交叉驗證", "固定 forecast 假設與 lineage",
        "檢查估值方法是否收斂", "界定事件研究能否支持因果", "讀社群原文但不升格為事實",
        "判斷新聞與社群是否有可比 claim", "寫出重跑觸發器與反證條件",
    ]
    rows: list[list[Any]] = [[_rich("<font color='white'><b>研究會議速查卡（可單獨列印）</b></font>", styles, "subheading")]]
    rows.append([_box("核心目標", ["先判定研究可信度，再決定是否進入高階模型二次推理；不是直接下單。"], PALE_BLUE, styles)])
    local_name = next((str(alias) for alias in target.get("aliases", []) if isinstance(alias, str) and any("一" <= char <= "鿿" for char in alias)), str(target.get("name") or "標的"))
    rating = "審慎" if str(card.get("rating")) == "Cautious" else str(card.get("rating") or "未提供")
    rows.append([_box("本次標的", [f"{local_name}（{target.get('symbol')}）｜目前觀點 {rating}｜信心 {card.get('confidence')}｜基準估值 {_num(rng.get('base'))}。"], PALE_GREEN, styles, accent=GREEN)])
    question_rows = [["順序", "必問問題"]] + [[str(i), q] for i, q in enumerate(questions, start=1)]
    rows.append([_table(question_rows, [14 * mm, 154 * mm], styles)])
    rows.append([_box("絕對不要做", ["把社群討論直接當事實或共識。", "把 weighted target 當成保證價格。", "把描述性事件研究寫成因果。", "把來源失敗從稽核紀錄刪掉。"], PALE_RED, styles, accent=RED)])
    rows.append([_box("四句必說", ["這份報告先維持研究用途，不直接轉成交易指令。", "估值區間是決策帶，不是統計信賴區間。", "社群只作線索，沒有可比 claim 不判定背離。", "來源失敗要保留在稽核紀錄，不可寫成沒有討論。"], BEIGE, styles, accent=GOLD)])
    table = Table(rows, colWidths=[174 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.2, TEAL), ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def render_professional_hybrid_pdf(report: Mapping[str, Any], output_path: Path) -> Path:
    """Render the complete report with ESG-style hierarchy and playbook appendix."""

    regular, bold = _register_fonts()
    styles = _styles(regular, bold)
    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    card = report.get("decision_card") if isinstance(report.get("decision_card"), Mapping) else {}
    rng = card.get("target_range") if isinstance(card.get("target_range"), Mapping) else {}
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    local_name = next((str(a) for a in aliases if any("一" <= char <= "鿿" for char in str(a))), str(target.get("name") or target.get("symbol") or "標的"))
    appendix = report.get("appendix") if isinstance(report.get("appendix"), Mapping) else {}
    evidence = appendix.get("evidence") if isinstance(appendix.get("evidence"), Mapping) else {}
    run_metadata = appendix.get("run_metadata") if isinstance(appendix.get("run_metadata"), Mapping) else {}
    unresolved = appendix.get("unresolved") if isinstance(appendix.get("unresolved"), list) else []
    exec_summary = report.get("executive_summary") if isinstance(report.get("executive_summary"), Mapping) else {}
    theses = exec_summary.get("theses") if isinstance(exec_summary.get("theses"), list) else []
    catalysts = exec_summary.get("catalysts") if isinstance(exec_summary.get("catalysts"), list) else []
    risks = exec_summary.get("risks") if isinstance(exec_summary.get("risks"), list) else []
    ch3, ch4, ch5, ch6 = (_chapter(report, str(i)) for i in ("3", "4", "5", "6"))
    ch7, ch8, ch9, ch10 = (_chapter(report, str(i)) for i in ("7", "8", "9", "10"))
    ch11, ch12, ch13, ch14 = (_chapter(report, str(i)) for i in ("11", "12", "13", "14"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(output_path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=15 * mm, bottomMargin=19 * mm,
        title=f"{local_name}（{target.get('symbol')}）專業個股研究報告", author="Evidence Research Pipeline",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="professional", frames=[frame], onPage=_footer(regular))])
    story: list[Any] = []

    # Cover - keep the formal report name; the playbook is only an appendix.
    story.extend([
        Spacer(1, 17 * mm), _p("EQUITY RESEARCH REPORT", styles, "cover_kicker"),
        HRFlowable(width="19%", thickness=2.2, color=GOLD, spaceBefore=3 * mm, spaceAfter=8 * mm, hAlign="CENTER"),
        _p(f"{local_name}（{target.get('symbol')}）\n專業個股研究報告", styles, "cover_title"),
        Spacer(1, 3 * mm), _p("投資研究與可稽核第二意見", styles, "cover_subtitle"),
        Spacer(1, 11 * mm),
        _p(f"報告日期　{str(report.get('generated_at') or '未提供')[:10]}\n資料截止　{str(card.get('market_price_as_of') or '未提供')[:10]}\n市場／幣別　{target.get('market')}／{target.get('currency')}\n研究期間　{card.get('horizon', '12 months')}\n研究定位　研究用途，不構成個人化投資建議", styles, "cover_meta"),
        Spacer(1, 9 * mm),
    ])
    cover_rating = "審慎" if str(card.get("rating")) == "Cautious" else str(card.get("rating") or "未提供")
    cover_metrics = Table([[
        _metric("目前研究觀點", cover_rating, f"信心 {card.get('confidence') or '未提供'}", styles),
        _metric("末筆市場價格", f"{_num(card.get('market_price'))} {target.get('currency')}", str(card.get("market_price_as_of") or "未提供")[:10], styles),
        _metric("12 個月基準", f"{_num(rng.get('base'))} {target.get('currency')}", f"區間 {_num(rng.get('low'))}-{_num(rng.get('high'))}", styles),
    ]], colWidths=[56 * mm, 56 * mm, 56 * mm])
    cover_metrics.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    story.extend([cover_metrics, Spacer(1, 8 * mm), _box("報告範圍與限制", [f"Evidence Pack：{evidence.get('item_count', '未提供')} 筆證據、{evidence.get('canonical_story_count', '未提供')} 個 canonical stories、{evidence.get('source_group_count', '未提供')} 個來源群。", "本報告整合官方／監管、財務、市場、新聞與公開社群資料；社群只作線索，不升格為事實。", "估值區間是決策帶，不是統計信賴區間；目前估值方法分歧與事件因果限制必須一併閱讀。"], BEIGE, styles, accent=GOLD), Spacer(1, 9 * mm), HRFlowable(width="19%", thickness=2.2, color=GOLD, spaceBefore=2 * mm, spaceAfter=5 * mm, hAlign="CENTER"), _p("報告識別：" + str(report.get("report_id") or "未提供"), styles, "cover_meta"), PageBreak()])

    # 1. Decision summary
    story.extend([_chapter_title("摘要", "決策摘要｜評等結果摘要與核心結論", styles), Spacer(1, 2 * mm), _p("本頁保留正式研究報告的結論密度，使用 ESG 報告的摘要卡與比例呈現，讓讀者在進入模型前先知道研究範圍與決策邊界。", styles)])
    summary_metrics = Table([[
        _metric("研究觀點", "審慎" if str(card.get("rating")) == "Cautious" else str(card.get("rating") or "未提供"), f"信心 {card.get('confidence') or '未提供'}", styles),
        _metric("基準潛在空間", f"{float(card.get('upside_downside_pct')):,.2f}%" if isinstance(card.get("upside_downside_pct"), (int, float)) else "未提供", "相對末筆市場價格", styles),
        _metric("方法分歧", f"{float(ch8.get('method_dispersion_pct')):,.1f}%" if isinstance(ch8.get("method_dispersion_pct"), (int, float)) else "未提供", "估值信心列為低", styles),
    ]], colWidths=[56 * mm, 56 * mm, 56 * mm])
    summary_metrics.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    story.extend([summary_metrics, Spacer(1, 4 * mm), _box("研究結論", ["收入與 AI／HPC 需求仍支持高成長假設，但價格已提前反映部分樂觀情境。", f"基準估值 {_num(rng.get('base'))}，相對市場價格 {_num(card.get('market_price'))}；方法包絡 {_num(rng.get('method_envelope_low'))}-{_num(rng.get('method_envelope_high'))}，不可解讀為單一精確合理價。", "目前最重要的驗證不是再增加敘事，而是持續核對營收、毛利率、資本支出、自由現金流與重大事件。"], PALE_BLUE, styles), Spacer(1, 3 * mm), _box("核心催化劑", [f"{item.get('event')}｜{item.get('mechanism')}｜機率 {_human_level(str(item.get('probability') or '').split('；')[0])}（{str(item.get('probability') or '').split('；')[1] if '；' in str(item.get('probability') or '') else '定期驗證'}）" for item in catalysts if isinstance(item, Mapping)], PALE_GREEN, styles, accent=GREEN), Spacer(1, 3 * mm), _box("主要風險", [f"{item.get('risk')}｜機率 {_human_level(item.get('probability'))}／影響 {_human_level(item.get('impact'))}｜領先指標 {item.get('leading_indicator')}" for item in risks if isinstance(item, Mapping)], PALE_RED, styles, accent=RED), PageBreak()])

    # 2. Basis and scope
    story.extend([_chapter_title("一", "報告依據、資料範圍與方法限制", styles), Spacer(1, 2 * mm), _box("研究範圍", ["本報告將標的身分、官方／監管文件、財務歷史、市場時間序列、估值模型、新聞與公開社群分層處理。", "機器可讀 Research Pack 是數字與證據的來源；本 PDF 只負責把已保存內容轉成可讀的研究敘事。"], PALE_BLUE, styles), Spacer(1, 3 * mm)])
    scope_rows = [["資料層", "本次納入", "用途與限制"], ["官方／監管", "SEC EDGAR、公司申報、公開指引", "事實層與 guidance lineage；不等於市場共識"], ["財務／市場", f"{len(ch6.get('annual_periods', []))} 年度、{len(ch6.get('quarterly_periods', []))} 季、{ch9.get('time_series', {}).get('point_count', '未提供')} 個價格點", "計算趨勢、盈餘品質與回報；先對齊 as-of"], ["估值／情境", f"{len(ch8.get('methods', []))} 種方法、3 年 bear/base/bull", "情境是決策帶，不是信賴區間"], ["新聞／社群", f"{ch11.get('canonical_story_count', '未提供')} stories、{len(_social_rows(report))} 筆社群原文", "社群是待查證線索；沒有可比 claim 不判定背離"]]
    story.extend([_table(scope_rows, [34 * mm, 55 * mm, 85 * mm], styles), Spacer(1, 4 * mm), _p("品質檢查摘要", styles, "subheading")])
    gates = report.get("quality_gates") if isinstance(report.get("quality_gates"), Mapping) else {}
    gate_rows = [["檢查項目", "狀態", "讀者解讀"]]
    for key, value in gates.items():
        label = {"identity": "標的身分", "financial_model": "財務模型", "valuation": "估值", "evidence": "證據覆蓋", "audit": "稽核軌跡", "qualitative_research": "質化研究"}.get(str(key), str(key))
        status = {"pass": "合格", "partial": "部分合格", "fail": "不合格"}.get(str(value), str(value))
        gate_rows.append([label, status, "可進入本章分析" if status == "合格" else "需補強後再升級結論"])
    story.extend([_table(gate_rows, [42 * mm, 32 * mm, 100 * mm], styles), Spacer(1, 3 * mm), _box("限制與責任", ["本報告是可稽核的研究第二意見，不是個人化投資建議。", "估值與預測是明列假設的模型結果，不是公司指引或市場共識。", "事件研究保留描述性定位；若 causal status 未解決，不寫成因果。", "完整 URL、response hash、計算輸入與程式版本保存在同批附件。"], BEIGE, styles, accent=GOLD), PageBreak()])

    # 3. Investment thesis and company model
    story.extend([_chapter_title("二", "投資論點與差異觀點", styles), Spacer(1, 2 * mm), *_thesis_cards([x for x in theses if isinstance(x, Mapping)], styles), PageBreak(), _chapter_title("三", "公司與商業模式", styles), Spacer(1, 2 * mm), _p(str(ch3.get("summary") or ""), styles), _box("營運規模", [str(ch3.get("scale") or "未提供")], PALE_BLUE, styles), Spacer(1, 3 * mm)])
    mix_rows = [["平台組合", "比重", "地區組合", "比重"]]
    platforms = ch3.get("platform_mix") if isinstance(ch3.get("platform_mix"), Mapping) else {}
    geographies = ch3.get("geography_mix") if isinstance(ch3.get("geography_mix"), Mapping) else {}
    for index in range(max(len(platforms), len(geographies))):
        p = list(platforms.items())[index] if index < len(platforms) else ("", "")
        g = list(geographies.items())[index] if index < len(geographies) else ("", "")
        mix_rows.append([p[0], _rate(p[1]) if isinstance(p[1], (int, float)) else str(p[1]), g[0], _rate(g[1]) if isinstance(g[1], (int, float)) else str(g[1])])
    story.extend([_table(mix_rows, [38 * mm, 24 * mm, 70 * mm, 42 * mm], styles), Spacer(1, 3 * mm), _box("護城河、收入與成本", ["護城河：" + "、".join(str(x) for x in ch3.get("moat", [])), "收入驅動：" + "、".join(str(x) for x in ch3.get("revenue_drivers", [])), "成本驅動：" + "、".join(str(x) for x in ch3.get("cost_drivers", []))], PALE_GREEN, styles, accent=GREEN), PageBreak()])

    # 4. Industry and governance
    story.extend([_chapter_title("四", "產業與競爭定位", styles), Spacer(1, 2 * mm), _box("市場位置與循環", [str(ch4.get("position") or ""), str(ch4.get("cycle") or ""), str(ch4.get("capacity") or "")], PALE_BLUE, styles), Spacer(1, 3 * mm)])
    peers = ch4.get("peer_set") if isinstance(ch4.get("peer_set"), list) else []
    peer_rows = [["同業", "價格", "EPS", "P/E", "幣別／期間", "資料狀態"]]
    for peer in peers:
        if not isinstance(peer, Mapping):
            continue
        price = peer.get("price")
        eps = peer.get("eps")
        pe = peer.get("pe") if peer.get("pe") is not None else (float(price) / float(eps) if isinstance(price, (int, float)) and isinstance(eps, (int, float)) and eps > 0 else None)
        peer_rows.append([str(peer.get("symbol") or ""), _num(price), _num(eps), _num(pe), f"{peer.get('currency')}/{peer.get('period_key')}", str(peer.get("fundamentals_status") or "未提供")])
    story.extend([_table(peer_rows, [30 * mm, 27 * mm, 24 * mm, 24 * mm, 38 * mm, 31 * mm], styles), Spacer(1, 4 * mm), _chapter_title("五", "管理層、治理與資本配置", styles), Spacer(1, 2 * mm), _box("治理判斷", [str(ch5.get("governance") or "未提供")], PALE_BLUE, styles), Spacer(1, 2 * mm), _box("資本配置與持股", [str(ch5.get("capital_allocation") or "未提供"), str(ch5.get("ownership") or "未提供")], BEIGE, styles, accent=GOLD), PageBreak()])

    # 5. Historical finance
    story.extend([_chapter_title("六", "歷史財務與盈餘品質", styles), Spacer(1, 2 * mm), _p("五年年度趨勢", styles, "subheading")])
    annual_rows = [["年度", "營收", "毛利率", "營業利益率", "EPS", "自由現金流"]]
    for row in ch6.get("annual_periods", []):
        if isinstance(row, Mapping):
            annual_rows.append([str(row.get("year") or row.get("period_key") or ""), _money(row.get("revenue")), _rate(row.get("gross_margin")), _rate(row.get("operating_margin")), _num(row.get("eps")), _money(row.get("free_cash_flow"))])
    story.extend([_table(annual_rows, [22 * mm, 32 * mm, 27 * mm, 31 * mm, 23 * mm, 39 * mm], styles), Spacer(1, 3 * mm), _p("截至最新資料的八季", styles, "subheading")])
    quarterly_rows = [["季度", "營收", "營業利益率", "淨利", "EPS", "自由現金流"]]
    for row in ch6.get("quarterly_periods", []):
        if isinstance(row, Mapping):
            quarterly_rows.append([str(row.get("period") or row.get("period_key") or ""), _money(row.get("revenue")), _rate(row.get("operating_margin")), _money(row.get("net_income")), _num(row.get("eps")), _money(row.get("free_cash_flow"))])
    story.extend([_table(quarterly_rows, [25 * mm, 31 * mm, 31 * mm, 31 * mm, 24 * mm, 32 * mm], styles), Spacer(1, 3 * mm), _box("盈餘品質", [f"CFO／淨利：{_num((ch6.get('annual_periods') or [{}])[-1].get('cash_conversion'))} 倍；自由現金流率：{_rate((ch6.get('annual_periods') or [{}])[-1].get('free_cash_flow_margin'))}。", "自由現金流以營業現金流減資本支出絕對值計算；期間完整性與資產負債勾稽保存在附錄。"], PALE_GREEN, styles, accent=GREEN), PageBreak()])

    # 6. Forecast and valuation
    story.extend([_chapter_title("七", "財務預測與關鍵假設", styles), Spacer(1, 2 * mm), _box("假設總表", [f"收入成長基準：{_rate((ch7.get('assumptions') or {}).get('revenue_growth', {}).get('base'))}；歷史 CAGR：{_rate((ch7.get('assumptions') or {}).get('revenue_growth', {}).get('historical_cagr'))}。", f"營業利益率基準：{_rate((ch7.get('assumptions') or {}).get('operating_margin', {}).get('base'))}；CAPEX／營收：{_rate((ch7.get('assumptions') or {}).get('capital_intensity', {}).get('base'))}。", f"CFO／淨利：{_num((ch7.get('assumptions') or {}).get('cash_conversion', {}).get('base'))} 倍；稅率：{_rate((ch7.get('assumptions') or {}).get('tax_rate', {}).get('base'))}。", str(ch7.get("limitations") or "")], PALE_BLUE, styles), Spacer(1, 4 * mm), _p("三年情境預測", styles, "subheading")])
    forecast_rows = [["年度", "情境", "營收", "成長率", "營業利益率", "EPS", "自由現金流"]]
    for period in ch7.get("forecast_periods", []):
        if not isinstance(period, Mapping):
            continue
        for scenario in ("bear", "base", "bull"):
            row = (period.get("scenarios") or {}).get(scenario) or {}
            forecast_rows.append([str(period.get("year") or ""), {"bear": "保守", "base": "基準", "bull": "樂觀"}[scenario], _money(row.get("revenue")), _rate(row.get("revenue_growth")), _rate(row.get("operating_margin")), _num(row.get("eps")), _money(row.get("free_cash_flow"))])
    story.extend([_table(forecast_rows, [18 * mm, 20 * mm, 30 * mm, 25 * mm, 31 * mm, 23 * mm, 27 * mm], styles), _source_note(ch7, styles) or Spacer(1, 0), PageBreak()])

    story.extend([_chapter_title("八", "估值與敏感度", styles), Spacer(1, 2 * mm), _box("估值判讀", [f"綜合估值區間：{_num(rng.get('low'))}-{_num(rng.get('high'))} TWD，基準 {_num(rng.get('base'))}。", f"方法包絡：{_num(rng.get('method_envelope_low'))}-{_num(rng.get('method_envelope_high'))} TWD；方法分歧 {float(ch8.get('method_dispersion_pct')):,.2f}%。", "加權估值是決策用區間，不是統計信賴區間；DCF 使用保守 FCFE proxy，forward P/E 作交叉驗證。"], PALE_RED, styles, accent=RED), Spacer(1, 4 * mm), _p("估值方法", styles, "subheading")])
    method_rows = [["方法", "保守", "基準", "樂觀", "權重", "狀態"]]
    for method in ch8.get("methods", []):
        if isinstance(method, Mapping):
            values = method.get("scenario_values") or {}
            label = {"dcf_fcfe_proxy": "FCFE proxy 折現", "forward_pe": "前瞻 P/E"}.get(str(method.get("method")), str(method.get("method")))
            method_rows.append([label, _num(values.get("bear")), _num(values.get("base")), _num(values.get("bull")), _rate(method.get("weight")), str(method.get("status") or "未提供")])
    story.extend([_table(method_rows, [42 * mm, 27 * mm, 27 * mm, 27 * mm, 24 * mm, 27 * mm], styles), Spacer(1, 3 * mm), _p("DCF 敏感度", styles, "subheading")])
    sens = ch8.get("sensitivity", {}).get("matrix") if isinstance(ch8.get("sensitivity"), Mapping) else []
    sens_rows = [["折現率", "終值成長率 2%", "終值成長率 3%", "終值成長率 4%"]]
    for rate in (0.09, 0.10, 0.11):
        values = {float(item.get("terminal_growth")): item.get("value_per_share") for item in sens if isinstance(item, Mapping) and float(item.get("discount_rate")) == rate}
        sens_rows.append([_rate(rate), _num(values.get(0.02)), _num(values.get(0.03)), _num(values.get(0.04))])
    story.extend([_table(sens_rows, [40 * mm, 44 * mm, 44 * mm, 44 * mm], styles), Spacer(1, 3 * mm), _source_note(ch8, styles, limit=1) or Spacer(1, 0), PageBreak()])

    # 7. Market, events, and narrative
    story.extend([_chapter_title("九", "市場表現、流動性與持股", styles), Spacer(1, 2 * mm)])
    ts = ch9.get("time_series") if isinstance(ch9.get("time_series"), Mapping) else {}
    returns = ts.get("returns") if isinstance(ts.get("returns"), Mapping) else {}
    latest_volume = (ch9.get("volume") or {}).get("latest") if isinstance(ch9.get("volume"), Mapping) else None
    latest_volume = latest_volume.get("value") if isinstance(latest_volume, Mapping) else latest_volume
    market_rows = [["指標", "本次數值", "讀法"], ["觀測期間", f"{ts.get('window_start', '未提供')} 至 {ts.get('window_end', '未提供')}", "完整時間序列的 as-of 邊界"], ["價格點數", str(ts.get("point_count") or "未提供"), "用於回報、波動與回撤"], ["一年報酬", f"{_num(returns.get('365d_observed_pct'))}%", "與大盤及同業比較，不單獨當成預測"], ["年化波動／最大回撤", f"{_num(ts.get('volatility_annualized_pct'))}%／{_num(ts.get('max_drawdown_pct'))}%", "風險與持有體驗"]]
    story.extend([_table(market_rows, [42 * mm, 60 * mm, 72 * mm], styles), Spacer(1, 3 * mm), _box("持股與流動性", [f"市值：約 {_money(ch9.get('market_cap'))} {target.get('currency')}。", f"末筆成交量：{_num(latest_volume)}。", str(ch9.get('ownership') or "未提供")], PALE_BLUE, styles), Spacer(1, 4 * mm), _chapter_title("十", "催化劑、事件日曆與事件研究", styles), Spacer(1, 2 * mm)])
    event_rows = [["事件", "時間／機率", "傳導機制", "研究邊界"]]
    for item in ch10.get("catalysts", []):
        if isinstance(item, Mapping):
            probability = str(item.get("probability") or "未提供")
            event_rows.append([str(item.get("event") or ""), f"{item.get('window') or '未提供'}／{_human_level(probability.split('；')[0])}", str(item.get("mechanism") or ""), "定期驗證；不保證結果"])
    event_rows.append(["歷史事件研究", f"{(ch10.get('historical_event_alignment') or {}).get('aligned_event_count', '未提供')} 則對齊事件", "benchmark-adjusted return", "描述性；not causal"])
    story.extend([_table(event_rows, [43 * mm, 35 * mm, 65 * mm, 31 * mm], styles), PageBreak()])

    story.extend([_chapter_title("十一", "新聞、社群與敘事背離", styles), Spacer(1, 2 * mm), _box("敘事分層政策", [str(ch11.get("policy") or ""), f"去重後 {ch11.get('canonical_story_count', '未提供')} 個 stories、{ch11.get('source_group_count', '未提供')} 個來源群。標題分類只作詞彙篩選，不是情緒真值。"], PALE_BLUE, styles), Spacer(1, 3 * mm), _p("媒體敘事候選", styles, "subheading")])
    news_rows = [["主題", "標題／候選事件", "狀態"]]
    for item in ch11.get("news_candidates", []):
        if isinstance(item, Mapping):
            news_rows.append([str(item.get("label") or "未分類"), str(item.get("title") or ""), str(item.get("causal_status") or "unresolved")])
    story.extend([_table(news_rows, [38 * mm, 108 * mm, 28 * mm], styles), Spacer(1, 3 * mm), _p("社群原文索引", styles, "subheading")])
    social_rows = [["原文標題", "points／comments", "可回溯 URL"]]
    for item in _social_rows(report):
        social_rows.append([str(item.get("title") or ""), f"{item.get('score') if item.get('score') is not None else '未提供'}／{item.get('comments') if item.get('comments') is not None else '未提供'}", str(item.get("url") or "未提供")])
    story.extend([_table(social_rows, [89 * mm, 35 * mm, 50 * mm], styles), Spacer(1, 3 * mm), _box("背離判定", ["社群只作敘事與情緒線索，未升格為財務證據。", "新聞與社群必須先有同一事件、同一時間窗與可比 claim，才可以判定背離。", "本批次沒有足夠可比 claim，因此背離維持未判定。"], PALE_RED, styles, accent=RED), PageBreak()])

    # 8. Risk and ESG
    story.extend([_chapter_title("十二", "投資風險與 Bear Case", styles), Spacer(1, 2 * mm)])
    risk_rows = [["風險", "機率／影響", "估值敏感度", "領先指標", "緩解因素"]]
    for item in ch12.get("risks", []):
        if isinstance(item, Mapping):
            risk_rows.append([str(item.get("risk") or ""), f"{_human_level(item.get('probability'))}／{_human_level(item.get('impact'))}", str(item.get("valuation_sensitivity") or ""), str(item.get("leading_indicator") or ""), str(item.get("mitigation") or "")])
    story.extend([_table(risk_rows, [32 * mm, 27 * mm, 42 * mm, 42 * mm, 31 * mm], styles), Spacer(1, 4 * mm), _chapter_title("十三", "ESG、法規與地緣政治重大性", styles), Spacer(1, 2 * mm), _box("重大性判定", [str(ch13.get("summary") or ""), "重大性篩選只納入會影響現金流、資本成本、營運許可或護城河的議題。", "需持續追蹤能源、水、碳成本、海外擴產、出口限制與供應鏈韌性。"], PALE_BLUE, styles), Spacer(1, 3 * mm), _table([["重大議題", "投資傳導"], *[[str(topic), "可能影響產能、成本、客戶訂單或資本成本"] for topic in ch13.get("material_topics", [])]], [62 * mm, 112 * mm], styles), PageBreak()])

    # 9. Conclusion and monitoring
    story.extend([_chapter_title("十四", "結論與監測計畫", styles), Spacer(1, 2 * mm), _box("結論", ["目前研究觀點維持審慎，原因不是缺少故事，而是價格、估值方法與事件因果仍存在明顯不確定性。", "若收入、營業利益率與自由現金流持續高於基準，估值可重新上修；若需求、資本支出回收或法規風險惡化，應立即重跑 bear 情境。", "下一次完整複核：財報、法說、重大公告或任一證偽門檻觸發時。"], PALE_BLUE, styles), Spacer(1, 4 * mm), _p("監測計畫", styles, "subheading")])
    monitor_rows = [["KPI／事件", "門檻", "頻率", "觸發動作"]]
    for item in ch14.get("monitoring", []):
        if isinstance(item, Mapping):
            monitor_rows.append([str(item.get("kpi") or ""), str(item.get("threshold") or ""), str(item.get("frequency") or ""), str(item.get("action") or "")])
    story.extend([_table(monitor_rows, [38 * mm, 53 * mm, 31 * mm, 52 * mm], styles), Spacer(1, 4 * mm), _box("研究交接", ["T+0：保存原始回應、摘要、假設 ledger 與報告 hash。", "事件觸發：以相同 target contract、as-of 與模型版本重跑。", "高階模型只讀已標記 evidence，輸出二次推理與反證，不自行補造來源。"], PALE_GREEN, styles, accent=GREEN), PageBreak()])

    # 10. Evidence and audit appendix
    story.extend([_chapter_title("附錄 A", "證據、模型與稽核附錄", styles), Spacer(1, 2 * mm), _box("方法與責任", ["數值由可重播公式產生；自動文字只能解釋已保存的模型與證據，不能補造資料。", "本報告是可稽核的研究第二意見，不是個人化投資建議。", "完整 URL、response hash、計算輸入、程式版本與執行識別保存於同批附件。"], BEIGE, styles, accent=GOLD), Spacer(1, 4 * mm)])
    audit_rows = [["稽核項目", "本次值", "讀者用途"], ["報告識別", str(report.get("report_id") or "未提供"), "鎖定本次報告版本"], ["研究執行識別", str(run_metadata.get("run_id") or "未提供"), "回到同批原始抓取與計算"], ["報告層級", str(report.get("report_level") or "未提供"), "與品質閘結果一併閱讀"], ["Evidence Pack", f"{evidence.get('item_count', '未提供')} 筆／{evidence.get('canonical_story_count', '未提供')} stories", "回到 evidence ID、URL、response hash"], ["來源群", str(evidence.get("source_group_count") or "未提供"), "檢查是否過度集中"], ["未解決限制", "無" if not unresolved else "；".join(str(x) for x in unresolved), "不能從摘要隱藏；補齊後再升級"]]
    story.extend([_table(audit_rows, [39 * mm, 59 * mm, 76 * mm], styles), Spacer(1, 4 * mm), _p("Evidence 索引（節錄）", styles, "subheading")])
    evidence_rows = [["Evidence ID", "來源／出版者", "標題／用途", "URL"]]
    for item in evidence.get("items", [])[:35] if isinstance(evidence.get("items"), list) else []:
        if isinstance(item, Mapping):
            evidence_rows.append([str(item.get("item_id") or "")[:12], str(item.get("publisher_id") or item.get("source_id") or item.get("group") or ""), str(item.get("title") or item.get("summary") or "")[:100], _short_url(str(item.get("canonical_url") or item.get("url") or ""), limit=54)])
    story.extend([_table(evidence_rows, [25 * mm, 34 * mm, 67 * mm, 48 * mm], styles), Spacer(1, 2 * mm), _p("完整來源 URL、原始 response 與計算輸入保存在同批 JSON 與 raw capture；本頁只作人讀索引。", styles, "small"), PageBreak()])

    # Final printable card, not the report title.
    story.extend([_chapter_title("附錄 B", "研究會議速查卡", styles), Spacer(1, 2 * mm), _p("本頁可單獨列印；它是研究流程的操作卡，不取代前述正式研究章節。", styles), _render_quick_card(report, styles)])
    doc.build(story)
    return output_path
