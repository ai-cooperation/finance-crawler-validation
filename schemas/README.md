# 資料契約版本規則

`schemas/` 是 GitHub 批次、Cloudflare Ingest Worker、D1／R2 與後續 MCP 之間的邊界契約。

- `schema_version` 是 major version。刪欄位、改型別、改語義或收緊既有合法值時必須升 major，並在 migration 期間平行接受舊版與新版。
- 同一 major 只允許不破壞既有 consumer 的修正；所有 schema 使用 `additionalProperties: false`，新增欄位前仍須同步更新 producer、consumer 與測試。
- 外部 payload 進入系統時先驗 schema，再驗跨欄位 invariant。HTTP 200、JSON 可解析或物件已上傳都不代表可發布。
- raw item 的 identity 是 `source_id + canonical_url + content_sha256`。內容修訂形成新 item，完全相同的重放保持 idempotent。
- metadata-only 來源的 `public_excerpt_chars` 必須為 0。完整 raw、topic evidence、研究報告與稽核封存預設只進 private R2，不提交 Git history。
- `current_snapshot` 只在 raw evidence 與 topic snapshot 都驗證、持久化成功後切換；失敗必須保留 last-good snapshot。
- `research-pack` 若由新版 Planner 產生，會攜帶 frozen `requirement`、`source_bundle_plan` 與由報告 claims 推導的 optional `evidence_graph`；既有 v1 consumer 仍可忽略這些 optional fields。新產生的 Pack 必須寫入 graph，且每條 edge 的 evidence ID 必須能在同一 Pack 的 evidence appendix 找到。

共用研究管線另外固定三個邊界契約，所有應用（議題雷達、投資研究、保險研究、產業研究）共用同一套語義：

- `research-plan` 在任何外部 retrieval 前凍結用戶問題、target identity 與 Required Data Contracts；各輪使用同一 `question_sha256`。
- `research-gap-plan` 只列出對決策有重要性的未滿足 requirements 和未嘗試 source routes；不允許用不相干來源灌高 coverage。
- `research-iteration-history` 保存 Round 0 庫存檢查及最多四輪收集的 coverage delta、route attempts、new critical requirements 與 stop reason。

- `source-registry` 把「來源出版者」與「抓取路徑」分開記錄。RSS、Browser、API 只是 transport；`publisher_id` 與 `independence_group` 才決定能否算獨立佐證，聚合器不能自動算獨立來源。
- `provider-catalog` 保存 GitHub 公開 API inventories 與官方文件交叉驗證後的來源能力。候選發現、live verification、認證／成本、rights 與 callable state 分開；`exact`、`derived`、`proxy` 不得互相冒充。Evidence Gap Broker 只輸出候選與阻擋原因，參數、secret 或 adapter 不完整時不能生成 collector route。
- `provider-activation-registry` 為每個尚未 route-integrated 的 provider 固定 adapter family、執行 runtime、probe scope、設定名稱與 next action；商業、blocked、deprecated provider 只能是 `not_executable`。
- `provider-activation-report` 保存每次受控 health probe 的狀態碼、final URL、content type、最多 64 KiB 樣本雜湊與重試次數，並分開計算 survival 與 data-payload verification。它不能直接改寫 provider catalog 的 callable state。
- `canonical-evidence` 保留每一筆 raw item，同時用 `canonical_story_id`、`duplicate_of` 與 `story_groups` 把同一則新聞的轉載、搜尋與 RSS 路徑去重。`canonical_items` 只供分析使用，raw `items` 仍是稽核與重播依據。
- `quality-gate` 是 fail-closed 的共用就緒判定。`professional_ready` 必須由所有必要檢查通過才能產生；資料可讀但缺官方／監管來源、獨立直接出版者、估值期間對齊或事件研究時，狀態只能是 `professional_partial` 或 `research_only`，不能由呼叫端覆寫。

Equity 的 `valuation_period_alignment` 以明示的共同 fiscal-year label 判定，並保留每個 peer 的實際 `as_of`；這不等同同日 LTM。`event_alignment.event_study_status=available` 只代表存在可對照事件，不代表研究完成；只有 `event_study_quality_status=complete`、至少八個獨立事件日期、顯著性已計算且 `unresolved_event_count=0` 才能通過 professional-ready gate。事件仍必須保留 benchmark 的 target／benchmark pre/post observation、abnormal return 與 `causal_status=unresolved`，不可宣稱因果。

Market provider payloads also carry a deterministic `source_ref.item_id` derived from provider route, canonical URL, and response SHA-256; that ID must be present in the time-series `source_item_ids`. Valuation alignment metadata is retained in the financial-depth object, and conflict analysis counts canonical `independence_group` values rather than transport routes. SEC filing keyword anchors are coverage markers only; they are not numeric financial claims until a table parser validates units and periods.

`financial-depth` 以 optional `evidence_pack` 與 `quality_gate` 回傳上述結果，讓每份人類可讀報告都能指出「目前資料深度」和「阻塞原因」，而不是把抓到資料誤寫成研究完成。

目前契約：source record、raw item、ingest envelope、topic snapshot、status response、soak observation、market snapshot、financial depth、time-series snapshot、research report、research job、research-job-complete、research-job-failure、research-job-status、research requirement、source-bundle manifest、Research Pack、audit event，以及上述 `source-registry`、`provider-catalog`、`provider-activation-registry`、`provider-activation-report`、`canonical-evidence`、`quality-gate`，均為 version 1。`research-report` 以 optional `report_profile`、摘要、催化劑、失效條件、資料缺口與 `professional_analysis` 欄位保持 v1 replay 相容；新 job 依 frozen `requested_outputs` 決定產生 detailed／quick report 或只保留 evidence appendix。`financial_depth` 將歷史市場序列、基本面狀態、明示同業倍數與估值狀態、非預測情境、日期事件對齊與來源衝突矩陣交給高階模型；缺資料時保留明確狀態，不以零補值，事件對齊固定標記為非因果。`research-pack.evidence_graph` 同樣是 optional 的 v1-compatible extension，但新 Pack 會固定產生 claim ID、category、report／topic 關聯與 evidence IDs。`status-response` 是公開只讀 operational API；`soak-observation` 是 schedule-only 的私有 D1 證據；`research-job` 是 App A 的非同步請求；`research-job-complete` 是 GitHub OIDC success callback，除了 run／commit identity 也必須帶回 frozen `research_target`、`research_requirement_id` 與 approved `research_source_ids`；`research-job-failure` 是 GitHub OIDC failure callback，帶回 frozen target／requirement 與明確 error code，將 admission denial／workflow failure 寫成可重試的 `blocked`／`failed` job；`research-job-status` 是 MCP 的可恢復狀態回應，包含 stage、progress、retryable 與 next_action；`research-requirement` 與 `source-bundle-manifest` 是 Planner 的可稽核交接物；`research-pack` 是 private R2 的跨應用交接物。它們都不把 private raw content 放入 public artifact。
