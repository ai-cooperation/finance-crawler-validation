# 可稽核的 AI 投資研究助手：完整 SDD／TDD 計畫

更新日期：2026-08-26
文件狀態：目標架構與施工契約；未標示為「已驗收」的項目不得宣稱完成。

本文件的應用層以 [`two-application-alignment-spec.md`](./two-application-alignment-spec.md) 為最新規範補充：研究報告產生器與決策討論助手是兩個分開驗收的產品，不把低成本資料編排模型與高階決策推理混成一個 Agent。

## 0. 定位與完成定義

本計畫把現有的 GitHub Actions、Crawl4AI、Cloudflare Worker、D1、R2、OpenBB 對齊與 Workers AI 第二意見，組成一個可追溯的投資研究助手；實際交付拆成兩個應用：

1. **研究報告產生器**：OpenCode Desktop＋Big Pickle＋MCP，負責按需求執行資料收集、預處理、Research Pack 與詳細報告。
2. **決策討論助手**：ChatGPT／Codex 高階 AI＋MCP，先用 Grill Me First 收斂使用者限制，再讀取 frozen Research Pack／研究報告，產出可稽核的決策 memo。

「完成」不是模型能產生一段文字，而是每個結論都能回答：

1. 使用了哪一批來源、哪個時間點、哪個版本的資料。
2. 哪個程式、模型、提示詞與設定產生這個結論。
3. 每個主張能否回到原始 evidence ID、URL、content hash 與權利狀態。
4. 資料不完整、來源失敗、模型不確定或報告過期時，系統是否拒絕過度推論。
5. 相同 input 重跑是否得到冪等結果，失敗是否保留 last-good 而不污染 current。

目前已驗收的是資料收集與研究垂直切片；App A 已新增可部署的 MCP／job／Research Pack 垂直切片，並在本機完成 Research Requirement Planner、source sufficiency、target-scoped source selection、Actions dispatch、success／failure callback、retry／callback binding 與 report profile／requested output semantics 的測試。OpenCode CLI 1.15.12 與 Desktop 共用的 `opencode.json` 已對部署 Worker 完成 authenticated MCP smoke。2026-08-21 又以新設定的 Big Pickle 實際完成完整遠端鏈：`plan → submit → Actions／OIDC admission → publish → success callback → get_job_status → Research Pack → report → evidence appendix`；證據見 [`experiments/app-a/20260821-big-pickle-gate-a-final.json`](../experiments/app-a/20260821-big-pickle-gate-a-final.json)，GitHub Actions run [`32462029861`](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/32462029861)。該研究 job 為 `partial/published`（12 個計畫來源中 1 個失敗、4 筆 evidence、3 份 report），四個 Big Pickle readback tool calls 全部成功；品質限制與 `include_market_data=false` 已保留，不能把它解讀為投資決策。前次 workflow 因外部告警 webhook 未配置而建立 issue #52，後續已配置 Telegram 中文告警並以 run [`32448634111`](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/32448634111) 驗證外部告警步驟成功。故 App A 的部署後工具鏈已實際通過，但完整品質 Gate、report v2、App B、OAuth onboarding 與連續營運仍未驗收。

本輪另外完成 Research Pack v1-compatible `evidence_graph`：每個報告主張會以穩定 claim ID 連回 report、topic 與 evidence appendix；遠端核心資料鏈已驗收，但正式 `research_report.v2` 與嚴格 Gate A client／告警條件仍未完成。

本輪再加入 `finance-app-a-remote` bounded verifier：部署後可用安全環境的 MCP token 執行 `initialize → tools/list → plan → submit → poll → Research Pack／report／appendix`，只輸出 job／artifact metadata 與 gate checks，不輸出 token 或 private raw content；非 terminal、callback 失敗或個人化建議會 fail closed。`opencode.json` 已固定 `opencode/big-pickle` 作為 App A 編排模型。verifier 是遠端驗收工具，不是部署或 Gate A 證據本身。

2026-08-26 的 Provider Activation 階段把 110 個研究資料 provider 與 120 個新聞品牌分開治理：110 個 provider 中 50 個為 L4 route-integrated，剩餘 60 個均已有 L1 connector contract；其中 51 個可繼續完成 parser／resolver／credential，9 個因商業合約、政策或停止服務而只供規劃、不可執行。這輪由 79 個原始 backlog 升級 19 個 route，剩餘 60 個重跑存活驗證為 58／60。完整定義與實測證據見 [`provider-catalog-spec.md`](./provider-catalog-spec.md) 與 [`provider-activation-run-20260826.md`](./provider-activation-run-20260826.md)。本機 Worker 已包含 REST／MCP provider discovery，但 Wrangler OAuth 仍是非目標 Cloudflare account，GitHub SSH 身分 `AlanChen75` 對 `ai-cooperation` repository 也只有讀取、沒有 push 權限，尚未完成 production 發布；不得用本機 commit 或 dry deploy 代替遠端驗收。

## 1. 目標能力邊界

### 1.1 可以交付的產品能力

- **資料層**：按來源權利與資源選擇 RSS、API、Browser／Crawl4AI，保存 raw evidence、checkpoint、rights 與 source health。
- **議題層**：找出熱門議題、議題演化、新聞／社群背離、相關公司、標的與產業。
- **研究報告應用層**：OpenCode Desktop 使用 Big Pickle 編排已授權 MCP tools，將 OpenBB／Crawl4AI／GitHub Actions 的結果組成 Research Pack、詳細報告與 evidence appendix；Big Pickle 不做個人化買賣決策。
- **決策討論應用層**：ChatGPT／Codex 高階 AI 先以 Grill Me First 收斂目標、期限、風險與限制，再以唯讀 MCP 讀取報告與證據，輸出 `no_conclusion|monitor|research_more|human_review|paper_trade_candidate`。
- **介面層**：App A 的 OpenCode MCP tools 與 App B 的高階 AI MCP tools 分開 scope；所有回應帶新鮮度、引用與 partial／unknown 狀態。
- **需求驅動層**：兩個應用都只能提交結構化 `research_requirement`；Research Requirement Planner 先檢查已有 artifact 是否足夠，再把缺口交給 Data Broker，不允許 Agent 直接指定任意 URL 或跳過 admission／rights／budget。
- **稽核層**：任何可見報告均有 input／output hash、版本、權限、模型、成本、時間與失敗狀態。

### 1.2 明確不自動承諾的能力

- 不把社群熱度等同民意，不把模型信心等同機率真值。
- 不在來源不足、資料過期、公司實體未解析或估值資料缺欄時產生買賣結論。
- 不在本計畫內接券商下單、代客操盤或繞過來源的 robots、auth、paywall 或 Cloudflare 防護。
- 不把一次 run 的成功率外推為所有時間或所有來源的長期成功率。
- 不把 Big Pickle 的免費執行結果當作私有研究或最終投資建議；免費模型的資料使用政策與可用性必須由 provider policy gate 控制。
- 不把 OpenCode Desktop 關閉後仍可輪詢，誤當成可靠的背景喚醒；背景 job 必須由 Cloudflare／GitHub job controller 持續保存與恢復。

### 1.3 任務型 Harness 架構（本專案採用）

