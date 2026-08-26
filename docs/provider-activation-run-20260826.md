# Provider Activation 執行紀錄（2026-08-26）

文件狀態：來源註冊表已於 2026-08-26 部署到指定的 GitHub／Cloudflare 驗證帳號；Production REST 與 OpenCode MCP transport 已查回。原先 OpenCode 1.15.12 的 agent turn 失敗已定位為本機 SQLite schema／程式回歸；官方 v1.18.23 隔離驗證已完成 Big Pickle → MCP tool call。

## 1. 目標與完成語義

起始 Provider Catalog 共 110 個 provider，其中 31 個已有可執行 route，79 個只有發現紀錄。本輪的「串接」拆成四個可稽核層級：

| 層級 | 定義 | 可交給 collector |
|---|---|---|
| L1 connector contract | adapter、runtime、probe、設定需求與 next action 均已指定 | 否 |
| L2 survival verified | provider 的資料面或控制面仍可到達 | 否 |
| L3 payload verified | 實際取得符合預期格式的 bounded payload | 尚不可；仍需 parser、rights 與 target mapping |
| L4 route integrated | endpoint、參數、auth injection、adapter 與政策閘均完成 | 是；缺 credential 時仍 fail closed |

存活不會自動升級為 callable。商業合約、停止服務或政策封鎖來源也會保留 connector contract，但標成 `not_executable`，避免系統與報告假裝已取得資料。

## 2. 本輪結果

| 指標 | 起始 | 本輪結果 |
|---|---:|---:|
| Provider Catalog | 110 | 110 |
| L4 route integrated | 31 | 50 |
| 待 activation | 79 | 60 |
| 79 個 connector contract | 0 | 79／79 |
| 本輪升級至 L4 | — | 19 |
| 目前技術上可繼續串接 backlog | — | 51 |
| 不可執行 backlog | — | 9 |

第一輪 79 個受控探測為 73 個存活、8 個直接取得資料 payload。修正 endpoint 與 auth contract並升級 19 個 route 後，對剩餘 60 個重跑雙層探測：58 個控制面存活，0 個新增 payload verified；這 0 筆不能解讀為來源死亡，因為多數 probe 的目的本來只是驗證文件／服務邊界，尚未配置金鑰與資料參數。

### 升級至 L4 的 19 個 provider

- 匿名或具官方識別要求：CFTC、DefiLlama、ECB、Eurostat、Fama-French、Federal Reserve、IMF、SEC EDGAR、US Treasury Fiscal Data。
- 需金鑰且已凍結 auth contract：CoinCap、CoinMarketCap、CryptoCompare、GNews、Massive、Mediastack、Messari、Nasdaq Data Link、NewsData、StockData.org。

上述「L4」只代表 route contract 可執行；需要 secret 的 provider 在未配置 secret 前仍回 `credential_not_configured`，不得降級成匿名假成功。

### 剩餘 60 個的邊界

| 類別 | 數量 | 下一步 |
|---|---:|---|
| 技術型 backlog | 51 | 完成 endpoint resolver、response parser、target mapping；需要授權者再配置命名 credential |
| 商業／封鎖／停止服務 | 9 | 保留於 registry 供規劃與採購，不允許自動執行 |

目前仍未證實完整資料路徑的特殊案例：

- US Census：官方文件控制面可到達，但 `api.census.gov` 在本執行環境連續逾時；狀態為 `survival=true`、`payload=false`。
- Dino Markets：目錄中的舊文件路徑回 404，尚未找到可由官方重新驗證的目前 endpoint。
- IEX Cloud：服務已退役，保留 deprecated 紀錄，不建立假 route。

## 3. 產物與重現

- 控制平面：`data/provider-activation-registry.json`
- Worker runtime：`ingest-worker/src/generated/provider-registry.json`
- 初始全量實測：`experiments/provider-activation/full-79-20260826.json`
- 目前 backlog 實測：`experiments/provider-activation/remaining-60-20260826.json`
- 探測與重生：`python scripts/probe_provider_activation.py --skip-probe`
- Worker REST：`GET /v1/providers`、`GET /v1/providers/:provider_id`
- MCP：`list_data_providers`、`get_data_provider`（需要 `research:read`）

探測器限制 concurrency 4（最大 8）、同一 endpoint 最多三次嘗試、最多保存 64 KiB sample，並區分 JSON／RSS／CSV／ZIP 與回登入頁的 HTML。GitHub Actions 只做 secret-free registry 重生與 diff gate，不新增全量排程，避免無意義消耗 Actions 額度。

## 4. 驗證與部署狀態

