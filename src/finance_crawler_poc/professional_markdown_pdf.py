"""Publish the canonical human report in the market-data/valuation PDF style.

The Markdown written by ``write_professional_artifacts`` is the正文 source.
This module intentionally does not rebuild the analysis from a second set of
summaries: it parses the canonical Markdown into reportlab flowables, then
adds only the cover, executive summary frame, and printable meeting-card
appendix.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
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
    _register_fonts,
)


@dataclass(frozen=True)
class Block:
    kind: str
    value: Any


def _styles(font: str, bold: str) -> dict[str, ParagraphStyle]:
    return {
        "cover_kicker": ParagraphStyle("md_cover_kicker", fontName=font, fontSize=9, leading=13, textColor=MUTED, alignment=TA_LEFT),
        "cover_year": ParagraphStyle("md_cover_year", fontName=font, fontSize=37, leading=42, textColor=INK, alignment=TA_LEFT),
        "cover_title": ParagraphStyle("md_cover_title", fontName=bold, fontSize=22, leading=29, textColor=TEAL_DARK, alignment=TA_LEFT),
        "cover_subtitle": ParagraphStyle("md_cover_subtitle", fontName=font, fontSize=9.5, leading=14, textColor=MUTED, alignment=TA_LEFT),
        "cover_meta": ParagraphStyle("md_cover_meta", fontName=font, fontSize=7.3, leading=11, textColor=MUTED, alignment=TA_LEFT),
        "chapter": ParagraphStyle("md_chapter", fontName=bold, fontSize=17, leading=23, textColor=TEAL_DARK, spaceBefore=0, spaceAfter=3 * mm),
        "section": ParagraphStyle("md_section", fontName=bold, fontSize=11.5, leading=16, textColor=TEAL_DARK, spaceBefore=2 * mm, spaceAfter=2 * mm),
        "body": ParagraphStyle("md_body", fontName=font, fontSize=8.6, leading=13.2, textColor=INK, spaceAfter=2 * mm),
        "small": ParagraphStyle("md_small", fontName=font, fontSize=7.1, leading=9.6, textColor=MUTED, spaceAfter=1 * mm),
        "quote": ParagraphStyle("md_quote", fontName=font, fontSize=8.3, leading=12, textColor=INK),
        "table": ParagraphStyle("md_table", fontName=font, fontSize=6.7, leading=8.8, textColor=INK),
        "table_head": ParagraphStyle("md_table_head", fontName=bold, fontSize=6.8, leading=8.8, textColor=colors.white),
        "metric_label": ParagraphStyle("md_metric_label", fontName=font, fontSize=6.8, leading=9, textColor=MUTED),
        "metric_value": ParagraphStyle("md_metric_value", fontName=bold, fontSize=13, leading=16, textColor=TEAL_DARK),
        "sidebar": ParagraphStyle("md_sidebar", fontName=font, fontSize=7.2, leading=12, textColor=MUTED),
        "sidebar_head": ParagraphStyle("md_sidebar_head", fontName=bold, fontSize=9, leading=13, textColor=TEAL),
        "card": ParagraphStyle("md_card", fontName=font, fontSize=7.4, leading=9.6, textColor=INK),
        "card_head": ParagraphStyle("md_card_head", fontName=bold, fontSize=8, leading=10, textColor=colors.white),
    }


def _inline(value: str) -> str:
    """Convert the report Markdown subset while retaining auditable links.

    The canonical Markdown is the source of truth, so a PDF citation must not
    silently degrade ``[證據](https://...)`` into the label alone.  We render
    the label as a clickable ReportLab link and show a compact, readable URL
    suffix next to it.  The full URL remains in the PDF annotation.
    """

    def compact_url(url: str) -> str:
        parsed = urlparse(url)
        if not parsed.netloc:
            visible = url
        elif parsed.query:
            # Query URLs are often hundreds of characters long.  Showing a
            # shortened path makes the shortened text look like a real URL and
            # leads readers to open a 404 path.  Keep the full URL in the link
            # annotation and use an explicit human-facing label instead.
            visible = f"{parsed.netloc} API（完整連結）"
        else:
            # SEC EDGAR accession paths are long but do not contain a query.
            # Never show a chopped path such as ``.../00016…``: PDF viewers
            # and copy/paste workflows can treat it as the actual URL and
            # produce a 404 even though the hyperlink annotation is correct.
            candidate = f"{parsed.netloc}{parsed.path}"
            visible = candidate if len(candidate) <= 48 else f"{parsed.netloc}（完整連結）"
        return visible if len(visible) <= 48 else "完整連結"

    text = value.strip()
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
    link_markup: dict[str, str] = {}
    anchor_markup: dict[str, str] = {}

    def preserve_anchor(match: re.Match[str]) -> str:
        anchor_id = match.group(1).strip()
        token = f"\x00ANCHOR_{len(anchor_markup)}\x00"
        anchor_markup[token] = f'<a name="{_esc(anchor_id)}"/>'
        return token

    text = re.sub(r'<a\s+id=["\'](ref-\d+)["\']\s*></a>', preserve_anchor, text, flags=re.IGNORECASE)

    def preserve_link(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        href = match.group(2).strip()
        token = f"\x00LINK_{len(link_markup)}\x00"
        if label == href or label.startswith(("https://", "http://")):
            # Reference lists intentionally print the complete URL. Keep the
            # visible text and annotation identical so readers can copy or
            # click the same auditable destination.
            link_markup[token] = f'<link href="{_esc(href)}">{_esc(href)}</link>'
        else:
            link_markup[token] = (
                f'<link href="{_esc(href)}">{_esc(label)}</link>'
                f' <font color="#54737A" size="6">({_esc(compact_url(href))})</font>'
            )
        if href.startswith("#"):
            link_markup[token] = f'<link href="{_esc(href)}">{_esc(label)}</link>'
        return token

    # Permit one level of brackets inside a link label, e.g. Hacker News
    # titles ending in ``[video]``.  Without this, the fallback substitution
    # leaves malformed Markdown visible in the PDF.
    text = re.sub(
        r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\(((?:https?://|#)[^)\s]+)\)",
        preserve_link,
        text,
        flags=re.IGNORECASE,
    )
    # Keep non-web Markdown links readable instead of emitting raw syntax.
    text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    # Protect the small amount of rich text we intentionally inserted.
    text = text.replace("<b>", "\x00BOLD_OPEN\x00").replace("</b>", "\x00BOLD_CLOSE\x00")
    text = _esc(text)
    for token, markup in link_markup.items():
        text = text.replace(_esc(token), markup)
    for token, markup in anchor_markup.items():
        text = text.replace(_esc(token), markup)
    return text.replace("\x00BOLD_OPEN\x00", "<b>").replace("\x00BOLD_CLOSE\x00", "</b>")


def _p(text: str, styles: Mapping[str, ParagraphStyle], name: str = "body") -> Paragraph:
    return Paragraph(_inline(text), styles[name])


def _parse_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _is_separator(row: Sequence[str]) -> bool:
    return bool(row) and all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in row)


def parse_markdown(path: Path) -> list[Block]:
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[Block] = []
    paragraph: list[str] = []
    bullets: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(Block("paragraph", " ".join(x.strip() for x in paragraph)))
            paragraph = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            blocks.append(Block("bullets", list(bullets)))
            bullets = []

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_bullets()
            index += 1
            continue
        if stripped.startswith("# "):
            flush_paragraph(); flush_bullets(); blocks.append(Block("title", stripped[2:].strip())); index += 1; continue
        match = re.match(r"^(#{2,4})\s+(.*)$", stripped)
        if match:
            flush_paragraph(); flush_bullets(); blocks.append(Block(f"h{len(match.group(1))}", match.group(2).strip())); index += 1; continue
        if stripped.startswith(">"):
            flush_paragraph(); flush_bullets(); blocks.append(Block("quote", stripped.lstrip("> "))); index += 1; continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_paragraph(); bullets.append(stripped[2:].strip()); index += 1; continue
        if stripped.startswith("|"):
            flush_paragraph(); flush_bullets(); rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_parse_table_row(lines[index])); index += 1
            if len(rows) >= 2 and _is_separator(rows[1]):
                rows.pop(1)
            blocks.append(Block("table", rows)); continue
        if re.fullmatch(r"-{3,}", stripped):
            flush_paragraph(); flush_bullets(); blocks.append(Block("rule", None)); index += 1; continue
        paragraph.append(stripped)
        index += 1
    flush_paragraph(); flush_bullets()
    return blocks


_COVER_ASSETS = {
    "market_data": "report-cover-market-data.png",
    "evidence_network": "report-cover-data-network.png",
}
DEFAULT_COVER_STYLE = "market_data"
SUPPORTED_COVER_STYLES = {"market_data", "valuation_focus", "evidence_network"}


def _cover_price_points(report: Mapping[str, Any]) -> list[tuple[datetime, float]]:
    """Return the latest twelve months of valid, ordered market observations."""

    raw_points: Sequence[Any] = []
    for chapter in report.get("chapters", []):
        if not isinstance(chapter, Mapping) or str(chapter.get("id")) != "9":
            continue
        content = chapter.get("content") if isinstance(chapter.get("content"), Mapping) else {}
        time_series = content.get("time_series") if isinstance(content.get("time_series"), Mapping) else {}
        raw_points = time_series.get("points") if isinstance(time_series.get("points"), list) else []
        break
    parsed: list[tuple[datetime, float]] = []
    for point in raw_points:
        if not isinstance(point, Mapping):
            continue
        try:
            observed = datetime.fromisoformat(str(point.get("observed_at") or "").replace("Z", "+00:00"))
            value = float(point.get("value"))
        except (TypeError, ValueError):
            continue
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        if value == value and value not in (float("inf"), float("-inf")):
            parsed.append((observed, value))
    parsed.sort(key=lambda item: item[0])
    if not parsed:
        return []
    cutoff = parsed[-1][0] - timedelta(days=365)
    return [item for item in parsed if item[0] >= cutoff]


def _cover_asset_path(style: str = DEFAULT_COVER_STYLE) -> Path:
    """Return the deterministic raster asset for a supported cover style."""
    filename = _COVER_ASSETS.get(style)
    if filename is None:
        supported = ", ".join(sorted(_COVER_ASSETS))
        raise ValueError(f"unsupported cover style {style!r}; choose one of: {supported}")
    return Path(__file__).resolve().parents[2] / "assets" / filename


class CoverArt(Flowable):
    """Render the standard market-data/valuation cover motif.

    ``market_data`` is the global default because these reports are derived
    from market prices, financial history, scenarios, and valuation bands.
    The evidence-network asset remains available as an explicit legacy style.
    """

    def __init__(
        self,
        width: float = 72 * mm,
        height: float = 100 * mm,
        style: str = DEFAULT_COVER_STYLE,
        *,
        market_price: Any = None,
        base_value: Any = None,
        low_value: Any = None,
        high_value: Any = None,
        currency: str = "",
        price_points: Sequence[tuple[datetime, float]] | None = None,
    ) -> None:
        super().__init__()
        if style not in SUPPORTED_COVER_STYLES:
            supported = ", ".join(sorted(SUPPORTED_COVER_STYLES))
            raise ValueError(f"unsupported cover style {style!r}; choose one of: {supported}")
        self.width = width
        self.height = height
        self.style = style
        self.market_price = market_price
        self.base_value = base_value
        self.low_value = low_value
        self.high_value = high_value
        self.currency = currency
        self.price_points = list(price_points or [])

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        self.canv.saveState()
        if self.style == "market_data":
            self._draw_market_data_snapshot()
            self.canv.restoreState()
            return
        if self.style == "valuation_focus":
            self._draw_valuation_focus()
            self.canv.restoreState()
            return
        asset = _cover_asset_path(self.style)
        if not asset.exists():
            raise FileNotFoundError(f"cover artwork asset missing: {asset}")
        self.canv.drawImage(str(asset), 0, 0, width=self.width, height=self.height, preserveAspectRatio=True, mask="auto")
        self.canv.restoreState()

    def _draw_market_data_snapshot(self) -> None:
        """Draw an auditable target-specific valuation snapshot.

        The former raster motif contained TSMC-like template numbers on every
        issuer cover.  A professional report must never present decorative
        sample values as target facts, so the standard cover is now a vector
        chart derived only from the canonical decision card.
        """

        canvas = self.canv
        width, height = self.width, self.height
        canvas.setFillColor(colors.HexColor("#F4F7F8"))
        canvas.rect(0, 0, width, height, stroke=0, fill=1)
        canvas.setFillColor(TEAL)
        canvas.setFont("Helvetica-Bold", 5.2)
        canvas.drawString(6 * mm, height - 8 * mm, "12M ACTUAL PRICE")
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 3.8)
        observation_label = f"{len(self.price_points)} OBSERVATIONS"
        if self.price_points:
            observation_label += f" | AS-OF {self.price_points[-1][0].date().isoformat()}"
        canvas.drawRightString(width - 6 * mm, height - 8 * mm, observation_label)

        plot_left, plot_bottom = 6 * mm, 38 * mm
        plot_width, plot_height = width - 12 * mm, 48 * mm
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.rect(plot_left, plot_bottom, plot_width, plot_height, stroke=1, fill=1)
        for step in range(1, 4):
            y = plot_bottom + plot_height * step / 4
            canvas.setStrokeColor(colors.HexColor("#D9E4E6"))
            canvas.line(plot_left, y, plot_left + plot_width, y)

        series_values = [value for _, value in self.price_points]
        if series_values:
            minimum, maximum = min(series_values), max(series_values)
            span = maximum - minimum
            padding = span * 0.12 if span > 0 else max(abs(maximum) * 0.12, 1.0)
            scale_low, scale_high = minimum - padding, maximum + padding
            start_time = self.price_points[0][0].timestamp()
            end_time = self.price_points[-1][0].timestamp()
            time_span = max(end_time - start_time, 1.0)

            def coordinates(point: tuple[datetime, float]) -> tuple[float, float]:
                observed, value = point
                x = plot_left + (observed.timestamp() - start_time) / time_span * plot_width
                y = plot_bottom + (value - scale_low) / (scale_high - scale_low) * plot_height
                return x, y

            sampled = self.price_points
            if len(sampled) > 160:
                indexes = sorted({round(index * (len(sampled) - 1) / 159) for index in range(160)})
                sampled = [sampled[index] for index in indexes]
            coords = [coordinates(point) for point in sampled]
            area = canvas.beginPath()
            area.moveTo(coords[0][0], plot_bottom)
            for x, y in coords:
                area.lineTo(x, y)
            area.lineTo(coords[-1][0], plot_bottom)
            area.close()
            canvas.setFillColor(colors.HexColor("#D7ECEF"))
            canvas.setFillAlpha(0.72)
            canvas.drawPath(area, stroke=0, fill=1)
            canvas.setFillAlpha(1)
            line = canvas.beginPath()
            line.moveTo(*coords[0])
            for x, y in coords[1:]:
                line.lineTo(x, y)
            canvas.setStrokeColor(TEAL)
            canvas.setLineWidth(1.6)
            canvas.drawPath(line, stroke=1, fill=0)
            canvas.setFillColor(TEAL_DARK)
            canvas.circle(coords[-1][0], coords[-1][1], 2.2, stroke=0, fill=1)
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 3.7)
            canvas.drawString(plot_left + 1.5 * mm, plot_bottom + plot_height - 4 * mm, f"HIGH {_num(maximum)}")
            canvas.drawString(plot_left + 1.5 * mm, plot_bottom + 2 * mm, f"LOW {_num(minimum)}")
        else:
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 5)
            canvas.drawCentredString(plot_left + plot_width / 2, plot_bottom + plot_height / 2, "PRICE SERIES UNAVAILABLE")

        currency = f" {self.currency}" if self.currency else ""
        metrics = (
            ("MARKET PRICE", f"{_num(self.market_price)}{currency}"),
            ("BASE VALUE", _num(self.base_value)),
            ("VALUATION BAND", f"{_num(self.low_value)}–{_num(self.high_value)}"),
        )
        column_width = plot_width / 3
        for index, (label, value) in enumerate(metrics):
            x = plot_left + index * column_width
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 3.7)
            canvas.drawString(x, 27 * mm, label)
            canvas.setFillColor(TEAL_DARK)
            canvas.setFont("Helvetica-Bold", 5.4 if index < 2 else 4.6)
            canvas.drawString(x, 22 * mm, value)
        canvas.setStrokeColor(GOLD)
        canvas.setLineWidth(1.7)
        canvas.line(plot_left, 10 * mm, plot_left + 14 * mm, 10 * mm)

    def _draw_valuation_focus(self) -> None:
        """Draw an institutional valuation-position cover without a price curve."""

        canvas = self.canv
        width, height = self.width, self.height
        canvas.setFillColor(colors.HexColor("#F4F7F8"))
        canvas.rect(0, 0, width, height, stroke=0, fill=1)
        canvas.setFillColor(TEAL)
        canvas.setFont("Helvetica-Bold", 5.2)
        canvas.drawString(6 * mm, height - 8 * mm, "VALUATION POSITION")
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 3.8)
        canvas.drawRightString(width - 6 * mm, height - 8 * mm, "MARKET VS 12M RANGE")

        gauge_left, gauge_right = 8 * mm, width - 8 * mm
        gauge_y = 58 * mm
        valid: list[float] = []
        for value in (self.market_price, self.base_value, self.low_value, self.high_value):
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if parsed == parsed and parsed not in (float("inf"), float("-inf")):
                valid.append(parsed)
        if valid:
            minimum, maximum = min(valid), max(valid)
            span = maximum - minimum
            padding = span * 0.14 if span else max(abs(maximum) * 0.14, 1)
            scale_low, scale_high = minimum - padding, maximum + padding

            def scale(value: Any) -> float | None:
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    return None
                return gauge_left + (parsed - scale_low) / (scale_high - scale_low) * (gauge_right - gauge_left)

            canvas.setStrokeColor(LINE)
            canvas.setLineWidth(1)
            canvas.line(gauge_left, gauge_y, gauge_right, gauge_y)
            low_x, high_x = scale(self.low_value), scale(self.high_value)
            if low_x is not None and high_x is not None:
                left, right = sorted((low_x, high_x))
                canvas.setFillColor(colors.HexColor("#E8C56C"))
                canvas.setFillAlpha(0.42)
                canvas.roundRect(left, gauge_y - 4 * mm, max(right - left, 2), 8 * mm, 2 * mm, stroke=0, fill=1)
                canvas.setFillAlpha(1)
            base_x = scale(self.base_value)
            if base_x is not None:
                canvas.setStrokeColor(GOLD)
                canvas.setLineWidth(1.2)
                canvas.line(base_x, gauge_y - 7 * mm, base_x, gauge_y + 7 * mm)
            market_x = scale(self.market_price)
            if market_x is not None:
                canvas.setFillColor(TEAL_DARK)
                canvas.circle(market_x, gauge_y, 3.2, stroke=0, fill=1)
                canvas.setFont("Helvetica-Bold", 4.5)
                canvas.drawCentredString(market_x, gauge_y + 10 * mm, "MARKET")

        canvas.setFillColor(TEAL_DARK)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(8 * mm, 73 * mm, _num(self.market_price))
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 4.2)
        canvas.drawString(8 * mm, 69 * mm, f"CURRENT PRICE / {self.currency or 'CURRENCY N/A'}")
        canvas.setFont("Helvetica", 3.9)
        canvas.drawString(8 * mm, 43 * mm, f"LOW {_num(self.low_value)}")
        canvas.drawCentredString(width / 2, 43 * mm, f"BASE {_num(self.base_value)}")
        canvas.drawRightString(width - 8 * mm, 43 * mm, f"HIGH {_num(self.high_value)}")
        canvas.setStrokeColor(GOLD)
        canvas.setLineWidth(1.7)
        canvas.line(8 * mm, 12 * mm, 24 * mm, 12 * mm)


def _chapter_title(title: str, styles: Mapping[str, ParagraphStyle]) -> Table:
    table = Table([[Paragraph(f"<b>{_inline(title)}</b>", styles["chapter"])]], colWidths=[174 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 3, TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _subheading(title: str, styles: Mapping[str, ParagraphStyle]) -> Table:
    table = Table([[Paragraph(f"<b>{_inline(title)}</b>", styles["section"])]], colWidths=[174 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 2, colors.HexColor("#2B78B8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return table


def _box(title: str, lines: Sequence[str], background: colors.Color, styles: Mapping[str, ParagraphStyle], accent: colors.Color = TEAL) -> Table:
    rows: list[list[Any]] = [[_p(title, styles, "section")]]
    rows.extend([[_rich_bullet(line, styles, accent)] for line in lines])
    table = Table(rows, colWidths=[174 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _rich_bullet(text: str, styles: Mapping[str, ParagraphStyle], accent: colors.Color) -> Paragraph:
    return Paragraph(f"<font color='{accent.hexval()}'>●</font> {_inline(text)}", styles["body"])


def _table(rows: Sequence[Sequence[str]], styles: Mapping[str, ParagraphStyle]) -> Table:
    if not rows:
        return Table([[""]], colWidths=[174 * mm])
    count = max(len(row) for row in rows)
    widths = [174 * mm / count] * count
    cooked: list[list[Any]] = []
    for row_index, row in enumerate(rows):
        cooked.append([Paragraph(_inline(str(cell)), styles["table_head" if row_index == 0 else "table"]) for cell in list(row) + [""] * (count - len(row))])
    table = Table(cooked, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE_BLUE]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _metric(label: str, value: str, note: str, styles: Mapping[str, ParagraphStyle]) -> Table:
    table = Table([[_p(label, styles, "metric_label")], [_p(value, styles, "metric_value")], [_p(note, styles, "small")]], colWidths=[40 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F4F6")), ("BOX", (0, 0), (-1, -1), 0.4, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _render_blocks(blocks: Sequence[Block], styles: Mapping[str, ParagraphStyle], *, skip_title: bool = True) -> list[Any]:
    story: list[Any] = []
    first_h2 = True
    for block in blocks:
        if block.kind == "title":
            if not skip_title:
                story.append(_chapter_title(str(block.value), styles))
            continue
        if block.kind == "h2":
            if not first_h2:
                story.append(PageBreak())
            first_h2 = False
            story.extend([_chapter_title(str(block.value), styles), Spacer(1, 1 * mm)])
            continue
        if block.kind in {"h3", "h4"}:
            story.extend([_subheading(str(block.value), styles), Spacer(1, 0.5 * mm)])
            continue
        if block.kind == "paragraph":
            story.append(_p(str(block.value), styles))
        elif block.kind == "quote":
            story.append(_box("重要揭露", [str(block.value)], BEIGE, styles, accent=GOLD))
        elif block.kind == "bullets":
            story.extend([_rich_bullet(str(value), styles, TEAL) for value in block.value])
        elif block.kind == "table":
            story.extend([_table(block.value, styles), Spacer(1, 2 * mm)])
        elif block.kind == "rule":
            story.append(HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=2 * mm, spaceAfter=2 * mm))
    return story


def _target_labels(report: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return local display name, ticker and legal name for any target.

    The PDF renderer is shared by every target profile.  Keeping the labels
    derived from the canonical report prevents a previous target's branding
    from leaking into a newly generated report.
    """

    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    local_name = next((str(item) for item in aliases if any("一" <= char <= "鿿" for char in str(item))), str(target.get("name") or "標的"))
    symbol = str(target.get("symbol") or "")
    ticker = symbol.split(".", 1)[0] if symbol else ""
    legal_name = str(target.get("name") or local_name)
    return local_name, ticker, legal_name


def _footer(font: str, report: Mapping[str, Any]):
    local_name, ticker, _ = _target_labels(report)
    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    symbol = str(target.get("symbol") or ticker)
    footer_label = f"{local_name}（{symbol}）專業個股研究報告｜研究用途"

    def draw(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        if doc.page > 1:
            canvas.setFont(font, 7)
            canvas.setFillColor(TEAL)
            canvas.drawString(18 * mm, 285 * mm, f"{local_name}｜{ticker}")
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
        canvas.setFont(font, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm, footer_label)
        canvas.drawRightString(192 * mm, 9 * mm, f"第 {doc.page} 頁")
        canvas.restoreState()
    return draw


def _quick_card(report: Mapping[str, Any], styles: Mapping[str, ParagraphStyle]) -> Table:
    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    card = report.get("decision_card") if isinstance(report.get("decision_card"), Mapping) else {}
    rng = card.get("target_range") if isinstance(card.get("target_range"), Mapping) else {}
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    local_name = next((str(item) for item in aliases if any("一" <= char <= "鿿" for char in str(item))), str(target.get("name") or "標的"))
    questions = ["確認研究目的與評等", "確認標的、市場、幣別與 as-of", "檢查財務趨勢與盈餘品質", "拆解公司與產業位置", "交叉核對官方／監管來源", "固定預測假設與 lineage", "檢查估值方法是否收斂", "界定事件研究能否支持因果", "讀社群原文但不升格為事實", "判斷新聞與社群是否有可比 claim", "寫出重跑觸發器與反證條件"]
    rows: list[list[Any]] = [[Paragraph("研究會議速查卡（可單獨列印）", styles["card_head"])] ]
    rows.append([_box("本次標的", [f"{local_name}（{target.get('symbol')}）｜目前觀點 {card.get('rating')}｜信心 {card.get('confidence')}｜基準估值 {_num(rng.get('base'))}。"], PALE_GREEN, styles, accent=GREEN)])
    rows.append([_box("今天一定要拿到", ["標的身分與時間對齊。", "至少兩條獨立官方／市場證據路徑。", "來源衝突、社群原文與反證條件。"], PALE_BLUE, styles)])
    rows.append([_table([["順序", "必問問題"]] + [[str(i), q] for i, q in enumerate(questions, 1)], styles)])
    rows.append([_box("絕對不要做", ["把社群討論當成事實或共識。", "把 weighted target 當成保證價格。", "把描述性事件研究寫成因果。", "把失敗來源從稽核紀錄刪掉。"], PALE_RED, styles, accent=RED)])
    table = Table(rows, colWidths=[174 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL), ("BOX", (0, 0), (-1, -1), 1.2, TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def render_professional_markdown_pdf(markdown_path: Path, report: Mapping[str, Any], output_path: Path, *, cover_style: str = DEFAULT_COVER_STYLE) -> Path:
    """Render the canonical Markdown report with the standard market-data cover."""
    regular, bold = _register_fonts()
    styles = _styles(regular, bold)
    target = report.get("target") if isinstance(report.get("target"), Mapping) else {}
    card = report.get("decision_card") if isinstance(report.get("decision_card"), Mapping) else {}
    rng = card.get("target_range") if isinstance(card.get("target_range"), Mapping) else {}
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    local_name = next((str(item) for item in aliases if any("一" <= char <= "鿿" for char in str(item))), str(target.get("name") or "標的"))
    blocks = parse_markdown(markdown_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(output_path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=17 * mm, bottomMargin=19 * mm, title=f"{local_name}（{target.get('symbol')}）專業個股研究報告", author="Evidence Research Pipeline")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="markdown-report", frames=[frame], onPage=_footer(regular, report))])
    story: list[Any] = []

    # Standard market-data cover: identity lockup, large year/title, valuation motif, scope and footer metadata.
    story.append(Spacer(1, 4 * mm))
    local_name, ticker, legal_name = _target_labels(report)
    lockup = Table([[Paragraph(ticker, ParagraphStyle("lockup", fontName=bold, fontSize=11, textColor=colors.HexColor("#0C78BD"))), _p(f"{local_name}\n{legal_name}", styles, "cover_kicker")]], colWidths=[27 * mm, 105 * mm], hAlign="LEFT")
    lockup.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    story.extend([lockup, Spacer(1, 11 * mm)])
    cover_left = [
        _p(str(card.get("market_price_as_of") or report.get("generated_at") or "2026")[:4], styles, "cover_year"),
        _p(f"{local_name}\n專業個股研究報告", styles, "cover_title"),
        Spacer(1, 3 * mm), _p("EQUITY RESEARCH REPORT\nINVESTMENT RESEARCH & AUDITABLE SECOND OPINION", styles, "cover_subtitle"),
        Spacer(1, 7 * mm), _p("本報告依 Research Pack 的官方／監管、財務、市場、新聞與公開社群證據，提供可稽核的投資研究第二意見。", styles, "body"),
    ]
    cover = Table([[
        cover_left,
        CoverArt(
            style=cover_style,
            market_price=card.get("market_price"),
            base_value=rng.get("base"),
            low_value=rng.get("low"),
            high_value=rng.get("high"),
            currency=str(target.get("currency") or ""),
            price_points=_cover_price_points(report),
        ),
    ]], colWidths=[94 * mm, 72 * mm], hAlign="LEFT")
    cover.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    story.append(cover)
    story.extend([Spacer(1, 7 * mm), _box("報告基本資料", [f"報告日期：{str(report.get('generated_at') or '未提供')[:10]}　資料截止：{str(card.get('market_price_as_of') or '未提供')[:10]}", f"市場／幣別：{target.get('market')}／{target.get('currency')}　研究期間：{card.get('horizon', '12 months')}", f"目前觀點：{card.get('rating')}　信心：{card.get('confidence')}　基準估值：{_num(rng.get('base'))}", "本報告為研究用途，不構成個人化投資建議；完整限制與稽核資訊見附錄。"], BEIGE, styles, accent=GOLD), Spacer(1, 8 * mm), HRFlowable(width="27%", thickness=2, color=GOLD, spaceBefore=1 * mm, spaceAfter=5 * mm, hAlign="LEFT"), _p(f"報告識別：{report.get('report_id') or '未提供'}", styles, "cover_meta"), PageBreak()])

    # Summary page with a left navigation rail and the canonical decision block.
    nav = ["決策摘要", "報告依據與範疇", "投資論點", "公司與產業", "財務與估值", "市場與事件", "風險與 ESG", "結論與附錄"]
    sidebar_content: list[Any] = [_p(local_name, styles, "sidebar_head"), _p(ticker, styles, "sidebar"), Spacer(1, 3 * mm)]
    for idx, item in enumerate(nav):
        style = "sidebar_head" if idx == 0 else "sidebar"
        sidebar_content.append(_p(("● " if idx == 0 else "") + item, styles, style))
        sidebar_content.append(Spacer(1, 1.5 * mm))
    sidebar = Table([[sidebar_content]], colWidths=[43 * mm], hAlign="LEFT")
    sidebar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F2F5")), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    summary_blocks = [block for block in blocks if block.kind in {"h2", "paragraph", "table", "h3", "bullets", "quote"}]
    decision_index = next((idx for idx, block in enumerate(summary_blocks) if block.kind == "h2" and "決策摘要" in str(block.value)), None)
    decision_blocks: list[Block] = []
    if decision_index is not None:
        for block in summary_blocks[decision_index:]:
            if block.kind == "h2" and decision_blocks and block is not summary_blocks[decision_index]:
                break
            decision_blocks.append(block)
    main_story: list[Any] = [_chapter_title("摘要｜決策摘要與核心結論", styles)]
    rating = "審慎" if str(card.get("rating")) == "Cautious" else str(card.get("rating") or "未提供")
    metric_table = Table([[_metric("研究觀點", rating, f"信心 {card.get('confidence')}", styles), _metric("末筆市場價格", f"{_num(card.get('market_price'))} {target.get('currency')}", str(card.get('market_price_as_of') or '')[:10], styles), _metric("12 個月基準", f"{_num(rng.get('base'))}", f"區間 {_num(rng.get('low'))}-{_num(rng.get('high'))}", styles)]], colWidths=[42 * mm, 42 * mm, 42 * mm])
    main_story.extend([metric_table, Spacer(1, 3 * mm)])
    if decision_blocks:
        rendered = _render_blocks(decision_blocks[1:], styles)
        main_story.extend(rendered[:12])
    else:
        main_story.append(_p("決策摘要區塊未能從 Markdown 解析；請回到 canonical Markdown 與 JSON。", styles))
    summary_layout = Table([[sidebar, main_story]], colWidths=[46 * mm, 122 * mm], hAlign="LEFT")
    summary_layout.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    story.extend([summary_layout, PageBreak()])

    # Full正文: canonical Markdown, including every table and source paragraph.
    # The summary page above is a navigation/decision view; the body below is
    # rendered once from the canonical report, starting at the first numbered
    # chapter so that the title and decision section are not duplicated.
    body_blocks: list[Block] = []
    in_decision = False
    after_decision = False
    for block in blocks:
        if block.kind == "h2" and "決策摘要" in str(block.value):
            in_decision = True
            continue
        if in_decision and not after_decision:
            if block.kind == "h2":
                after_decision = True
                body_blocks.append(block)
            continue
        if after_decision:
            body_blocks.append(block)
    reference_index = next(
        (index for index, block in enumerate(body_blocks) if block.kind == "h2" and "參考來源" in str(block.value)),
        None,
    )
    report_blocks = body_blocks if reference_index is None else body_blocks[:reference_index]
    reference_blocks = [] if reference_index is None else body_blocks[reference_index:]
    story.extend(_render_blocks(report_blocks, styles))
    story.extend([PageBreak(), _chapter_title("附錄 B｜研究會議速查卡", styles), Spacer(1, 2 * mm), _p("本頁可單獨列印；它是研究流程的操作卡，不取代正式研究正文。", styles), _quick_card(report, styles)])
    if reference_blocks:
        # References are deliberately the final PDF section.  The operational
        # quick card is useful, but it must not push the audit trail away from
        # the end of the document where readers expect it.
        story.append(PageBreak())
        story.extend(_render_blocks(reference_blocks, styles))
    doc.build(story)
    return output_path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    report_path = root / "output/targets/tsmc/tsmc-2330tw-research-report.json"
    markdown_path = root / "output/targets/tsmc/tsmc-2330tw-professional-report.md"
    output_path = root / "output/targets/tsmc/tsmc-2330tw-professional-report.pdf"
    print(render_professional_markdown_pdf(markdown_path, json.loads(report_path.read_text(encoding="utf-8")), output_path))


if __name__ == "__main__":
    main()