本專案採用「一個 runtime＋多個 Harness Pack」，不為來源收集、研究報告與決策討論各自建立一個常駐 Agent。MCP 是連接層；Harness Pack 是任務控制層；Cloudflare Worker／GitHub Actions 是非同步執行與狀態層。

每個 `research_job` 必須在建立時凍結：

- `task_type` 與 `harness_pack_id@version`。
- 允許的 MCP tools、來源 transport、OpenBB provider、模型與資源上限。
- input／output schema、成功標準、fail-closed 條件、retry／callback policy。
- verifier、audit event、artifact hash 與下一個可接收的 task type。

首版 Harness Pack 分為：

| Harness Pack | 任務責任 | 主要 output | 不允許越界 |
|---|---|---|---|
| `source-validation` | 120 品牌／166 endpoint 全量路徑與能力矩陣 | source matrix、raw refs、route outcome | 不宣稱研究相關性 |
| `evidence-normalization` | RSS／API／HTML／Browser payload 轉 item、去重、去噪、entity/topic extraction | normalized items、duplicate groups、quality report | 不產生投資結論 |
| `target-research` | 以 frozen target 組合議題、OpenBB、事件與證據 | Research Pack、report v2、evidence appendix | 缺 target evidence 時 fail closed |
| `decision-discussion` | Grill Me First 後唯讀讀取 frozen Research Pack | decision memo、decision trace | 不自行抓來源、不下單 |
| `operations-recovery` | callback、retry、告警、last-good、quota 與 soak | run record、recovery evidence、soak verdict | 不覆寫 current 或刪除稽核紀錄 |

OpenCode／Big Pickle 是通用 runtime；它只依 `task_type` 載入對應 Harness Pack。資料收集與正規化的 deterministic script 不消耗模型 token；模型只在需要分類、摘要或報告推理的 Harness Pack 中啟用。這使同一套 MCP／D1／R2／Actions 基礎設施可以換任務而不混淆權限與驗收標準。

Signal Engine／Action Engine 的進階契約與未來應用擴張，另以 [`signal-action-harness-spec.md`](./signal-action-harness-spec.md) 為 normative extension。兩者不是新的常駐 Agent：Signal Engine 產生有 evidence、反證、新鮮度與 expiry 的可重播觀察；Action Engine 只依 signal、policy、approval、quota 與 idempotency 建立下一個任務。投資研究是第一個 application Harness，保險研究、產業研究、商機／市場開發各自新增 domain signal pack＋action pack＋output schema，不複製 runtime、crawler、job controller 或 D1／R2。

因此現有 Stage 3–4 對齊為共用 Signal／Action 能力的第一個實作面：議題發現與追蹤先產生 `signal_event.v1`，再由 `action_task.v1` 觸發資料補齊、重算、研究包、通知或人工覆核。任何 signal 在 evidence 不足、entity 未解析、來源不獨立或已過期時，必須停在 `insufficient_data|needs_review`，不能直接進入研究結論或外部副作用。

## 2. 現有基礎與缺口

### 2.1 已有基礎（已部署／已實測）

- GitHub public repository：程式、來源 manifest、workflow、schema、測試與非敏感 fixture。
- Cloudflare Ingest Worker：OIDC、schema validation、staging／current、last-good、冪等、D1 admission、status／freshness。
- D1：來源狀態、run、topic snapshot、market snapshot、alignment、research report 索引與 append-only audit。
- R2：依權利保存 raw object、私有 evidence、研究報告與 hash。
- App A job／Research Pack：`research_jobs`、`research_packs` D1 index，private R2 `research-packs/*`，MCP `/mcp` tool catalog 與 token scope。
- P2 全量來源驗證：120 個唯一品牌、166 個 endpoint 已以 `exhaustive_endpoints=true` 實際執行；品牌 fallback 成功 116／120（96.67%），endpoint 成功 123／166（74.10%），證據為 GitHub Actions run [`32469254983`](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/32469254983)。這只證明來源能力與 fallback 邊界，不代表文章級去重、標的相關性或投資研究品質已通過。
- P1 垂直切片：一次 15 來源 run，14／15 成功，產生 3 topics、market alignment 與 3 份 Workers AI 第二意見。
- Provider Catalog：110 個研究資料 provider 已有統一能力與權利目錄；50 個 L4 route-integrated，60 個在 activation registry。Worker runtime registry 不含 secret 值，可由 REST／MCP 查詢 adapter、auth 欄位、地區、metric 與 next action；production 部署仍待驗證帳號授權。

### 2.2 必須補上的產品能力

1. `entity`／`instrument`／`industry` ontology 與可回溯的實體消歧。
2. topic history、趨勢、變化點、告警與追蹤清單。
3. news／social stance、可信度、代表性與背離校準。
4. OpenBB 的價格、財報、基本面、估值、宏觀與公司事件資料。
5. evidence graph、主張／反主張／未知狀態與研究任務包。
6. Research Pack、研究報告 v2、報告 profile 與受政策約束的決策 memo。
7. App A 的 OpenCode Desktop／Big Pickle MCP 執行鏈、App B 的 Grill Me First decision session。
8. MCP tools、OAuth scope、雙應用回應格式與使用者稽核。
9. Actions failure callback、外部告警、低頻 schedule、七日 soak、用量 ceiling 與災難恢復證據。

App A 目前可執行的市場 provider 僅是 CoinGecko crypto。Planner 對沒有對應 provider 的市場 target 會 fail closed；新增 equity／ETF／公司資料 provider 是擴大標的支援的必要工作，不得以通用新聞資料冒充標的市場資料。

## 3. 全域資料與稽核契約

### 3.1 所有階段共用的 Artifact Envelope

所有跨階段交接只傳「結構化 envelope＋物件引用」，不在 D1 或 log 中複製大型全文。新增能力以新 major schema 演進，現有 v1 producer／consumer 在 migration 期間雙讀，不直接改變 v1 語義。

```json
{
  "schema_version": 2,
  "artifact_type": "topic_snapshot",
  "artifact_id": "topic_snapshot_...",
  "run_id": "run_...",
  "stage": "topic_scored",
  "producer": {
    "repository": "ai-cooperation/finance-crawler-validation",
    "commit_sha": "40-char-sha",
    "workflow_run_id": "...",
    "component_version": "..."
  },
  "as_of": "RFC-3339",
  "generated_at": "RFC-3339",
  "input_refs": [{"artifact_id": "...", "sha256": "..."}],
  "object_ref": {"store": "d1|r2", "key": "...", "sha256": "..."},
  "rights_class": "metadata_only|excerpt|full|private",
  "quality": {
    "status": "complete|partial|stale|failed|skipped",
    "coverage_ratio": 0.0,
    "freshness_state": "healthy|warning|stale",
    "unresolved_count": 0,
    "quality_ruleset_version": "..."
  },
  "audit_ref": {"event_id": "uuid", "event_hash": "sha256"}
}
```

### 3.2 穩定 ID 與關聯

