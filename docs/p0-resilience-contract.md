# P0 重放、last-good 與 status 契約

更新日期：2026-08-13

## 範圍與成功標準

本批只處理 Ingest Worker 的三個 P0 風險：同一 GitHub run 安全重放、發布失敗保留 last-good、以及從 D1 讀取可驗證的 snapshot freshness。不啟用 schedule、不建立 MCP Server、不接任何商業 proxy，也不在這批實作 120 來源排程。

完成必須同時滿足：

- 同一 ingest payload 重放不重寫 R2、不新增業務資料或稽核事件，並回傳實際 run status 與 `replayed=true`。
- 同一 `run_id` 但內容不同時回傳 HTTP 409 `run_payload_conflict`，原始 D1／R2 資料不變。
- 同一 topic snapshot 重放不重寫 R2、不重設 current，並回傳 `replayed=true`。
- 同一 `snapshot_id` 但內容不同時回傳 HTTP 409 `snapshot_payload_conflict`。
- 無論有無 current snapshot，`GET /v1/status` 都只從 D1 回傳 version 1 的狀態契約，不暴露 raw content、token 或私有證據。
- 手動 workflow 的 resilience 驗證是預設關閉的選配項；開啟時使用同一輪 15 來源垂直切片的真實 payload，在同一個 run 內完成 replay 與 invalid publish，不另外多跑第二輪爬取。禁止使用會覆蓋 current snapshot 或汙染來源健康分母的 synthetic publish。

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

## Given／When／Then 驗收

- Given 已成功 ingest，When 重放同 payload，Then 回傳 `replayed=true` 且 item、link、audit 數不變。
- Given 已成功 ingest，When 同 run ID 改變 payload，Then 回傳 409 且原 R2 object 不變。
- Given 已發布 snapshot，When 重放同 snapshot，Then current pointer 與 `updated_at` 不變。
- Given 已有 last-good，When 發送 invalid snapshot，Then 回傳 422 且 status 仍指向 last-good。
- Given 無 snapshot，When 查詢 status，Then 回傳 version 1 `empty` response。
- Given snapshot 已過 stale 門檻，When 查詢 status，Then 回傳 `stale` 與 `freshness_stale`。
- Given 手動 workflow 開啟 resilience 選項，When 資料發布完成，Then 同一 job 驗證 ingest replay、publish replay、invalid publish 與 status pointer 不變。
