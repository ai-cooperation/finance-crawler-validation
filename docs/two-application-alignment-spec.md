# 兩個目標應用對齊 SPEC：研究報告產生器＋決策討論助手

更新日期：2026-08-21
文件狀態：應用層規格修正版（Normative Amendment）
適用範圍：`complete-investment-research-assistant-plan.md` 的 Stage 7–9；資料收集、D1／R2、GitHub Actions 與來源驗收仍以既有平台規格為準。

## 0. 這次對齊的結論

系統不是一個把「爬蟲、分析、投資決策」混成單一 Agent 的產品，而是兩個有明確交接邊界的應用：

1. **研究報告產生器（Research Report Generator）**：由 OpenCode Desktop 作為操作介面，使用 Big Pickle 做低成本的任務編排與 MCP tool 呼叫，完成資料補齊、證據整理與詳細研究報告。它不得直接做個人化買賣決策。
2. **決策討論助手（Decision Discussion Assistant）**：由 ChatGPT／Codex 等高階 AI 與使用者對話，先用 Grill Me First 收斂目標、期限、風險與限制，再讀取已完成的 Research Pack／研究報告，產出可稽核的決策 memo。它也不自動下單。

Cloudflare Worker、D1、R2、GitHub Actions、Crawl4AI、OpenBB 與研究模型是兩個應用共用的後台基礎建設，不是使用者直接操作的第三個 Agent。

### 0.1 共用基礎建設與應用 Flow 的邊界（Normative）

MCP 是共用的授權、工作提交、artifact 讀取與稽核邊界；它不是把兩個應用合併成同一個 Agent。應用差異放在客戶端的 skill／flow／plugin：

```text
使用者與應用 Agent 對話
  → 結構化 Research Requirement
  → Research Requirement Planner
  → 檢查既有 snapshot／資料是否足夠
  → 只補缺的 Data Broker（RSS／API／Browser／Crawl4AI／OpenBB／文件引擎）
  → Research Pack
  → 應用 Agent 產出報告或決策 memo
```

Agent 不直接猜 URL、繞過權限或同步等待爬蟲完成；它只提交結構化需求並讀取已授權 artifact。Worker 依來源 manifest、權利、quota、freshness 與 budget 選擇最小必要執行路徑。既有資料足夠時可直接使用 `latest_published`；不足時才建立 `actions` 或其他允許的 refresh job。

目前 App A 的可執行市場 provider 垂直切片是 CoinGecko crypto；含市場資料的 equity／ETF／company／industry／topic request 若沒有對應 provider，Planner 會明確回 `blocked/market_target_not_supported`，不會用不相干的市場資料產生報告。新增 provider 後才能解除這個 gate。

`include_market_data` 是 Research Requirement 的明確邊界，不是由 executor 猜測：

- `true`：需求包含市場資料；Planner 必須選出與 target 對應的 provider，無法對應時回 `blocked/market_target_not_supported`。
- `false`：需求不包含市場資料；Planner 不得加入 market source，Actions 以 `research_include_market_data=false` 傳遞到 workflow，OpenBB alignment 產生 `provider=not_requested`、空的 instruments 與 coverage `0` 的合法快照。下游只能標示「未要求市場資料」，不得把空快照解讀成價格／估值為零，也不得偷偷改抓 CoinGecko。
- 任何非 `true|false` 的 workflow input 在 admission 前拒絕；這個選擇必須保留在 frozen requirement、dispatch input、Research Pack 與 audit 中。

### 0.1.1 任務型 Harness（Normative）

兩個應用共用一個 agent runtime，不建立多個互相轉交上下文的常駐 Agent。每個非同步 job 在 admission 時綁定一個版本化 `harness_pack_id`，由 Harness Pack 決定：允許的 tools／sources、skill／rule、input／output schema、成功標準、fail-closed 條件、retry／callback policy 與 verifier。

本專案固定以下任務類型：

| `task_type` | Harness Pack | 交接 output |
|---|---|---|
| `source_validation` | 120 品牌／166 endpoint 全量能力與 raw capture | `source_matrix.v1` |
| `evidence_normalization` | item extraction、canonical URL／content fingerprint 去重、noise／relevance gate | `normalized_evidence.v1` |
| `signal_evaluation` | 共用 Signal Engine＋domain signal pack，計算可重播的變化／背離／風險／催化／意圖觀察 | `signal_snapshot.v1`、`signal_event.v1` |
| `action_dispatch` | 共用 Action Engine＋domain action pack，依 signal／policy／approval 建立下一個可追蹤任務 | `action_task.v1`、`action_receipt.v1` |
| `target_research` | target resolver、topic／OpenBB／evidence graph、report generator | `research_pack.v1`＋`research_report.v2` |
| `decision_discussion` | Grill Me First、唯讀 evidence tools、policy guard | `decision_memo.v1`＋`decision_trace.v1` |
| `operations_recovery` | callback、retry、alert、last-good、quota、soak | `run_record.v1`＋`recovery_evidence.v1` |

