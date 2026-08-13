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
- Action failure 與 freshness stale／empty 只在 primary 或 fallback 外部 webhook 回應已驗證成功後寫入告警 receipt；同一事件去重，freshness 恢復時發送 recovery。
- 每次真實 `schedule` 在同一 job 結束前寫入一筆私有 soak observation；手動 dispatch 不可偽造，重放不得重查 R2 或新增 receipt。

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

### POST `/v1/alerts/action-failure`、`/v1/alerts/action-recovery`、`/v1/alerts/freshness-check` 與 scheduled watchdog

Action failure request 同樣綁定 OIDC run ID、commit SHA 與固定 repository run URL。`freshness-check` 也綁定 OIDC run ID 與 commit SHA，但不取得 admission、不 checkout、不安裝 Python／Chromium、不爬取，只對真實 D1 status 執行與 Cron 相同的 watchdog。Action failure 的故障注入同樣在 checkout 與 admission 前執行。

只有已取得 admission 並成功 publish 的工作流才呼叫 `action-recovery`。它綁定當次成功 run 的 OIDC run ID、commit SHA 與固定 repository run URL，Worker 另以 D1 證明該 workflow run 已 published 且正是 `current_snapshot`，再查出所有仍為 open 的 `github_action_failure:*`。一次最多處理 100 筆，超限直接拒絕，不能只結案部分 backlog。Worker 先送出一筆包含結案筆數的 recovery 通知，provider 確認收件後才以條件式更新將這批告警標為 resolved。沒有 open Action 告警時回傳 healthy 且不要求 webhook；外送失敗、寫入失敗或競態不一致時 fail closed，既有告警不得靜默結案。freshness 告警由獨立 watchdog 狀態機結案，不受此 endpoint 影響。

Worker 優先對 `ALERT_WEBHOOK_URL` 送出 HTTPS 告警；primary 失敗且已配置 `ALERT_FALLBACK_WEBHOOK_URL` 時才嘗試 fallback，兩者都失敗就向上拋出。`auto` 格式依精確官方 hostname 選擇 Slack Incoming Webhook、Telegram Bot API、ntfy，其他目的地使用 version 1 generic JSON。外送有 10 秒 timeout，非 HTTPS、redirect、network error、非 2xx 或 provider-level rejection 都是明確失敗，不留下「已通知」receipt。日誌不記錄可能回顯 webhook URL／token 的 fetch error message。Workers fetch traces 會保存 `url.full`、`url.path` 與 `url.query`，而 Slack／Telegram URL 含 bearer secret，因此本 Worker 必須關閉 traces，只開啟已去除 secret 的結構化 logs。

已有 D1 receipt 的一般 workflow replay 不重送。但 provider 與 D1 之間無法建立跨系統交易；若 Worker 在 provider 已收件、D1 receipt 尚未落盤的極小 crash window 中斷，後續重試可能重複告警。因此契約是 at-least-once 與可識別 `alert_key`，不宣稱跨系統 exactly-once；告警必須偏向重複而不得靜默遺失。

Watchdog 讀取同一個 D1 status：`empty` 或 `stale` 開啟 `topic_radar_freshness`；重複異常只更新偵測時間，不重送；恢復到 `healthy`／`warning` 時發送 resolved，再更新 D1。外部 generic JSON transport 已以臨時 sink驗證 open 與 replay 去重。正式 soak 必須先配置並驗證有人訂閱的 Slack／Telegram／自有 webhook 或 ntfy topic；在此之前 GitHub schedule 與 Worker Cron 保持關閉。不啟用 email、商業出口或付費功能。

Cloudflare scheduled handler 自身若因 D1／status／未預期程式錯誤失敗，必須先嘗試以同一 primary→fallback transport 發出 `cloudflare_watchdog_failure:SCHEDULED_AT`，payload 只含排程時間、cron 與穩定錯誤碼，不得外洩 exception message；之後仍重新拋出原錯誤，讓 Worker invocation 保持 failed。若原錯誤已是 webhook transport failure，不得對同一失效 transport 遞迴告警。

### POST `/v1/soak/observe`

