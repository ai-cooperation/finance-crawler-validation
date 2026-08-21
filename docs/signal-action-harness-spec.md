# Signal Engine／Action Engine 任務型 Harness 延伸 SPEC

更新日期：2026-08-21  
文件狀態：平台延伸規格（Normative）  
適用範圍：[`complete-investment-research-assistant-plan.md`](./complete-investment-research-assistant-plan.md) 與 [`two-application-alignment-spec.md`](./two-application-alignment-spec.md)

本文件把 Signal Engine 與 Action Engine 定義成所有應用共用的能力層，再把投資研究、保險研究、產業研究、商機／市場開發各自定義成版本化的 application Harness Pack。它不新增另一個常駐 Agent，也不把不同領域的規則混在同一個 prompt。

## 0. 對齊結論

### 0.1 Harness 不是 Agent

- **Runtime**：OpenCode／Big Pickle 或其他已授權模型，負責填寫結構化參數、選擇工具與整理結果。
- **Harness**：任務的輸入／輸出契約、工具 allowlist、品質閘門、失敗語義、資源上限、verifier 與稽核要求。
- **Signal Engine**：只回答「發生了什麼變化、證據有多完整、是否值得注意」，不決定採取什麼外部行動。
- **Action Engine**：只在通過 signal、政策、權限與 budget gate 後建立下一個可追蹤任務，不把 signal 直接轉成交易、核保或外部聯繫。
- **Application Harness**：把一個領域的 signal pack、action pack、資料需求、報告輸出與政策組合起來。

同一個 runtime 可以載入不同 Harness Pack；每個 task 在 admission 時凍結 `harness_pack_id@version`，不得在執行中由模型自行換包、擴大工具權限或改寫成功標準。

### 0.2 為何拆成兩個 Engine

`Signal` 是可重播、可撤回的觀察；`Action` 是有副作用等級與授權要求的任務。拆開後可以讓同一個 signal 被不同應用消費，也能在 signal 品質不足時停止 action，而不污染研究報告或稽核鏈。

```text
使用者需求
  → Application Harness／Requirement Planner
  → Data Broker（只補資料缺口）
  → Evidence Normalization
  → Signal Engine＋Signal Verifier
  → Action Engine（policy／approval／budget gate）
  → Application Harness 產出 Research Pack／報告／任務結果
  → 高階 AI 進行唯讀討論或人工核准
```

Action Engine 可以在同一個 job 中被多次呼叫：第一次建立資料補齊任務，資料完成後再建立重算 signal 或產生報告任務。每次都要有新的 `action_task` 與 receipt，不以模型上下文作為狀態。

## 1. 共用資料契約（v1）

### 1.1 `signal_event.v1`

Signal 是由 frozen normalized evidence 計算出的不可變事件；更正或撤回要新增事件，不覆寫原事件。

```json
{
  "schema_version": 1,
  "signal_id": "sig_...",
  "signal_type": "novelty|momentum|divergence|catalyst|risk|intent|anomaly|trend_break",
  "subject_refs": [{"type": "entity|instrument|industry|topic", "id": "..."}],
  "as_of": "RFC-3339",
  "detected_at": "RFC-3339",
  "score": 0.0,
  "confidence": 0.0,
  "severity": "info|watch|material|critical",
  "status": "active|cooling|expired|insufficient_data|needs_review|retracted",
  "evidence_refs": [{"item_id": "...", "sha256": "..."}],
  "counter_evidence_refs": [{"item_id": "...", "sha256": "..."}],
  "source_diversity": {"source_count": 0, "independent_groups": 0},
  "freshness": {"state": "healthy|warning|stale", "expires_at": "RFC-3339"},
  "algorithm_version": "...",
  "input_hash": "sha256",
  "audit_ref": {"event_id": "...", "event_hash": "sha256"}
}
```

`score` 與 `confidence` 是排序／品質訊號，不是報酬機率、事故機率或真實世界的保證。樣本數、來源獨立性、entity resolution 或 freshness 不足時，必須輸出 `insufficient_data`／`needs_review`，不可靜默補 0 或降級成一般訊息。

