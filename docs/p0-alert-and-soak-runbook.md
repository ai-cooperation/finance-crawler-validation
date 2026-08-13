# P0 外部告警與低頻 soak runbook

更新日期：2026-08-13

## 目標與門檻

這份 runbook 只用於 `ai-cooperation/finance-crawler-validation` 與 Cloudflare 隔離驗證帳號。完成順序不可對調：

1. 配置 primary 與一個不同 provider／故障域的 fallback 目的地，至少 primary 必須有真人訂閱。
2. 以手動 action failure 與 freshness watchdog 證明 provider 成功、fallback、D1 receipt、GitHub issue 去重與真人收件。
3. 只有第 2 步全數通過，才能開啟 GitHub schedule 與 Worker Cron。
4. 連續七日保存 run、freshness、告警與用量證據；任一失敗先關閉排程，不擴大來源數或轉向付費服務。

目前 GitHub workflow 無 `schedule`、Worker 設定無 `crons`、Cloudflare 無 primary／fallback 告警 secret；因此不會自動耗用 Actions 或發送告警。

## 支援的目的地

`ALERT_WEBHOOK_FORMAT=auto` 依精確 hostname 選擇 adapter，相似或嵌套 hostname 會被拒絕：

| 類型 | Worker secret 內的 URL 格式 | 外送內容 |
|---|---|---|
| Slack Incoming Webhook | `https://hooks.slack.com/services/...` | `text` 只包含摘要、alert key 與 GitHub run URL |
| Telegram Bot API | `https://api.telegram.org/botTOKEN/sendMessage?chat_id=CHAT_ID` | `chat_id`、`text`、關閉 link preview |
| ntfy | `https://ntfy.sh/TOPIC` | topic、title、message、priority、tags 與選配 click URL |
| generic HTTPS | 其他 `https://...` | version 1 結構化 JSON |

Slack webhook URL、Telegram bot token 與 ntfy topic 名稱都應視為 secret，不可寫入 repository、issue、artifact 或 Actions log。Telegram 私人聊天的使用者必須先主動聯絡 bot，否則 bot 無法主動建立對話。