因此流程改為：

```text
MCP submit task
  → Worker 凍結 task_type + harness_pack@version + input hash
  → GitHub Actions／OpenBB／文件引擎執行
  → Signal Engine（必要時）產生 signal snapshot
  → Action Engine（必要時）建立 refresh／enrich／recalculate／review task
  → Harness verifier 判定 pass／partial／blocked
  → D1/R2 寫入 artifact + audit
  → 只有符合 handoff contract 才能交下一個 Harness
```

Big Pickle 只做任務編排與已授權 tool calls；資料收集、正規化、去重與基礎統計不經模型。高階 AI 只在 `decision_discussion` 讀取 frozen Research Pack，不得自行回來源抓取。

Signal／Action 的共用契約、application Pack registry、保險／產業／商機開發的後續擴張順序，以及 `SIG-*`／`ACT-*`／`HAR-*` 的 test matrix，固定於 [`signal-action-harness-spec.md`](./signal-action-harness-spec.md)。本文件只規範目前投資研究報告與決策討論兩個應用的交接；新增領域必須先通過該延伸 SPEC 的 H0–H2，再以自己的 domain Pack 進入本文件同樣的 Research Pack／decision boundary。

為避免平台完整化反過來延後第一個應用，採用該延伸 SPEC 的 MVP 快速路徑：`H0-MVP → H1-MVP → H2-MVP → H3-MVP` 可先交付投資研究候選版；`H1-Full／H2-Full` 在背景並行。候選版若為 `partial` 或 `research_only`，不得被 Gate A／B 或使用者介面誤標為投資級完成。

### 0.1 目前實作狀態（2026-08-21）

App A 已完成第一個可部署垂直切片：`/mcp` 支援 `initialize`、`tools/list`、`tools/call`，`plan_research_sources` 現在會產出需求、來源 bundle 與 snapshot sufficiency decision；`submit_research_job` 會建立帶 planner artifact 的 D1 job，結果寫入私有 R2 Research Pack、第二意見報告與 evidence appendix，並可用同一個 job ID 讀回。Cloudflare `waitUntil` 的背景執行約 30 秒邊界已由遠端 tail 驗證；因此 `collection_scope=full_catalog` 的 MCP 路徑先產出 deterministic evidence-linked report，較長模型推理改走 `source_strategy=actions` 的 GitHub Actions／OIDC callback。`report_profile`／`requested_outputs` 已在本機實作：detailed／compact profile 會傳入模型邊界，僅要求 evidence appendix 時不啟動模型；`0009_research_planner.sql`、`0010_report_profiles.sql` 已套用驗證帳號。`0008_research_jobs.sql`、target-scoped workflow input、Actions dispatch client、`/v1/research/jobs/complete` 成功 callback 與 `/v1/research/jobs/fail` 失敗 callback 已部署。

2026-08-21 已以新設定的 OpenCode／Big Pickle 完成部署後全量鏈：`plan → submit → get_job_status 輪詢 → Research Pack → report → evidence appendix`。BTC 實際 job `research_20260821161302_5aa7f526` 的 Gate A 為 `passed`：135 source groups、181 endpoint attempts、132 normalized／evidence items、3 份 reports、132 筆 appendix；GitHub Actions run `32501782884` 的 OIDC admission、全量收集與 completion callback 均為 success。來源品質仍為 `partial`（16 個 source failures，coverage `0.8741`），所以這是可稽核的研究候選版，不是投資級決策或 100% 來源成功。Cloudflare Worker 的 evidence loading 已改為每 topic 最多 6 筆，避免 full-catalog R2 讀取超時；report-only 的 70B 路徑以 run `32502952521` 通過 HTTP 200，但因同一 Research Pack 已有 deterministic first-opinion 報告而是 `replayed=true`，不冒充新模型生成。正式配額已恢復為每日 2 次、最小間隔 21,600 秒；完整 metadata 見 [`experiments/app-a/20260821-mvp-full-actions.json`](../experiments/app-a/20260821-mvp-full-actions.json)。

### 0.2 本輪本機驗證與未完成項（Normative）