| ID | 意義 | 不可變依據 |
|---|---|---|
| `source_id` | 來源目錄主鍵 | manifest 定義 |
| `item_id` | 一筆 raw evidence | `source_id + canonical_url + content_sha256` |
| `entity_id` | 公司／人物／基金／指數等 canonical entity | ontology namespace + provider key |
| `instrument_id` | 可交易標的 | provider + symbol + exchange |
| `industry_id` | 產業分類節點 | taxonomy + taxonomy version + code |
| `topic_id` | 正規化議題 | topic namespace + canonical label |
| `snapshot_id` | 一次雷達快照 | run + stage + content hash |
| `alignment_id` | 議題與市場對齊 | topic snapshot + market snapshot |
| `report_id` | 一份研究報告 | topic + plan + model + input hash |
| `decision_id` | 一份決策支援 memo | report bundle + user constraint hash |
| `request_id` | 一次 MCP 請求 | server generated UUID |
| `event_hash` | 稽核鏈節點 | canonical event + previous hash |

### 3.3 必帶稽核欄位

每個 stage 的完成事件至少保存：

`run_id`、`stage`、`status`、`happened_at`、`producer_commit_sha`、`workflow_run_id`、`source_manifest_hash`、`input_artifact_ids`、`input_sha256`、`output_artifact_ids`、`output_sha256`、`schema_version`、`config_hash`、`model_or_algorithm_version`、`rights_class`、`coverage_ratio`、`freshness_state`、`error_class`、`previous_event_hash`、`event_hash`。

模型階段另加：`model`、`agent_version`、`prompt_template_hash`、`tool_call_log_ref`、`token_usage`、`estimated_cost`、`authorization_decision`、`budget_ceiling`。

任何聲稱為「已發布」的 artifact 必須能從 D1 audit event 往回走到 raw item；任何缺少 evidence ID 的模型主張不得發布。

## 4. 階段與交接契約

施工順序不可跳過前一階段的 gate。每階段都有明確的 input、output、驗收與失敗語義。

### Stage 0：契約、政策與版本基線

**目的**：把目前 v1 資料契約升級為可支援 entity、history、evidence、decision 與 MCP 的版本化 contract。

**Input**

- 現有 `schemas/` v1、D1 migrations、source manifest、rights policy。
- Git commit、Cloudflare binding、GitHub OIDC claim 與帳號隔離設定。
- 研究政策：哪些結果只能是 second opinion，哪些情況必須拒答。

**Output／交接包**

- `contract_manifest.v2.json`：所有 schema、欄位 owner、consumer、migration window。
- `policy_manifest.v1.json`：資料權利、保留、模型授權、投資建議邊界與拒答規則。
- `quality_ruleset.v1.json`：freshness、coverage、entity confidence、evidence coverage、report expiry 的門檻。
- `id_registry.v1.json`：ID 產生、碰撞與重播規則。

**Gate 0**：每一個 requirement 都有 test_matrix row；所有新欄位都有 producer／consumer；沒有未標記的 breaking change。

**Failure**：契約不完整時只允許留在 branch／CI，不得部署或切換 current。

### Stage 1：來源收集與 provenance

**目的**：把 120 品牌與後續來源變成可持續、可續跑、可解釋的 raw evidence stream。

**Input**

- `source_manifest_hash`、來源權利與 SLA。
- GitHub Actions trigger、上一個成功 checkpoint、resource budget。
- 路由策略：RSS、json API、static HTML、Browser／Crawl4AI。
- `provider_runtime_registry`：只允許 L4 route 進入 collector；L1–L3 只能供 Data Broker 規劃、probe 或產生 activation task。

**Output／交接包**

- `ingest_envelope.v2`：raw item、source checkpoint、實際 URL、HTTP 狀態、extraction method。
- R2 raw object：原始 payload／正規化 raw，帶 `content_sha256`、rights、retention。
- `source_observation`：成功／部分／失敗、原因類別、耗時、用量。
- `raw_collected` audit event。

**發布閘門**

- 所有 item 通過 schema、content hash 與 rights check。
- 同一 item 重播不得產生第二筆業務資料。
- 每個失敗來源留下可追溯原因；`robots_denied`、`auth_required`、paywall 等合規終止不得自動切換成規避策略。
- provider control-plane 存活不得自動改寫 `callable=true`；缺 credential、parser、target mapping 或 rights gate 時必須保留 blocked reason。
- 覆蓋率低於規則集門檻時標記 `partial`；不足以支撐結論時 current 保留 last-good。

**Failure／recovery**：只前進成功 checkpoint；runner、timeout、429 交由 catch-up 重試；raw 不完整時不進入研究階段。

### Stage 2：公司、標的與產業本體（Entity Resolution）

**目的**：把文字中的公司、股票、ETF、基金、指數、人物、產品與產業，轉成可跨來源合併的 canonical entity。

**Input**

- Stage 1 `raw_item` 與 title／summary／content span。
- 公司與標的 master：provider key、ticker、exchange、ISIN／CIK（若可得）。
- 產業 taxonomy：GICS／ICB 版本、mapping table、別名字典。
- resolver algorithm／model 版本與人工 override 表。

**Output／交接包**

- `entity_resolution_envelope.v1`：mention span、候選 entity、resolved entity、confidence、reason。
- `company_profile_snapshot`：名稱、別名、ticker、exchange、parent／subsidiary 關聯、有效期間。
- `instrument_snapshot`：asset type、currency、provider symbols、交易所與有效期間。
- `industry_mapping`：taxonomy、code、level、mapping confidence。
- `unresolved_mentions`：不得默認成公司或產業。

**發布閘門**

- 高信心（預設 `>=0.85`，校準後凍結）才能自動 canonical；中間區間進 unresolved／人工審查；低信心維持 unknown。
- 每個 resolved mention 必須保留原文 span、候選集合、resolver version 與 evidence item。
- 同一 canonical entity 不得在同一 taxonomy version 產生兩個 active primary ID。

**Failure**：entity 不確定不阻塞 raw 與 topic，但阻塞公司／產業投資結論；報告必須揭露 unresolved count。

### Stage 3：議題發現與議題卡片

**目的**：從新收集內容發現熱門、上升、跨來源擴散的議題，並保留新聞／社群來源結構。

**Input**

- raw items、entity annotations、source kind／layer／credibility config。
- 過去 topic snapshot（若存在）與 topic ontology／embedding version。

**Output／交接包**

- `topic_snapshot.v2`：top topics、score、novelty、item／source／news／social counts、evidence IDs。
- `topic_item_links`：item ↔ topic、membership score、extraction method。
- `topic_entity_links`：topic ↔ company／instrument／industry。
- `divergence_observation`：news lead／social lead／aligned／insufficient_data、magnitude、sample size。
- `topic_scored` audit event。

**發布閘門**

- 每個 topic 至少一個 evidence ID；score 可由固定 ruleset 重算。
- news／social 數量與 evidence links 必須可由輸入反查。
- 少於最低樣本數時只能是 `insufficient_data`，不可生成方向性背離結論。

**Failure**：topic algorithm 失敗時保留 raw，current topic snapshot 不切換；輸出空集合不得冒充「沒有熱門議題」。

### Stage 4：議題時間序列、追蹤與告警

**目的**：從單次雷達變成「追蹤議題」：看熱度、方向、擴散、冷卻、重新升溫與告警。

**Input**

- 多個已發布 `topic_snapshot`。
- source／topic 的 freshness SLA、baseline window、threshold config。
- 使用者 watchlist（可選）與告警目的地 scope。

**Output／交接包**