Adapter 格式依 [Slack Incoming Webhooks](https://api.slack.com/messaging/webhooks)、[Telegram Bot API `sendMessage`](https://core.telegram.org/bots/api#sendmessage) 與 [ntfy publish API](https://docs.ntfy.sh/publish/) 的公開契約實作；不以行銷範例或非官方 SDK 當成驗收依據。

## 安全配置

先確認帳號與目標，不輸出任何 token：

```bash
gh auth status
cd ingest-worker
npx wrangler whoami
npx wrangler secret list
```

將目的地 URL 以隱藏輸入寫入 Worker secret，避免進入 shell history：

```bash
read -r -s ALERT_ENDPOINT
printf '%s' "$ALERT_ENDPOINT" | npx wrangler secret put ALERT_WEBHOOK_URL
unset ALERT_ENDPOINT
npx wrangler secret list
```

fallback 使用同一流程寫入 `ALERT_FALLBACK_WEBHOOK_URL`。它應使用與 primary 不同的 provider 或至少不同的故障域；相同 URL 會被拒絕。primary 成功時不呼叫 fallback，只有 primary 失敗才嘗試 fallback。

`secret list` 只應出現 secret 名稱，不應顯示值。Worker 的 outbound request 有 10 秒 timeout、`redirect: manual`；任何 3xx、非 2xx、Slack 非 `ok` 或 Telegram JSON `ok != true` 都是失敗，且不寫入 D1 「已通知」receipt。Workers tracing 必須保持關閉，因為官方自動 fetch spans 會收集完整 URL，可暴露 Slack／Telegram bearer secret；結構化 logs 不得寫入 fetch error message。外部 provider 已收件但 D1 尚未落盤時若 Worker 中斷，重試可能重複送達；這是 at-least-once 的安全取捨，收件端應以 `alert_key` 識別同一事件。

## 手動故障注入驗收

手動觸發一次預期失敗；不用它擴大來源或重跑 P2：

```bash
gh workflow run topic-radar.yml \
  --repo ai-cooperation/finance-crawler-validation \
  -f verify_alert_delivery=true \
  -f verify_freshness_watchdog=false \
  -f verify_resilience=false
```

取得新 run ID 後，驗收以下證據：

- workflow 結論必須為 `failure`，因為這是故意注入；`Deliver external failure alert through OIDC` step 必須成功。
- checkout、Python 安裝、admission、Chromium、collect 與 ingest 必須全部跳過；這個驗證不佔用爬取租約。
- 真人在 Slack／Telegram／ntfy／自有系統看到該 run ID，且訊息不含 token、raw content 或 private evidence。
- D1 `operational_alerts` 只有一筆 `github_action_failure:RUN_ID`，GitHub 只有一個對應 issue。
- 從 GitHub UI 重跑同一 run 後，真人目的地、D1 receipt 與 issue 均不增加。

這個步驟不能只依據 HTTP 2xx 或臨時 webhook sink 判定通過；必須有真人收件確認。

### 2026-08-13 fallback 控制面實測

[Actions run 31692408769](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31692408769) 以 `Synthetic for testing only` 的預期 failure，在 checkout／admission 前驗證 primary 拒絕後的 fallback。attempt 1 的臨時 sink request count 為 1；重跑同一 run 的 attempt 2 後仍為 1。兩次的 checkout、Python、admission、Chromium、collect 與 ingest 均為 skipped；D1 `run_admissions` 前後都為 5，`github_action_failure:31692408769` 只有 1 筆。

[Actions run 31692456847](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31692456847) 隨後對真實 D1 `healthy` freshness 執行 OIDC watchdog，workflow 成功、昂貴步驟仍全部 skipped，sink request count 維持 1。測試後 primary／fallback secret 清空、臨時 sink 刪除，Worker `/health` 與 `/v1/status` 均為 HTTP 200。結構化證據見 [`../experiments/p0-alerts/fallback-validation-20260813.json`](../experiments/p0-alerts/fallback-validation-20260813.json)。這次只證明機器 fallback、replay 去重與健康控制路徑，不取代真人收件，也尚未驗證真實 stale 的 open／deduplicated／resolved。

### 2026-08-13 freshness 狀態機實測

未修改 D1 snapshot，僅以 `Synthetic for testing only` 的 1／2 秒暫時門檻讓正式 status 可逆地進入 stale。[run 31693420412](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31693420412) 產生 open 與 sink 第 1 筆；[run 31693443611](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31693443611) 去重後 sink 仍為 1，D1 只更新 `last_detected_at`；重新 deploy 正式 21,600／86,400 秒門檻後，[run 31693496333](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31693496333) 送出 resolved，sink 最終事件序列恰為 `open, resolved`。三個 run 均跳過 checkout／admission／Chromium／collect／ingest，D1 admission 前後均為 5。

清理後 status freshness 為 healthy、正式門檻已還原、D1 告警為 resolved、secret 清單為空、臨時 sink 已刪除，兩個端點均為 HTTP 200，且仍無 schedule／Cron。結構化證據見 [`../experiments/p0-alerts/freshness-state-machine-20260813.json`](../experiments/p0-alerts/freshness-state-machine-20260813.json)。這完成機器狀態機證明，但不等於真人目的地已收件。

再手動執行一次真實 D1 freshness watchdog：

```bash
gh workflow run topic-radar.yml \
  --repo ai-cooperation/finance-crawler-validation \
  -f verify_alert_delivery=false \
  -f verify_freshness_watchdog=true \
  -f verify_resilience=false
```

這條路徑也必須跳過 checkout／Python／admission／Chromium／collect／ingest，只以 OIDC 綁定的 request 執行與 Cron 相同的 watchdog。機器端 stale 的 open、重複檢查的 deduplicated 與恢復後的 resolved 已驗證；正式啟用前仍須在真人目的地重做收件確認。

## 啟用與回退 soak

真人收件驗收後，開啟的上限為：

- GitHub `topic-radar.yml`：每日一次，且仍受 D1 每 UTC 日 2 次、最小間隔 21,600 秒的 admission 限制。
- Worker Cron：每 6 小時只執行 freshness watchdog，不執行 Crawl4AI。

啟用前先以不取 admission、不 checkout、不安裝 Chromium的手動 control-plane run 驗證 production schedule boundary：

```bash
gh workflow run topic-radar.yml \
  --repo ai-cooperation/finance-crawler-validation \
  -f verify_soak_boundary=true \
  -f verify_alert_delivery=false \
  -f verify_freshness_watchdog=false \
  -f verify_resilience=false
```

該 run 必須成功，`Verify manual identity cannot write soak evidence` 必須確認 endpoint 回傳 HTTP 403 `schedule_identity_required`，其餘 checkout／admission／Chromium／collect／ingest 全部 skipped，D1 `soak_observations` 不新增 row。真正正向路徑只能由啟用後的第一個 `schedule` OIDC token 驗證，不能為方便測試而放寬 event claim。

2026-08-13 已以 production [run 31698746139](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31698746139) 完成這項負向驗證：workflow 成功，只有 boundary step 執行；D1 soak/admission 前後維持 0／5，health/status HTTP 200，freshness healthy，secret 空且 schedule／Cron 仍關閉。機器證據見 [`../experiments/p0-alerts/soak-boundary-validation-20260813.json`](../experiments/p0-alerts/soak-boundary-validation-20260813.json)。這不替代真正 schedule token 的正向路徑、真人收件或七日 soak。

排程必須透過 PR 啟用，並在合併後重新部署 Worker。不在 runbook 中隱藏建立 schedule 的側寫 API。任一下列情況發生時，先以 PR 移除 GitHub `schedule` 與 Worker `crons`，部署無 Cron 設定，再排查：

- 真人未收到已完成 provider delivery 的告警。
- Cloudflare scheduled watchdog 出現 failed invocation，或 `cloudflare_watchdog_failure:SCHEDULED_AT` 未送達真人目的地。
- scheduled run 連續失敗，或沒有從 checkpoint 續跑。
- `current_snapshot` 倒退、partial 被誤報 healthy，或重放產生重複業務事件。
- Actions、Workers、D1 或 R2 任一用量超出本期設定的受控上限。

## 七日驗收證據

每日只保存以下 metadata，不保存完整受保護內容：

- GitHub run ID、commit SHA、conclusion、duration 與 admission decision。
- 每個來源的 status、route、實際 request URL、item count 與 checkpoint。
- 私有 D1 soak receipt：current snapshot、freshness、source counts、該 schedule run 狀態、run/admission/alert 計數。
- R2 current topic 加最多三個 raw object 的 `head` size／SHA-256 metadata 一致性；不列舉 bucket。
- GitHub Actions、Workers、D1 與 R2 的實際用量記錄。
- 所有 open、deduplicated 與 resolved 告警的真人收件證據。

只有連續七日驗收證據齊全，才將 P0 關閉。P2 的 120 品牌驗收不重跑，已有的 114/120（95.00%）品牌級結果保持獨立證據。

### 私有 observation 與公開 artifact

`Record schedule-only soak observation` 只將回應做當場契約檢查，完整 observation 保存在 D1 `soak_observations`。它包含 object key、hash、D1 計數與稽核狀態，因此不得上傳到 public repository artifact。GitHub artifact 只保存 `run-report.json`；該報表包含 15 個來源的 status、route、實際 request URL、item count 與 checkpoint，不含 raw content。

operator 於 soak 結束後在 Cloudflare 驗證帳號匯出 receipt；命令的 SQL 必須限制指定的 7 個 workflow run ID，不可匯出整庫。再將 GitHub REST API 的 run metadata、每日 source-health artifact、Cloudflare GraphQL telemetry 與真人收件的 private reference 組成 private evidence bundle，執行：

```bash
finance-soak-verify private-soak-evidence.json \
  --output private-soak-verdict.json
```

驗收器要求恰好七個連續 UTC 日與七個不同 schedule run；denied、failed、stale、open alert、來源集合不完整、D1 counter 倒退、R2 metadata 不一致、任何一項 telemetry 缺失或超出 ceiling 均失敗。測試 fixture 均標示 `Synthetic for testing only`，不可當成實際 soak 或免費額度證據。

每日 UTC 日結束後，由 operator 在不受版本控制的私有目錄執行：

```bash
export CF_ANALYTICS_API_TOKEN='由 o970117818@gmail.com 建立的 Analytics 唯讀 token'
finance-soak-usage-collect \
  --workflow-run-id RUN_ID \
  --day YYYY-MM-DD \
  --output /absolute/private/path/usage-YYYY-MM-DD.json
```

公開 repo 的 GitHub metadata 可匿名讀取；若遇 API rate limit，可另在 operator 環境設定 `GITHUB_TOKEN`。輸出檔以建立後硬連結的方式發布、拒絕覆寫既有證據並強制為 mode `0600`，不得放入 repo、GitHub artifact 或 CI log。collector 會核對固定 repository、公開 visibility、workflow path、schedule、run attempt、commit SHA 與 standard GitHub-hosted Ubuntu runner；Cloudflare 查詢固定帳號 `ca985c195ab218488fc0744692dbde21`、Worker、D1 database ID、R2 bucket 與單一 UTC 日。任何 API/GraphQL error、空資料、未知 R2 action 或身份不符都 fail closed，不補零。

### 官方用量來源

- GitHub Actions 實際 runner 秒數取自該 run attempt 的 GitHub REST jobs `started_at`／`completed_at` 加總；run metadata另核對 `event=schedule`、`conclusion=success`、run ID、attempt 與 commit SHA。公開 repository 且所有 job 都是 standard GitHub-hosted runner 時，另記 `github_actions_billable_seconds=0`；這項免費資格不由已進入關閉程序、且公開 repo 本來就回傳 0 的 workflow-run timing endpoint推論。
- Workers requests 使用 Cloudflare GraphQL `workersInvocationsAdaptive`，固定 script name 與單一 UTC 日。
- D1 rows read/written 使用 `d1AnalyticsAdaptiveGroups`，固定 database ID 與單一 UTC 日。
- R2 operations 使用 `r2OperationsAdaptiveGroups`，固定 bucket 與單一 UTC 日；分類表依官方 pricing 的 Class A／Class B／free action 名稱。未知 action fail closed。

Cloudflare GraphQL 是 Dashboard 使用的觀測資料，不是 Cloudflare 計費帳本；每日證據必須標示 `cloudflare_analytics_scope=observed_not_billing`。因此本 gate 能證明「觀測用量低於預先 ceiling」，不能把它包裝成 Cloudflare 官方帳單。若未來驗證帳號可取得官方 billing export，應另存為獨立證據，不得覆寫 GraphQL 原始語意。

GraphQL 自動採集必須使用 Cloudflare 建立的只讀 Analytics token，放在 operator 環境變數或 GitHub environment secret，不可使用可部署 Worker 的高權限 token，也不可寫進 repo、artifact 或 log。未取得此 token前可以部署 observation API，但不能宣稱用量驗收完成，也不能關閉 P0。