### 1.2 `action_task.v1`

Action 是具冪等鍵的任務提案；真正的結果另以 `action_receipt.v1` 保存。

```json
{
  "schema_version": 1,
  "action_id": "act_...",
  "action_type": "refresh_data|enrich_entity|recalculate_signal|build_research_pack|notify|open_review|request_user_input|draft_outreach|export",
  "trigger_signal_ids": ["sig_..."],
  "input_refs": [{"artifact_id": "...", "sha256": "..."}],
  "allowed_tools": ["..."],
  "side_effect_level": "none|internal_write|notification|external_write",
  "approval": {"required": true, "status": "not_required|pending|approved|denied"},
  "idempotency_key": "sha256",
  "policy_version": "...",
  "budget_ceiling": {"requests": 0, "seconds": 0, "cost_units": 0},
  "status": "planned|queued|running|completed|blocked|failed|cancelled",
  "retry_policy": {"max_attempts": 3, "backoff": "..."},
  "deadline": "RFC-3339",
  "result_refs": [],
  "error_class": null,
  "audit_ref": {"event_id": "...", "event_hash": "sha256"}
}
```

首版允許 `refresh_data`、`enrich_entity`、`recalculate_signal`、`build_research_pack`、`notify`、`open_review`、`request_user_input` 與 `draft_outreach`。`broker_order`、`underwrite`、`bind_policy`、`send_outreach` 等外部或受監管副作用不在本計畫的 allowlist；若未來要開放，必須另立 policy、人工核准與 rollback 專案。

### 1.3 `action_receipt.v1`

Receipt 至少包含 `action_id`、`status`、`started_at`、`finished_at`、實際 tool／workflow run、input／output hash、side-effect receipt、error class、last-good pointer 與 `audit_ref`。只收到 dispatch response 不算完成；必須從目的端讀回 terminal 狀態與 artifact。

## 2. Harness Pack Registry

每個 Pack 都必須登錄以下欄位：

`id`、`domain`、`layer`、`version`、`consumes`、`emits`、`allowed_tools`、`source_policy`、`quality_ruleset`、`fail_closed`、`retry_policy`、`side_effect_ceiling`、`budget_ceiling`、`verifier`、`negative_fixture_set`、`rollback_policy`、`owner`。

### 2.1 共用平台 Pack

| Pack | 責任 | 產出 | 禁止越界 |
|---|---|---|---|
| `source-validation@1` | 來源路徑與 raw capture 能力基線 | `source_matrix.v1` | 不產生 signal 或研究結論 |
| `evidence-normalization@1` | item extraction、canonical URL／fingerprint、entity／topic mention、noise gate | `normalized_evidence.v1` | 不宣稱因果或投資意義 |
| `signal-core@1` | 依正規化 evidence 計算可重播 signal | `signal_event.v1`、`signal_snapshot.v1` | 不建立外部副作用 action |
| `action-core@1` | policy、approval、quota、idempotency、retry、receipt | `action_task.v1`、`action_receipt.v1` | 不接受任意 URL、任意 tool 或自由文字 side effect |
| `operations-recovery@1` | callback、告警、last-good、soak、rollback | `run_record.v1`、`recovery_evidence.v1` | 不覆寫 current、不刪除 audit |

### 2.2 應用 Pack

每一個應用都是新的 Pack 組合，不是新的常駐 Agent：

| 應用 | Signal Pack | Action Pack | 主要 output | 首版副作用上限 |
|---|---|---|---|---|
| 投資研究報告 | `investment-signal@1` | `investment-research-action@1` | `research_pack.v1`、`research_report.v2`、evidence appendix | `internal_write`／受控 `notify` |
| 保險研究 | `insurance-signal@1` | `insurance-research-action@1` | policy／regulation／claims evidence pack、研究報告 | `internal_write`；不核保、不定價、不 bind |
| 產業研究 | `industry-signal@1` | `industry-research-action@1` | industry／competitor／supply-chain pack、研究報告 | `internal_write`／受控 `notify` |
| 商機／市場開發 | `market-demand-signal@1` | `market-development-action@1` | demand signal、company enrichment、qualification、outreach draft | `draft_outreach`；外部寄送必須人工核准 |

