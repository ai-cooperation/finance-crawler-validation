from __future__ import annotations

import json
import subprocess
from pathlib import Path

from finance_crawler_poc.professional_hybrid_pdf import render_professional_hybrid_pdf


def test_hybrid_pdf_keeps_report_title_and_full_research_sections(tmp_path: Path) -> None:
    report_path = Path("output/targets/tsmc/tsmc-2330tw-research-report.json")
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output = tmp_path / "tsmc-professional-hybrid.pdf"
    result = render_professional_hybrid_pdf(report, output)
    assert result == output
    assert output.exists()
    assert output.stat().st_size > 40_000

    text_path = tmp_path / "report.txt"
    subprocess.run(["pdftotext", "-layout", str(output), str(text_path)], check=True)
    text = text_path.read_text(encoding="utf-8")
    assert "台積電（2330.TW）專業個股研究報告" in text
    assert "決策摘要" in text
    assert "歷史財務與盈餘品質" in text
    assert "估值與敏感度" in text
    assert "證據、模型與稽核附錄" in text
    assert "研究會議速查卡" in text