- `topic_timeseries.v1`：daily／intraday counts、score、source breadth、news/social ratio、market linkage。
- `topic_state_transition`：`emerging|active|accelerating|cooling|resolved|stale`。
- `topic_alert.v1`：alert key、觸發規則、before／after、evidence snapshot、dedupe state。
- `tracking_view`：topic ↔ entities ↔ instruments ↔ industries 的可查詢索引。

**發布閘門**

- 趨勢只能使用已發布 snapshot；不得用未驗證 staging 資料。
- 每一個變化率都帶 window、baseline snapshot IDs、timezone 與 missing days。
- 同一 alert key 重播只更新 `last_detected_at`，不得重複外送。

**Failure**：歷史資料缺口標為 `stale` 或 `insufficient_history`；不以零補缺；告警 transport 失敗要落 D1 並走 fallback。

### Stage 5：OpenBB 市場、基本面與事件資料

**目的**：把「議題」與可驗證的價格、財報、估值、宏觀、公司事件對齊；OpenBB 是資料標準化層，不是結論產生器。

**Input**

- resolved instruments／companies／industries。
- provider registry、quota、as-of、timezone、currency policy。
- topic snapshot 與 topic-entity links。

**Output／交接包**

- `market_snapshot.v2`：price、return、volume、market cap、observed_at、provider response hash。
- `fundamentals_snapshot.v1`：revenue、earnings、margin、cash/debt、guidance（缺欄保留 null 與原因）。
- `valuation_snapshot.v1`：multiples、method、period、peer set、assumptions。
- `event_snapshot.v1`：earnings、filings、dividend、macro release、calendar。
- `market_topic_alignment.v2`：topic ↔ instrument/company/industry、direction、coverage、time alignment。
- `openbb_normalized` audit event。

**發布閘門**

- provider、query、response hash、observed_at 與 missing fields 必須保存。
- 不同幣別、時區、交易日不可直接混算；調整方法與版本需記錄。
- 無法解析的 ticker 不得靜默改成另一家公司。

**Failure**：provider quota／transport 失敗不阻塞 topic radar，但阻塞 market-aware research；報告標示 `market_data_unavailable`。

**按需邊界**：若 frozen `research_requirement.include_market_data=false`，此 stage 不選 market source，也不呼叫不相關的 OpenBB provider；workflow 產出合法的 `market_snapshot`（`provider=not_requested`、`instruments=[]`）與零 coverage alignment，Research Pack 標示「未要求市場資料」。只有 `include_market_data=true` 才進入 provider quota／target support gate；unsupported target 必須回 `market_target_not_supported`，不得以其他標的或通用新聞資料替代。

### Stage 6：證據圖、主張與新聞／輿論背離

**目的**：將原文、實體、議題、市場與分析主張組成可反查的 evidence graph，避免 LLM 把敘事當事實。

**Input**

- raw items、entity／topic／market snapshots、topic history。
- source reliability config、stance／sentiment classifier、calibration set。

**Output／交接包**

- `evidence_graph.v1`：item、span、claim、counterclaim、entity、topic、market edge。
- `stance_observation.v1`：`positive|negative|neutral|questioning|unknown`、confidence、model version。
- `divergence_report.v1`：news／social stance distribution、sample size、magnitude、uncertainty、confounders。
- `claim_matrix`：支持、反駁、未知、未驗證。
- `evidence_quality`：source diversity、independence、freshness、rights、duplicate／coordination flags。

**發布閘門**

- 每個 claim／stance 都有原文 span 與 item ID；沒有 citation 的 claim 不能進 report。
- 分類器需以 frozen calibration set 報告 precision／recall、版本與適用語言。
- 社群數量不直接轉成「大眾意見」；若來源集中、樣本少或疑似協同轉發，必須降級。

**Failure**：模型不可用時保留 metadata 與 raw；不填入 neutral 充當未知；只輸出 `unknown`／`insufficient_data`。

### Stage 7：TradingAgents／Workers AI 研究第二意見

**目的**：對熱門議題、重大背離或使用者指定任務，產出可重播的多空與風險研究，不冒充投資事實。

**Input**

- `research_task.v2`：topic／entity／instrument／industry、目的、as-of、資料範圍、budget、model authorization。
- evidence graph、market／fundamentals／valuation／event snapshots。
- prompt template hash、agent graph version、工具白名單。

**Output／交接包**

- `research_report.v2`：
  - research question、scope、as-of、expiry；
  - executive summary；
  - bull／bear／risk claims；
  - catalysts、invalidators、unknowns、data gaps；
  - scenario table（base／bull／bear，假設與非保證的範圍）；
  - evidence IDs、confidence、model／agent／prompt versions；
  - `recommendation_status`：`research_only|monitor|requires_human_review`，預設不得是自動交易命令。
- R2 私有完整報告、prompt／tool trace reference、D1 report index。
- `tradingagents_completed` audit event。

**發布閘門**

- 每一個可驗證句子至少一個 evidence ID；所有 evidence ID 必須存在且 hash 相符。
- report schema、expiry、partial／stale、unresolved entity 與 market coverage 先驗證再發布。
- 未經明確授權不得執行模型；超過 token／費用 ceiling 要 `budget_denied` 並留 audit。
- 同一 task／input／model 重播必須冪等或明確標記新版本，不覆蓋舊報告。

**Failure**：AI 失敗不回退成假摘要；保留 task failed、原因類別、last-good report 與重試條件。

### Stage 8：投資決策支援（非自動下單）

**目的**：將研究報告轉成可供人審核的決策 memo；此層才回答「是否值得進一步研究／持有／觀察」，不直接執行交易。

**Input**

- 一份或多份未過期 `research_report`。
- 使用者明確提供的目標、時間 horizon、風險承受度、持倉／現金與限制；缺少就標記 unknown，不自行推測。
- portfolio snapshot、risk policy、backtest／benchmark evidence（若要做量化建議）。

**Output／交接包**

- `decision_memo.v1`：research question、data cutoff、thesis、supporting／contradicting evidence、scenario ranges、key risks、monitor conditions、invalidators、action class。
- `action_class` 僅允許：`no_conclusion|monitor|research_more|human_review|paper_trade_candidate`；除非另立受監管專案，不產生 broker order。
- `decision_trace`：使用的 report IDs、constraint hash、policy version、計算公式、回測版本與人工核准狀態。

**發布閘門**

- 資料 stale／partial、entity unresolved、估值缺欄、證據互相矛盾且未解釋時，最多輸出 `no_conclusion` 或 `human_review`。
- 不得把情境範圍、模型 confidence 或歷史回測當成未來報酬保證。
- 若使用者沒有提供適合度資料，不輸出個人化買賣建議。

**Failure**：決策支援失敗不影響研究報告；永遠保留可追溯的研究層輸出。

### Stage 9：MCP／ChatGPT 使用者介面

**目的**：將已發布 artifact 以最小權限提供查詢、追蹤與研究請求；ChatGPT 做敘事與追問，Worker 做授權與資料邊界。

**Input**

- OAuth identity、scope、使用者 query、時間範圍、entity／topic／industry filters。
- MCP tool schema、freshness policy、response budget。

**Output／交接包**

