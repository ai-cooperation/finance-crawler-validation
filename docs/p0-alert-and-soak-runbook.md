# P0 外部告警與低頻 soak runbook

更新日期：2026-08-13

## 目標與門檻

這份 runbook 只用於 `ai-cooperation/finance-crawler-validation` 與 Cloudflare 隔離驗證帳號。完成順序不可對調：

1. 配置一個真人已訂閱的外部告警目的地。
2. 以手動故障注入證明 provider 2xx、D1 receipt、GitHub issue 去重與真人收件。
3. 只有第 2 步全數通過，才能開啟 GitHub schedule 與 Worker Cron。
4. 連續七日保存 run、freshness、告警與用量證據；任一失敗先關閉排程，不擴大來源數或轉向付費服務。

目前 GitHub workflow 無 `schedule`、Worker 設定無 `crons`、Cloudflare 無 `ALERT_WEBHOOK_URL` secret；因此不會自動耗用 Actions 或發送告警。

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

`secret list` 只應出現 secret 名稱，不應顯示值。Worker 的 outbound request 有 10 秒 timeout、`redirect: manual`；任何 3xx、非 2xx、Slack 非 `ok` 或 Telegram JSON `ok != true` 都是失敗，且不寫入 D1 「已通知」receipt。外部 provider 已收件但 D1 尚未落盤時若 Worker 中斷，重試可能重複送達；這是 at-least-once 的安全取捨，收件端應以 `alert_key` 識別同一事件。

## 手動故障注入驗收

手動觸發一次預期失敗；不用它擴大來源或重跑 P2：

```bash
gh workflow run topic-radar.yml \
  --repo ai-cooperation/finance-crawler-validation \
  -f verify_alert_delivery=true \
  -f verify_resilience=false
```

取得新 run ID 後，驗收以下證據：

- workflow 結論必須為 `failure`，因為這是故意注入；`Deliver external failure alert through OIDC` step 必須成功。
- 真人在 Slack／Telegram／ntfy／自有系統看到該 run ID，且訊息不含 token、raw content 或 private evidence。
- D1 `operational_alerts` 只有一筆 `github_action_failure:RUN_ID`，GitHub 只有一個對應 issue。
- 從 GitHub UI 重跑同一 run 後，真人目的地、D1 receipt 與 issue 均不增加。

這個步驟不能只依據 HTTP 2xx 或臨時 webhook sink 判定通過；必須有真人收件確認。

## 啟用與回退 soak

真人收件驗收後，開啟的上限為：

- GitHub `topic-radar.yml`：每日一次，且仍受 D1 每 UTC 日 2 次、最小間隔 21,600 秒的 admission 限制。
- Worker Cron：每 6 小時只執行 freshness watchdog，不執行 Crawl4AI。

排程必須透過 PR 啟用，並在合併後重新部署 Worker。不在 runbook 中隱藏建立 schedule 的側寫 API。任一下列情況發生時，先以 PR 移除 GitHub `schedule` 與 Worker `crons`，部署無 Cron 設定，再排查：

- 真人未收到已完成 provider delivery 的告警。
- scheduled run 連續失敗，或沒有從 checkpoint 續跑。
- `current_snapshot` 倒退、partial 被誤報 healthy，或重放產生重複業務事件。
- Actions、Workers、D1 或 R2 任一用量超出本期設定的受控上限。

## 七日驗收證據

每日只保存以下 metadata，不保存完整受保護內容：

- GitHub run ID、commit SHA、conclusion、duration 與 admission decision。
- 每個來源的 status、route、實際 request URL、item count 與 checkpoint。
- D1 current snapshot、freshness state、source counts、run/admission/alert receipt 計數。
- R2 object 數與抽樣 SHA-256 一致性。
- GitHub Actions、Workers、D1 與 R2 的實際用量記錄。
- 所有 open、deduplicated 與 resolved 告警的真人收件證據。

只有連續七日驗收證據齊全，才將 P0 關閉。P2 的 120 品牌驗收不重跑，已有的 114/120（95.00%）品牌級結果保持獨立證據。
