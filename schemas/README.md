# 資料契約版本規則

`schemas/` 是 GitHub 批次、Cloudflare Ingest Worker、D1／R2 與後續 MCP 之間的邊界契約。

- `schema_version` 是 major version。刪欄位、改型別、改語義或收緊既有合法值時必須升 major，並在 migration 期間平行接受舊版與新版。
- 同一 major 只允許不破壞既有 consumer 的修正；所有 schema 使用 `additionalProperties: false`，新增欄位前仍須同步更新 producer、consumer 與測試。
- 外部 payload 進入系統時先驗 schema，再驗跨欄位 invariant。HTTP 200、JSON 可解析或物件已上傳都不代表可發布。
- raw item 的 identity 是 `source_id + canonical_url + content_sha256`。內容修訂形成新 item，完全相同的重放保持 idempotent。
- metadata-only 來源的 `public_excerpt_chars` 必須為 0。完整 raw、topic evidence、研究報告與稽核封存預設只進 private R2，不提交 Git history。
- `current_snapshot` 只在 raw evidence 與 topic snapshot 都驗證、持久化成功後切換；失敗必須保留 last-good snapshot。
- `research-pack` 若由新版 Planner 產生，會攜帶 frozen `requirement`、`source_bundle_plan` 與由報告 claims 推導的 optional `evidence_graph`；既有 v1 consumer 仍可忽略這些 optional fields。新產生的 Pack 必須寫入 graph，且每條 edge 的 evidence ID 必須能在同一 Pack 的 evidence appendix 找到。

目前契約：source record、raw item、ingest envelope、topic snapshot、status response、soak observation、market snapshot、research report、research job、research-job-complete、research-job-failure、research-job-status、research requirement、source-bundle manifest、Research Pack、audit event，均為 version 1。`research-report` 以 optional `report_profile`、摘要、催化劑、失效條件與資料缺口欄位保持 v1 replay 相容；新 job 依 frozen `requested_outputs` 決定產生 detailed／quick report 或只保留 evidence appendix。`research-pack.evidence_graph` 同樣是 optional 的 v1-compatible extension，但新 Pack 會固定產生 claim ID、category、report／topic 關聯與 evidence IDs。`status-response` 是公開只讀 operational API；`soak-observation` 是 schedule-only 的私有 D1 證據；`research-job` 是 App A 的非同步請求；`research-job-complete` 是 GitHub OIDC success callback，除了 run／commit identity 也必須帶回 frozen `research_target`、`research_requirement_id` 與 approved `research_source_ids`；`research-job-failure` 是 GitHub OIDC failure callback，帶回 frozen target／requirement 與明確 error code，將 admission denial／workflow failure 寫成可重試的 `blocked`／`failed` job；`research-job-status` 是 MCP 的可恢復狀態回應，包含 stage、progress、retryable 與 next_action；`research-requirement` 與 `source-bundle-manifest` 是 Planner 的可稽核交接物；`research-pack` 是 private R2 的跨應用交接物。它們都不把 private raw content 放入 public artifact。