- `mcp_response_envelope.v1`：answer、result IDs、citations、as_of、freshness、partial／unknown、next query suggestion。
- 工具候選：`search_topics`、`get_topic_timeline`、`compare_news_social`、`get_company`、`get_industry`、`get_market_snapshot`、`get_research_report`、`get_decision_memo`、`get_source_status`、`request_refresh`。
- `request_audit`：request ID、scope、工具、參數 hash、artifact IDs、response hash、拒絕原因。

**發布閘門**

- `raw:read`、`research:read`、`evidence:read`、`audit:read`、`refresh:write` 分離；公開使用者不得讀 private R2 或 audit 詳情。
- 回應必須揭露資料時間、snapshot、freshness、partial、unknown 與 citation；沒有引用的敘述標為 inference。
- `request_refresh` 只能建立受 admission／budget 限制的 job，不直接讓 MCP 任意啟動昂貴爬蟲。

**Failure**：scope 不足、資料 stale 或結果為空時回結構化拒絕／unknown，不猜測、不洩漏 private object key。

### Stage 10：正式營運、恢復與持續驗收

**目的**：證明它是可維運的服務，而不只是成功跑過一次的 demo。

**Input**

- 所有 stage 的 health／freshness／usage／audit。
- GitHub schedule、Worker Cron、primary／fallback 告警目的地。
- replay、故障注入、provider 變更與 schema migration cases。

**Output／交接包**

- `soak_observation.v1`：每日 run、source status、current snapshot、D1/R2 hash、用量、告警收件證據。
- `soak_verdict.v1`：連續七日、失敗與恢復、是否超 ceiling、是否可開放 MCP。
- runbook、rollback、last-good restore、schema migration evidence。

**發布閘門**

- 真人可收到 primary 與 fallback 告警；告警去重與 recovery 都有證據。
- 連續七日 source freshness、current 不倒退、重播不重複、用量低於 ceiling。
- 任一 stage failed 不得讓 MCP 讀到未驗證 current；排程可安全關閉並從 checkpoint catch-up。

### 4.1 Signal／Action Engine 與後續應用 Harness（延伸施工面）

Signal／Action 不另開一條平行產品線，而是把 Stage 3–4 的議題能力升級成共用平台層，再用 application Harness Pack 延伸到不同領域：

| Harness phase | 主要工作 | frozen output | 依賴與 Gate |
|---|---|---|---|
| H0 平台契約 | `signal_event.v1`、`action_task.v1`、`action_receipt.v1`、registry、policy、verifier | `harness_registry.v1` | Stage 0；每條 requirement 都有 test_matrix |
| H1 Signal Engine | normalized evidence、entity／topic、novelty／momentum／divergence／risk／catalyst | `signal_snapshot.v1`、golden replay | Stage 1–4；不足資料必須 fail closed |
| H2 Action Engine | refresh、enrich、recalculate、build pack、notify、review、retry／callback | `action_task.v1`、`action_receipt.v1` | H1；冪等、approval、quota、last-good 通過 |
| H3 投資研究 Harness | investment signal＋OpenBB＋Research Pack／report／App A／B | `research_report.v2`、`decision_memo.v1` | MVP 可先用 H0-MVP–H2-MVP；完整 Gate A／B 需 H1/H2 Full |
| H4 保險研究 Harness | policy／regulation／claims／exclusion evidence 與 insurance signal | `insurance_research_pack.v1`、`insurance_report.v1` | H0–H2；不核保、不定價、不 bind |
| H5 產業研究 Harness | industry graph、競品、供應鏈、capacity／demand／regulation signal | `industry_research_pack.v1`、`industry_report.v1` | H0–H2；entity／taxonomy 可反查 |
| H6 商機／市場開發 Harness | Demand First、company enrichment、qualification、outreach draft | `opportunity_pack.v1`、`outreach_draft.v1` | H0–H2；外部寄送需人工核准 |
| H7 跨應用營運 | 多 Pack 版本、quota、告警、七日 soak、rollback | `recovery_evidence.v1` | H3–H6；不污染 current |

這個順序保留「先由應用 Agent 收斂需求，再由共用資料服務補齊」的使用者體驗：Application Harness 先產生 `research_requirement`，Data Broker 補資料，Signal Engine 重新計算，Action Engine 再決定要不要建立下一個內部任務。未來新增領域只新增 domain adapter、policy、fixtures、report schema 與 MCP scope，不複製 Crawl4AI、GitHub Actions、Worker、D1／R2 或 runtime。完整欄位契約與 Signal／Action test matrix 見 [`signal-action-harness-spec.md`](./signal-action-harness-spec.md)。

**MVP 不等待完整 H0–H2**：先凍結 `H0-MVP` 的最小 envelope／pack registry，對 120 品牌／166 endpoint 做 full-catalog collection 與 target item-level normalization，再以 `H1-MVP` 實作 2–3 種 deterministic signal；`H2-MVP` 只開放 `refresh_data`、`build_research_pack`、`open_review`，直接打通投資研究的 `Planner → Data Broker → Signal → Action → Research Pack → report`。完整 signal 長期校準、跨 Pack policy、告警與恢復走 `H1-Full／H2-Full` 並行施工；MVP 只能標示 `candidate|partial|research_only`，不能宣稱投資級 Gate A／B 或正式上線。模型不接收 120 個來源全文，而是讀取經去重、target relevance、source-group summary 與支持／反證選擇後的 context；Research Pack 仍保留完整相關 evidence refs。

## 5. 儲存與權限配置

| 內容 | GitHub public repo | D1 | R2 | MCP scope |
|---|---|---|---|---|
| 程式、manifest、schema、測試 | ✅ | — | — | — |
| 可公開小型 fixture／摘要／hash | ✅ | — | — | `sources:read` |
| source catalog、checkpoint、status | — | ✅ | — | `sources:read` |
| raw payload | 不提交全文 | 索引與 hash | 依 rights 公開或 private | `raw:read` |
| entity／topic／market 索引 | — | ✅ | 大型快照可放 R2 | `research:read` |
| evidence graph／完整證據 | — | 索引 | private | `evidence:read` |
| research report／decision memo | — | 索引 | private | `research:read` |
| audit event／用量／soak | — | append-only | archive | `audit:read` |
| secret、token、prompt private context | ❌ | ❌ | ❌ | 僅 Worker secret／受控執行環境 |

GitHub Actions 只能取得 OIDC／窄權限 ingest 能力；不能持有廣泛 D1／R2 管理 token。所有大型 object 以 Worker 管理的短效、單物件引用寫入，URL 不進 log。

## 6. 一次到位的施工順序與交接

「一次到位」指一次凍結目標架構與契約，不代表跳過驗收。實作按以下順序；每一階段完成後才把其 output 設為下一階段的 frozen input。

### 6.0 積極執行模式（本輪採用）

本專案不再以 12–20 條來源的 smoke test 作為主要進度單位。那類測試只保留給程式回歸；正式施工直接以全量 catalog 與最終應用輸出為驗收對象：

