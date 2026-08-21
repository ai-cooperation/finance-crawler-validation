# App A remote Gate A runbook

本文件是把本機已驗證的研究報告產生器切片，推進到驗證帳號的最小真實路徑。所有值都應在 `o970117818@gmail.com` Cloudflare 帳號與 `ai-cooperation/finance-crawler-validation` repository 內確認；secret 不得寫入 shell history、GitHub log、D1 或 R2。

## 1. 發布前檢查

在 `finance-crawler-validation/ingest-worker` 執行：

```bash
npm test
npm run typecheck
npm run types:check
npm run deploy:dry
```

根目錄再執行：

```bash
uv run pytest -q
git diff --check
uv run finance-app-a-gate --evidence-dir experiments/app-a
```

最後一個命令是 fail-closed detector：它只能確認本機 evidence contract，若尚未提供遠端部署／Actions／OIDC／D1／R2 證據，預期結果仍是 `gate_a_status=blocked`。

需確認新版檔案已經由 reviewer commit 並發布到 `main`；未發布的工作樹不能作為遠端驗收證據。

## 2. Cloudflare 驗證帳號

先查 migration：

```bash
npx wrangler d1 migrations list finance-crawler-validation-core --remote
```

若列出 `0009_research_planner.sql` 或 `0010_report_profiles.sql`，才套用尚未套用的 migration：

```bash
npx wrangler d1 migrations apply finance-crawler-validation-core --remote
```

部署新版 Worker：

```bash
npx wrangler deploy --keep-vars
```

確認 `wrangler deployments list` 的最新 deployment version，再查：

```bash
curl --fail-with-body https://finance-crawler-validation-ingest.smallgreen-sandbox.workers.dev/health
```

`GET /mcp` 必須在帶有效 MCP bearer token 時回 `200 text/event-stream`，不能再是舊版 `405 method_not_allowed`。

## 3. Actions dispatch 憑證

Worker 只需要觸發本 repository 的 workflow，不應持有 D1／R2 讀取權。以互動方式設定，不把值放在命令列：

```bash
npx wrangler secret put GITHUB_DISPATCH_TOKEN
```

完成後只用 `npx wrangler secret list` 確認名稱存在；不要讀出或回報 secret 值。

## 4. 真實 App A 驗收

使用 `opencode.json`；專案設定已固定 `model=opencode/big-pickle`，並在 client 安全環境提供 `FINANCE_RESEARCH_MCP_TOKEN`。先確認 `tools/list`，再由 Big Pickle 提交一個明確 target（例如 `kind=crypto, symbol=BTC`）與 `source_strategy=actions` 的 research request。

部署後可用下列 bounded verifier 取代手動逐項抄錄。它只將 request/job ID、狀態、數量、品質與 gate 結果寫到 stdout，不會輸出 bearer token 或 private R2 raw content；`--timeout-seconds` 上限為 3600 秒，非 terminal job 會 fail closed：

```bash
export FINANCE_RESEARCH_MCP_TOKEN='在安全環境注入，不要寫入 repo 或 log'
uv run finance-app-a-remote \
  --base-url "$INGEST_WORKER_URL" \
  --kind crypto \
  --symbol BTC \
  --source-strategy actions \
  --collection-scope full_catalog \
  --max-sources 120 \
  --timeout-seconds 900 \
  > /tmp/app-a-remote-gate.json
jq '{gate_a_status,remote_status,checks,blocking_reasons,job_id,request_id,pack,appendix}' /tmp/app-a-remote-gate.json
```

`collection_scope=full_catalog` 是 H3 生產路徑；`max_sources` 僅作為模型 context 的相容欄位，不得縮小收集。verifier 必須核對 120 個 news brands／166 個 endpoints、15 個 radar sources、normalized items 與 target-relevant evidence。若 Worker 仍是舊版、token 無效、`GITHUB_DISPATCH_TOKEN` 缺少或 callback 沒有完成，結果必須是 `gate_a_status=blocked`／`failed`，並保留明確原因。舊版 `max_sources=12` 的結果只可作 transport smoke 歷史證據。

## 4.1 本次遠端真實結果（2026-08-21）

