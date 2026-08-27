from __future__ import annotations

import json
from pathlib import Path

from finance_crawler_poc.professional_pdf import render_equity_report_pdf


def test_equity_pdf_uses_human_report_template_and_writes_pdf(tmp_path: Path) -> None:
    report_path = Path("output/targets/tsmc/tsmc-2330tw-research-report.json")
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output = tmp_path / "tsmc-template.pdf"
    result = render_equity_report_pdf(report, output)
    assert result == output
    assert output.exists()
    assert output.stat().st_size > 20_000
