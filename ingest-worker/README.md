# Finance Crawler Ingest Worker

此 Worker 是 GitHub Actions 到 Cloudflare D1／private R2 的窄寫入邊界。它驗證 GitHub OIDC immutable claims、workflow run ID、commit SHA 與 JSON Schema，再以 deterministic object key、D1 unique constraints、checkpoint 及 staging／current snapshot 實作可重放 ingest。

本機驗證：

```bash
npm ci --ignore-scripts
npm run types
npm run typecheck
npm test
npm run deploy:dry
```

部署前必須完成，否則 `oidc_not_configured` 是預期結果：

1. 建立 remote D1 與 private R2，將實際 resource ID 寫入 Wrangler 環境設定。
2. 套用 `migrations/0001_initial.sql`。
3. 將 `GITHUB_REPOSITORY_ID`、`GITHUB_OWNER_ID` 與 workflow ref 設成不可變的實際值，不可只信 repository name。
4. GitHub workflow 只在手動垂直切片 job 開啟 `id-token: write`，取得指定 audience 的 OIDC token後呼叫 `/v1/ingest/items` 與 `/v1/ingest/publish`。
5. 接上外部失敗通知與 staleness watchdog 後才啟用 schedule。

目前 `wrangler.jsonc` 中的 repository／owner ID 保持 `TBD`，因此本 repository 尚未部署可寫的遠端入口。