只接受 OIDC `event_name=schedule`，並同時核對 request 與 token 的 workflow run ID、run attempt、commit SHA。Worker 將 admission、該 workflow 對應的 published run、current status、D1 控制面計數，以及 current topic object 加最多 3 個 raw object 的 R2 `head` metadata 寫入 `soak_observations`。每次最多 4 次 R2 Class B 操作，不執行 bucket list；同一 `(workflow_run_id, run_attempt)` 重放只讀既有 D1 receipt，不再存取 R2，GitHub Re-run 的下一個 attempt 則建立獨立證據。任何 object 缺失、size 為零或 `content_sha256` 與 D1 不同都回傳明確錯誤且不寫成功 receipt。

這是核心私有紀錄，不上傳到 public GitHub artifact。公開 artifact 仍只有 `run-report.json` 的一般 source-health metadata；D1 receipt 由驗證帳號 operator 使用 Wrangler 匯出。七日驗收用 `finance-soak-verify` 同時核對 7 個不同 schedule run、GitHub 官方 run metadata、完整 15 來源結果與 checkpoint、D1/R2 integrity，以及 GitHub API／Cloudflare GraphQL 的真實用量。任何缺日、手動 run、未發布、freshness 非 healthy、open alert、counter 倒退、來源集合不符、未知 R2 operation 或用量超出事先鎖定 ceiling 都 fail closed。

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
18. 手動 action／freshness 告警驗證不得呼叫 `POST /v1/run/plan`、安裝 collector／Chromium或寫入 ingest。
19. primary 送達成功時不呼叫 fallback；primary 失敗時才呼叫，且兩者失敗不寫 receipt。
20. scheduled watchdog 的執行錯誤必須嘗試外部告警並保留 failed invocation；transport 自身失敗不得遞迴送告警。
21. 同一 GitHub `(workflow_run_id, run_attempt)` 只能建立一個 soak receipt；GitHub Re-run 的新 attempt 可以留下獨立 receipt，並檢查該 workflow 最新的業務 run。
22. soak observation 必須由 schedule identity 產生；手動 dispatch 必須在 checkout／admission／crawl 前以 HTTP 403 拒絕。
23. Action failure 只能在 D1 證明後續 OIDC run 已 published 且為 current snapshot、recovery 通知送達後結案；不得刪除歷史告警或用未驗證的管理指令直接清零。一次最多結案 100 筆，超限不得部分處理。
24. 已 admission 但沒有 published current run 必須記為 `not_started`／`incomplete`，不得借用舊 snapshot 冒充成功。
25. 七日用量只接受 GitHub API 與 Cloudflare GraphQL 真實觀測資料；不允許估算或補零，未知 R2 action 必須拒絕分類。GitHub runner duration 與公開 repo 的零計費資格分開記錄；Cloudflare GraphQL 必須標為 `observed_not_billing`，不得宣稱為官方帳單。
26. 每日 usage evidence 必須綁定同日 GitHub run ID、attempt 與 commit SHA；固定 Cloudflare account、Worker、D1 與 R2 scope，私有輸出不得進入 public repo 或 artifact。

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
- Given 已選擇告警驗證模式，When workflow 執行，Then checkout、Python、admission、Chromium、collect 與 ingest 全部跳過。
- Given primary 目的地拒絕送達且 fallback 成功，When 送出 action 或 freshness 告警，Then 只在 fallback 成功後寫入 receipt。
- Given 手動 OIDC identity，When 呼叫 soak endpoint，Then 回傳 403 且 D1 不新增 observation。
- Given schedule 已發布，When寫入 soak observation，Then current run、D1 counts 與 1–4 個 R2 metadata 全部一致；同 request 重放時 R2 不可用仍能回傳 receipt。
- Given 七個連續 UTC 日的證據，When 執行 `finance-soak-verify`，Then只有全部 GitHub run 成功、15 來源報告 accepted、D1/R2 正確、真人雙通道已驗證且實際用量低於 ceiling 才回傳 accepted。
- Given 一或多筆 open Action failure，When 後續 admitted run publish 成功且 recovery provider 接受，Then只送一筆彙總 recovery 並將精確讀出的 Action failure 全數標為 resolved；若 provider 拒絕，則所有 row 保持 open。

## 遠端驗收紀錄

2026-08-13 以隔離 GitHub 帳號 `ai-cooperation` 與 Cloudflare 驗證帳號完成一次額度受控實測；schedule 保持關閉，沒有啟用商業 proxy 或 web unlocker。