各 Pack 必須有自己的 ontology、來源 bundle、品質規則、golden／negative fixtures 與 verifier；不得因為共用 `signal_event` schema 就共用未驗證的 domain score 或結論模板。

## 3. Signal／Action 交接契約

| Handoff | Input | Output | 進入下一階段的必要條件 |
|---|---|---|---|
| S0 requirement → evidence | `research_requirement.v1`、source policy、既有 snapshot | `normalized_evidence.v1` | target／as-of／欄位需求可解析；來源終態與 rights 已記錄 |
| S1 evidence → signal | frozen normalized evidence、ontology、ruleset | `signal_snapshot.v1`、`signal_event.v1` | 每個 signal 有 item／hash；樣本不足標 `insufficient_data`；可重播 hash 一致 |
| S2 signal → action | signal snapshot、policy、budget、使用者授權 | `action_task.v1` | signal status 非 `retracted`；action type 在 allowlist；idempotency key 固定 |
| S3 action → application | terminal `action_receipt.v1`、新增 artifact refs | `research_pack.v1`／domain artifact | 目的端讀回 terminal；partial／failed／stale 明示；last-good 未被污染 |
| S4 application → decision | frozen report／domain pack、user constraints | `decision_memo.v1`／domain memo | citation、constraint hash、policy guard 通過；缺資料只能 `no_conclusion`／`human_review` |

每個交接都產生 `handoff_manifest.json`，保存 from／to pack、pack version、input／output hashes、quality verdict、known limitations、verifier、audit hash。下一個 Pack 不接受沒有 manifest 的自由文字或模型上下文。

## 4. 分階段施工計畫

這是同一個計畫的延伸，不是把四個領域拆成四個基礎設施專案。

| Phase | 交付內容 | 必要 input | Frozen output | Gate |
|---|---|---|---|---|
| H0 平台契約 | signal／action schema、registry、policy、artifact envelope、MCP task type | 現有 source／evidence／job contract | `harness_registry.v1`、schemas、test matrix | 每條 REQ 都有測試與 verifier |
| H1 Signal Engine | normalization、entity/topic、novelty／momentum／divergence／risk／catalyst 基礎 signal | 120／166 全量 frozen raw 與 normalized evidence | `signal_snapshot.v1`、golden replay、signal quality report | deterministic replay、insufficient-data、counter-evidence 通過 |
| H2 Action Engine | refresh、enrich、recalculate、build pack、notify、review 的任務與 receipt | verified signal、policy、quota | `action_task.v1`、`action_receipt.v1`、failure／recovery evidence | idempotency、approval、callback、last-good、無未授權副作用 |
| H3 投資研究 Harness | target resolver、OpenBB、investment signal、Research Pack／report／evidence appendix、App A／B | H1/H2（MVP 可先接 H1-MVP／H2-MVP）、equity／ETF／crypto provider | `research_report.v2`、`decision_memo.v1` | MVP 只可 `candidate|partial|research_only`；完整 Gate A／B 仍需 H1/H2 Full |
| H4 保險研究 Harness | policy／regulation／claims／exclusion／rate evidence 與保險 signal | H0–H2、保險來源與 ontology | `insurance_research_pack.v1`、`insurance_report.v1` | 引用完整、法域／as-of 正確；不核保／不定價 |
| H5 產業研究 Harness | industry graph、competitor、supply chain、capacity／demand／regulation signal | H0–H2、產業 taxonomy | `industry_research_pack.v1`、`industry_report.v1` | entity／industry mapping、反證與資料缺口通過 |
| H6 商機／市場開發 Harness | Demand First、company enrichment、qualification、outreach draft | H0–H2、商機來源與 CRM 權限 | `opportunity_pack.v1`、`outreach_draft.v1` | 不把熱度當需求；外部寄送人工核准 |
| H7 跨應用營運 | 多 Pack quota、告警、版本遷移、七日 soak、rollback | H3–H6 frozen outputs | cross-domain run／recovery evidence | 來源、模型、Actions 失敗可恢復且不污染 current |