2026-08-22 已完成 `professional-research-report-v2` 的第一次部署後全量驗證：新增的 `financial-depth`／`time-series-snapshot` 契約、365 日 CoinGecko 歷史序列、非預測情境表、估值缺口狀態與 lexical source-conflict screening 均實際寫入 Research Pack。BTC job `research_20260822003634_493b163c` 對應 Actions run `32540891073` 成功，MCP read-back 的 full catalog 為 135 source groups／181 endpoint attempts／149 normalized evidence，coverage `0.9185`，但 10 個 source groups 失敗，因此 pack 是 `partial`。時間序列可用（366 點、365 日 observed return `-33.320429%`、年化波動 `43.781446%`、最大回撤 `-53.049398%`），基本面 provider 尚未配置，crypto intrinsic valuation 明確為 `not_applicable`，情境只是 observed range、`not_a_forecast=true`。本次 full-catalog job 仍固定產生 `deterministic-evidence-v1` first opinion；`professional_analysis.status=professional_partial`，尚未完成高階模型 fresh generation、7／30 日驅動拆解、ETF／衍生品／鏈上資料與校準 stance oracle，因此不能宣稱 `professional_ready`。不含 private raw／token 的完整 redacted evidence 見 [`experiments/app-a/20260822-btc-professional-full-catalog.json`](../experiments/app-a/20260822-btc-professional-full-catalog.json)。

本輪新增的 App A 邊界測試已通過：Worker 共 140 tests；Python gate 全部通過；新增 `finance-app-a-remote` bounded verifier，並通過 `typecheck`、workflow YAML parse、`git diff --check` 與 `deploy:dry`。這些是 CI／發布前證據；遠端鏈證據見 [`experiments/app-a/20260821-mvp-full-actions.json`](../experiments/app-a/20260821-mvp-full-actions.json)。

目前的開發交接固定為：

1. **完成嚴格品質 Gate A 收尾**：部署後 Big Pickle 的 `submit → status → Research Pack → report／appendix` bounded run 已通；下一個 gate 是修復／替換失敗 Browser source、補足與 target 對應的市場資料 provider，並讓品質不再是 `partial`，才能稱 App A 研究品質完整通過。
2. **再完成 report v2／Evidence Graph**：目前 Research Pack 已先加入 v1-compatible `evidence_graph`（穩定 claim ID、report／topic 關聯與 evidence subset assertion）；仍需把報告本身升級成正式 `research_report.v2`、`quality`、`source_bundle_ref`、`evidence_appendix_ref`、claim／counterclaim／unknown graph、expiry 與 usage receipt，且現有 v1 replay 必須維持雙讀。
3. **再開發 App B**：完成 `decision-session.schema.json`、`decision-memo.schema.json`、Grill Me First 欄位閘門、唯讀 evidence tools、constraint hash 與 `decision_trace`；App B 不得在缺資料時自行回來源抓取。
4. **最後才做產品化與連續營運**：雙應用 scope／OAuth onboarding、request_id 恢復 UX、外部告警、低頻 schedule、七日 soak、用量 ceiling、last-good restore 與 rollback。

若任一遠端步驟缺少部署授權、dispatch secret、OIDC callback 或私有 artifact read-back，狀態必須維持 `UNRESOLVED`／`blocked`，不可用本機測試或舊版快照替代。

## 1. 產品邊界與非目標

### 1.1 應用 A：研究報告產生器

**使用者入口**：OpenCode Desktop。
**編排模型**：OpenCode 可選 `opencode/big-pickle`；模型只負責選擇已授權工具、補齊結構化參數、整理結果與形成報告草稿。
**執行邊界**：MCP 工具、Cloudflare job controller 與 GitHub Actions／OpenBB／Crawl4AI 執行真正的工作。

輸出必須是 `research_report`＋`evidence_appendix`，至少包含：

- 研究問題、target identity、as-of 與 expiry。
- 使用過的來源、實際 URL、抓取方式、時間、內容 hash 與權利狀態。
- 議題、公司／標的／產業、市場與基本面資料。
- 支持、反駁、未知、資料缺口與來源背離。
- bull／bear／risk 第二意見、催化劑、失效條件與情境假設。
- 摘要、`recommendation_status=research_only` 與報告 profile；任何催化劑／失效條件／資料缺口都必須帶 evidence IDs。
- `research_only|monitor|requires_human_review` 狀態；不得產生 broker order。

`detailed_traceable` 是完整研究交付；`compact_traceable` 對應快速卡。若 objective 是 `meeting_battle_card`，後續 report v2 還必須把資料缺口、紅旗、會議問題、談判／驗證動作與會後追蹤獨立成可引用欄位；目前先保留在 Research Requirement，尚未把這些欄位冒充成已完成的模型輸出。

Big Pickle 免費期間的資料政策可能允許模型改善用途，因此 App A 預設只傳送公開資料或去識別化的中間結果；私人 R2 文件與完整稽核內容只可由受控後端或 App B 的授權高階 AI 讀取。

### 1.2 應用 B：決策討論助手

**使用者入口**：ChatGPT／Codex 的高階模型與 MCP。
**主要任務**：把已完成的研究結果轉成「人與 AI 的決策討論」，不是重新盲抓全部來源。

在產生 `decision_memo` 前，必須先確認：

- 使用者要解決的決策問題。
- 時間 horizon、管轄地與資料截止時間。
- 風險承受度、持倉／現金背景與不可違反限制；缺少不得自行推測。
- 使用者希望比較的情境、替代方案與需要人工確認的事項。

