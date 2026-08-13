# CF＋GitHub＋MCP 財經資料平台實作計畫

更新日期：2026-08-13
狀態：資料契約、Ingest Worker、D1／R2、15 來源議題雷達、遠端冪等重放、invalid publish 保留 last-good 與只讀 status 已在隔離驗證帳號實測；實作順序固定為 P0 基礎關卡 → P2 120 來源關卡 → P1 應用整合 → MCP／Agent 使用者介面裁示。正式排程仍關閉，P0 尚待 catch-up、外部 staleness／失敗告警、額度保護與低頻 soak。

本計畫延續 [120 家新聞品牌的按需資源架構](./resource-aware-news-architecture.md)，並採用 SB 筆記中已確認的 GitHub Actions 失效策略：[GitHub Actions 爬蟲與 CF MCP 架構](https://github.com/AlanChen75/knowledge-base/blob/main/tech/devops/2026-08-06-GitHub-Actions-%E7%88%AC%E8%9F%B2%E8%88%87-CF-MCP-%E6%9E%B6%E6%A7%8B.md)。

## 一、已確認的架構決策

- GitHub Actions 是可失敗、可重跑、可被替換的批次算力，不是資料真實來源。
- Cloudflare D1＋R2 是 system of record；Actions 中斷只能影響新鮮度，不得讓既有 MCP 查詢失效。
- Worker 依權限拆成公開 Relay、只寫 Ingest 與授權讀取 MCP；GitHub Actions 不持有廣泛的 D1／R2 管理權限。
- Crawl4AI、OpenBB 與 TradingAgents 均以背景批次執行，不要求即時 Python 後端。
- chat.ai 透過 MCP 讀取已整理的資料，負責後續追問與即時推理。
- 免費額度優先；Workers Paid、Container、商業 proxy／web unlocker 與雙雲高可用都不是第一階段必要條件。OpenBB provider 與 TradingAgents 模型費用另設獨立 budget gate，不併入 GitHub／Cloudflare 免費額度宣稱。
- 爬取維持 Browser＋API＋RSS 分層，並按來源、資源與失敗類型選擇執行平台。
- 交付順序以資料供應能力為先：P0 完成後先驗收 120 個不重複新聞品牌，再串接 OpenBB 與 TradingAgents；MCP 與 Agent 對外介面留到最後由使用者裁示。
- P0 只預留穩定的 ID、schema、權限邊界與版本策略，不因預留 MCP 契約而提前實作 MCP Server 或 Chat Agent UI。

## 二、目標資料流

```mermaid
flowchart LR
    A["公開 GitHub Repo<br/>程式、來源規則、Workflow、Schema、測試"] --> B["GitHub Actions<br/>Crawl4AI＋OpenBB＋選擇性 TradingAgents"]
    B --> C["Cloudflare Ingest Worker<br/>OIDC 驗證、Schema、去重、快照發布"]
    C --> D["D1<br/>來源狀態、索引、研究中繼資料、稽核事件"]
    C --> E["R2 Private Raw<br/>public-read Worker 只放行可再散布資料"]
    C --> F["R2 Private<br/>研究報告、證據、私有文件、稽核封存"]
    D --> G["OAuth MCP Server"]
    E --> G
    F --> G
    G --> H["chat.ai<br/>查詢、追問、交叉驗證"]
```

大型原文與報告放 R2，D1 只保存結構化狀態、索引、關聯與版本資訊。第一版以單一 operational D1 保持 checkpoint、snapshot 與發布索引在同一交易邊界；權限由不同 Worker 與 MCP scope 隔離，而非先拆成無法跨庫交易的多個 D1。Vectorize 延後到 D1 FTS 被證明不足時再導入。

## 三、資料與權限分離

| 資料類型 | 建議位置 | 公開程度 | 主要內容 |
|---|---|---:|---|
| 程式與來源定義 | GitHub public repo | 公開 | Workflow、來源 manifest、schema、測試、非敏感樣本與摘要 |
| 來源目錄 | operational D1 `source_catalog` | MCP 依 scope | 來源、分類、授權、路由、抓取策略與健康狀態 |
| 一般原始資料 | private R2 `raw-objects` | public-read Worker 只放行可再散布項目 | 正規化前後的公開資料、物件 hash、擷取時間 |
| 研究索引 | operational D1 `research_index` | 私有 MCP | 議題、證據關聯、快照、OpenBB 市場資料索引 |
| 分析報告 | R2 `research-reports` | 私有 MCP | 熱門議題、多空論證、背離與風險第二意見 |
| 私有證據與文件 | R2 `private-evidence` | 嚴格私有 MCP | 不可公開內容、完整研究附件與授權資料 |
| 稽核紀錄 | operational D1 `audit_events`＋private R2 `audit-archive` | 稽核 scope | append-only 事件、hash chain、每日封存 |

原始資料不應大量提交進 Git history。只有授權清楚的小型 fixture、manifest、hash 與可公開樣本能留在 repository；完整 raw data 優先放 R2。

## 四、背景分析管線

1. Crawl4AI 依來源策略執行 RSS、API、HTTP 或 Browser 擷取。
2. OpenBB 整理市場資料、標的與時間軸，產生可比對的市場快照。
3. 議題雷達計算熱度、成長率、來源廣度、新聞／社群背離與資料可信度。
4. 只有以下工作會啟動 TradingAgents：
   - 當期熱門議題前三名；
   - 重大新聞／社群背離；
   - 使用者或研究任務明確指定的題目。
5. Worker 驗證 schema、證據引用、hash 與最低覆蓋率後，才將 staging snapshot 切換成 current snapshot。

每個 run 必須保存以下階段狀態，下一輪只補缺少的階段：

- `raw_collected`
- `openbb_normalized`
- `topic_scored`
- `tradingagents_completed`（未達啟動條件時記錄 `skipped` 與原因）
- `published`

TradingAgents 產出一律標示為模型生成的「第二意見」，至少包含 bull case、bear case、risk view、evidence IDs、模型／agent 版本、`generated_at`、confidence 與 `expires_at`，不得表示為交易事實或最終投資結論。

## 五、GitHub 到 Cloudflare 的寫入邊界

優先採 GitHub OIDC 呼叫 ingest Worker。Worker 驗證 repository、ref、workflow、commit SHA 與 workflow run ID，並只接受已註冊 schema 的 payload。

- GitHub Actions 不取得廣泛 D1／R2 token。
- 大型物件由 Worker 以只存在 Worker Secret 的 R2 S3 簽章憑證，發放短效、單一物件、限定 HTTP method 與 content type 的 presigned URL；URL 本身視為 bearer token，不得寫入 log。
- 若第一版尚未完成 OIDC，只能暫用窄權限、write-only、可輪替的 ingest token。
- 所有輸出先進 staging；驗證完成才更新 `current_snapshot_id`。
- token、cookie、私有來源內容、完整私有結果不得進入 GitHub log、artifact 或 repository。

每筆稽核事件至少包含：`run_id`、repository、workflow ref、commit SHA、workflow run ID、source manifest hash、input snapshot、output manifest hash、status、`previous_event_hash` 與 `event_hash`。

## 六、GitHub Actions 失效與恢復

本段直接採用既有 SB 策略，不另建即時 heartbeat 或雙雲排程系統。

### Checkpoint

D1 對每個來源保存 `last_discovered_at`、`last_successful_crawl`、`last_article_date`、cursor、stage status 與 last-good snapshot。Checkpoint 只在該階段驗證成功後前進。

### Idempotency

至少以 `source_id＋canonical_url＋content_hash` 去重。相同 workflow 或相同 payload 重放，不得新增重複文章、報告或稽核業務事件。

### Catch-up crawling

下一次 Action 從最後成功 checkpoint 接續，並以來源的時間窗或 cursor 補抓中斷區間。單頁暫時性失敗最多重試 2–3 次；runner 或 workflow 基礎設施失敗則結束本輪，交給後續排程補跑。

### Staleness watchdog

Cloudflare 端以目前時間減去 `last_successful_crawl` 判斷 freshness。預設高頻來源可採 `<6h` healthy、`6–24h` warning、`>24h` stale；實際門檻必須依來源頻率配置。MCP 回應必須揭露 `as_of`、snapshot ID、freshness、partial 狀態與失敗來源。

### 告警與替代路徑

- 正式啟用 schedule 前，必須完成外部失敗通知與「資料超過 N 小時未更新」的反向健康檢查；GitHub Actions 頁面本身不算告警。
- Browser Run 不是 GitHub Actions 故障時的全面備援，只用於即時／按需、互動式、長期不穩定或需立即驗證的少數來源。
- timeout、runner unavailable、資源不足等可重試技術失敗，才能排除已嘗試 executor 後重新路由。
- `robots_denied`、`auth_required`、paywall 是終止狀態，不切換 IP、browser 或 unlocker 規避。
- 商業 executor、residential proxy 或 web unlocker 只有在明確授權與成本預算開啟後才能使用。

## 七、MCP 介面候選契約（待最終裁示）

本節只作為 P0 資料邊界與權限設計的候選契約，不代表已批准實作的使用者介面。必須先完成 120 來源驗收與 OpenBB／TradingAgents 應用層，再由使用者裁示 MCP tools、Chat Agent 形態、對外權限與是否開放寫入型 tool。

OAuth scope 預計分為：

- `sources:read`
- `raw:read`
- `research:read`
- `evidence:read`
- `audit:read`
- `refresh:write`（選配）
- `admin`

候選 MCP tools：

- `search_topics`
- `get_topic_evidence`
- `compare_news_social`
- `get_market_snapshot`
- `get_research_report`
- `get_source_status`
- `request_refresh`
- `get_job_status`

公開介面不得取得研究報告、私有證據、稽核明細或私有文件；完整資料只經過授權 MCP scope 存取。

## 八、交付順序與驗收關卡

P0、P2、P1 是既有 backlog 標籤，不直接代表施工先後。本專案以下表的 Gate 順序為唯一執行順序；前一關未驗收，不開始後一關的主體實作。

| 順序 | Backlog | 交付範圍 | 進入下一關前的驗收證據 |
|---:|---|---|---|
| Gate 1 | P0 | 補齊 SDD／TDD、遠端重放與故障注入、checkpoint／catch-up、D1 status／freshness、外部告警、額度保護與低頻 soak | 契約可追溯，失敗不破壞 last-good，重放不產生重複資料，過期與 workflow 失敗會產生外部告警，並有實際用量記錄 |
| Gate 2 | P2 提前執行 | 120 個不重複新聞品牌的 Browser＋API＋RSS 路由、fallback、來源健康、跨時間 observation state 與同 URL executor A／B | 120／120 都有品牌級實測記錄；成功分子只計不重複品牌；每個失敗都有可追溯原因與合規終止／fallback 判定；使用按需、分批、優先重驗失敗或證據過期來源，不固定全量重跑三輪 |
| Gate 3 | P1 應用整合 | 先以 OpenBB 建立市場快照與時間軸對齊，再以 TradingAgents 產生 bull／bear／risk 可溯源「第二意見」 | 實際來源資料可完成 topic↔market↔evidence 關聯；報告可重跑、可中斷續跑、有預算關卡，且不將模型輸出宣稱為交易事實 |
| Gate 4 | 使用者裁示 | 決定 MCP／Chat Agent 的用戶任務、tools、OAuth scopes、讀寫邊界、回應形態與客戶端 | 使用者批准介面 SDD 與驗收用例後，才開始 MCP Server 與 Agent 實作 |

Gate 2 的 90% 與 95% 品牌成功門檻依 [120 家新聞品牌的按需資源架構](./resource-aware-news-architecture.md) 計算為 108／120 與 114／120；這些是待驗證的驗收目標，不是已實測結果。現有 99／120 基準來自舊 GitHub 帳號的單輪實驗，只當失敗分群與優先重驗的起點，不取代 `ai-cooperation` 與 Cloudflare 隔離驗證帳號下的驗收證據。

### 現有能力狀態（不代表施工順序）

| 階段 | 狀態 | 交付與驗證 |
|---|---|---|
| 0. 來源與路由 POC | 部分完成 | 已完成來源矩陣、120 品牌單一 GitHub executor 擴大樣本與 15 來源垂直切片；跨時間 observation state 與同 URL 多 executor A/B 待辦 |
| 1. 資料契約 | 本機完成 | 八份 version 1 JSON Schema（含只讀 status response）、嚴格邊界驗證、content-based item ID 與版本策略已通過測試 |
| 2. CF system of record | 遠端垂直切片完成 | APAC D1 migration、private R2 binding、staging／current、last-good、稽核 hash chain、ingest receipt、只讀 status 與 Ingest Worker 已部署；runs `31369726174`、`31676925023` 均完成 D1／R2 直讀驗證 |
| 3. GH 批次管線 | 手動 OIDC／重放實接完成 | ingest envelope、immutable repo／owner／workflow／ref claims、commit SHA／workflow run 綁定、checkpoint、staging→publish、遠端 ingest／publish replay 與 invalid publish 保留 last-good 已實辦；catch-up、真正外部告警與 staleness 待辦 |
| 4. 議題雷達與研究 | 遠端垂直切片完成 | 15 個唯一來源、熱門前三名、新聞／社群背離與 partial 揭露已由 GitHub-hosted runner 實測；OpenBB 正規化與選擇性 TradingAgents 待辦 |
| 5. OAuth MCP | 待最終裁示 | 本階段只保留 scope 與 tool 候選契約；須在 120 來源與 OpenBB／TradingAgents 完成後由使用者批准介面 SDD |
| 6. 穩定性驗收 | 待辦 | 故障注入、額度觀測、連續一週正常排程與補跑紀錄 |

不以「已送出 workflow」視為完成。每一階段必須有可重現測試、實際輸出或 health response 才能改成已完成。

### 2026-08-10 議題雷達垂直切片

本機以 `radar-sources.yaml` 執行 15 個不重複來源，不把同一來源的不同路徑重複計數，也未觸發 GitHub Actions。驗收門檻是至少 12 個來源成功且產出三個可溯源議題。

| 路徑 | 成功／總數 | 結果 |
|---|---:|---|
| RSS | 5/5 | Fed、ECB、BBC Business、CNBC、MarketWatch |
| Public API | 7/7 | Hacker News、Money／Quant Stack Exchange、CoinGecko、World Bank、OpenBB／TradingAgents GitHub Issues |
| Browser＋Crawl4AI | 2/3 | OpenBB Discussions、TradingView Ideas 成功；Bogleheads 回傳 Cloudflare JS challenge／HTTP 403 |
| 合計 | 14/15 | 38 筆 raw items、三個 topic、snapshot `partial=true`、驗收通過 |

當次前三名為 equities／earnings（社群領先）、AI／semiconductors（新聞領先）與 personal finance（社群領先）。這只是該次資料的可重現規則式雷達結果，不是投資結論，也不外推為長期來源成功率。完整 raw 與 ingest payload 僅保存在本機暫存目錄，未提交 public repo；程式內 fixture 都明確標為 synthetic。

### 2026-08-10 GitHub OIDC → Cloudflare 遠端實接

[Actions run 31369726174](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31369726174) 從 commit `64d78dfbda9eae9876a266085d64853ba6e510a7` 執行一次手動垂直切片，耗時 1 分 52 秒。GitHub-hosted runner 實際取得 OIDC，通過 `finance-crawler-validation-ingest` 寫入 APAC D1 `476bd84f-e924-4b9b-a9d9-dfca9ea29a1a` 與 private R2 `finance-crawler-validation-raw`。

- 來源：14/15 成功，RSS 5/5、Public API 7/7、Browser 2/3。
- 產物：38 個 raw items、3 個 topics、snapshot `radar_20260810t082255z`，因 Bogleheads Browser 失敗而 `partial=true`。
- D1：`runs.status=published`，`raw_items=38`，`run_items=38`，`topic_snapshots=1`，`current_snapshot` 指向當次 snapshot；`raw_collected` 與 `published` audit events 各 1 筆。
- R2：直接讀回 topic JSON（4,085 bytes）與一個 raw JSON（66,104 bytes）；topic SHA-256 `4aff9ea563bf190ab4bee9bd9e187b92b9cbbd7f5a118c138d0f358978fc7093` 與 D1 完全一致。R2 bucket usage 彙總當時仍顯示 0，因此驗收以直接 object get 為準，不以延遲的彙總指標推定。
- 公開 artifact 只有 source-health `run-report.json`；raw items 與 topic snapshot 沒有上傳為 GitHub artifact。

進入 OpenBB 前仍須依序完成 Gate 1 與 Gate 2：catch-up 視窗、外部失敗通知、staleness watchdog、額度保護與低頻 soak，以及 120 個不重複新聞品牌的隔離帳號實測與失敗分群。正式 schedule 仍保持關閉。

### 2026-08-13 P0 重放與 status 實作

本機已完成 canonical ingest receipt migration、同 ID 異 payload 409 衝突、publish replay 不重設 current、D1-backed `GET /v1/status`，以及手動 workflow 的選配 `verify_resilience`。驗證選項預設關閉；開啟時使用同一輪 15 來源垂直切片的真實 payload，在同一 job 內完成 replay、invalid publish，再比對 status 的 snapshot ID 與 content hash，不另外多跑第二輪爬取。

- 本機回歸：Python 121、Ingest Worker 31、RSS Relay 5、Crawlee 9、Browser Run 12，合計 178 項通過。
- Python coverage：81.27%，pyproject 80% 門檻通過。
- Ingest Worker coverage：statements 86.89%、branches 81.30%、functions 94.64%、lines 88.14%；四項 80% 門檻已寫入 Vitest config。
- Wrangler type check、TypeScript typecheck、workflow YAML 語法與 deploy dry-run 通過。
- 遠端部署與驗收證據如下；Gate 1 的「遠端重放／last-good 故障注入／D1 status」子項已完成，但 catch-up、外部告警、額度保護與 soak 仍未完成，因此 P0 整體尚未關閉。

#### 遠端部署與單次額度驗證

- [PR #1](https://github.com/ai-cooperation/finance-crawler-validation/pull/1) 合併為 commit `e0078b2aeea3fd6807f8ceff5e090768711fe1e3`；D1 migration `0002_ingest_receipts.sql` 已套用，舊 1 筆 run 保留且未補寫不可驗證 receipt。
- Worker version `9eb9a838-8d38-44ac-9eed-db1c37c991e5` 已部署；`GET /health` 與 `GET /v1/status` 均回 HTTP 200，status response 通過 version 1 JSON Schema。
- 只觸發一次 [Actions run 31676925023](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31676925023)，耗時 1 分 58 秒；同一 job 先抓 15 個來源一次，再重放同一 ingest／publish payload 與注入一個 invalid publish，沒有第二輪爬取，也沒有開啟 schedule。
- 實測來源為 13/15：RSS 5/5、Public API 7/7、Browser＋Crawl4AI 1/3，共 37 items、3 topics。OpenBB GitHub Discussions 發生 HTTP/2 refused stream，Bogleheads 遭 Cloudflare JS challenge／403；snapshot 因此正確標記 `partial=true`，不把單次成功外推為長期成功率。
- D1 run `run_20260813t071516z` 為 `published`，`item_count=37` 且 `run_items=37`；只有 1 筆 `completed` ingest receipt、1 個新 snapshot，以及 `raw_collected`／`published` audit 各 1 筆，證明 replay 沒有增加 run、snapshot、link 或 audit 業務事件。
- invalid publish 回傳 422 `invalid_payload` 後，current 仍指向 `radar_20260813t071516z`；status 顯示 freshness `healthy`、整體 `warning`，原因為 2 個來源失敗與 partial snapshot。
- R2 直接讀回 topic object（4,195 bytes）與一個 raw object（1,073 bytes）；topic SHA-256 `807b851c2c062307da79dccde1349b3e4461a8d785416bc4990bacd633f7d5be` 與 D1、status 完全一致。公開 artifact 仍只有 source-health `run-report.json`。

## 九、驗收條件

- 任一分析階段中斷時，`current_snapshot_id` 保持 last-good，不發布半成品。
- 下一次執行能從 checkpoint 補齊中斷區間，不必整批重跑。
- 相同 payload、workflow run 或文章重放時不產生重複資料。
- Actions 停止期間，MCP 仍可查詢舊快照並明確顯示 stale。
- GitHub Actions 無法讀取 private evidence、private reports、audit archive 或私有文件。
- 公開端點無法越權取得研究、稽核與私有資料。
- GitHub log、artifact 與 repository 不包含 secret 或私有內容。
- 外部通知可收到人工注入的 workflow 失敗與資料過期告警。
- 一週 soak test 期間記錄實際 GitHub Actions、Workers、D1、R2 用量；是否位於免費額度內以觀測值判定，不以估算宣稱。

## 十、目前不做

- 即時交易、下單或自動投資決策。
- 為即時 OpenBB／TradingAgents 預先升級 Workers Paid 或 Container。
- 全來源一律使用 proxy、residential browser 或 web unlocker。
- GitHub Actions 的雙雲熱備與即時 heartbeat 狀態機。
- 未取得授權的全文鏡像或繞過 robots、登入與付費牆。