H3 是目前投資研究工作；H4–H6 必須等 H0–H2 的共用契約與 verifier 完成後，以新 Pack 逐一開發。新增應用只新增 domain adapter、policy、fixtures、report schema 與 MCP scope，不複製 crawler、D1/R2、job controller 或 runtime。

### 4.1 MVP 快速路徑（Normative Amendment）

上表的 H0–H2 是完整平台 Gate，不得被誤解成投資 MVP 的全部硬前置。為了先做出可操作的應用，採用「薄版契約先行、應用垂直切片先通、平台完整化並行」：

| 快速路徑 | 只做的範圍 | 可交付結果 | 明確不能宣稱 |
|---|---|---|---|
| `H0-MVP` | 凍結共用 envelope、最小 `signal_event`／`action_task`、單一 investment Pack registry | job 能帶 pack／version、input／output hash、quality 狀態 | 不能宣稱所有 domain 已共用完成 |
| `H1-MVP` | 先接現有已發布 evidence，實作 2–3 種 deterministic signal（例如 novelty、divergence、risk） | `signal_snapshot.v1`、evidence refs、`partial|insufficient_data` | 不能宣稱全量 item normalization 或投資級 signal 校準 |
| `H2-MVP` | 只開放 `refresh_data`、`build_research_pack`、`open_review` 三種 internal action | `action_task`、terminal receipt、Research Pack refresh | 不開放 broker、核保、外部寄送或自動交易 |
| `H3-MVP` | 用一個明確 target 打通 Planner → Data Broker → Signal → Action → Research Pack → report | 可操作的投資研究報告候選版 | 不宣稱研究品質 Gate A／B 或正式上線 |

`H1-Full`（120／166 item normalization、去重、entity／topic、source diversity、counter-evidence）與 `H2-Full`（完整 action policy、告警、配額、恢復、跨 Pack scope）同步施工，不阻塞 `H3-MVP`；但任何 `research_only` 以外的決策結論仍必須等待 Full Gate。這樣可以先驗證使用者流程與 MCP tool UX，又不把候選版誤報為投資級產品。

## 5. Fail-closed 與副作用規則

1. Signal 無 evidence、entity 未解析、source diversity 不足、freshness 過期或 counter-evidence 未處理時，狀態只能是 `insufficient_data`／`needs_review`，不得產生可執行 action。
2. Action 必須列出觸發 signal、policy version、工具 allowlist、budget、idempotency key 與 side-effect level；缺一項就 `blocked`。
3. `notify` 也視為副作用，必須去重並保存 receipt；外部寫入一律需要明確 scope 與人工 approval。
4. Action 失敗時只更新 action／run 狀態，保留 last-good artifact；禁止用空結果覆蓋 current。
5. 應用 Harness 只能消費自己的 domain artifact 與被授權的共用 signal；跨域讀取須由 policy manifest 明確宣告，不能靠模型自行拼接。

## 6. SDD → TDD test matrix

本表是本文件的 RED 清單；每列先寫測試再實作。外部來源、告警、Actions 與模型漂移必須有部署 detector，不能只用 mock。