輸出只允許：`no_conclusion`、`monitor`、`research_more`、`human_review`、`paper_trade_candidate`。是否採取行動由使用者決定；系統不連券商、不下單、不把模型 confidence 當成報酬保證。

### 1.3 兩個應用的最小交接

App A 的輸入是「研究問題與資料需求」，輸出是「可追溯的 Research Pack、詳細報告與 evidence appendix」。App B 的輸入是 App A 的 frozen artifact 加上使用者在 Grill Me First 中確認的限制，輸出是「decision memo 與 decision trace」。App B 不得在找不到資料時自行回到來源抓取；需要新資料時只能提交新的 refresh request，等待新的 Research Pack 版本。

## 2. 共用角色責任

| 元件／角色 | 負責 | 不負責 |
|---|---|---|
| OpenCode Desktop | App A 的互動、MCP tool call、權限提示、狀態查看 | 不保存 system of record；關閉後不保證背景喚醒 |
| Big Pickle | 低成本規劃、工具選擇、結構化整理、報告草稿 | 不做最終投資決策；不取得私有證據 |
| MCP Server | 驗證身份、schema、scope、建立 job、回傳 artifact | 不讓單次 tool call 無限等待；不繞過 admission／budget |
| GitHub Actions／Crawl4AI／OpenBB | 批次收集、標準化與計算 | 不直接向使用者下結論 |
| Cloudflare Worker／D1／R2 | job controller、system of record、索引、權限、稽核與 last-good | 不把 public repo 的原始資料直接當成已驗證研究結論 |
| ChatGPT／Codex 高階模型 | App B 的 Grill Me First、證據推理與決策討論 | 不替使用者猜測風險偏好；不自動交易 |
| 使用者 | 確認 target、限制、是否執行 refresh、最終決策 | 不應把 AI 報告視為保證或授權自動下單 |

## 3. 端到端交接契約

每次交接只傳結構化 envelope＋artifact reference；大型內容留在 R2，D1 保存索引與稽核。所有 envelope 都必須帶 `schema_version`、`request_id`、`artifact_id`、`sha256`、`as_of`、`generated_at`、`quality` 與 `audit_ref`。

| Handoff | Input | Output | 通過條件 |
|---|---|---|---|
| H0 使用者需求 → target resolution | target 名稱／ticker／URL／文件、objective、as-of | `target_resolution.v1` | 唯一解析或回 `disambiguation_required`；不可猜同名公司 |
| H1 target → research requirement | target resolution、研究問題、as-of、horizon、constraints、requested outputs | `research_requirement.v1` | 缺少 target identity 或研究目的時回 `scope_missing`，不得猜測 |
| H2 requirement → planner／admission | `research_requirement`、source policy、budget、既有 snapshot 狀態 | `source_bundle_manifest.v1`、sufficiency decision | 說明哪些資料可重用、哪些缺口必須補；`include_market_data=false` 時不得選 market layer；只允許 manifest 內來源與 executor |
| H3 planner → research job | source bundle、checkpoint、route policy、output profile | `research_job.v1`、`request_id` | bounded timeout 建 job；refresh 受 quota／rights／admission 約束 |
| H4 job → raw evidence | source bundle、checkpoint、route policy | `ingest_envelope.v2`、source observations | 每個來源有終態；內容、hash、rights 通過 |
| H5 evidence → Research Pack | raw items、entities、topics、market／fundamentals／events | `research_pack.v1` | 所有 facts 有 evidence refs；partial／stale／unresolved 明示；未要求市場時 `market=null`，由 frozen requirement 與 alignment 的 `provider=not_requested` 保留原因 |
| H6 Research Pack → detailed report | Research Pack、report profile、prompt／agent version | `research_report.v2`＋evidence appendix | schema、citation、expiry、budget 與 audit 通過 |
| H7 report → decision session | report IDs、使用者回答、constraint hash | `decision_session.v1` | Grill Me First 欄位完整；缺欄標 `scope_missing` |
| H8 decision session → decision memo | frozen reports、使用者限制、policy version | `decision_memo.v1`、`decision_trace` | 證據可反查；資料不足時 fail closed；不含 order |

### 3.1 非同步 job 契約

`submit_research_job` 必須在 bounded timeout 內回傳 `request_id`，不可等待整個爬蟲／OpenBB／報告流程。`get_job_status(request_id)` 必須回傳：

`queued|running|blocked|completed|partial|failed|stale`、目前 stage、進度、last-good artifact、retryable、錯誤類別與下一步。

OpenCode Desktop 可以在工作階段內輪詢；若桌面程式關閉，job 仍由後台執行，使用者日後以 `request_id` 恢復。不得把「桌面仍開著」當成可靠的背景喚醒機制。

## 4. Research Pack 規格