已以部署版本 `95321b92-eb39-4f87-8cd7-2a40908dd79e`、crypto target `BTC` 完成一次 `source_strategy=actions` 真實鏈。`plan` 回傳 12 個來源與 `refresh_required`；Actions run `32432108862` 完成 OIDC admission／publish 與 success callback；job `research_20260821001750_5ad37adc` 讀回 `partial`，Research Pack 有 15 筆 evidence、3 份 report，evidence appendix 有 15 筆，且沒有個人化買賣建議。Redacted evidence 保存於 [`experiments/app-a/20260821-remote-gate-a.json`](../experiments/app-a/20260821-remote-gate-a.json)。

這次 workflow 的研究資料路徑已通，但整體 run 後續因驗證帳號尚未配置 `ALERT_WEBHOOK_URL`，告警／告警恢復步驟回 503；GitHub fallback issue #52 已建立。另有一個 Browser source (`bogleheads_investing_browser`) 失敗，所以 Research Pack 正確標記 `partial`。兩項都必須保留在 release evidence，不得把 `remote_gate=passed` 解讀成所有營運條件已完成。

## 4.2 告警通道收尾驗證（2026-08-21）

已在 Cloudflare 驗證帳號配置 Telegram `ALERT_WEBHOOK_URL`；Worker 只保存 secret 名稱，不在 repo、D1 或 log 暴露 bot token。Slack／Telegram／ntfy 對外的人類告警文字已統一為繁體中文，機器摘要仍保留穩定英文供 D1／稽核查詢，例如：`🚨 財經議題雷達資料快照已過期`、`告警識別碼：topic_radar_freshness`。

以限 repo、僅 `Actions: Read and write`、`Metadata: Read-only` 的 fine-grained token 取代原先 dispatch secret 後，GitHub Actions run [`32448634111`](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/32448634111) 的 `verify_alert_delivery=true` 控制平面驗證完成：外部告警步驟成功、fallback issue 步驟成功，D1 已寫入 `github_action_failure:32448634111`。該 run 刻意在 checkout 前失敗，未啟動爬蟲，避免消耗資料收集額度。

這證明「新 token 可觸發 workflow」及「部署 Worker 可送達 Telegram」；仍不等於完整 App A Gate A，因為 OpenCode Desktop／Big Pickle 的部署後實際 tool-call 與七日連續 soak 仍是獨立驗收項目。

## 4.3 OpenCode／Big Pickle MCP smoke（2026-08-21）

根目錄 [`opencode.json`](../opencode.json) 已固定 `opencode/big-pickle` 與部署 Worker 的 remote MCP；Authorization 改以 `{file:~/.config/opencode/finance-research-mcp.token}` 讀取本機 `0600` 檔案，token 不進 repo。`opencode mcp list` 實測為 `finance-research connected`。

再以 `opencode run --model opencode/big-pickle` 實際呼叫部署 Worker 的唯讀工具 `finance-research_plan_research_sources`。Big Pickle 成功完成 tool call，回傳 3 個來源與 `blocked/source_budget_too_low`（因刻意使用 `max_sources=3` 的小型 smoke），未建立 job、未讀取 private raw content，模型成本回報為 0。這證明 Desktop／CLI 共用設定可完成 MCP 認證與工具呼叫；完整鏈的 bounded run 結果見下一節。

必須保存以下證據：

1. H3 的 `plan_research_sources` 回傳 full-catalog source manifest、120 brands／166 endpoints 的 collection counts、target-relevance policy 與 sufficiency decision；12–20 source IDs 只屬於歷史 bounded smoke，不得作為 H3 研究資料上限。
2. `submit_research_job` bounded 回傳 `request_id/job_id`，狀態先為 `queued` 或 `dispatching`。
3. `get_job_status` 從 `dispatching` → `processing` → `published`／`partial`，並可在關閉 client 後用同一 `job_id` 恢復；若 dispatch 暫時失敗，`retry_research_job` 只能重試原 job，不得產生另一份需求。
4. GitHub Actions run 使用完全相同的 `research_target`／`research_source_ids`／`research_requirement_id`，成功時呼叫 OIDC completion callback；若 admission 被拒或 workflow 失敗，必須呼叫 `/v1/research/jobs/fail`，並將同一 job 結束為 `blocked`／`failed`，不可停在 queued。
5. callback 後 D1 `research_jobs` 有 `run_id`／`pack_id`，R2 有 `research-packs/<pack_id>.json`。
6. `get_research_pack`、`get_research_report`、`get_evidence_appendix` 都能讀回；facts／claims 可由 evidence ID、URL、hash、as-of 反查。
7. `detailed_traceable` 至少產生 detailed report＋evidence appendix；`compact_traceable` 產生 quick card＋evidence appendix；只要求 `evidence_appendix` 時不得啟動模型，Research Pack 的 reports 應為空陣列。

