# Finance Crawler Ingest Worker

此 Worker 是 GitHub Actions 到 Cloudflare D1／private R2 的窄寫入邊界。它驗證 GitHub OIDC immutable claims、workflow run ID、commit SHA 與 JSON Schema，再以 deterministic object key、canonical payload receipt、D1 unique constraints、checkpoint 及 staging／current snapshot 實作可重放 ingest。

`GET /v1/status` 是公開且只讀的 D1 operational status，只回傳 current snapshot、freshness 與來源狀態計數，不包含 raw content 或私有證據。契約見 `schemas/status-response.schema.json`，預設 warning／stale 門檻分別為 21,600／86,400 秒。

本機驗證：

```bash
npm ci --ignore-scripts
npm run types
npm run typecheck
npm test
npm run test:coverage
npm run deploy:dry
```

部署前必須完成，否則 `oidc_not_configured` 是預期結果：

1. 建立 remote D1 與 private R2，將實際 resource ID 寫入 Wrangler 環境設定。
2. 依序套用 `migrations/0001_initial.sql`、`migrations/0002_ingest_receipts.sql` 與 `migrations/0003_operational_alerts.sql`。
3. 將 `GITHUB_REPOSITORY_ID`、`GITHUB_OWNER_ID` 與 workflow ref 設成不可變的實際值，不可只信 repository name。
4. GitHub workflow 只在手動垂直切片 job 開啟 `id-token: write`，取得指定 audience 的 OIDC token後呼叫 `/v1/ingest/items` 與 `/v1/ingest/publish`。選配 `verify_resilience` 會在同一輪真實 15 來源 payload 內驗證 replay 與 last-good，預設關閉，不另外多跑第二輪爬取。
5. 使用 `ALERT_WEBHOOK_URL` Worker secret 接上外部失敗通知。`ALERT_WEBHOOK_FORMAT=auto` 會依精確官方 hostname 辨識 Slack Incoming Webhook、Telegram Bot API 與 ntfy；其他 HTTPS URL 使用 version 1 generic JSON。所有外送設 10 秒 timeout、不跟隨 redirect，只在 provider 回覆成功後寫入 D1 receipt。Workers fetch traces 會保存完整 URL，因此本 Worker 必須保持 traces 關閉，只保留無 secret 的結構化 logs。
6. 故障注入的機器送達、D1／GitHub issue 去重與真人收件都驗證後，才啟用 schedule 與 scheduled watchdog。完整步驟見 [`../docs/p0-alert-and-soak-runbook.md`](../docs/p0-alert-and-soak-runbook.md)。

隔離驗證環境已將 repository ID `1329574278`、owner ID `258149792` 與 `topic-radar.yml@refs/heads/main` 固定在 `wrangler.jsonc`。[Actions run 31369726174](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31369726174) 已完成一次 OIDC staging→publish，遠端 D1 和 R2 的讀回驗證也已通過。schedule 仍未啟用；Cloudflare 也不存在 `ALERT_WEBHOOK_URL` secret。
