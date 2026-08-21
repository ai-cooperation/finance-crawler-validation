# Finance Crawler Ingest Worker

此 Worker 是 GitHub Actions 到 Cloudflare D1／private R2 的窄寫入邊界。它驗證 GitHub OIDC immutable claims、workflow run ID、commit SHA 與 JSON Schema，再以 deterministic object key、canonical payload receipt、D1 unique constraints、checkpoint 及 staging／current snapshot 實作可重放 ingest。

`GET /v1/status` 是公開且只讀的 D1 operational status，只回傳 current snapshot、freshness 與來源狀態計數，不包含 raw content 或私有證據。契約見 `schemas/status-response.schema.json`，預設 warning／stale 門檻分別為 21,600／86,400 秒。

App A 的研究報告產生器 MCP endpoint 是 `POST /mcp`。它支援 `initialize`、`tools/list` 與下列工具：`resolve_target`、`plan_research_sources`、`submit_research_job`、`get_job_status`、`retry_research_job`、`get_research_pack`、`get_research_report`、`get_evidence_appendix`。`plan_research_sources` 會檢查目前 published snapshot／source freshness，回傳 `research_requirement`、`source_bundle` 與 sufficiency decision；它不會直接啟動爬蟲。MCP 使用獨立的 `MCP_API_TOKEN` Worker secret，未配置或 token 不符時 fail closed；不要把 token 放進 repository、log 或文件。`submit_research_job` 只回 bounded job metadata，`get_job_status` 可用 `job_id` 或跨桌面重啟仍穩定的 `request_id` 查詢，並回傳 `stage`、`progress`、`retryable` 與 `next_action`。`collection_scope=full_catalog` 的 MCP 垂直切片在 Cloudflare 30 秒背景執行邊界內先產出 deterministic evidence-linked report，模型欄位會標示 `deterministic-evidence-v1`；這份報告只做研究整理，不下買賣指令。需要較長模型推理時使用 `source_strategy=actions`，由 GitHub Actions 執行並透過 OIDC callback 寫入 private R2／D1／audit。`retry_research_job` 只重新派送或重新排入背景執行，且 running 工作超過 10 分鐘會自動轉成可重試失敗。dispatch credential 缺少或拒絕時明確落為 `blocked/actions_dispatch_not_configured`。`/v1/research/jobs/complete` 與 `/v1/research/jobs/fail` 綁定 frozen `research_target`、`research_requirement_id`、approved source IDs 與 OIDC run／commit，避免 Actions job 永久停在 queued。

`requirements.include_market_data` 是明確的按需旗標。`false` 時 planner 排除 market source，Actions 傳遞 `research_include_market_data=false`，workflow 以 `--skip-market-data` 產生 `provider=not_requested` 的空 market snapshot；這不是 provider 失敗，也不會用不相干的 CoinGecko 資料補洞。`true` 時才執行 target/provider 支援檢查。

本機驗證：

```bash
npm ci --ignore-scripts
npm run types
npm run typecheck
npm test
npm run test:coverage
npm run deploy:dry
```

P1 的 `/v1/ingest/market-alignment` 是同一個 GitHub OIDC 寫入邊界的窄路徑。它只接受已發布 run 的
`market-alignment-envelope`，會驗證 market snapshot、topic alignment 與 run evidence，並將市場快照與對齊結果寫入 private R2、D1 index 與 `openbb_normalized` audit event；重送相同 payload 回傳 `replayed=true`。完整契約見 [`../docs/p1-openbb-alignment-spec.md`](../docs/p1-openbb-alignment-spec.md)。

`/v1/ingest/tradingagents-plan` 只保存已通過 budget gate 的 bounded run plan；
`/v1/ingest/research-report` 才接受實際的私有「第二意見」。兩者都要求同一個已發布 run、同一個 market alignment 與 run evidence，報告寫入 private R2、D1 index 與 append-only audit event，並以 `report_id`／payload hash 做冪等重放。此版本不在 workflow 中自動呼叫付費模型；沒有模型輸出時不會產生研究報告。

新產生的 Research Pack 會額外寫入 `evidence_graph`：每個報告的 bull／bear／risk／catalyst／failure condition／data gap 都有穩定 `claim_id`，並保留 `report_id`、`topic_id` 與 evidence IDs。這是 v1-compatible optional extension；讀取舊 Pack 時若沒有 graph 仍可 replay，但新 Pack 不得省略主張—證據交接。

部署前必須完成，否則 `oidc_not_configured` 是預期結果：