1. **全量來源基線**：120 個唯一品牌、166 個 endpoint 全部納入；成功、封鎖、robots、格式錯誤與空內容都要寫入同一份 source matrix，不得用 first-success fallback 掩蓋未測路徑。
2. **全量資料後處理**：所有可取得 payload 都必須進入 item-level normalization、canonical URL／content fingerprint 去重、entity／topic extraction、relevance／noise gate；只做 endpoint capability report 不算完成。
3. **目標研究交付**：以一個 frozen target（首輪使用 BTC/crypto，之後補 equity／ETF）跑完整 `requirement → refresh → Research Pack → report v2 → evidence appendix`，每個 claim 都有 evidence refs、freshness、counterclaim 與 unknown。
4. **雙應用交付**：同一份 frozen Research Pack 直接驗收 App A（OpenCode／Big Pickle）與 App B（Grill Me First／decision memo），不把兩個應用拆成無限期的串接小任務。
5. **正式營運交付**：最後一次完成告警、schedule、quota ceiling、last-good restore、failure recovery 與七日 soak；未達成前只能標示 `candidate`，不稱正式上線。

除非發現會污染資料或破壞安全 invariant 的重大問題，中間不以單一來源、單一工具或單一小樣本停工回報；每次回報以以上四個 gate 的完成證據為準。

1. **Contract freeze**：Stage 0 的 contract／policy／quality manifest；完成後才改 schema。
2. **Entity foundation**：Stage 2 schema、D1 ontology tables、resolver fixture 與 unresolved queue。
3. **Topic history**：Stage 3＋4 的 links、timeseries、watchlist、alert state machine。
4. **Market depth**：Stage 5 OpenBB fundamentals／valuation／event providers 與 missing-data policy。
5. **Evidence quality**：Stage 6 claim graph、stance calibration、divergence report。
6. **Research Pack／Research v2**：先完成 `research_pack.v1`、Stage 7 research task／report、budget gate、replay 與 expiry。
7. **App A MVP**：OpenCode Desktop＋Big Pickle＋remote MCP，完成 `submit → status → Research Pack → detailed report`。
8. **App B MVP**：Stage 8 decision session、Grill Me First、policy guard、human review；不接 broker。
9. **MCP／Agent productization**：雙應用 tools、OAuth scopes、citation response、refresh job 與恢復 UX。
10. **Production gate**：Stage 10 真人告警、schedule／Cron、七日 soak、usage／rollback。

每次交接都生成 `handoff_manifest.json`，至少包含：`from_stage`、`to_stage`、input artifact IDs／hash、output artifact IDs／hash、schema version、quality verdict、known limitations、owner、approved_at、audit event hash。下一階段只接受 manifest 中 `quality.verdict=pass|pass_with_partial` 且 partial 原因已明列的 input。

## 7. 驗收門檻與不變量

### 必須永遠成立的 invariant

- **INV-01 可追溯**：任何 claim、topic、alignment、report、decision 都能走到至少一個 raw item 與 content hash。
- **INV-02 冪等**：相同 source／URL／content hash 的重播不新增業務資料或告警。
- **INV-03 last-good**：staging、invalid、partial below gate 或 publish failure 不得移動 current pointer。
- **INV-04 權利隔離**：metadata-only 不得產出全文 excerpt；private object 不得由 public scope 讀取。
- **INV-05 新鮮度揭露**：所有查詢回應都包含 `as_of`、snapshot、freshness、partial／stale。
- **INV-06 未知不補零**：缺資料、未解析 entity、分類器失敗與 provider error 均保留 unknown，不靜默填入 neutral／0。
- **INV-07 模型可重播**：prompt／model／agent／tool／input hash 齊全；未授權模型 execution 直接拒絕。
- **INV-08 稽核鏈**：audit event 使用 previous hash 與 event hash，不能刪除或覆寫既有事件。
- **INV-09 安全邊界**：MCP scope 最小化，refresh 不得繞過 admission／budget／rights。
- **INV-10 決策邊界**：沒有明確使用者限制、過期資料或未解釋矛盾時，不產生個人化買賣結論。
- **INV-11 正式 H3 全量邊界**：production H3 必須使用 frozen full-catalog manifest；`max_sources`、15-source radar 與 12-source remote smoke 不得成為 collection、quality denominator 或 Research Pack 的資料上限。

## 8. SDD → TDD test matrix

此表是施工時的 RED 清單；每列先寫測試再實作。外部世界依賴不能只放 CI，必須部署 detector／告警。

| Req／Invariant | 驗證方式 | 測試類型 | 落點 |
|---|---|---|---|
| REQ-01 每個 artifact 可由 envelope 找回 object 與 hash | 產生、讀回、重算 SHA-256 | integration | CI＋遠端 replay |
| REQ-02 source route 依 manifest 選擇且記錄實際 URL | 異質 RSS／API／Browser fixture 對照表 | fixture truth-table | CI＋production source detector |
| REQ-03 checkpoint 只在 stage 成功後前進 | 注入 timeout／429／schema failure | unit＋integration | CI＋遠端故障注入 |
| REQ-04 raw item content identity 冪等 | 相同 payload 重播兩次，D1 count 不變 | invariant | CI＋遠端 replay |
| REQ-05 current 不因 invalid／partial below gate 倒退 | publish 422、低覆蓋率與 R2 failure | integration | CI＋遠端 last-good |
| REQ-06 entity 高／中／低信心分流正確 | frozen mention truth table | fixture truth-table | CI |
| REQ-07 canonical entity 不因別名產生重複 primary ID | alias／exchange／語言案例掃描 | invariant | CI＋生產 detector |
| REQ-08 topic score 可由 input 重算 | golden snapshot 重算與 hash 比對 | fixture truth-table | CI |
| REQ-09 topic history 不以缺日補零 | 缺日、時區、來源 stale fixture | unit＋invariant | CI＋detector |
| REQ-10 alert key 重播去重且 recovery 可送達 | 同 key、provider failure、恢復序列 | integration | CI＋遠端 sink＋真人收件 |
| REQ-11 OpenBB provider response 可追溯 | response hash、as-of、currency／timezone fixture | integration | CI＋provider detector |
| REQ-12 claim 必須帶 evidence span／item ID | 無 citation、錯 hash、過期 item | invariant | CI＋publish gate |
| REQ-13 news／social divergence 在樣本不足時輸出 insufficient_data | 小樣本與單一來源 fixture | fixture truth-table | CI＋production detector |
| REQ-14 AI report schema、expiry、budget、authorization 完整 | model success／invalid JSON／denied／timeout | unit＋integration | CI＋Workers AI replay |
| REQ-15 研究報告重播不複製業務 row | 相同 task／input／model 重放 | invariant | CI＋遠端 replay |
| REQ-16 decision memo 在 stale／矛盾／缺限制時拒答或 human_review | policy truth table | fixture truth-table | CI |
| REQ-17 MCP scope 不可跨讀 private／audit | 每個 scope 的 allow／deny matrix | integration＋security | CI＋遠端 endpoint |
| REQ-18 MCP 回應帶 citation、as-of、freshness、partial | snapshot、stale、empty、forbidden fixture | invariant | CI＋endpoint detector |
| REQ-19 Actions／Cron 失敗會通知且不耗用第二次爬取 | failure injection、fallback、dedupe | integration | 遠端＋真人收件 |
| REQ-20 七日 soak 的 run、用量、告警、R2 hash 可重現 | 私有 observation verifier | detector | production only |
| REQ-21 Actions job 有 success／failure terminal callback，不永久停在 queued | admission denial／workflow failure、OIDC binding、D1 status／audit read-back | integration＋invariant | CI＋遠端 bounded failure run |
| REQ-22 market data 依需求選配且不以錯誤 provider 補資料 | `include_market_data=false` 排除 market source 並產出 `provider=not_requested`；`true` 的 unsupported target 回 `market_target_not_supported` | fixture truth-table＋integration | CI＋遠端 bounded run |
| REQ-23 Provider activation 不把 survival 冒充 data route | L1–L4 truth table、登入頁／JSON／RSS／CSV／ZIP fixture、缺 credential 與 deprecated provider | unit＋schema＋遠端 bounded probe | CI＋production registry detector |
| REQ-24 Provider registry 可由 REST／MCP 查詢且不洩漏 secret | summary、filter boundary、scope allow／deny、generated registry 重生 diff | integration＋security | CI＋部署後 endpoint detector |
| INV-01～INV-11 永遠成立 | 每次 schema／migration／release 執行 invariant suite | invariant | CI＋部署前 gate＋生產 watchdog |