Research Pack 是兩個應用之間的核心交接物，不能只把 `research_report` 當成唯一輸出。它至少包含：

- target／entity／instrument／industry canonical IDs。
- frozen `research_requirement` 與 `source_bundle_plan`，讓下游知道原始問題、限制、freshness 判斷與實際採用的來源。
- source bundle、raw item IDs、content hashes、權利狀態與 freshness。
- topic snapshot、topic history、news／social divergence。
- market、fundamentals、valuation、event snapshots；缺欄保留 null 與原因。
- evidence graph：claim、counterclaim、unknown、span、source diversity。
- quality：coverage ratio、partial、stale、unresolved count、ruleset version。
- input／output artifact refs、producer commit、workflow run、model／algorithm versions、audit hash。

Research Pack 不得含沒有來源的模型敘事；報告可以有 inference，但必須明確標示 inference，並引用其依據的 evidence IDs。

## 5. MCP tool 介面

### App A：研究執行 tools

- `resolve_target`
- `plan_research_sources`
- `submit_research_job`
- `get_job_status`
- `retry_research_job`
- `get_research_pack`
- `get_research_report`
- `get_evidence_appendix`

### App B：決策討論 tools

- `get_research_report`
- `get_research_pack`
- `get_evidence`
- `get_topic_timeline`
- `compare_news_social`
- `get_market_snapshot`
- `get_source_status`
- `record_decision_session`

`request_refresh` 必須是明確的 `refresh:write` scope，且只建立受 admission、budget、rights 與來源 policy 約束的 job；不能由模型任意指定 URL 或直接啟動 Browser。

## 6. 應用層開發缺口與順序

### A0：契約凍結（優先）

- 已新增 `research-pack.schema.json`、`research-job.schema.json`，並套用 `0008_research_jobs.sql`；`decision-session.schema.json`、`decision-memo.schema.json` 尚未實作。`research-report` 目前以 optional `report_profile` 與 migration `0010_report_profiles.sql` 保持 v1 replay 相容。
- 仍需將 `research_report.schema.json` 升級為 v2，加入 `quality`、`source_bundle_ref`、`evidence_appendix_ref`、`recommendation_status` 等完整報告欄位。
- 新增 D1 index／audit migration，保留既有 v1 producer／consumer 的雙讀期。

### A1：App A OpenCode Desktop 執行鏈

- 已實作 `/mcp`、最小 token scope、`submit_research_job`、`get_job_status`、`retry_research_job`、`get_research_pack`、`get_research_report`、`get_evidence_appendix` 與非同步 job state。
- 已將目前已發布的 OpenBB／議題／raw evidence 組成 Research Pack，寫入 private R2、D1 index 與 audit event；detailed report／evidence appendix 可讀回。
- Actions OIDC success／failure callback endpoint、`research_job_id` dispatch bridge 與 target-scoped workflow input 已部署到驗證帳號；真實 run 32432108862 已完成 success callback，D1 job `research_20260821001750_5ad37adc` 讀回 `partial`、published run、private pack/report/appendix。failure callback 的 OIDC／target／requirement binding 仍由本機測試覆核；本次 workflow 後續告警步驟因 webhook 未配置而失敗，已建立 issue #52。
- repository 已加入不含 secret 的 `opencode.json` remote MCP 設定與 env header，並固定 `model=opencode/big-pickle`；Worker 也有 GET endpoint-event 相容層。OpenCode CLI 1.15.12 的 resolved config 已確認模型與 MCP URL 生效；對本機 Wrangler Worker 的 Streamable HTTP 實測當時已連線並讀到 7 個 tools，後續新增 `retry_research_job` 後 catalog 為 8 個 tools。Big Pickle 已完成 bounded 唯讀 planner 與 `submit → status` tool-call，前者正確回傳 12-source `refresh_required`，後者在缺少 dispatch credential 時正確回傳 `blocked`／`configure_actions_dispatch_and_retry`，且沒有產生投資建議；`finance-app-a-remote` 已以有效 token 對部署後 Worker 完成 redacted `initialize → tools/list → plan → submit → poll → artifact read-back`，證據見 [`experiments/app-a/20260821-remote-gate-a.json`](../experiments/app-a/20260821-remote-gate-a.json)。callback binding／retry／duplicate-dispatch suppression 的本機證據見 [`experiments/app-a/20260820-local-callback-retry-smoke.json`](../experiments/app-a/20260820-local-callback-retry-smoke.json)，Big Pickle 證據見 [`experiments/app-a/20260820-big-pickle-planner-smoke.json`](../experiments/app-a/20260820-big-pickle-planner-smoke.json) 與 [`experiments/app-a/20260820-big-pickle-submit-status-smoke.json`](../experiments/app-a/20260820-big-pickle-submit-status-smoke.json)。handler `waitUntil` 的 submit→status→pack→report→appendix 本機端到端證據見 [`experiments/app-a/20260820-local-handler-e2e.json`](../experiments/app-a/20260820-local-handler-e2e.json)。尚缺 OpenCode Desktop 實際 tool-call、quick card profile，以及 public／private payload 的更細粒度去識別閘門。