1. 建立 remote D1 與 private R2，將實際 resource ID 寫入 Wrangler 環境設定。
2. 依序套用 `migrations/0001_initial.sql`、`migrations/0002_ingest_receipts.sql`、`migrations/0003_operational_alerts.sql`、`migrations/0004_soak_observations.sql`、`migrations/0005_market_alignment.sql`、`migrations/0006_tradingagents_plans.sql`、`migrations/0007_research_reports.sql`、`migrations/0008_research_jobs.sql`、`migrations/0009_research_planner.sql` 與 `migrations/0010_report_profiles.sql`。
3. 將 `GITHUB_REPOSITORY_ID`、`GITHUB_OWNER_ID` 與 workflow ref 設成不可變的實際值，不可只信 repository name。
4. GitHub workflow 只在手動垂直切片 job 開啟 `id-token: write`，取得指定 audience 的 OIDC token後呼叫 `/v1/ingest/items` 與 `/v1/ingest/publish`。選配 `verify_resilience` 會在同一輪真實 15 來源 payload 內驗證 replay 與 last-good，預設關閉，不另外多跑第二輪爬取。
5. 使用 `ALERT_WEBHOOK_URL` Worker secret 接上外部失敗通知。`ALERT_WEBHOOK_FORMAT=auto` 會依精確官方 hostname 辨識 Slack Incoming Webhook、Telegram Bot API 與 ntfy；其他 HTTPS URL 使用 version 1 generic JSON。所有外送設 10 秒 timeout、不跟隨 redirect，只在 provider 回覆成功後寫入 D1 receipt。後續 admitted 且 publish 成功的 workflow 會先送出 action recovery，送達後才將既有 Action failure 標為 resolved；不刪除歷史告警。Workers fetch traces 會保存完整 URL，因此本 Worker 必須保持 traces 關閉，只保留無 secret 的結構化 logs。
6. 正式 soak 另配置 `ALERT_FALLBACK_WEBHOOK_URL` secret；primary 失敗時才嘗試 fallback，兩者失敗不寫 receipt。手動 action failure 與 freshness watchdog 驗證都在 checkout／admission 前執行，不安裝 Chromium、不爬取。機器送達、D1／GitHub issue 去重與真人收件都驗證後，才啟用 schedule 與 scheduled watchdog。完整步驟見 [`../docs/p0-alert-and-soak-runbook.md`](../docs/p0-alert-and-soak-runbook.md)。

Actions refresh 另需設定窄權限的 `GITHUB_DISPATCH_TOKEN` Worker secret；它只允許觸發本 repository 的 `topic-radar.yml` workflow，不可用來讀取 R2／D1。secret 未配置時，App A 仍會保存 planner／job，但狀態會是 `blocked/actions_dispatch_not_configured`，不會假裝已收集。

MCP token 建立方式（值只在安全的 client onboarding 交付）：

```bash
npx wrangler secret put MCP_API_TOKEN
```

驗證環境曾以舊版 `latest_published` 垂直切片測過 `initialize → tools/list → submit → status → Research Pack／report／appendix read-back`；這不代表新版 Planner／Actions callback 已通過 Gate A。token smoke 後已輪替，不能從 repository 或 log 還原。

Repository 根目錄的 `opencode.json` 是不含 secret 的 OpenCode Desktop 設定，並固定 `model: opencode/big-pickle` 作為 App A 編排模型；Authorization 透過 `{file:~/.config/opencode/finance-research-mcp.token}` 讀取本機 `0600` token 檔案，檔案不得放進 repository 或 log。若改用 CLI／CI，可改以 `FINANCE_RESEARCH_MCP_TOKEN` 環境變數注入。MCP endpoint 同時保留 Streamable HTTP POST 與 GET endpoint-event 相容層，讓 OpenCode 的 remote connector 能完成連線探測。

發布前可在 repository 根目錄執行 `uv run finance-app-a-gate --evidence-dir experiments/app-a`。這個 detector 只檢查本機 App A evidence；即使本機檢查通過，沒有部署版本、有效 MCP token、Actions run、OIDC callback 與 D1/R2 read-back 時仍會回報 `gate_a_status=blocked`，不可把它當成遠端 Gate A 通過。

部署新版 Worker 後，可用 `uv run finance-app-a-remote --base-url "$INGEST_WORKER_URL" --source-strategy actions --timeout-seconds 900` 執行一次 bounded remote Gate A。MCP token 只從 `FINANCE_RESEARCH_MCP_TOKEN` 讀取；輸出只保留 job／artifact metadata 與 gate checks，不保存 bearer token 或 private raw content。非 terminal job、callback 失敗、私有 Pack 讀回不一致或報告含個人化建議時，命令回傳非零並維持 Gate A blocked。

隔離驗證環境已將 repository ID `1329574278`、owner ID `258149792` 與 `topic-radar.yml@refs/heads/main` 固定在 `wrangler.jsonc`。[Actions run 31369726174](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31369726174) 已完成一次 OIDC staging→publish，遠端 D1 和 R2 的讀回驗證也已通過。schedule 仍未啟用；Cloudflare 也不存在 `ALERT_WEBHOOK_URL` secret。
