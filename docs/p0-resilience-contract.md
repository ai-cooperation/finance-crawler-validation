# P0 重放、last-good 與 status 契約

更新日期：2026-08-13

## 範圍與成功標準

本契約先完成 Ingest Worker 的安全重放、發布失敗保留 last-good 與 D1 snapshot freshness，再補上 checkpoint catch-up、原子額度租約、外部告警與 freshness watchdog。不建立 MCP Server、不接商業 proxy；Cron 必須等外部 webhook 實際送達後才可啟用。

完成必須同時滿足：

- 同一 ingest payload 重放不重寫 R2、不新增業務資料或稽核事件，並回傳實際 run status 與 `replayed=true`。
- 同一 `run_id` 但內容不同時回傳 HTTP 409 `run_payload_conflict`，原始 D1／R2 資料不變。
- 同一 topic snapshot 重放不重寫 R2、不重設 current，並回傳 `replayed=true`。
- 同一 `snapshot_id` 但內容不同時回傳 HTTP 409 `snapshot_payload_conflict`。
- 無論有無 current snapshot，`GET /v1/status` 都只從 D1 回傳 version 1 的狀態契約，不暴露 raw content、token 或私有證據。
- 手動 workflow 的 resilience 驗證是預設關閉的選配項；開啟時使用同一輪 15 來源垂直切片的真實 payload，在同一個 run 內完成 replay 與 invalid publish，不另外多跑第二輪爬取。禁止使用會覆蓋 current snapshot 或汙染來源健康分母的 synthetic publish。
- 收集前以同一 GitHub OIDC identity 呼叫 `POST /v1/run/plan`；未獲 admission 不安裝 Chromium、不爬取、不寫入 ingest。
- 每 UTC 日最多租出 2 個 run admission，成功租約至少間隔 21,600 秒；同一 workflow run 重放相同決策，不重複占用額度。
- Action failure 與 freshness stale／empty 只在外部 webhook 回應 2xx 後寫入告警 receipt；同一事件去重，freshness 恢復時發送 recovery。

## 契約與資料擁有權

### POST `/v1/ingest/items`

成功回應保留 version 1 ingest envelope，並加入：

- `status`: D1 `runs.status` 的真實值，可為 `staging` 或 `published`。
- `replayed`: 本次是否命中已完成的同內容 receipt。

D1 以 `run_id` 擁有 run identity 與 canonical payload SHA-256；R2 只保存 deterministic raw objects。已存在但沒有 receipt 的舊 run 不猜測原 payload，回傳 HTTP 409 `run_receipt_missing`。

### POST `/v1/ingest/publish`

成功回應加入 `replayed`。D1 `topic_snapshots.content_sha256` 是 snapshot identity receipt；已發布且 hash 相同時直接回傳，不把舊 snapshot 重新切成 current。

### GET `/v1/status`

公開、只讀、`Cache-Control: no-store`。回應使用 `status-response.schema.json` version 1，包含：

- `state`: `empty | healthy | warning | stale`。
- `reasons`: `no_snapshot | freshness_warning | freshness_stale | partial_snapshot | source_failures | clock_skew`。
- `current_snapshot`: D1 current pointer 的 snapshot ID、run ID、`as_of`、partial、failed source 數、topic 數與 hash；無 current 時為 `null`。
- `freshness`: age 與 warning／stale 門檻。預設來自既有計畫的 21,600 秒與 86,400 秒，且 stale 必須大於 warning。
- `source_counts`: D1 `source_state` 的 total／success／partial／failed，不回傳來源內容。

D1 讀取失敗回傳 HTTP 503 `status_unavailable`。

### POST `/v1/run/plan`

只接受 `topic-radar.yml` 的 GitHub OIDC。request 必須綁定 token 的 workflow run ID 與 commit SHA，並包含與 manifest 同序的 1–20 個唯一 `source_id`。回應包含 admission 決策、重試秒數、每日與最短間隔政策，以及 D1 `source_state` 的 checkpoint metadata；不回傳 raw content。

Runner 將 checkpoint 轉成三種有限追補：

- `rss_window`：feed URL 不變，先依 last article／crawl 時間減五分鐘 overlap 過濾，再套 `max_items`。
- `api_since`：HN Algolia 注入 `numericFilters=created_at_i>`、Stack Exchange 注入 `fromdate`、GitHub Issues 注入 `since`。
- `latest_only`：CoinGecko、World Bank 與 Browser 不宣稱可追補歷史，只保存當期 snapshot。

所有可追補窗最多回看七天，未有 checkpoint 或過舊 checkpoint 都 clamp 到七天；未來 checkpoint clamp 到本輪時間。

### POST `/v1/alerts/action-failure` 與 scheduled watchdog

Action failure request 同樣綁定 OIDC run ID、commit SHA 與固定 repository run URL。Worker 對 `ALERT_WEBHOOK_URL` 送出通用 HTTPS JSON，目的地可由 Slack／Telegram adapter 或自有 webhook 轉接。非 HTTPS、redirect、network error 或非 2xx 都是明確失敗，不留下「已通知」receipt。