### A1.1：Research Requirement Planner／Data Broker

- 將 `resolve_target` 與 `plan_research_sources` 升級為可保存的 `research_requirement.v1` 與 `source_bundle_manifest.v1`，不是只回傳示意 JSON。
- 先以 target、as-of、freshness、欄位需求與既有 snapshot 做 sufficiency check；只有缺口才建立 refresh job。
- 為每個缺口選擇 RSS／API／Browser／Crawl4AI／OpenBB／文件引擎，保存選擇理由、預估成本、quota、rights 與 fallback；不得由模型直接指定任意 URL。
- `include_market_data=false` 時走明確的 no-market 路徑：不選 market source、不呼叫 OpenBB provider，仍產出可驗證的 `not_requested` market snapshot，避免把「未要求」與「provider 失敗」混為一談。
- 在 Research Pack 中列出「重用資料」與「新抓資料」的 artifact IDs，讓報告能區分資料來源與新鮮度。

### A1.2：Gate A 判定器（避免誤報）

- `finance-app-a-gate` 讀取四份固定 App A smoke artifact，驗證 source bundle、`submit → status`、callback／retry contract 是否為本機 `passed`。
- detector 也會檢查 target-aware prompt、OpenBB symbol scope 與 callback target binding 的證據；目前證據見 [`experiments/app-a/20260820-target-binding-smoke.json`](../experiments/app-a/20260820-target-binding-smoke.json)。
- detector 永遠把遠端檢查分成獨立欄位；未提供部署版本、有效 MCP token、Actions run、OIDC callback 與 D1／R2 read-back 時，結果固定為 `gate_a_status=blocked`。
- detector 不讀取或列印 secret，不執行 dispatch、不修改 Worker／D1／R2；它只是發布前與回歸測試的 fail-closed 判定器。

### A2：App A 報告品質與故障路徑

- citation／hash／rights／freshness／partial gate。
- OpenCode 連線中斷後以 `request_id` 恢復；`get_job_status` 必須同時接受 `job_id` 與 `request_id`，不得要求桌面客戶端保存易變的暫存 session。
- MCP tool timeout、429、Actions failure、model failure、invalid JSON 的 last-good 與 retry policy。
- market data 是按需求選配；no-market request 必須完成 `planner → dispatch → --skip-market-data → not_requested alignment`，market-aware request 則必須 fail closed 或使用 target 對應 provider。
- Big Pickle tool-call smoke test；先使用唯讀 health／小型公開資料，不直接測完整 120 品牌。
- 這條 smoke test 只驗 MCP tool contract，不得替代全量資料驗證；正式 Gate A 必須使用已完成 120 品牌／166 endpoint normalization、去重與品質標記的 frozen source artifact。
- `retry_research_job` 必須只重派 Actions 或重新排入 bounded background execution，且不能讓 MCP request 無界等待爬蟲／模型；running 工作超過 10 分鐘要轉成可重試失敗。所有 Actions job 都必須有 success callback 或 failure callback；沒有 callback 的 workflow 不得通過 Gate A。

### B1：App B Grill Me First 與決策 session

- 將需求收斂欄位做成結構化 `decision-session.schema.json`，保存問題、horizon、jurisdiction、risk／portfolio constraints、使用者確認與缺欄狀態。
- 高階 AI 只能讀取已發布 report／evidence；不得自己改寫 raw 或繞過 MCP scope。
- 建立 `decision_trace`，保存 report IDs、constraint hash、問答紀錄、policy version 與使用者確認狀態。

### B2：App B policy guard

- stale／partial／unresolved／矛盾證據時只允許 `no_conclusion` 或 `human_review`，並以 `decision-memo.schema.json` 固定 action enum。
- 缺少適合度資料時不得產生個人化買賣建議。
- action class 只到 monitor／research_more／human_review／paper_trade_candidate，不接 broker。

### C：產品化與連續營運

- OpenCode Desktop 使用說明、MCP OAuth onboarding、錯誤與恢復 UI。
- ChatGPT／Codex MCP response 的 citation、as-of、freshness、partial 格式。
- 低頻 schedule、七日 soak、用量 ceiling、告警收件與 last-good restore。

### 6.1 開發順序（依賴不可跳過）