- D1 migration `0002_ingest_receipts.sql` 套用成功；Worker version `9eb9a838-8d38-44ac-9eed-db1c37c991e5` 部署後，`/health`、`/v1/status` 均為 HTTP 200，status schema 驗證通過。
- [Actions run 31676925023](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31676925023) 從 commit `e0078b2aeea3fd6807f8ceff5e090768711fe1e3` 執行一次 15 來源收集，耗時 1 分 58 秒；同一 job 的 ingest replay 與 publish replay 都回傳 `replayed=true`。
- invalid publish 回傳 HTTP 422 `invalid_payload`，前後 status 的 snapshot ID 與 content hash 相同，last-good 未被破壞。
- D1 只產生 1 筆 `completed` receipt、1 個新 run、37 個 run links、1 個新 snapshot，以及 2 個 audit events；R2 topic object SHA-256 與 D1、status 相同。
- 該次重放／last-good 實測的來源為 13/15：RSS 5/5、Public API 7/7、Browser＋Crawl4AI 1/3。這是當次 run 的真實結果，不外推為長期成功率。
- D1 migration `0003_operational_alerts.sql` 已套用。[Actions run 31684943198](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31684943198) 取得 admission 後實際執行 checkpoint catch-up，來源 14/15 成功、產生 19 items；`run-report.json` SHA-256 為 `a6aed887b92a7d73a4e34d07216c3fae80c7cf2a9e1da54478e72c1bbdc11d75`，並保留 RSS/API 改寫後的實際 URL。
- D1 記錄 run `31684943198` 為 `admitted`；後續 `31685137981`、`31685320719`、`31685905458` 與 `31687414440` 均為 `minimum_interval` denial。修正合法 `false` 被 jq 誤判後，run `31687414440` 在 19 秒內正常結束，未安裝 Chromium，也未執行 collect 或 ingest。
- [Actions run 31685905458](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31685905458) 以手動故障注入驗證外部 transport。臨時 HTTPS sink、D1 `operational_alerts` 與 GitHub issue 各只產生 1 筆；重放同一 run 後仍各為 1。測試 sink 已移除、Cloudflare 告警 secret 已清空，因此這項只證明機器到機器的送達與去重，不宣稱真人收件成功。
- [Actions run 31692408769](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31692408769) 以 `Synthetic for testing only` 的預期 failure 驗證 production fallback：primary 拒絕後臨時 sink 收到 1 筆；重跑同一 run 後仍為 1。兩次 checkout、admission、Chromium、collect 與 ingest 都 skipped，D1 admission count 維持 5，該 alert key 只有 1 筆。接著 [Actions run 31692456847](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31692456847) 對真實 `healthy` D1 status 執行 OIDC freshness watchdog，成功且沒有新增外送。測試後兩個 secret 與 sink 都已清除，Worker 健康端點仍為 HTTP 200；結構化證據見 [`../experiments/p0-alerts/fallback-validation-20260813.json`](../experiments/p0-alerts/fallback-validation-20260813.json)。
- freshness 狀態機另以不修改 snapshot 的暫時 1／2 秒門檻完成遠端驗證：[run 31693420412](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31693420412) opened、[run 31693443611](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31693443611) deduplicated、恢復正式 21,600／86,400 秒門檻後 [run 31693496333](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31693496333) resolved。sink 事件只有 open 與 resolved，D1 admission 前後均為 5；清理後 secret 空、status healthy、D1 alert resolved、端點 200。此 `Synthetic for testing only` 注入只證明機器狀態轉移；完整證據見 [`../experiments/p0-alerts/freshness-state-machine-20260813.json`](../experiments/p0-alerts/freshness-state-machine-20260813.json)。
- Gate 2 已另以 120 個唯一新聞品牌驗收，114/120 成功（95.00%）。P0 的 soak observation 與七日驗收器已完成本機 TDD，待 migration／Worker 遠端驗證；關閉 P0 仍需配置真人可收取的 primary/fallback、啟用低頻 schedule／Cron，累積七日真實證據。在此之前兩種排程均保持關閉。
- D1 `0004_soak_observations.sql` 與 Worker version `fb2ade46-2ebd-4897-b258-ff63fdb7e58d` 已部署。[run 31698746139](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31698746139) 證明 production `workflow_dispatch` OIDC 被 schedule-only endpoint 以 403 拒絕，且 checkout、admission、Chromium、collect、ingest 全部 skipped，D1 soak/admission 前後維持 0／5。證據見 [`../experiments/p0-alerts/soak-boundary-validation-20260813.json`](../experiments/p0-alerts/soak-boundary-validation-20260813.json)；schedule 正向路徑仍待啟用後第一輪驗證。