## 4.4 Big Pickle 完整鏈驗收（2026-08-21）

已用新設定的 OpenCode／Big Pickle 實際完成唯一一個 bounded job，證據保存於 [`experiments/app-a/20260821-big-pickle-gate-a-final.json`](../experiments/app-a/20260821-big-pickle-gate-a-final.json)。鏈路為：

`plan_research_sources → submit_research_job → GitHub Actions 32462029861 → OIDC admission → ingest／publish → success callback → get_job_status → get_research_pack → get_research_report → get_evidence_appendix`

讀回結果：`job_id=research_20260821080829_3c4c2121`、`run_id=run_20260821t081239z`、`pack_id=pack_20260821080829_3c4c2121`，終態 `partial/published`，3 份 reports、4 筆 evidence，四個 Big Pickle readback tool calls 全部成功；R2／D1 索引可交叉驗證。這證明部署後的 MCP client／Big Pickle 實際鏈已通，不再只是本機或舊設定 smoke。

品質仍是 `partial`：12 個計畫來源中 `bogleheads_investing_browser` 失敗，`coverage_ratio=0.25`；本次 `include_market_data=false` 是明確需求，因此報告只能標示 `research_only`，不能解讀為 BTC 投資決策。驗證完成後已把 Worker admission 恢復為 `RUN_DAILY_LIMIT=2`、`RUN_MIN_INTERVAL_SECONDS=21600`。

## 4.5 品質缺口修正（2026-08-21）

上一輪的缺口不是「12 個來源都沒有連線」，而是三件事混在一起：Browser 的 Bogleheads forum 頁被 Cloudflare JS challenge 擋下、8 個來源在增量視窗內沒有新文章、topic seed 只用全域關鍵字且沒有讀取 target。已修正：

- `bogleheads_investing_browser` 改為官方 Atom feed `bogleheads_investing_rss`（`https://www.bogleheads.org/forum/feed.php?f=1`），以 RSS extractor 收 5 筆，避開 forum HTML challenge。
- source result 新增 `content_status=items|empty_window|failed`；`successful_sources` 只代表 transport 健康，另有 `content_sources` 與 `empty_sources`，並以至少 3 個有內容來源作為 radar evidence gate。
- crypto target 的 source planner 優先納入市場、新聞、Fed／ECB 官方資料與社群來源；workflow 將 frozen target／question 傳入 collector，topic ranking 對目標議題加權，避免 generic ETF／portfolio 文字蓋過標的議題。
- Research Pack 會保留本輪全部可引用 item，再從核准來源的近 7 日 last-good corpus 補足增量視窗空洞；item_id 去重，source／hash／as-of 仍可追溯。

Worker 已部署版本 `7755df00-df70-40c6-9b18-5a8bc1366497`，`GET /health` 實測 HTTP 200。GitHub workflow 的 Python／manifest 修改需隨 repository 變更發布後，才會在下一次 Actions refresh 生效；未為此修正額外觸發 Actions。

## 5. 失敗即停止條件

- 沒有 `GITHUB_DISPATCH_TOKEN`：保留 `blocked/actions_dispatch_not_configured`，不可改用舊 snapshot 假裝 refresh 成功。
- workflow 沒有 success／failure callback 或 run／commit identity 不符：Gate A 不通過，保留 job blocked／failed，不發布 Research Pack。
- R2／D1 read-back 不一致：Gate A 不通過，保留 last-good。
- Big Pickle 傳入 arbitrary URL、`asset`／`asset_class` 等不在 schema 的欄位：回 `invalid_payload`，不可猜測修正成另一個標的。