| 順序 | 要開發的環節 | 依賴 | 交接 output | 目前狀態 |
|---:|---|---|---|---|
| 1 | Contract／policy／quality manifest | 現有 schemas、D1、rights policy | `contract_manifest.v2`、policy／quality rules | 部分完成；decision schemas 尚缺 |
| 2 | Research Requirement Planner | target resolver、source catalog、freshness | `research_requirement.v1`、`source_bundle_manifest.v1` | 本機 schema／D1 migration／MCP preview 已完成；遠端尚未部署 |
| 3 | Data Broker target refresh | planner、Actions admission、OIDC callback | target-specific `research_job` → ingest／OpenBB outputs | dispatch client、部署與真實 OIDC completion callback 已通；品質仍可能為 `partial` |
| 4 | App A client integration | remote MCP、OpenCode config、Big Pickle policy | `submit → status → pack → report` client evidence | full-catalog BTC 的 `submit → status → Research Pack → report／appendix` 已通；70B report-only replay 已標記，不冒充 fresh generation |
| 5 | Research Pack／report quality | evidence graph、report v2、profile | full／quick／appendix、citation graph | v1-compatible evidence graph、profile／requested_outputs 本機完成；正式 report v2、quality／citation graph 擴充仍缺 |
| 6 | App B decision session | frozen report、Grill Me First | `decision_session`、`decision_memo`、trace | 尚未開發 |
| 7 | Auth／MCP productization | 兩應用 scope、OAuth onboarding | client onboarding、read／write deny matrix | token smoke 有；OAuth 尚缺 |
| 8 | Production operations | alerts、schedule、last-good | soak verdict、recovery evidence | Telegram 真人告警已通；七日 soak、schedule 與 recovery evidence 尚缺 |
| 9 | Gate A／B release decision | detector、兩個 MVP evidence bundle | release decision、未解問題清單 | full Actions Gate A 已通過但品質為 `partial`；App B 尚未通過 |

## 7. 兩個應用的 MVP gate

Gate A 的遠端執行步驟與停止條件見 [`app-a-remote-gate-runbook.md`](./app-a-remote-gate-runbook.md)。

### Gate A：研究報告產生器 MVP

先完成全量 source baseline，再以一個明確 target、該 target 的 frozen source artifact、真實 remote MCP、真實後台 job 執行一次；驗證階段不得退回 12–20 條垂直切片。正式產品運行時才可依 target 與 quota 採最小必要來源集合。若既有資料不足，必須真的觸發一次 target-specific refresh，不能只重用 `latest_published`；通過條件：

- OpenCode Desktop 能連 MCP 並完成 tool catalog／權限檢查。
- Big Pickle 能完成至少一個唯讀工具呼叫與一個 `submit → status → result` 鏈路。
- 至少一條 `planner → Actions dispatch → OIDC callback → Research Pack` 路徑有真實 workflow evidence；只完成 `latest_published` 不算 Gate A。
- Research Pack、detailed report、evidence appendix 寫入私有目的端並可讀回。
- 報告所有 facts／claims 可由 evidence ID、URL、hash、as-of 反查。
- 任何失敗都留下狀態，不以空報告或模型猜測冒充成功。

### Gate B：決策討論助手 MVP

使用 Gate A 的 frozen report，不重新抓資料；通過條件：

- 高階 AI 先完成 Grill Me First，缺欄時停止個人化結論。
- 能讀取 Research Pack／evidence appendix，回答追問並保留引用。
- 能輸出 `decision_memo` 與 `decision_trace`。
- stale、partial、矛盾與無限制資料會 fail closed。
- 絕不產生 broker order 或未授權的自動交易動作。

### Production gate

120 品牌／166 endpoint 的全量驗證是 Gate A 品質判定的前置條件，不是 Gate A／B 之後才開始的擴張項目。Gate A、Gate B 都通過後，進入多 target、連續追蹤、外部告警與七日 soak；兩個 MVP 通過不等於完成長期穩定性驗收。

## 8. SDD → TDD test matrix

本表就是 RED 清單；每列先寫測試再實作。外部服務不能只測 mock，必須有 bounded live detector。

