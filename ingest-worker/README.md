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
2. 依序套用 `migrations/0001_initial.sql` 與 `migrations/0002_ingest_receipts.sql`。
3. 將 `GITHUB_REPOSITORY_ID`、`GITHUB_OWNER_ID` 與 workflow ref 設成不可變的實際值，不可只信 repository name。
4. GitHub workflow 只在手動垂直切片 job 開啟 `id-token: write`，取得指定 audience 的 OIDC token後呼叫 `/v1/ingest/items` 與 `/v1/ingest/publish`。選配 `verify_resilience` 會在同一輪真實 15 來源 payload 內驗證 replay 與 last-good，預設關閉，不另外多跑第二輪爬取。
5. 接上外部失敗通知與 staleness watchdog 後才啟用 schedule。

隔離驗證環境已將 repository ID `1329574278`、owner ID `258149792` 與 `topic-radar.yml@refs/heads/main` 固定在 `wrangler.jsonc`。[Actions run 31369726174](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31369726174) 已完成一次 OIDC staging→publish，遠端 D1 和 R2 的讀回驗證也已通過。schedule 仍未啟用。
