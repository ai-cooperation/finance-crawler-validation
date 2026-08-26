# Investment Research Provider Catalog v1

## 目的與範圍

把 GitHub 公開 API 清單與開源財經資料專案中的候選來源，轉成 Evidence Gap Broker 可查詢、可稽核且不會誤啟用的共用能力目錄。本目錄涵蓋投資研究所需的公司申報、行情、總經、產業統計、商品、新聞、社群、治理、ESG、加密資產與識別碼；支付、個人銀行帳戶、發票、稅號驗證及交易下單服務不在本次範圍。

「全接入」指所有通過上述範圍篩選的候選都必須留在目錄，包含付費、需要金鑰、尚待 adapter 或已停止服務的來源；不代表所有候選都已驗證可抓。候選的發現狀態與執行狀態必須分離。

## Frozen discovery sources

每次匯入必須凍結 repository、branch、commit SHA、path／section、retrieved_at 與清單授權。v1 使用：

- `public-apis/public-apis`：Finance、News、Cryptocurrency、Government、Open Data。
- `OpenBB-finance/OpenBB`：`openbb_platform/providers`。
- `FinMind/FinMind`：台灣市場資料集與 REST API。
- `akfamily/akshare`：公開財經來源 adapter 能力。
- `bytewax/awesome-public-real-time-datasets`：即時公開資料候選。
- `JerBouma/FinanceDatabase`：跨資產 symbol master。

GitHub 清單只證明「被發現」，不能證明 endpoint 存活、授權可用或資料適合商業保存。正式放行必須回查 provider 官方文件或 endpoint。

## Provider contract

每個 provider 必須記錄：

- 穩定 `provider_id`、publisher／independence group 與 provider type。
- GitHub discovery provenance、官方首頁與文件 URL。
- `categories`、`requirement_ids`、逐項 `metric_support`、`geographies`、`asset_classes` 與 languages。
- transport、auth、cost tier、rights／raw storage policy。
- integration status、adapter、endpoint template、credential env、驗證時間與證據 URL。

`metric_support` 必須區分 `exact`、`derived` 與 `proxy`。代理指標不能在 Coverage Gate 冒充直接量化證據。

## 連線成熟度與執行狀態

Provider 的「存在、網站活著、資料 payload 可取、已能形成 canonical evidence」是四件不同的事。標準 pipeline 使用以下成熟度，禁止跳級：

| 等級 | 證明內容 | 可否進研究路由 |
|---|---|---|
| L0 discovered | GitHub／開源目錄找到候選 | 否 |
| L1 connector contract | 已指定 adapter family、runtime、health probe、設定需求與 next action | 否，只能 probe |
| L2 survival verified | 官方文件、登入邊界或服務端可達 | 否 |
| L3 payload verified | 固定、低流量的資料 endpoint 回傳符合預期格式 | 尚不可；仍需 parser／rights gate |
| L4 route integrated | endpoint template、參數白名單、credential 名稱、驗證證據與 adapter 均完成 | 是；執行時仍受 credential／rights gate |

`data/provider-activation-registry.json` 是 L1–L3 的控制平面；`data/provider-catalog.yaml` 中 `callable=true` 才代表 L4。`survival_verified` 絕不能自動改寫為 `callable=true`。

### 2026-08-26 activation baseline

- 來源總數：110。
- 本輪前已有 L4 route：31；待處理：79。
- 79 個已全部建立 connector contract；全量受控 probe 結果為 73 個 survival verified、8 個當場取得 data payload、6 個未證實存活。
- 修正 Treasury v1 endpoint、確認 CoinCap v3 credential boundary，並將 CFTC、DefiLlama、ECB、Eurostat、Fama-French、Federal Reserve RSS、IMF、SEC EDGAR 等實際 payload 路徑升級；另依官方 API contract 接入 CoinMarketCap、CryptoCompare、GNews、Massive、Mediastack、Messari、Nasdaq Data Link、NewsData 與 StockData.org 的 endpoint／auth injection，目前 L4 route 為 50（24 個 public、26 個 requires-key）。
- 尚餘 60 個 activation backlog：51 個技術上可繼續做 parser／resolver／credential 工作；9 個屬商業合約、政策封鎖或停止服務，必須保持不可執行。
- 雙層 probe 重跑後，60 個中 58 個已證實 control-plane 存活；Census 文件存活但資料 API 從本執行環境連續逾時，Dino Markets 舊文件路徑為 404，IEX Cloud 已退役。Census 因此是「survival=true、payload=false」，不是來源死亡。

完整探測證據：`experiments/provider-activation/full-79-20260826.json` 與 `experiments/provider-activation/remaining-60-20260826.json`。Worker 內嵌的 secret-free runtime registry 由 `scripts/probe_provider_activation.py --skip-probe` 生成，部署前必須重建。

### 2026-08-26 production baseline

