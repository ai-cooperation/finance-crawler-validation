# Provider Activation 執行紀錄（2026-08-26）

文件狀態：本機實作與外部存活探測已完成；Cloudflare／GitHub 發布尚待正確帳號憑證，因此不得把本文件解讀為已部署驗收。

## 1. 目標與完成語義

起始 Provider Catalog 共 110 個 provider，其中 31 個已有可執行 route，79 個只有發現紀錄。本輪的「串接」拆成四個可稽核層級：

| 層級 | 定義 | 可交給 collector |
|---|---|---|
| L1 connector contract | adapter、runtime、probe、設定需求與 next action 均已指定 | 否 |
| L2 survival verified | provider 的資料面或控制面仍可到達 | 否 |
| L3 payload verified | 實際取得符合預期格式的 bounded payload | 尚不可；仍需 parser、rights 與 target mapping |
| L4 route integrated | endpoint、參數、auth injection、adapter 與政策閘均完成 | 是；缺 credential 時仍 fail closed |

存活不會自動升級為 callable。商業合約、停止服務或政策封鎖來源也會保留 connector contract，但標成 `not_executable`，避免系統與報告假裝已取得資料。

## 2. 本輪結果

| 指標 | 起始 | 本輪結果 |
|---|---:|---:|
| Provider Catalog | 110 | 110 |
| L4 route integrated | 31 | 50 |
| 待 activation | 79 | 60 |
| 79 個 connector contract | 0 | 79／79 |
| 本輪升級至 L4 | — | 19 |
| 目前技術上可繼續串接 backlog | — | 51 |
| 不可執行 backlog | — | 9 |

第一輪 79 個受控探測為 73 個存活、8 個直接取得資料 payload。修正 endpoint 與 auth contract並升級 19 個 route 後，對剩餘 60 個重跑雙層探測：58 個控制面存活，0 個新增 payload verified；這 0 筆不能解讀為來源死亡，因為多數 probe 的目的本來只是驗證文件／服務邊界，尚未配置金鑰與資料參數。

### 升級至 L4 的 19 個 provider

- 匿名或具官方識別要求：CFTC、DefiLlama、ECB、Eurostat、Fama-French、Federal Reserve、IMF、SEC EDGAR、US Treasury Fiscal Data。
- 需金鑰且已凍結 auth contract：CoinCap、CoinMarketCap、CryptoCompare、GNews、Massive、Mediastack、Messari、Nasdaq Data Link、NewsData、StockData.org。

上述「L4」只代表 route contract 可執行；需要 secret 的 provider 在未配置 secret 前仍回 `credential_not_configured`，不得降級成匿名假成功。

### 剩餘 60 個的邊界

| 類別 | 數量 | 下一步 |
|---|---:|---|
| 技術型 backlog | 51 | 完成 endpoint resolver、response parser、target mapping；需要授權者再配置命名 credential |
| 商業／封鎖／停止服務 | 9 | 保留於 registry 供規劃與採購，不允許自動執行 |

目前仍未證實完整資料路徑的特殊案例：

- US Census：官方文件控制面可到達，但 `api.census.gov` 在本執行環境連續逾時；狀態為 `survival=true`、`payload=false`。
- Dino Markets：目錄中的舊文件路徑回 404，尚未找到可由官方重新驗證的目前 endpoint。
- IEX Cloud：服務已退役，保留 deprecated 紀錄，不建立假 route。

## 3. 產物與重現

- 控制平面：`data/provider-activation-registry.json`
- Worker runtime：`ingest-worker/src/generated/provider-registry.json`
- 初始全量實測：`experiments/provider-activation/full-79-20260826.json`
- 目前 backlog 實測：`experiments/provider-activation/remaining-60-20260826.json`
- 探測與重生：`python scripts/probe_provider_activation.py --skip-probe`
- Worker REST：`GET /v1/providers`、`GET /v1/providers/:provider_id`
- MCP：`list_data_providers`、`get_data_provider`（需要 `research:read`）

探測器限制 concurrency 4（最大 8）、同一 endpoint 最多三次嘗試、最多保存 64 KiB sample，並區分 JSON／RSS／CSV／ZIP 與回登入頁的 HTML。GitHub Actions 只做 secret-free registry 重生與 diff gate，不新增全量排程，避免無意義消耗 Actions 額度。

## 4. 驗證與部署狀態

- Python：完整工作樹 519 tests 全數通過、總 coverage 81.26%；另由 Git index 匯出待發布 snapshot，297 tests 全數通過，避免未提交模組掩蓋依賴缺口。
- Worker pure lane：完整工作樹 9 tests、待發布 snapshot 6 tests，均全數通過；新增 `provider-registry.ts` coverage 為 statements 98.7%、branches 97.14%、functions 100%、lines 100%。需要 workerd／AI binding 的遠端 integration lane 仍須以正確 Cloudflare account 執行。
- Worker staged snapshot typecheck／dry deploy：已通過；bundle 565.69 KiB、gzip 85.60 KiB。
- 現行 production `/health`（2026-08-26 16:34 台北時間）：HTTP 200，但只回既有 `{ok, service}`，尚未包含本輪 `provider_registry` summary，因此可證實新版本尚未部署。
- Cloudflare 阻擋：本機 Wrangler OAuth 目前是 `alan.chen75@gmail.com`／account `cb8f37b75da7355292c6c23a17adf6c6`，目標資源屬驗證 account `ca985c195ab218488fc0744692dbde21`。為避免跨帳號誤部署，本輪不以錯誤 OAuth 發布。
- GitHub 阻擋：本機 `gh` 的 `ai-cooperation` 與 Alan token 均失效；SSH 已驗證為 `AlanChen75`，可讀 repository refs，但 push `ai-cooperation/finance-crawler-validation` 被 GitHub 明確拒絕。限定變更已建立本機 commit `e2f3ec9`，尚未出現在遠端，不能取得新的 Actions run 證據。

正式發布完成的驗收條件是：正確帳號 deploy 成功、production `/health.provider_registry.route_integrated=50`、兩個 REST route 讀回一致、MCP tools/list 與一次有權限讀回成功，且 GitHub commit／workflow run 可追溯。執行 deploy 指令本身不算完成。