| ID | Requirement | 測試類型 | 驗證與落點 |
|---|---|---|---|
| APP-A-01 | OpenCode Desktop 能讀取 remote MCP catalog | integration | 真實 MCP health／catalog；bounded live |
| APP-A-02 | MCP scope 只暴露 App A 允許的工具 | security | allow／deny matrix；CI＋遠端 endpoint |
| APP-A-03 | `submit_research_job` 在 bounded timeout 回 `request_id` | integration | fake slow executor＋handler `waitUntil` e2e＋真實 Worker |
| APP-A-04 | 相同 research request 冪等，不產生重複 job | invariant | fixture replay＋D1 remote replay |
| APP-A-05 | `get_job_status` 可跨桌面重啟恢復 | integration | `research-job-status.v1` 的 stage／progress／retryable／next_action＋server-side job state＋resume test |
| APP-A-06 | Research Pack 所有 facts 有 evidence refs | invariant | schema＋evidence subset assertion |
| APP-A-07 | Research Pack 標出 stale／partial／unresolved | fixture truth-table | quality truth table；CI |
| APP-A-08 | Big Pickle input filter 不傳 private R2／audit | security | private marker／egress fixture；CI＋gateway detector |
| APP-A-09 | Big Pickle tool args 通過 schema／allowlist | fixture truth-table | malformed／unknown tool／arbitrary URL cases |
| APP-A-10 | report profile 能產出 quick／full／appendix 三種交付物 | integration | frozen Research Pack replay |
| APP-A-11 | report claim 可回溯到 URL、item ID、hash、as-of | invariant | citation graph traversal；CI＋遠端 read-back |
| APP-A-12 | model／tool／prompt／budget／audit 都能讀回 | integration | D1/R2 index、audit hash、usage receipt |
| APP-A-13 | planner 先判斷資料是否足夠，再決定 reuse 或 refresh | fixture truth-table | existing snapshot／missing field／stale cases；CI＋遠端 bounded run |
| APP-A-14 | target-specific Actions job 能以 OIDC callback 完成研究 job | integration | workflow dispatch、callback、D1/R2 read-back；遠端真實 run |
| APP-A-15 | blocked／failed job 可安全重試且不重複派送 | integration＋invariant | `retry_research_job`、dispatch idempotency、background resume；CI＋遠端 bounded run |
| APP-A-16 | callback 只能發布 Planner 核准的 target／source bundle | security＋invariant | target／requirement／source ID set binding、run source allowlist；CI＋遠端 bounded run |
| APP-A-17 | 本機綠燈不可誤報遠端 Gate A | release guard | `finance-app-a-gate`：local evidence passed 但 remote 未驗證時必為 `gate_a_status=blocked` |
| APP-A-18 | Actions admission／workflow failure 不讓 job 永久卡在 queued | integration＋invariant | `/v1/research/jobs/fail` 的 OIDC／target／requirement binding、D1 status／audit read-back、workflow failure step；CI＋遠端 bounded failure run |
| APP-A-19 | market data 依需求選配且不以錯誤 provider 補資料 | fixture truth-table＋integration | `include_market_data=false` 排除 market source 並產出 `provider=not_requested`；`true` 的 unsupported target 回 `market_target_not_supported`；CI＋遠端 bounded run |
| APP-B-01 | decision session 在結論前要求 Grill Me First 欄位 | fixture truth-table | 缺 objective／horizon／risk／constraints |
| APP-B-02 | 高階 AI 只能讀已發布 report／evidence | security | MCP scope deny matrix；CI＋遠端 endpoint |
| APP-B-03 | decision memo 引用 frozen report IDs 與 constraint hash | invariant | schema＋trace assertion |
| APP-B-04 | stale／partial／矛盾資料只輸出 no_conclusion／human_review | fixture truth-table | policy matrix；CI |
| APP-B-05 | 缺少適合度資料不產生個人化買賣建議 | invariant | request truth table；CI |
| APP-B-06 | action class 不包含 broker order／自動交易 | schema＋security | enum rejection＋tool catalog deny |
| APP-B-07 | 使用者確認與 AI 追問能重播 | integration | frozen session replay；D1 trace |
| APP-B-08 | MCP response 帶 citation、as-of、freshness、partial | invariant | endpoint response fixture＋live detector |
| APP-B-09 | decision session 可跨對話恢復且不重抓 frozen report | integration | D1 trace replay；report ID／constraint hash 固定 |
| APP-C-01 | Actions／Worker／model failure 不污染 last-good | integration | failure injection＋remote status |
| APP-C-02 | 120 品牌擴張只在兩個 MVP gate 通過後啟動 | workflow guard | workflow contract test；CI |
| APP-C-03 | OpenCode 關閉後 job 仍可由 request_id 查回 | detector | server job persistence；live bounded run |
| APP-C-04 | Big Pickle 免費模型政策變更時可切換 fallback | integration | provider switch／model policy fixture |

## 9. 完成判定

### App A 可稱為完成

只有在 Gate A 的真實 OpenCode Desktop＋MCP＋後台 job＋Research Pack＋報告 read-back 全部通過，且至少一條 target-specific Actions refresh path 真實完成（含 success 或 failure terminal callback），`finance-app-a-gate` 讀到完整遠端證據，test matrix 的 APP-A-01～18 沒有關鍵 `UNRESOLVED` 時，才能稱為「研究報告產生器 MVP 完成」。

### App B 可稱為完成

只有在 Gate B 的高階 AI＋Grill Me First＋decision memo＋policy guard＋trace 全部通過，且 APP-B-01～09 沒有關鍵 `UNRESOLVED` 時，才能稱為「決策討論助手 MVP 完成」。

### 整體服務可稱為完成

兩個 MVP 都完成後，還必須通過 Stage 10 的外部告警、七日 soak、用量 ceiling、last-good restore 與 failure recovery；否則只能稱為「可用垂直切片」，不能稱為正式上線的完整服務。