Signal／Action Engine 的延伸 requirement 不以本表的投資應用欄位代替；`signal-action-harness-spec.md` 的 `SIG-*`、`ACT-*`、`HAR-*`、`APP-*`、`OPS-*` 是同一計畫的附加 RED 清單。任何新的保險、產業或商機 Pack 都必須先讓共用 H0–H2 與自己的 domain rows 變綠，才能宣稱該應用完成。

### 最低測試交付

- schema／pure logic：80% 以上 statements、branches、functions、lines，並包含 truth table，不以 coverage 單獨代替語意測試。
- 外部來源、provider、告警與 LLM：至少一個 fixture／mock 路徑、一個真實遠端 bounded run、一個 failure path。
- 每個 migration：向前 migration、既有 v1 資料讀回、重播冪等與 rollback／last-good 證據。
- 每個 detector：部署後實際回應、告警 receipt、dedupe 與 recovery；沒有真人／受控目的地收件證據時標 `UNRESOLVED`。

## 9. 最終交付物與使用者可見結果

完成 Stage 10 後，兩個應用透過 MCP 能得到四類可稽核結果：

1. **議題雷達**：熱門／上升／冷卻議題、來源廣度、新聞／社群差異、最新 evidence。
2. **公司／產業卡片**：canonical company、ticker、產業、相關議題、事件、基本面與市場快照，並揭露 unresolved／stale。
3. **研究報告**：研究問題、時間截點、多空、風險、催化劑、失效條件、情境與證據鏈。
4. **決策 memo**：在使用者提供限制且資料通過 gate 時，輸出 monitor／research_more／human_review／paper_trade_candidate；否則明確回 `no_conclusion`。

App A 的交付重點是「詳細研究報告＋證據附錄」；App B 的交付重點是「使用者參與的決策討論＋decision trace」。App A 不替代 App B，App B 也不應跳過 App A 直接把來源敘事轉成決策。

這代表它可以支援投資決策流程，但「決策支援」與「自動投資決策／下單」是兩個不同產品。後者若要做，必須另立受監管、安全、券商授權與人工核准專案，不得從本計畫默認延伸。

## 10. 完成判定

完整投資研究助手只有在以下條件全部成立時才可稱為完成：

- Stage 0～9 的 handoff manifest 全部通過，且每一個新 schema 有對應 test matrix。
- Stage 2 能穩定解析公司／標的／產業，未解析量可查詢且不被靜默歸類。
- Stage 4 能從多日 snapshot 產生趨勢、狀態轉移與去重告警。
- Stage 5～7 能將市場／基本面／證據／模型輸出串成可重播報告。
- Stage 8 的 policy guard 能在資料不足、矛盾或缺少使用者限制時拒答。
- Stage 9 的 MCP scope、引用、新鮮度與拒絕路徑通過遠端測試。
- Stage 10 具真人告警、連續七日 soak、用量 ceiling、last-good restore 與 failure recovery 證據。
- 沒有任何 `UNRESOLVED` 的關鍵 evidence path；若有非關鍵 partial，使用者介面必須明示其範圍與影響。

## 11. Target-driven 研究：丟一個標的即可啟動

### 11.1 使用者輸入契約

使用者可以提交單一標的、議題、文件或一組 watchlist。系統不會盲目重跑全部 120 個來源，而是先解析 target，再依 target type、資源成本、來源權利與新鮮度選擇最小必要來源集合；需要全域背景時才引用最近的 topic snapshot／industry snapshot。

這個「最小必要來源集合」是產品正式運行時的成本策略，不適用於目前的全量驗證階段；全量驗證必須先完成 120 品牌／166 endpoint 的 normalization、去重與品質基線，否則無法知道按需選源是否漏掉關鍵證據。

```json
{
  "schema_version": 1,
  "request_id": "uuid",
  "target": {
    "type": "public_equity|etf|fund|index|crypto|fx|commodity|macro_theme|industry|private_company|private_asset|deal|document|topic",
    "name": "target name",
    "ticker": "optional",
    "exchange": "optional",
    "country": "optional",
    "identifier": "provider id, URL or deal id",
    "disambiguation": "optional user hint"
  },
  "objective": "screen|research|monitor|compare|due_diligence|meeting_battle_card|decision_support",
  "as_of": "RFC-3339 or latest",
  "horizon": "intraday|days|months|years|transaction",
  "constraints": {
    "currency": "optional",
    "risk_tolerance": "optional; never inferred",
    "jurisdiction": "optional",
    "portfolio_context_ref": "optional private object"
  },
  "attachments": [
    {"object_ref": "private R2 or user upload", "rights_class": "private", "sha256": "..."}
  ],
  "requested_outputs": ["quick_card", "detailed_report", "evidence_appendix"],
  "authorization": {"allow_model_execution": false, "budget_ceiling": 0}
}
```

若使用者只提供名稱而存在多個同名公司／ticker，系統必須先回 `disambiguation_required`，不得自行猜測。若提供的是私有資產，必須有文件、URL、現場紀錄或使用者提供的資料；沒有公開市場資料時，市場欄位保持 `not_covered`。

### 11.2 可接受的標的與對應資料路徑

| Target type | 最小識別輸入 | 主要資料 | 可產出的報告 |
|---|---|---|---|
| 公開股票／公司 | ticker＋exchange（或 CIK／ISIN） | 新聞、社群、財報、價格、估值、事件、產業 | 公司研究報告、議題追蹤、決策 memo |
| ETF／基金／指數 | ticker＋provider | holdings、benchmark、流量、價格、產業曝險 | 組成與風險報告、比較報告 |
| Crypto／數位資產 | symbol＋chain／provider | 市場資料、鏈上／社群來源、事件 | 資產研究報告、風險與敘事追蹤 |
| FX／商品／利率 | pair／contract／tenor | 價格、宏觀、庫存、央行／官方資料 | 宏觀驅動報告、情境與風險 |
| 產業 | taxonomy code 或產業名稱 | 公司集合、供需、政策、資本支出、議題 | 產業雷達、公司比較、週期判讀 |
| 宏觀議題／主題 | 自然語言問題 | 官方資料、新聞、社群、相關標的 | 議題雷達、背離與市場對齊 |
| 私有公司 | 公司名稱＋國家／法人提示 | 公司文件、新聞、註冊／認證資料、使用者附件 | DD brief、公司卡片、待核實清單 |
| 私有資產／交易案 | 資產名稱＋地點＋交易目的 | NDA 文件、財務、營運、法律／認證、現勘紀錄 | Meeting battle card、DD 報告、議價情境 |
| URL／文件 | URL 或 private R2 object | 文件內容、引用、相關外部查證 | 文件摘要、事實核查、會議作戰卡 |
| Watchlist | 2 個以上已解析 target | 各 target 的同一時間截面資料 | 比較矩陣、排名、共同／差異議題 |

