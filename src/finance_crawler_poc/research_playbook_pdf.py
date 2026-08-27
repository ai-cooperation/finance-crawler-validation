"""Render an equity Research Pack as a human-readable meeting playbook.

The machine-readable report remains the source of truth.  This renderer turns
the same evidence into the compact, question-led format used by the supplied
``MEETING PLAYBOOK`` reference: positioning first, scripted questions next,
signal interpretation after that, and a one-page card for live use.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
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


TEAL = colors.HexColor("#0E5A66")
TEAL_DARK = colors.HexColor("#083E49")
GOLD = colors.HexColor("#D89C17")
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#65727A")
BEIGE = colors.HexColor("#FBF5EA")
PALE_GREEN = colors.HexColor("#F1F9F4")
PALE_RED = colors.HexColor("#FFF3F1")
PALE_BLUE = colors.HexColor("#EEF7FA")
LINE = colors.HexColor("#CBD9DE")
GREEN = colors.HexColor("#16734D")
RED = colors.HexColor("#B7473C")
BLUE = colors.HexColor("#2D6D8A")

_FONT_REGULAR = "/Users/user/Library/Fonts/TaipeiSansTCBeta-Regular.ttf"
_FONT_BOLD = "/Users/user/Library/Fonts/TaipeiSansTCBeta-Bold.ttf"


def _register_fonts() -> tuple[str, str]:
    regular, bold = "FinancePlaybook", "FinancePlaybookBold"
    if regular not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular, _FONT_REGULAR))
    if bold not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold, _FONT_BOLD))
    return regular, bold


def _esc(value: Any) -> str:
    return escape(str(value if value is not None else "").replace("\n", " "))


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _num(value: Any, digits: int = 2) -> str:
    return f"{float(value):,.{digits}f}" if _finite(value) else "未提供"


def _pct(value: Any, digits: int = 1) -> str:
    return f"{float(value):,.{digits}f}%" if _finite(value) else "未提供"


def _styles(font: str, bold: str) -> dict[str, ParagraphStyle]:
    return {
        "cover_kicker": ParagraphStyle("pb_cover_kicker", fontName=font, fontSize=10, leading=14, textColor=MUTED, alignment=TA_CENTER),
        "cover_title": ParagraphStyle("pb_cover_title", fontName=bold, fontSize=27, leading=34, textColor=TEAL_DARK, alignment=TA_CENTER),
        "cover_subtitle": ParagraphStyle("pb_cover_subtitle", fontName=font, fontSize=12, leading=18, textColor=INK, alignment=TA_CENTER),
        "cover_meta": ParagraphStyle("pb_cover_meta", fontName=font, fontSize=8.5, leading=14, textColor=MUTED, alignment=TA_CENTER),
        "heading": ParagraphStyle("pb_heading", fontName=bold, fontSize=18, leading=23, textColor=TEAL_DARK, spaceBefore=2 * mm, spaceAfter=3 * mm),
        "subheading": ParagraphStyle("pb_subheading", fontName=bold, fontSize=12, leading=16, textColor=TEAL_DARK, spaceBefore=2 * mm, spaceAfter=2 * mm),
        "body": ParagraphStyle("pb_body", fontName=font, fontSize=8.7, leading=13.5, textColor=INK, spaceAfter=1.7 * mm),
        "small": ParagraphStyle("pb_small", fontName=font, fontSize=7.2, leading=10, textColor=MUTED, spaceAfter=1 * mm),
        "table": ParagraphStyle("pb_table", fontName=font, fontSize=7.1, leading=9.5, textColor=INK),
        "table_head": ParagraphStyle("pb_table_head", fontName=bold, fontSize=7.2, leading=9.5, textColor=colors.white),
        "box_title": ParagraphStyle("pb_box_title", fontName=bold, fontSize=9.5, leading=13, textColor=TEAL_DARK, spaceAfter=1.2 * mm),
        "box_body": ParagraphStyle("pb_box_body", fontName=font, fontSize=8.3, leading=12.5, textColor=INK),
        "question": ParagraphStyle("pb_question", fontName=bold, fontSize=10.3, leading=14, textColor=TEAL_DARK),
        "quote": ParagraphStyle("pb_quote", fontName=font, fontSize=9, leading=13, textColor=INK),
        "quick": ParagraphStyle("pb_quick", fontName=font, fontSize=7.1, leading=9.4, textColor=INK),
        "quick_head": ParagraphStyle("pb_quick_head", fontName=bold, fontSize=7.3, leading=9.5, textColor=colors.white),
    }


def _p(value: Any, styles: Mapping[str, ParagraphStyle], name: str = "body") -> Paragraph:
    return Paragraph(_esc(value), styles[name])


def _rich(value: str, styles: Mapping[str, ParagraphStyle], name: str = "body") -> Paragraph:
    return Paragraph(value, styles[name])


def _section(title: str, styles: Mapping[str, ParagraphStyle], number: str | None = None) -> Table:
    label = f"{number}  {title}" if number else title
    table = Table([[Paragraph(f"<b>{_esc(label)}</b>", ParagraphStyle("section", parent=styles["subheading"], textColor=colors.white))]], colWidths=[174 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _callout(title: str, lines: Sequence[str], background: colors.Color, styles: Mapping[str, ParagraphStyle], border: colors.Color = LINE, accent: colors.Color = TEAL) -> Table:
    content: list[list[Any]] = [[_p(title, styles, "box_title")]]
    for line in lines:
        content.append([_rich(f"<font color='{accent.hexval()}'>●</font> {_esc(line)}", styles, "box_body")])
    table = Table(content, colWidths=[174 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background), ("BOX", (0, 0), (-1, -1), 0.6, border),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _compact_callout(title: str, lines: Sequence[str], background: colors.Color, styles: Mapping[str, ParagraphStyle], border: colors.Color = LINE, accent: colors.Color = TEAL) -> Table:
    """Small callout used inside the printable card so it stays one page."""
    compact_title = ParagraphStyle("pb_compact_title", parent=styles["quick"], fontName=styles["box_title"].fontName, fontSize=8.2, leading=10.2, textColor=TEAL_DARK)
    compact_body = ParagraphStyle("pb_compact_body", parent=styles["quick"], fontSize=7.0, leading=8.7, textColor=INK)
    content: list[list[Any]] = [[Paragraph(f"<b>{_esc(title)}</b>", compact_title)]]
    for line in lines:
        content.append([Paragraph(f"<font color='{accent.hexval()}'>●</font> {_esc(line)}", compact_body)])
    table = Table(content, colWidths=[168 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background), ("BOX", (0, 0), (-1, -1), 0.5, border),
        ("LINEBEFORE", (0, 0), (0, -1), 2, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    return table


def _table(rows: Sequence[Sequence[Any]], widths: Sequence[float], styles: Mapping[str, ParagraphStyle], header: bool = True, row_colors: Sequence[colors.Color] = (colors.white, PALE_BLUE)) -> Table:
    cooked: list[list[Any]] = []
    for row_index, row in enumerate(rows):
        cooked.append([cell if isinstance(cell, Paragraph) else _p(cell, styles, "table_head" if header and row_index == 0 else "table") for cell in row])
    table = Table(cooked, colWidths=list(widths), repeatRows=1 if header else 0, hAlign="LEFT")
    commands: list[tuple[Any, ...]] = [
        ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), TEAL))
        if len(cooked) > 1:
            commands.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), list(row_colors)))
    table.setStyle(TableStyle(commands))
    return table


def _chapter(report: Mapping[str, Any], chapter_id: str) -> Mapping[str, Any]:
    for item in report.get("chapters", []):
        if isinstance(item, Mapping) and str(item.get("id")) == chapter_id:
            value = item.get("content")
            return value if isinstance(value, Mapping) else {}
    return {}


def _social_rows(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    content = _chapter(report, "11")
    values = content.get("social_candidates")
    return [row for row in values if isinstance(row, Mapping)] if isinstance(values, list) else []


def _social_label(row: Mapping[str, Any]) -> str:
    title = str(row.get("title") or "未命名社群原文")
    score = row.get("score") if row.get("score") is not None else "未提供"
    comments = row.get("comments") if row.get("comments") is not None else "未提供"
    return f"{title}｜points {score}｜comments {comments}"


def _question(number: int, title: str, tag: str, prompt: str, why: str, reading: str, styles: Mapping[str, ParagraphStyle], accent: colors.Color = TEAL) -> Table:
    head = _rich(f"<font color='{accent.hexval()}'><b>Q{number}</b></font>　<b>{_esc(title)}</b>　<font backColor='{accent.hexval()}' color='white'> {_esc(tag)} </font>", styles, "question")
    quote = Table([[_p(f"「{prompt}」", styles, "quote")]], colWidths=[166 * mm])
    quote.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE), ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    inner = [[head], [Spacer(1, 1.5 * mm)], [quote], [_rich(f"<b>為什麼問：</b>{_esc(why)}", styles, "small")], [_rich(f"<b>判讀：</b>{_esc(reading)}", styles, "small")]]
    table = Table(inner, colWidths=[166 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent), ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _footer(font: str):
    def draw(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
        canvas.setFont(font, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm, "標的研究作戰卡｜證據型 Research Pack｜研究用途")
        canvas.drawRightString(192 * mm, 9 * mm, f"第 {doc.page} 頁")
        canvas.restoreState()
    return draw


def _value_line(label: str, value: Any, suffix: str = "") -> str:
    return f"{label}{_num(value)}{suffix}" if _finite(value) else f"{label}未提供"


def _positioning(report: Mapping[str, Any], styles: Mapping[str, ParagraphStyle]) -> tuple[list[str], list[str], list[str]]:
    card = report.get("decision_card") if isinstance(report.get("decision_card"), Mapping) else {}
    rng = card.get("target_range") if isinstance(card.get("target_range"), Mapping) else {}
    appendix = report.get("appendix") if isinstance(report.get("appendix"), Mapping) else {}
    evidence = appendix.get("evidence") if isinstance(appendix.get("evidence"), Mapping) else {}
    methods = _chapter(report, "8")
    social = _social_rows(report)
    available = [
        f"官方與監管路徑已納入：財務歷史、公司申報與市場資料可回溯。",
        f"Research Pack 已收錄 {evidence.get('item_count', '未提供')} 筆證據，整理為 {evidence.get('canonical_story_count', '未提供')} 個 canonical stories。",
        f"12 個月情境估值：基準 {_num(rng.get('base'))}，低點 {_num(rng.get('low'))}，高點 {_num(rng.get('high'))}。",
        f"社群有 {len(social)} 筆 Hacker News 公開原文；留言只作線索，不升格為事實。",
    ]
    risks = [
        f"估值方法分歧 {_pct(methods.get('method_dispersion_pct'))}；方法包絡 {_num(rng.get('method_envelope_low'))}-{_num(rng.get('method_envelope_high'))}，不可當作單一合理價。",
        "目前社群樣本集中在單一公開社群，尚不足以代表投資人整體共識。",
        "事件研究目前是描述性結果；若要主張因果，仍需補齊事件定義、窗口與獨立 benchmark。",
        "新聞敘事與社群敘事的可比 claim 尚未充分對齊，背離維持未判定。",
    ]
    objective = [
        "先判定基本面與資料是否足以支持研究結論。",
        "再判定目前價格是否已反映收入、毛利、資本支出與產業循環。",
        "最後列出會推翻目前觀點的可觀測條件與下一次重跑觸發器。",
    ]
    return objective, available, risks


def render_equity_playbook_pdf(report: Mapping[str, Any], output_path: Path) -> Path:
    """Render a reference-style, question-led human research playbook."""

    regular, bold = _register_fonts()
    styles = _styles(regular, bold)
    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    card = report.get("decision_card") if isinstance(report.get("decision_card"), Mapping) else {}
    rng = card.get("target_range") if isinstance(card.get("target_range"), Mapping) else {}
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    local_name = next((str(alias) for alias in aliases if any("一" <= char <= "鿿" for char in str(alias))), str(target.get("name") or target.get("symbol") or "標的"))
    appendix = report.get("appendix") if isinstance(report.get("appendix"), Mapping) else {}
    evidence = appendix.get("evidence") if isinstance(appendix.get("evidence"), Mapping) else {}
    unresolved = appendix.get("unresolved") if isinstance(appendix.get("unresolved"), list) else []
    objective, available, risks = _positioning(report, styles)
    social = _social_rows(report)
    financials = _chapter(report, "6")
    forecast = _chapter(report, "7")
    valuation = _chapter(report, "8")
    event = _chapter(report, "10")
    monitoring = _chapter(report, "14")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(output_path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=15 * mm, bottomMargin=19 * mm,
        title=f"{local_name} 投研作戰卡", author="Evidence Research Pipeline",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="playbook", frames=[frame], onPage=_footer(regular))])
    story: list[Any] = []

    # 1. Cover
    story.extend([
        Spacer(1, 22 * mm), _p("MEETING PLAYBOOK", styles, "cover_kicker"),
        HRFlowable(width="21%", thickness=2.2, color=GOLD, spaceBefore=3 * mm, spaceAfter=8 * mm, hAlign="CENTER"),
        _p(f"{local_name}（{target.get('symbol')}）\n投研作戰卡", styles, "cover_title"),
        Spacer(1, 4 * mm), _p("基於 Research Pack 的投資研究、驗證與追蹤腳本", styles, "cover_subtitle"),
        Spacer(1, 14 * mm),
        _p(f"研究日期　{str(card.get('market_price_as_of') or report.get('generated_at') or '未提供')[:10]}\n產出日期　{str(report.get('generated_at') or '未提供')[:10]}\n研究定位　研究用途，不構成個人化投資建議\n末頁　速查卡可單獨列印，帶進研究會議", styles, "cover_meta"),
        Spacer(1, 14 * mm), HRFlowable(width="21%", thickness=2.2, color=GOLD, spaceBefore=3 * mm, spaceAfter=8 * mm, hAlign="CENTER"),
        _p("這張卡的用途：把資料、假設、衝突與下一步，排成一條可稽核的研究路徑。", styles, "cover_meta"), PageBreak(),
    ])

    # 2. Positioning and objective
    story.extend([_callout("本次研究定位已調整", [f"目前觀點：{card.get('rating', '未提供')}｜信心：{card.get('confidence', '未提供')}｜研究期限：{card.get('horizon', '12 months')}。", "先做證據與假設的壓力測試，再交給高階模型二次推理；不直接轉成交易指令。"], BEIGE, styles, border=GOLD, accent=GOLD), Spacer(1, 4 * mm), _section("研究目標", styles, "01"), Spacer(1, 2 * mm), _callout("這次研究要回答的三件事", objective, PALE_BLUE, styles), Spacer(1, 3 * mm), _callout("目前可確認", available, PALE_GREEN, styles, accent=GREEN), Spacer(1, 3 * mm), _callout("今天最大的風險", risks, PALE_RED, styles, accent=RED), PageBreak()])

    # 3. Data confidence and timetable
    story.extend([_section("研究位置與資料可信度", styles, "02"), Spacer(1, 2 * mm)])
    source_rows = [
        ["資料層", "本次狀態", "讀法"],
        ["官方／監管", "已納入", "作為財務、申報與身分的優先證據"],
        ["市場／時間序列", f"{financials.get('annual_periods') and len(financials.get('annual_periods')) or '未提供'} 年度資料", "先對齊 as-of，再做趨勢與 benchmark 比較"],
        ["估值／預測", f"{len(valuation.get('methods', [])) if isinstance(valuation.get('methods'), list) else '未提供'} 種方法", "情境值是決策帶，不是統計信賴區間"],
        ["社群原文", f"{len(social)} 筆 Hacker News", "只作線索；沒有可比 claim 就不判定背離"],
        ["Evidence Pack", f"{evidence.get('item_count', '未提供')} 筆／{evidence.get('source_group_count', '未提供')} 個來源群", "每個結論要能回到 evidence ID、URL 與 response hash"],
    ]
    story.extend([_table(source_rows, [34 * mm, 48 * mm, 92 * mm], styles), Spacer(1, 4 * mm), _section("研究流程建議（60-90 分鐘）", styles, "03"), Spacer(1, 2 * mm)])
    timeline = [
        ["時間", "研究動作", "交接輸出"],
        ["0-10 分", "確認標的、交易所、幣別與 as-of", "target identity contract"],
        ["10-25 分", "讀官方／監管與市場原始資料", "canonical evidence set"],
        ["25-40 分", "看 5 年／8 季財務與現金流", "trend + quality findings"],
        ["40-55 分", "固定 bear／base／bull 假設", "forecast assumption ledger"],
        ["55-70 分", "DCF、forward PE、方法分歧", "valuation decision band"],
        ["70-80 分", "事件研究、新聞／社群衝突", "conflict register"],
        ["最後 10 分", "寫監測條件並保存稽核收據", "Research Pack handoff"],
    ]
    story.extend([_table(timeline, [25 * mm, 73 * mm, 76 * mm], styles), Spacer(1, 3 * mm), _callout("交接規則", ["研究員／Agent 只接收已標記來源、時間、假設與限制的 Research Pack。", "任何缺失都要寫成待處理項目，不得用空白、推測或單一社群替代。"], PALE_GREEN, styles, accent=GREEN), PageBreak()])

    # 4-7. Scripted questions.  Values are intentionally rendered in human language.
    first_period = (forecast.get("forecast_periods") or [{}])[0] if isinstance(forecast.get("forecast_periods"), list) else {}
    assumptions = forecast.get("assumptions") if isinstance(forecast.get("assumptions"), list) else []
    assumption_text = "、".join(str(item.get("name") or item.get("label") or item.get("key")) for item in assumptions[:3] if isinstance(item, Mapping)) or "收入、毛利率、資本支出"
    questions = [
        ("先定義本次研究要做的決策", "最好的開場題", f"這次先維持 {card.get('rating', '目前評等')}，還是要做明確的 bullish / bearish stress？", "若目標不清楚，後面的資料再多也只會變成摘要堆疊。", "先固定研究用途與觀點，不把報告誤讀為下單建議。", TEAL),
        ("確認標的與時間邊界", "本場核心", f"請確認 {local_name}（{target.get('symbol')}）、市場 {target.get('market')}、幣別 {target.get('currency')}，以及所有資料是否對齊同一個 as-of。", "標的身分或日期錯位會讓估值、事件與價格比較全部失真。", "缺一項就標記待補，不進入專業級結論。", GOLD),
        ("財務趨勢是否支持論點", "關鍵", f"過去年度與季度的收入、營業利益率、自由現金流，哪一段真正支持目前觀點？目前資料涵蓋 {len(financials.get('annual_periods', [])) if isinstance(financials.get('annual_periods'), list) else '未提供'} 年度。", "收入成長不等於盈餘品質；現金流與利潤率是第二條驗證線。", "若只看到單季高增長，先降級信心並追問週期性。", BLUE),
        ("公司與產業位置", "本場核心", "台積電的護城河、產能配置、AI／HPC 需求與競爭者變化，哪一個是可觀測、哪一個只是敘事？", "公司故事要拆成可驗證的收入驅動、成本驅動與供需條件。", "把敘事拆成指標，才可放進 forecast ledger。", TEAL),
        ("官方與監管是否能交叉驗證", "重要", "請用官方申報、TWSE／監管資料和公司公開說法交叉核對營運、資本支出與風險揭露。", "官方／監管來源是身分與重大事實的優先證據。", "路徑成功不代表內容已支持結論；要留下 URL、日期與 hash。", GOLD),
        ("預測假設從哪裡來", "新增", f"目前 forecast 使用哪些假設？例如 {assumption_text}；每個假設對應哪個來源或管理層 guidance？", "沒有 lineage 的數字不能稽核，也不能交給第二個模型放心推理。", "把假設、版本、來源與敏感度放在同一份 ledger。", BLUE),
        ("估值方法是否收斂", "本場核心", f"目前 12 個月基準值 {_num(rng.get('base'))}，情境區間 {_num(rng.get('low'))}-{_num(rng.get('high'))}；方法包絡 {_num(rng.get('method_envelope_low'))}-{_num(rng.get('method_envelope_high'))}，這個分歧要如何解讀？", "區間很寬時，單一 weighted target 會掩蓋模型不確定性。", "只把它當決策帶，先說清楚分歧來源，再談價格。", RED),
        ("事件研究能說到哪裡", "關鍵", "事件窗口、benchmark、樣本數與回報對齊了嗎？哪些是描述性結果，哪些尚未有因果證據？", "事件研究最容易把同時發生誤寫成因果。", "若 causal status 未解決，報告必須保留未判定。", RED),
        ("社群原文提供什麼訊號", "最有價值的動作", f"這批有 {len(social)} 筆 Hacker News 原文；每一筆的原始標題、points、comments 與留言，能否提出可比對的 claim？", "社群適合找早期線索，不適合直接當作事實或共識。", "保留原文 URL 與留言節錄；沒有對齊 claim 就不下背離結論。", GREEN),
        ("新聞與社群是否背離", "判讀", "哪些新聞 claim 能與社群原文一一對齊？若只有話題相似、沒有同一主張，是否應維持未判定？", "背離判定需要同一事件、同一時間窗、不同來源層的可比主張。", "目前不足時，誠實寫成未判定，而不是硬湊反向訊號。", GOLD),
        ("下一次重跑要看什麼", "收尾", f"下一次應追蹤哪些 KPI、事件或風險？目前監測項目 {len(monitoring.get('monitoring', [])) if isinstance(monitoring.get('monitoring'), list) else '已保存'} 筆，什麼變化會推翻目前觀點？", "研究的價值在於可持續更新，而不是一次性的長摘要。", "把觸發條件、負責角色、來源路徑與重跑日期寫入交接。", TEAL),
    ]
    for group_index, start in enumerate((0, 3, 6, 9), start=1):
        story.extend([_section("問題腳本（照順序問）", styles, f"0{group_index + 3}"), Spacer(1, 1 * mm)])
        for index in range(start, min(start + 3, len(questions))):
            q = questions[index]
            story.extend([_question(index + 1, q[0], q[1], q[2], q[3], q[4], styles, q[5]), Spacer(1, 2.3 * mm)])
        if start + 3 < len(questions):
            story.append(PageBreak())

    # 8. Signals and handoff
    story.extend([_section("判讀訊號速查", styles, "08"), Spacer(1, 2 * mm)])
    signal_rows = [
        ["如果看到／聽到", "訊號", "代表什麼／下一步"],
        ["官方與監管資料、as-of、標的身分一致", "好", "可以進入基本面與估值；保留 evidence ID。"],
        ["財務歷史完整，CFO／FCF 與獲利方向一致", "好", "提高論點可信度，但仍需檢查週期與 forward assumptions。"],
        ["估值方法分歧超過 50%", "警訊", "不可只看 weighted target；改用決策帶並做敏感度。"],
        ["社群原文只有單一社群群組", "留意", "可作線索，不代表整體市場情緒。"],
        ["新聞與社群只有相似話題，沒有可比 claim", "待處理", "背離維持未判定，要求補齊同事件與時間窗。"],
        ["事件研究未交代 benchmark 或 causal status", "警訊", "只寫描述性回報，不寫因果結論。"],
        ["來源路徑失敗或內容不可回溯", "警訊", "保留失敗紀錄與 response metadata，不可寫成沒有討論。"],
    ]
    story.extend([_table(signal_rows, [62 * mm, 23 * mm, 89 * mm], styles), Spacer(1, 4 * mm), _callout("會後研究動作", ["保存本次 Research Pack、來源清單、假設 ledger、計算輸入與報告 hash。", "將未解決項目分派給資料收集 engine；補齊後以同一標的與同一 as-of 重跑。", "高階模型只讀已標記 evidence，輸出二次推理與反證，不自行補造來源。"], PALE_GREEN, styles, accent=GREEN), Spacer(1, 3 * mm), _callout("交接 cadence", ["T+0：保存原始回應與摘要；T+1：補官方／監管或 benchmark 缺口；下一個事件窗口：重跑衝突判定與估值敏感度。"], PALE_BLUE, styles, accent=BLUE), PageBreak()])

    # 9. Handoff and evidence receipt
    story.extend([_section("Research Pack 交接單", styles, "09"), Spacer(1, 2 * mm), _p("以下文字可直接貼給另一個 Agent 或研究協作者；它只描述任務與證據邊界，不把機器欄位當成結論。", styles), _callout("交接訊息", [f"標的：{local_name}（{target.get('symbol')}）｜市場：{target.get('market')}｜幣別：{target.get('currency')}。", f"目前觀點：{card.get('rating', '未提供')}，信心：{card.get('confidence', '未提供')}；基準估值：{_num(rng.get('base'))}。", "請先讀 evidence map，再回答：哪些論點有兩條獨立來源支持？哪些是未解決限制？哪些條件會推翻目前觀點？", "禁止：將社群原文當成事實、把估值加權值當成目標價保證、把描述性事件研究寫成因果。"], BEIGE, styles, border=GOLD, accent=GOLD), Spacer(1, 5 * mm), _p("稽核收據（摘要）", styles, "subheading")])
    audit_rows = [["項目", "本次值", "讀者應如何使用"]]
    audit_rows.extend([
        ["Evidence Pack", f"{evidence.get('item_count', '未提供')} 筆／{evidence.get('canonical_story_count', '未提供')} stories", "回到完整 JSON、URL、response hash"],
        ["來源群", str(evidence.get('source_group_count', '未提供')), "檢查是否過度集中在單一 provider"],
        ["社群原文", str(len(social)), "保留標題、URL、points、comments 與留言節錄"],
        ["未解決項目", "無" if not unresolved else "；".join(str(item) for item in unresolved), "不能在摘要中隱藏；補齊後再升級評等"],
        ["研究版本", str(report.get('report_id') or "未提供"), "搭配 run metadata、程式版本與輸入 hash"],
    ])
    story.extend([_table(audit_rows, [36 * mm, 56 * mm, 82 * mm], styles), Spacer(1, 5 * mm), _p("社群原文索引", styles, "subheading")])
    social_rows = [["原文", "可回溯網址", "留言"]]
    for row in social[:6]:
        url = str(row.get("url") or "未提供")
        social_rows.append([_social_label(row), url, str(len(row.get("comment_excerpts", []))) if isinstance(row.get("comment_excerpts"), list) else "0"])
    if len(social_rows) == 1:
        social_rows.append(["本次沒有可回溯社群原文", "未提供", "0"])
    story.extend([_table(social_rows, [90 * mm, 67 * mm, 17 * mm], styles), Spacer(1, 3 * mm), _p("完整 URL 與原始 response 不在這張卡重複展開；請以同批 Research Pack 與 evidence appendix 為準。", styles, "small"), PageBreak()])

    # 10. Printable quick card
    quick_rows: list[list[Any]] = []
    quick_rows.append([_rich("<font color='white'><b>速查卡（可單獨列印，帶進研究會議）</b></font>", styles, "subheading")])
    quick_rows.append([_compact_callout("核心目標", ["先判定研究可信度，再決定是否進入高階模型二次推理；不是直接下單。"], PALE_BLUE, styles, accent=TEAL)])
    quick_rows.append([_compact_callout("今天一定要拿到的三件事", ["標的身分與時間對齊。", "至少兩條獨立官方／市場證據路徑。", "來源衝突、社群原文與反證條件。"], PALE_GREEN, styles, accent=GREEN)])
    quick_question_rows = [["順序", "必問問題", "不要跳過"]]
    for index, q in enumerate(questions, start=1):
        quick_question_rows.append([str(index), q[0], q[1]])
    quick_rows.append([_table(quick_question_rows, [14 * mm, 112 * mm, 48 * mm], styles)])
    quick_rows.append([_compact_callout("絕對不要做", ["把社群討論直接當事實或共識。", "把 weighted target 當成保證價格。", "把描述性 event study 寫成因果。", "把失敗來源從稽核紀錄刪掉。"], PALE_RED, styles, accent=RED)])
    quick_rows.append([_compact_callout("四句必說", ["這份報告先維持研究用途，不直接轉成交易指令。", "估值區間是決策帶，不是統計信賴區間。", "社群只作線索，沒有可比 claim 不判定背離。", "來源失敗要保留在稽核紀錄，不可寫成沒有討論。"], BEIGE, styles, border=GOLD, accent=GOLD)])
    quick_rows.append([_compact_callout("紅旗與重跑錨點", ["估值方法分歧 > 50%：改用決策帶與敏感度。", "社群只有單一群組：降級為線索。", "時間或 benchmark 未對齊：停止專業級結論。", "新官方申報、重大事件或假設突破：以同一 contract 重跑。"], PALE_BLUE, styles, accent=BLUE)])
    quick = Table(quick_rows, colWidths=[176 * mm], hAlign="LEFT")
    quick.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.3, TEAL), ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(quick)
    doc.build(story)
    return output_path
