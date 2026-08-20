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
  --max-sources 12 \
  --timeout-seconds 900 \
  > /tmp/app-a-remote-gate.json
jq '{gate_a_status,remote_status,checks,blocking_reasons,job_id,request_id,pack,appendix}' /tmp/app-a-remote-gate.json
```

這個 verifier 不是部署替代品：若 Worker 仍是舊版、token 無效、`GITHUB_DISPATCH_TOKEN` 缺少或 callback 沒有完成，結果必須是 `gate_a_status=blocked`／`failed`，並保留明確原因。

必須保存以下證據：

1. `plan_research_sources` 回傳 `research_requirement`、12–20 個 source IDs 與 sufficiency decision。
2. `submit_research_job` bounded 回傳 `request_id/job_id`，狀態先為 `queued` 或 `dispatching`。
3. `get_job_status` 從 `dispatching` → `processing` → `published`／`partial`，並可在關閉 client 後用同一 `job_id` 恢復；若 dispatch 暫時失敗，`retry_research_job` 只能重試原 job，不得產生另一份需求。
4. GitHub Actions run 使用完全相同的 `research_target`／`research_source_ids`／`research_requirement_id`，成功時呼叫 OIDC completion callback；若 admission 被拒或 workflow 失敗，必須呼叫 `/v1/research/jobs/fail`，並將同一 job 結束為 `blocked`／`failed`，不可停在 queued。
5. callback 後 D1 `research_jobs` 有 `run_id`／`pack_id`，R2 有 `research-packs/<pack_id>.json`。
6. `get_research_pack`、`get_research_report`、`get_evidence_appendix` 都能讀回；facts／claims 可由 evidence ID、URL、hash、as-of 反查。
7. `detailed_traceable` 至少產生 detailed report＋evidence appendix；`compact_traceable` 產生 quick card＋evidence appendix；只要求 `evidence_appendix` 時不得啟動模型，Research Pack 的 reports 應為空陣列。

## 5. 失敗即停止條件

- 沒有 `GITHUB_DISPATCH_TOKEN`：保留 `blocked/actions_dispatch_not_configured`，不可改用舊 snapshot 假裝 refresh 成功。
- workflow 沒有 success／failure callback 或 run／commit identity 不符：Gate A 不通過，保留 job blocked／failed，不發布 Research Pack。
- R2／D1 read-back 不一致：Gate A 不通過，保留 last-good。
- Big Pickle 傳入 arbitrary URL、`asset`／`asset_class` 等不在 schema 的欄位：回 `invalid_payload`，不可猜測修正成另一個標的。