| ID | Requirement | 測試類型 | 驗證與落點 |
|---|---|---|---|
| SIG-01 | Signal 只能由 frozen normalized evidence 產生 | invariant | 無 input ref、錯 hash、未完成 normalization 時拒絕；CI＋publish gate |
| SIG-02 | 相同 input／ruleset 重播產生相同 signal hash | invariant | golden snapshot replay；CI＋遠端 replay |
| SIG-03 | 樣本／來源獨立性／freshness 不足輸出 `insufficient_data` | fixture truth-table | 小樣本、單一來源、stale、entity unresolved cases；CI＋production detector |
| SIG-04 | Signal 同時保存支持與反證 evidence | invariant | claim／counterclaim truth table；CI |
| SIG-05 | signal expiry、cooling、retracted 狀態可重播且不倒灌 action | integration | 時間序列與撤回 fixture；CI＋遠端 detector |
| SIG-06 | score／confidence 不被序列化成報酬或事故機率 | schema＋policy | forbidden field／copy regression；CI |
| ACT-01 | Action 只能由 allowlist signal／policy 觸發 | security＋fixture truth-table | retracted／insufficient signal、未知 action type；CI |
| ACT-02 | 相同 idempotency key 不重複 dispatch／通知 | invariant | duplicate submit、retry、callback replay；CI＋遠端 Actions |
| ACT-03 | side-effect level 依 scope／approval gate 限制 | security | no-scope、pending approval、external write deny matrix；CI＋遠端 endpoint |
| ACT-04 | 每個 action 都能讀回 terminal receipt | integration | dispatch accepted 但 workflow failure／timeout；D1/R2 read-back＋detector |
| ACT-05 | action failure 不污染 last-good 或刪除 audit | invariant | failure injection、partial publish、rollback；CI＋遠端 recovery |
| ACT-06 | `draft_outreach` 不會自動變成 `send_outreach` | security＋policy | tool catalog／enum／approval denial；CI |
| HAR-01 | job admission 凍結 pack id／version、工具、budget、verifier | integration | manifest hash 與執行中變更嘗試；CI＋遠端 bounded run |
| HAR-02 | Application Pack 只能讀宣告的 domain／private scope | security | cross-domain allow／deny matrix；CI＋遠端 MCP |
| HAR-03 | Handoff 沒有 manifest 或 quality gate 不 pass 時被拒絕 | invariant | missing manifest、partial below gate、unknown schema；CI |
| HAR-04 | 新 domain Pack 不會改變共用 signal／action schema 語義 | contract | registry compatibility／schema conformance；CI |
| APP-01 | 投資 Pack 的 signal 只產生研究／刷新／review action | policy | broker／order／personalized-advice forbidden matrix；CI |
| APP-02 | 保險 Pack 不產生核保／定價／bind action | policy | forbidden action enum／scope test；CI |
| APP-03 | 產業 Pack 的 entity／industry mapping 可反查來源 | invariant | taxonomy fixture、unresolved queue；CI＋detector |
| APP-04 | 市場開發 Pack 先驗證需求再產生 company enrichment／draft | fixture truth-table | Demand First 缺口、熱度≠需求、人工 approval；CI＋bounded run |
| OPS-01 | Pack／Engine 版本、model、prompt、tool、input hash 可完整追溯 | invariant | run record／audit traversal；CI＋remote replay |
| OPS-02 | quota、callback、告警、last-good、rollback 在每個 Pack 共用且可驗證 | integration＋detector | failure／recovery／soak；部署後＋真人告警 receipt |

## 7. 完成判定

- **Signal Engine 完成**：H0＋H1 gate 通過，`SIG-01`～`SIG-06` 無關鍵 `UNRESOLVED`，至少一份 120／166 frozen normalized fixture 能重播，且不足資料會 fail closed。
- **Action Engine 完成**：H0＋H2 gate 通過，`ACT-01`～`ACT-06`、`HAR-01`～`HAR-03` 通過，真實 Actions／Worker failure 有 terminal receipt、告警與 last-good evidence。
- **投資研究 Harness 完成**：H3 的 Gate A／B 通過；這不代表保險、產業或商機 Harness 已完成。
- **其他應用完成**：各自的 Pack、domain test matrix、negative fixtures、verifier、MCP scope 與報告 schema 通過；不得沿用投資研究的通過證據冒充。
- **整個平台完成**：H7 的版本遷移、跨 Pack quota、七日 soak、回復與安全稽核通過。