- GitHub source of truth：`ai-cooperation/finance-crawler-validation` 的 `main`，目前驗證提交 `8562f310fed801b27dbb27a25fb60b378ad3ddb7`；未合併到 `main` 的工作樹不可作為 production 證據。
- Cloudflare account：`ca985c195ab218488fc0744692dbde21`；Worker version `5f2e9c27-fe44-4c50-b7e0-62f27819831c`。
- `GET /health` 必須回報 `total=110`、`route_integrated=50`、`activation_backlog=60`、`technically_connectable_backlog=51`、`not_executable=9`。
- `GET /v1/providers` 與 `GET /v1/providers/:provider_id` 已在 production 取得 HTTP 200；OpenCode 已查回 MCP transport connected。
- 模型服務是否可用與 registry／MCP transport 是否可用是兩個獨立 gate。模型 upstream error 不得改寫為 provider route failure；未取得實際 tool result 時，也不得只憑 transport connected 宣稱 agent 端到端完成。

## 執行狀態與安全 invariant

| status | 意義 | 可生成路徑 |
|---|---|---|
| `verified_public` | 官方 endpoint 已驗證、免憑證 | 是 |
| `verified_requires_key` | endpoint／adapter 已驗證，執行時需要 secret | 是，但缺 secret 時必須 blocked |
| `catalogued_unverified` | 只完成 GitHub 發現與分類 | 否 |
| `adapter_required` | 來源存在但尚無穩定 parser／resolver | 否 |
| `commercial_only` | 研究所需能力只有付費方案 | 否 |
| `blocked` | robots、條款、地區或其他政策阻擋 | 否 |
| `deprecated` | 已停止或被替代 | 否 |

硬規則：

1. `callable=true` 只允許前兩種 verified status，且必須有 adapter 與 endpoint template。
2. `verified_requires_key` 必須只記環境變數名稱，不得保存 secret。
3. `commercial_only`、`blocked`、`deprecated` 與未驗證來源永遠不能被 Broker 自動執行。
4. `public_raw_storage=restricted` 的資料只能存私有 R2 或只存 canonical facts／引用，不得進 public raw repo。
5. aggregator／wrapper 不得創造新的 independence group；原始 publisher 才能算獨立佐證。
6. URL template 只能使用白名單參數並逐值 URL encode，不能接受完整任意 URL。
7. control-plane 200 只代表網站可達；只有預期 content family 的 data endpoint 才能標記 payload verified。
8. 每個 endpoint 最多重試 3 次、單回應最多讀取 64 KiB、併發上限 8；正式全量預設併發 4。
9. 商業、blocked、deprecated provider 必須有 connector contract，但 `execution_policy=not_executable`。

## Broker I/O

Input：material gaps、missing metrics／roles／geographies、target identity、允許的成本層與已配置 credential names。

Output：每個 gap 的 ranked provider candidates：

- `provider_id`、metric support level、地區匹配、獨立來源群組。
- `callable_now` 與明確 `blocked_reasons`。
- adapter、transport 與尚未展開的 endpoint template。
- 排序分數：本地官方／監管 > 本地直接來源 > 區域 > 全球；exact > derived > proxy；免費可執行 > 需 key > 未驗證／付費。

Broker 只能把 `callable_now=true` 且參數完整的 candidate 轉成 collector route；其他項目保留為採購、adapter 或驗證 backlog。

## Requirement → test matrix

| Requirement | 驗收測試 |
|---|---|
| PC-001 frozen provenance | 所有 discovery source 有 40 字元 commit SHA、repository URL 與 path |
| PC-002 完整入庫 | 六個 discovery projects 均有候選；OpenBB provider 目錄全部被登錄或有明確 scope exclusion |
| PC-003 schema | catalog 可通過 `provider-catalog` JSON Schema |
| PC-004 唯一性 | provider ID 唯一；discovery reference 不懸空 |
| PC-005 安全放行 | callable/status/auth/adapter/endpoint/credential invariants 全數成立 |
| PC-006 metric coverage | 七標的 Required Data Contracts 的非衍生 metric 均至少有 exact／derived 候選或明確 unavailable policy |
| PC-007 region ranking | 台灣標的查詢時 TW > Asia > global，同層 exact > derived > proxy |
| PC-008 URL rendering | 缺參數失敗；query 值 URL encode；禁止未宣告參數 |
| PC-009 rights | 受限 raw 不會被標為 public raw allowed |
| PC-010 Broker handoff | gap 可得到 provider candidates，未驗證來源保留但不可執行 |
| PC-011 activation completeness | 所有非 route provider 恰有一份 activation contract；無 `adapter=none` 的技術型 backlog |
| PC-012 proof separation | health report 分開輸出 survival 與 data payload；HTML 登入頁不能冒充 JSON／RSS／CSV／ZIP payload |
| PC-013 deployed discovery | Worker REST 與 MCP 可查 110 個 secret-free provider；summary 與凍結 runtime registry 一致 |