- Python：完整工作樹 519 tests 全數通過、總 coverage 81.26%；另由 Git index 匯出待發布 snapshot，297 tests 全數通過，避免未提交模組掩蓋依賴缺口。
- Worker clean snapshot：9 個 test files、146 tests 全數通過；新增 `provider-registry.ts` coverage 為 statements 98.7%、branches 97.14%、functions 100%、lines 100%。
- Worker clean snapshot typecheck／dry deploy：已通過；bundle 565.69 KiB、gzip 85.60 KiB。
- GitHub：`gh api user` 查回 active identity 為 `ai-cooperation`；遠端 `deploy/radar-quality-fix` 已查回 commit `21db62e5819941075d9e3a1298858174f59b5e5d`。
- Cloudflare：Wrangler OAuth 已查回 `o970117818@gmail.com`／account `ca985c195ab218488fc0744692dbde21`；production version ID 為 `5f2e9c27-fe44-4c50-b7e0-62f27819831c`。
- Production REST（2026-08-26 17:34 台北時間）：`GET /health`、`GET /v1/providers?limit=1`、`GET /v1/providers/sec_edgar` 均為 HTTP 200；health summary 為 `total=110`、`route_integrated=50`、`activation_backlog=60`、`technically_connectable_backlog=51`、`not_executable=9`。
- Production secrets：Wrangler 僅以名稱查回 `ALERT_WEBHOOK_URL`、`GITHUB_DISPATCH_TOKEN`、`MCP_API_TOKEN`，部署未覆蓋其值。
- MCP transport：舊版 `opencode mcp list` 對 production `/mcp` 查回 `finance-research connected`，但 agent turn 在建立第一筆 session message 時被本機 `NOT NULL constraint failed: session_message.seq` 阻斷，token input/output 均為 0。以官方 v1.18.23、隔離 XDG data 目錄及相同 MCP 設定重跑後，Big Pickle 成功呼叫 `finance-research_list_data_providers {"limit":1}`，回傳 `total=110`、`route_integrated=50`、`activation_backlog=60`、`technically_connectable_backlog=51`、`not_executable=9`，first provider `akshare`。
- 根因交叉驗證：本機 OpenCode 1.15.12 的 `--pure`（停用 MCP／外掛）控制組仍重現同一 SQLite 錯誤；官方 OpenCode issue [#31412](https://github.com/anomalyco/opencode/issues/31412) 與修復 PR [#31419](https://github.com/anomalyco/opencode/pull/31419) 描述相同 `session_message.seq` 回歸。故不能把原錯誤歸因於 Big Pickle、模型供應商或 finance-research Worker。

本輪已完成正確帳號 deploy、production `/health.provider_registry.route_integrated=50`、兩個 REST route 讀回一致、MCP transport connected，以及全域 OpenCode 1.18.20 的 Big Pickle `list_data_providers` tool result。另完成一次端到端台積電工作：`research_20260826121116_0380e48e` 最終 `partial`，Research Pack／研究報告／Evidence Appendix 均可讀回；初次被 planner 以 `TWSE market not supported` 阻擋，retry 後使用最近已發布但標記 stale 的 `run_20260822t003800z`，因此這是鏈路通過、資料新鮮度與台灣市場 coverage 尚未達專業級的明確結果。

## 5. OpenCode 修正後的真實驗證（2026-08-26）

### 5.1 Big Pickle 完整鏈（BTC，修正前基準）

使用全域 OpenCode `1.18.20`、`opencode/big-pickle` 與 production MCP，並指定 `source_strategy=latest_published`、`collection_scope=full_catalog`，完成：

`plan_research_sources → submit_research_job → get_job_status（2 次輪詢）→ get_research_pack → get_research_report → get_evidence_appendix`

- `job_id=research_20260826123216_9bb743d3`、`pack_id=pack_20260826123216_9bb743d3`
- terminal=`partial`，`report_count=1`；三個 artifact read-back tool calls 均成功。
- 快照 `run_20260822t003800z`；`coverage_ratio=0.9259`、`partial=true`、`stale=true`。
- 135 source groups／181 endpoints、149 normalized items、73 target-relevant items、78 appendix items；10 個來源失敗。

這次實測同時確認資料品質仍未達 professional-ready：快照已過期、fundamentals unavailable、evidence appendix 沒有 freshness／relevance／verification 欄位；因此 `partial` 不得被解讀為品質通過。

### 5.2 Freshness gate（修正後）

`latest_published` 現在會以需求 SLA 檢查 run 的 `collected_at`：含市場資料超過 6 小時、其餘需求超過 24 小時即 fail closed，回 `blocked / research_snapshot_refresh_required / next_action=request_refresh`，不建立 Research Pack。只有明確 `source_strategy=actions` 才可進入刷新路徑。

production Big Pickle 實測：`job_id=research_20260826124243_c12870d9`，一輪 `get_job_status` 後即回 `blocked`，`pack_id=null`、`report_count=0`；未 retry、未觸發 Actions。這修正了先前 stale run 被誤產成 partial 報告的邏輯錯誤。

### 5.3 Target-scoped topic gate 修正

為取得新鮮 BTC snapshot 只觸發一輪 Actions：workflow run `32971031774` 的 15 個來源全部成功、15 個來源都有內容、共 38 筆 item、0 個 failed source，但舊 collector 仍因固定要求 3 個 topic 回 `accepted=false`，因此沒有發布任何 artifact。這是 gate 邏輯錯誤，不是來源失敗。

已將 gate 改為依 scope 判定：未指定 target 的全域 radar 仍要求 3 個 topic；指定 target 的研究 refresh 至少要求 1 個 target-relevant topic，0 個 topic 仍 fail closed。相同 15 個公開來源在本機允許網路環境重跑得到 15/15 成功、43 筆 item、1 個 topic、`accepted=true`；回歸測試與完整 Python 測試集均通過。此次不再重跑 Actions，待修正提交至 `main` 後才使用下一輪配額驗證遠端 publish。
