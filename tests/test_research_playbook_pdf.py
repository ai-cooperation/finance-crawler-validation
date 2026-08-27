from __future__ import annotations

import json
import subprocess
from pathlib import Path

from finance_crawler_poc.research_playbook_pdf import render_equity_playbook_pdf


def test_equity_playbook_pdf_matches_reference_structure(tmp_path: Path) -> None:
    report_path = Path("output/targets/tsmc/tsmc-2330tw-research-report.json")
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output = tmp_path / "tsmc-playbook.pdf"
    result = render_equity_playbook_pdf(report, output)
    assert result == output
    assert output.exists()
    assert output.stat().st_size > 20_000

    text_path = tmp_path / "tsmc-playbook.txt"
    subprocess.run(["pdftotext", "-layout", str(output), str(text_path)], check=True)
    text = text_path.read_text(encoding="utf-8")
    assert "MEETING PLAYBOOK" in text
    assert "問題腳本（照順序問）" in text
    assert "判讀訊號速查" in text
    assert "速查卡（可單獨列印" in text
    assert "社群原文" in text
