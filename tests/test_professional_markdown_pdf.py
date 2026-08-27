from __future__ import annotations

import subprocess
from pathlib import Path

from finance_crawler_poc.professional_markdown_pdf import (
    DEFAULT_COVER_STYLE,
    SUPPORTED_COVER_STYLES,
    CoverArt,
    _cover_price_points,
    _cover_asset_path,
    _inline,
    render_professional_markdown_pdf,
)


def test_markdown_pdf_preserves_canonical_report_content_and_market_data_cover(tmp_path: Path) -> None:
    markdown_path = Path("output/targets/tsmc/tsmc-2330tw-professional-report.md")
    report_path = Path("output/targets/tsmc/tsmc-2330tw-research-report.json")
    if not markdown_path.exists() or not report_path.exists():
        return
    import json

    report = json.loads(report_path.read_text(encoding="utf-8"))
    output = tmp_path / "tsmc-markdown-professional.pdf"
    result = render_professional_markdown_pdf(markdown_path, report, output)
    assert result == output
    assert output.stat().st_size > 40_000
    text_path = tmp_path / "report.txt"
    subprocess.run(["pdftotext", "-layout", str(output), str(text_path)], check=True)
    text = text_path.read_text(encoding="utf-8")
    assert "台積電（2330.TW）專業個股研究報告" in text
    assert "五年年度趨勢" in text
    assert "自由現金流以營業現金流減資本支出" in text
    assert "附錄 A、證據、模型與揭露" in text
    assert "研究會議速查卡" in text
    assert "sec.gov" in text


def test_inline_markdown_links_remain_clickable_and_auditable() -> None:
    rendered = _inline("40.00％ [證據](https://www.sec.gov/example?id=40)")
    assert '<link href="https://www.sec.gov/example?id=40">' in rendered
    assert "證據" in rendered
    assert "完整連結" in rendered


def test_long_sec_paths_do_not_render_as_fake_truncated_urls() -> None:
    url = "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm"
    rendered = _inline(f"[SEC EDGAR]({url})")
    assert f'<link href="{url}">' in rendered
    assert "www.sec.gov（完整連結）" in rendered
    assert "00016…" not in rendered


def test_reference_list_prints_full_urls_as_clickable_text() -> None:
    url = "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm"
    rendered = _inline(f"[1] SEC EDGAR — [{url}]({url})")
    assert f'<link href="{url}">{url}</link>' in rendered


def test_numbered_citation_links_to_reference_anchor() -> None:
    rendered = _inline('[[1]](#ref-1)')
    assert '<link href="#ref-1">[1]</link>' in rendered


def test_reference_anchor_is_emitted_for_internal_jump() -> None:
    rendered = _inline('<a id="ref-1"></a>[1] SEC EDGAR')
    assert '<a name="ref-1"/>' in rendered


def test_inline_markdown_links_support_bracketed_labels() -> None:
    rendered = _inline("[TSMC Uses Old Fabs [video]](https://news.ycombinator.com/item?id=49325115)")
    assert '<link href="https://news.ycombinator.com/item?id=49325115">' in rendered
    assert "TSMC Uses Old Fabs [video]" in rendered
    assert "[video)]" not in rendered
    assert "news.ycombinator.com/item" in rendered


def test_cover_uses_original_vector_motif_assets() -> None:
    svg = Path("assets/report-cover-data-network.svg")
    png = Path("assets/report-cover-data-network.png")
    assert svg.exists() and png.exists()
    source = svg.read_text(encoding="utf-8")
    assert "EVIDENCE" in source
    assert "<circle" in source  # nodes, not the reference's overlapping cover circles
    assert "<path" in source and "linearGradient" in source


def test_market_data_cover_is_the_global_default() -> None:
    assert DEFAULT_COVER_STYLE == "market_data"
    assert CoverArt().style == DEFAULT_COVER_STYLE
    asset = _cover_asset_path(DEFAULT_COVER_STYLE)
    assert asset.name == "report-cover-market-data.png"
    assert asset.exists()


def test_market_data_cover_prints_target_specific_values_instead_of_template_numbers(tmp_path: Path) -> None:
    markdown_path = tmp_path / "sample.md"
    markdown_path.write_text(
        "# 測試研究報告\n\n## 決策摘要\n\n本頁驗證封面快照。\n\n## 研究正文\n\n內容。\n",
        encoding="utf-8",
    )
    report = {
        "report_id": "equity_cover_test",
        "generated_at": "2026-08-26T00:00:00Z",
        "target": {
            "aliases": ["測試公司"],
            "name": "Test Corporation",
            "symbol": "9999.TW",
            "market": "TW",
            "currency": "TWD",
        },
        "decision_card": {
            "market_price": 180.0,
            "market_price_as_of": "2026-08-25T00:00:00Z",
            "rating": "Cautious",
            "confidence": "low",
            "horizon": "12 months",
            "target_range": {"low": 12.46, "base": 12.48, "high": 20.28},
        },
        "chapters": [{
            "id": "9",
            "content": {"time_series": {"points": [
                {"observed_at": "2025-08-25T01:00:00Z", "value": 100.0},
                {"observed_at": "2026-01-15T01:00:00Z", "value": 125.0},
                {"observed_at": "2026-08-25T01:00:00Z", "value": 180.0},
            ]}},
        }],
    }
    output = tmp_path / "sample.pdf"
    render_professional_markdown_pdf(markdown_path, report, output)
    text_path = tmp_path / "sample.txt"
    subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-layout", str(output), str(text_path)], check=True)
    cover_text = text_path.read_text(encoding="utf-8")
    assert "MARKET PRICE" in cover_text
    assert "12M ACTUAL PRICE" in cover_text
    assert "3 OBSERVATIONS" in cover_text
    assert "180.00 TWD" in cover_text
    assert "12.48" in cover_text
    assert "12.46–20.28" in cover_text
    assert "2,410" not in cover_text
    assert "2,014" not in cover_text


def test_cover_price_points_uses_latest_twelve_months_and_preserves_order() -> None:
    report = {
        "chapters": [{
            "id": "9",
            "content": {"time_series": {"points": [
                {"observed_at": "2024-01-01T00:00:00Z", "value": 70},
                {"observed_at": "2025-07-01T00:00:00Z", "value": 90},
                {"observed_at": "2025-08-26T00:00:00Z", "value": 100},
                {"observed_at": "2026-08-25T00:00:00Z", "value": 180},
            ]}},
        }],
    }
    points = _cover_price_points(report)
    assert [value for _, value in points] == [100.0, 180.0]


def test_three_professional_cover_styles_are_available() -> None:
    assert SUPPORTED_COVER_STYLES == {"market_data", "valuation_focus", "evidence_network"}