Watchdog 讀取同一個 D1 status：`empty` 或 `stale` 開啟 `topic_radar_freshness`；重複異常只更新偵測時間，不重送；恢復到 `healthy`／`warning` 時發送 resolved，再更新 D1。Cloudflare Cron 在 webhook secret 與實際送達驗證前保持關閉。

## 狀態與不變量

1. `current_snapshot` 只能指向已驗證且已持久化的 topic snapshot。
2. invalid publish 不得改變 current pointer、current `updated_at` 或舊 R2 object。
3. 舊 snapshot 重放不得將 current pointer 倒退。
4. run ID 或 snapshot ID 只能對應一個 canonical payload hash。
5. status 的 `as_of` 是 Worker 產生回應的時間；snapshot `as_of` 保留 producer 原值。

## 邊界條件

1. D1 全空時回傳 `empty`，不回傳 404。
2. current snapshot 剛好等於 warning 門檻時為 `warning`。
3. current snapshot 剛好等於 stale 門檻時為 `stale`。
4. snapshot 時間比 Worker 未來超過五分鐘時為 `warning` 並附 `clock_skew`。
5. freshness 健康但 snapshot `partial=true` 時為 `warning`。
6. freshness 健康但 D1 有 partial 或 failed source 時為 `warning`。
7. 重放 JSON 只改變 object key 順序時視為同一 canonical payload。
8. 同 run ID 改變任一 item、checkpoint 或 identity 欄位時拒絕。
9. 同 snapshot ID 改變 topics、evidence 或 partial 時拒絕。
10. 舊 run 無 receipt 時拒絕，不自動補寫不可驗證的 hash。
11. warning／stale 設定非正整數或 stale 不大於 warning 時拒絕啟動 status 判定。
12. status 不接受 POST，ingest 不接受 GET，未知路徑保持 404。
13. admission 使用 D1 條件式 insert，兩個並行 run 不得同時穿透每日或最短間隔限制。
14. 同一 workflow run 只能對應一個 commit SHA 與一個 admission 決策。
15. RSS／API catch-up 的實際 request URL 與 filter 必須保存於 run report，不只保存 checkpoint。
16. 外部 webhook 成功前不得寫入 open／resolved receipt；失敗必須向上拋出。
17. `operational_alerts` 同一 key 只允許 open → deduplicated → resolved 的可稽核轉移。

## Given／When／Then 驗收

- Given 已成功 ingest，When 重放同 payload，Then 回傳 `replayed=true` 且 item、link、audit 數不變。
- Given 已成功 ingest，When 同 run ID 改變 payload，Then 回傳 409 且原 R2 object 不變。
- Given 已發布 snapshot，When 重放同 snapshot，Then current pointer 與 `updated_at` 不變。
- Given 已有 last-good，When 發送 invalid snapshot，Then 回傳 422 且 status 仍指向 last-good。
- Given 無 snapshot，When 查詢 status，Then 回傳 version 1 `empty` response。
- Given snapshot 已過 stale 門檻，When 查詢 status，Then 回傳 `stale` 與 `freshness_stale`。
- Given 手動 workflow 開啟 resilience 選項，When 資料發布完成，Then 同一 job 驗證 ingest replay、publish replay、invalid publish 與 status pointer 不變。
- Given 已有 checkpoint，When 建立 catch-up window，Then RSS 先過濾再截斷，支援 since 的 API 實際改寫 URL，latest-only 來源不偽裝歷史追補。
- Given quota 已滿或 quiet interval 未過，When workflow 取得 run plan，Then 在安裝 Chromium 前正常跳過昂貴步驟。
- Given GitHub run 失敗，When webhook 回應 2xx，Then D1 只保存一次 open receipt；回應非 2xx 時不保存。
- Given freshness 已 stale 且後續恢復，When watchdog 連續檢查，Then 外部只收到 open 與 resolved 各一次。

## 遠端驗收紀錄

2026-08-13 以隔離 GitHub 帳號 `ai-cooperation` 與 Cloudflare 驗證帳號完成一次額度受控實測；schedule 保持關閉，沒有啟用商業 proxy 或 web unlocker。

- D1 migration `0002_ingest_receipts.sql` 套用成功；Worker version `9eb9a838-8d38-44ac-9eed-db1c37c991e5` 部署後，`/health`、`/v1/status` 均為 HTTP 200，status schema 驗證通過。
- [Actions run 31676925023](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31676925023) 從 commit `e0078b2aeea3fd6807f8ceff5e090768711fe1e3` 執行一次 15 來源收集，耗時 1 分 58 秒；同一 job 的 ingest replay 與 publish replay 都回傳 `replayed=true`。
- invalid publish 回傳 HTTP 422 `invalid_payload`，前後 status 的 snapshot ID 與 content hash 相同，last-good 未被破壞。
- D1 只產生 1 筆 `completed` receipt、1 個新 run、37 個 run links、1 個新 snapshot，以及 2 個 audit events；R2 topic object SHA-256 與 D1、status 相同。
- 來源實測 13/15：RSS 5/5、Public API 7/7、Browser＋Crawl4AI 1/3。這證明重放與 last-good 契約成立，但 Browser 成功率仍是 Gate 2 擴大來源與路由驗證的風險，不宣稱 P0 整體或 120 來源已完成。
