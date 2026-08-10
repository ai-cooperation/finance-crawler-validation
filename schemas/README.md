# 資料契約版本規則

`schemas/` 是 GitHub 批次、Cloudflare Ingest Worker、D1／R2 與後續 MCP 之間的邊界契約。

- `schema_version` 是 major version。刪欄位、改型別、改語義或收緊既有合法值時必須升 major，並在 migration 期間平行接受舊版與新版。
- 同一 major 只允許不破壞既有 consumer 的修正；所有 schema 使用 `additionalProperties: false`，新增欄位前仍須同步更新 producer、consumer 與測試。
- 外部 payload 進入系統時先驗 schema，再驗跨欄位 invariant。HTTP 200、JSON 可解析或物件已上傳都不代表可發布。
- raw item 的 identity 是 `source_id + canonical_url + content_sha256`。內容修訂形成新 item，完全相同的重放保持 idempotent。
- metadata-only 來源的 `public_excerpt_chars` 必須為 0。完整 raw、topic evidence、研究報告與稽核封存預設只進 private R2，不提交 Git history。
- `current_snapshot` 只在 raw evidence 與 topic snapshot 都驗證、持久化成功後切換；失敗必須保留 last-good snapshot。

目前契約：source record、raw item、ingest envelope、topic snapshot、market snapshot、research report、audit event，均為 version 1。