所有 target type 共用同一條 provenance、entity、evidence、audit pipeline；差別只在 source bundle、可用欄位與 report profile。

### 11.3 以附件的 STP Planet 案為例

這個附件應被解析成 `private_asset/deal` target，而不是股票 ticker。系統可以從文件抽出「待驗證事實」、「分析推論」、「會議話術／操作指示」三種不同資料類型：

| 類型 | 例子 | 系統處理方式 |
|---|---|---|
| 來源事實 claim | 報價、容量、認證日期、公司名稱、出租櫃數等 | 進 evidence graph，標 `unverified`，等待 NDA／官方文件／現勘交叉確認 |
| 分析推論 | 供給可能超前、資產可能有議價空間 | 顯示為 analyst inference，要求市場與營運資料支持，不當成事實 |
| 使用者／分析師指示 | 不簽 MOA、不付訂金、先簽 NDA、約看廠 | 只有在使用者明確採用 playbook 後才進 `constraints`／`meeting_plan`，不會被系統自行執行 |

附件本身不會授權系統聯絡對方、簽約、付款或改變交易條件；它只是私有研究輸入與報告版型參考。任何外部行動仍需使用者另行確認。附件內容的原始來源與版型參考： :codex-file-citation{path="/Users/user/Downloads/【8-4會議】泰國STP_Planet_會議作戰卡.pdf" purpose="source"}

## 12. 報告輸出 profile

### 12.1 所有報告共用的首頁結構

1. **標題／版本／as-of**：target、report profile、產生時間、有效期限。
2. **一句話結論**：只允許 `monitor`、`research_more`、`human_review`、`paper_trade_candidate` 或 `no_conclusion` 等受政策控制的 action class。
3. **資料完整度**：source coverage、freshness、partial、unresolved entity、market coverage。
4. **決策問題**：使用者到底要判斷什麼；沒有問題就列為 `scope_missing`。
5. **證據摘要**：支持、反駁、未知，各自列 evidence ID。

### 12.2 公開標的研究報告

```text
封面：公司／標的、ticker、交易所、產業、as-of、action class
一頁摘要：核心 thesis、主要催化劑、主要風險、資料缺口
一、Target identity：canonical entity、aliases、exchange、instrument mapping
二、議題雷達：熱門議題、上升／冷卻、新聞／社群背離
三、公司與產業：商業模式、競爭者、產業位置、供應鏈／政策暴露
四、營運與財務：營收、成長、margin、現金、負債、guidance、缺欄原因
五、估值與市場：價格、returns、multiples、peer set、假設與日期
六、Evidence graph：支持／反證／未知 claim matrix
七、TradingAgents 第二意見：bull、bear、risk、catalysts、invalidators、scenarios
八、決策支援：monitor／research_more／human_review；不得直接下單
九、追蹤條件：價格、財務、事件、議題熱度、社群／新聞背離告警
附錄：來源、URL、item ID、content hash、模型／prompt／agent 版本、audit chain
```

### 12.3 私有交易案／會議作戰卡

這是附件所示的輸出形態，報告 profile 為 `meeting_battle_card`：

```text
封面：資產／交易案、對手方、地點、交易規模、會議日期、資料 as-of
一、案件定位：整廠／少數股權／承租／分階段；本次是否進入 DD
二、會議目標：本場必須拿到的資料、關係成果、下一步
三、談判位置：買方／賣方訊號、供給與時間因素、可接受範圍
四、交易算術：價格、訂金、尾款、折扣、差額與待確認公式
五、營運現況：容量、出租率、客戶產業、合約年期、電費模式
六、法人／法律／資格：公司關係、土地／建物、認證、BOI／許可、股權轉讓
七、技術與擴充：可交付容量、設備、成本、時間、土地／附加專案範圍
八、問題腳本：按優先順序的問題、預期證據、追問與判讀
九、訊號矩陣：正面／待處理／警訊／退出條件
十、替代結構：承租、少數股權、option、earn-out；每個結構的風險與資料要求
十一、會後動作：NDA、資料清單、現勘、紀要信、下次聯絡時間
十二、一頁速查卡：三個必拿成果、十個問題、四句必說、紅燈
附錄 A：文件 claim 逐項驗證表
附錄 B：估值／情境模型與假設
附錄 C：所有來源、hash、未解問題與稽核紀錄
```

### 12.4 三層輸出，不讓使用者被報告淹沒

- **Quick card**：一頁，適合會議或每日追蹤；只有結論、紅旗、必問與下一步。
- **Full report**：5–15 頁，包含市場／公司／產業／證據／模型／情境。
- **Evidence appendix**：私有長文、原始文件、逐項引用、hash、模型 trace 與 audit event。

## 13. Target report 的標準交接

```text
Target request
  → target_resolution.v1
  → source_bundle_manifest.v1
  → raw_ingest.v2
  → entity_resolution.v1
  → topic_snapshot.v2 / topic_timeseries.v1
  → market_fundamentals_valuation.v1
  → evidence_graph.v1
  → research_report.v2 或 meeting_battle_card.v1
  → decision_memo.v1（可選）
  → mcp_response.v1
```

每一次交接都產生 `handoff_manifest.json`，並且：

- `target_resolution` 未通過，不允許查市場或生成公司報告。
- `source_bundle` 不足時可產生 partial report，但不可隱藏 coverage。
- `entity_resolution` 未完成時只允許 topic／document report，不允許混用同名公司資料。
- `market_fundamentals` 缺失時仍可產生 qualitative report，但 action class 最多是 `research_more` 或 `human_review`。
- `evidence_graph` 沒有支持／反證鏈時，AI 只能輸出 `no_conclusion`。
- `decision_memo` 永遠引用 report IDs 與 constraint hash，不直接重新抓取或自行改寫 raw evidence。

## 14. 對本附件的預期系統輸出

若把這份 STP Planet 文件作為 input，系統第一版應輸出：

- `meeting_battle_card`，不是股票買賣報告。
- 一頁結論：`human_review`／`preserve_option` 類型，並標示「資料尚未完成 DD」。
- 需要核實的價格算術、出租率、營收／EBITDA、法人關係、BOI、Tier III、實際容量、土地／太陽能範圍。
- 會議問題腳本與每個答案的預期證據。
- 正面訊號、待處理訊號、紅旗與退出條件。
- NDA、資料清單、現勘、紀要信與 4–6 週追蹤任務。
- 附件內每個數字與判斷的來源位置、hash、`verified|unverified|inference` 狀態。

它不會直接輸出「應該買下 STP Planet」，因為目前缺少已驗證的財務、出租率、法人／土地關係、BOI 延續與現場證據；正確輸出應是可執行的 DD 與會議作戰卡，而不是假裝完成估值的投資結論。
