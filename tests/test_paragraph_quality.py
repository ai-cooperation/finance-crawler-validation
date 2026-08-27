from __future__ import annotations

from finance_crawler_poc.paragraph_quality import audit_markdown_report, aggregate_paragraph_audits


def test_every_content_block_is_audited_and_machine_payload_fails() -> None:
    markdown = """# 台泥（1101.TW）專業個股研究報告

## 4、產業與競爭定位

台泥在土耳其與歐洲的水泥需求曝險，仍須對照 2025 年銷量與售價。[產業統計](https://example.test/stat)

- **industry-001：** 美國 GDP 成長帶動水泥需求。
  - 類型／信心／證據品質：fact／high／direct

| 指標 | 數值 |
|---|---:|
| 銷量 | 10 |

## 參考來源

[1] https://example.test/stat
"""

    audit = audit_markdown_report(markdown, target={"symbol": "1101.TW", "name": "台泥"}, as_of="2026-08-26T00:00:00Z")

    assert audit["summary"]["audited_block_count"] == len(audit["paragraphs"])
    assert {item["block_type"] for item in audit["paragraphs"]} >= {"prose", "list", "table", "reference"}
    assert any("machine_payload_in_body" in item["issues"] for item in audit["paragraphs"])
    assert audit["summary"]["release_ready"] is False


def test_duplicate_and_generic_method_paragraph_are_detected() -> None:
    generic = "讀者應先確認來源，再判斷資料是否一致；本章不是提醒清單，而是後續研究的輸入。"
    markdown = f"""# 華碩（2357.TW）專業個股研究報告

## 3、公司與商業模式

{generic}

{generic}
"""

    audit = audit_markdown_report(markdown, target={"symbol": "2357.TW", "name": "華碩"}, as_of="2026-08-26T00:00:00Z")

    assert sum("duplicate_block" in item["issues"] for item in audit["paragraphs"]) == 2
    assert any("generic_process_prose" in item["issues"] for item in audit["paragraphs"])


def test_cross_target_issue_becomes_systemic_at_four_of_seven() -> None:
    audits = []
    for index in range(7):
        paragraph = "讀者應確認來源與期間。" if index < 4 else f"標的 {index} 於 2025 年營收成長 10％。[證據](https://example.test/{index})"
        audits.append(
            audit_markdown_report(
                f"# 標的 {index}\n\n## 4、產業與競爭定位\n\n{paragraph}\n",
                target={"symbol": f"{index}.TW", "name": f"標的 {index}"},
                as_of="2026-08-26T00:00:00Z",
            )
        )

    aggregate = aggregate_paragraph_audits(audits)

    issue = next(item for item in aggregate["failure_modes"] if item["issue"] == "generic_process_prose")
    assert issue["target_count"] == 4
    assert issue["systemic"] is True


def test_unescaped_space_in_markdown_url_is_blocking() -> None:
    audit = audit_markdown_report(
        "# 台泥\n\n## 3、公司與商業模式\n\n[年報](https://example.test/annual report.pdf)\n",
        target={"symbol": "1101.TW", "name": "台泥"},
        as_of="2026-08-26T00:00:00Z",
    )

    assert any("invalid_markdown_url" in item["issues"] for item in audit["paragraphs"])
    assert audit["summary"]["release_ready"] is False


def test_internal_requirement_id_and_failed_model_notice_are_blocking_in_body() -> None:
    markdown = """# 台泥（1101.TW）專業個股研究報告

## 3、公司與商業模式

原有公司與商業模式模型敘事未通過 requirement、期間或決策傳導驗證，已移至質化主張稽核表。

- company.business_model
- segment.disclosure

## 附錄 B、質化主張稽核表

- company.business_model
"""

    audit = audit_markdown_report(
        markdown,
        target={"symbol": "1101.TW", "name": "台泥"},
        as_of="2026-08-26T00:00:00Z",
    )

    body_failures = [
        item
        for item in audit["paragraphs"]
        if item["chapter"] == "3" and item["issues"]
    ]
    assert any("internal_requirement_id_in_body" in item["issues"] for item in body_failures)
    assert any("generic_process_prose" in item["issues"] for item in body_failures)
    assert not any(
        "internal_requirement_id_in_body" in item["issues"]
        for item in audit["paragraphs"]
        if str(item["chapter_title"]).startswith("附錄")
    )
    assert audit["summary"]["release_ready"] is False


def test_synthetic_catalyst_placeholder_is_blocking() -> None:
    markdown = """# 台泥（1101.TW）專業個股研究報告

## 10、催化劑與事件日曆

- **外部新聞線索（待公司／監管原文驗證）**｜時間：未來 12 個月／日期待公司公告
- **產業需求與價格變化**｜時間：下一次正式公告
"""

    audit = audit_markdown_report(
        markdown,
        target={"symbol": "1101.TW", "name": "台泥"},
        as_of="2026-08-26T00:00:00Z",
    )

    assert any("synthetic_research_placeholder" in item["issues"] for item in audit["paragraphs"])
    assert audit["summary"]["release_ready"] is False
