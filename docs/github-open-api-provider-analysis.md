# GitHub Open API Provider Analysis

研究日：2026-08-26
凍結目錄：`data/provider-catalog.yaml`

## 結論

本次不是把 GitHub awesome list 複製進文件，而是把相關候選轉成共用 Provider Catalog，並接到 Evidence Gap Broker 的 candidate handoff。正規化後共有 110 個 provider；每一筆都有來源發現紀錄、能力、地區、資產類別、成本／認證、rights、integration status 與 metric support level。

目前狀態：

| Integration status | 數量 | 可由 route renderer 使用 |
|---|---:|---:|
| `verified_public` | 24 | 是 |
| `verified_requires_key` | 26 | 是；缺 secret 時 blocked |
| `adapter_required` | 28 | 否 |
| `catalogued_unverified` | 23 | 否 |
| `commercial_only` | 7 | 否 |
| `blocked` | 1 | 否 |
| `deprecated` | 1 | 否 |
| 合計 | 110 | 50 條 provider template |

50 個 route-integrated provider 同時包含 live payload 驗證與官方文件 endpoint contract 驗證；需要 credential 的 route 若尚未以本專案 credential 取得資料，仍必須在 `verification_method` 保留差異，不能合併宣稱為 50 個全數實抓成功。起始 79 個 backlog 的 live probe 曾直接取得 8 個資料 payload；升級後的實際可呼叫性仍由 route 參數、credential、rights 與 parser gate 逐次判斷。

## GitHub discovery coverage

下列計數可以重疊；同一 provider 可能同時出現在 OpenBB 與 public-apis：

| Frozen project | Catalog 關聯筆數 | 處理方式 |
|---|---:|---|
| `public-apis/public-apis` | 83 | 篩選 Finance、News、Crypto、Government、Open Data 中與投研證據有關的 provider |
| `OpenBB-finance/OpenBB` | 36 | 32 個 provider 目錄全部登錄，另含 OpenBB wrapper 與交叉發現來源 |
| `bytewax/awesome-public-real-time-datasets` | 17 | 保留市場、總經、新聞與即時資料候選 |
| `FinMind/FinMind` | 5 | FinMind 本體及台灣官方 upstream 能力交叉索引 |
| `akfamily/akshare` | 1 | 以 library／adapter 登錄，不把 wrapper 當獨立 publisher |
| `JerBouma/FinanceDatabase` | 1 | 只作 symbol universe／peer seed，不作公司事實證據 |

每個 GitHub project 都保存 branch、40 字元 commit SHA、repository path、retrieved_at 與 list license。清單授權不會覆蓋 provider 自己的資料條款。

## 篩選邊界

「全部接入」以投資研究資料需求為範圍，不等於把 public-apis 每一列都放進研究系統。

納入：

- 公司申報、財務、分部、治理、ESG、股權與事件。
- 行情、估值、同業、識別碼、衍生品與 benchmark。
- 總經、產業量價／產能、商品、能源、貿易。
- 新聞、事件、情緒與主要加密交易所／鏈上資料。
- wrapper／directory，但標明 aggregator，不能增加獨立來源數。

排除：

- 支付、匯款、發票、IBAN／VAT 驗證、個人銀行帳戶與記帳 API。
- 純交易下單、錢包建立、支付處理器，且沒有研究資料價值者。
- 地方政府服務、犯罪、交通、郵遞區號等與目前投資研究 requirement 無關的 open data。
- RSS reader 本身、示範或娛樂 API、只保存歷史新聞但不適合目前時效需求的來源。

排除是 scope decision，不代表來源不存在；當新的 Application Harness 產生相應 requirement 時，必須重新執行 discovery，不得用本次清單聲稱永久完整。

## 研究問題 → 主要來源能力

| 研究問題 | 台灣優先 | 區域／全球補充 | 商業或待 adapter 邊界 |
|---|---|---|---|
| 公司與分部 | MOPS XBRL／年報、TWSE OpenAPI | SEC EDGAR、FMP、Intrinio | MOPS filing resolver、PDF/XBRL table parser |
| 同業與估值 | FinMind、TWSE、FinanceDatabase peer seed | FMP、Alpha Vantage、Finnhub、Tiingo、Twelve Data | ratio 必須統一期間／幣別；wrapper 不算獨立證據 |
| 台灣產業量價 | MOEA 工業產品統計、海關貿易 | UN Comtrade、OECD、World Bank | MOEA／海關需要 product／HS resolver |
| 水泥／鋼鐵 | 台灣水泥公會、鋼鐵公會、MOEA | USGS、Worldsteel、OECD、Pink Sheet | 月度明細與部分 capacity 資料為付費或需 parser |
| 石化／能源 | MOEA、海關 | EIA、FRED、Pink Sheet | 亞洲產品 spread 常是商業資料，不能用原油價格冒充 |
| 半導體／PC／Server | MOEA、WSTS | OECD、Gartner、TrendForce | Gartner／TrendForce 深度 tracker 為商業限定 |
| 治理／ESG | TWSE OpenAPI、MOPS | EPA、Eurostat、issuer reports | `financial_transmission` 是分析結果，不是下載欄位 |
| 新聞／事件 | 本地 RSS／新聞 profile、TWSE 重大訊息 | GDELT、NewsAPI、Guardian、NYT、MarketAux | 聚合器不可自動算第二獨立來源 |
| 加密資產 | — | CoinGecko、Binance、Coinbase、Kraken、Bybit、OKX、mempool.space | 各家交易所是行情 venue，不等於鏈上基本面 |

## Live payload 抽查

本次只對 6 個異質公開路徑做最小抽查，沒有使用 GitHub Actions 或 API key：

| Provider | 結果 | 語意檢查 |
|---|---|---|
| TWSE OpenAPI | HTTP 200 JSON | ESG dataset 有公司代號、年度、Scope 1–3 與驗證欄位 |
| FinMind | HTTP 200 JSON | `TaiwanStockInfo` 回 `status=200`、2 筆 2330 市場資料；匿名路徑可用 |
| World Bank | HTTP 200 JSON | API contract 正常，但 `TW` 測試回 0 筆，因此移除直接 TW coverage，只保留 global／Asia |
| GDELT DOC 2.0 | HTTP 200 JSON | TSMC 查詢回 1 篇文章與 canonical URL；曾出現一次非 JSON 暫態回應，collector 必須驗 content type／parse result |
| CoinGecko | HTTP 200 JSON | BTC 一日市場序列 289 個 price／volume observations |
| Binance | HTTP 200 JSON | BTCUSDT 日 K 回 1 列、12 欄 |

抽查只支持這 6 條路徑與當下時間，不支持「93 個來源全數可抓」。

## Broker handoff

`providers_for_gaps()` 依 `missing_metrics` 分別找來源，再合併同一 provider 的覆蓋，不要求單一來源回答整個研究問題。候選輸出包含：

- `matched_metric_support` 與 `covered_metric_count`。
- 本地／區域／全球、source tier 與 exact／derived／proxy 排序。
- `provider_not_callable`、`credential_not_configured`、`route_parameters_missing`。
- adapter、transport、endpoint template 與尚缺參數。

只有無 blocked reason 的 candidate 才能成為 collector route。2026-08-26 已把起始的 79 個非 route-integrated provider 全部納入 activation registry，完成 adapter family、runtime、health probe、設定需求與 next action 的 L1 串接；受控全量 probe 證實 73 個服務邊界存活、8 個直接資料 payload 成功。修正 Treasury／CoinCap 並接入 9 個有官方 endpoint 與 auth contract 的 key provider 後，共 19 個升級為 route-integrated，route 總數由 31 增至 50。

目前仍有 60 個 activation backlog，其中 51 個可繼續做 endpoint／parser／credential 工作，9 個為商業合約、政策封鎖或停止服務。雙層 probe 已證實 58 個 control plane 存活；Census 是文件存活但 payload route 在本環境逾時，Dino Markets 舊文件路徑未找到，IEX Cloud 已退役。這 60 個可以透過 Worker REST／MCP 被查詢與規劃，但尚不能冒充已能產生 canonical evidence 的 collector route。完整證據位於 `experiments/provider-activation/full-79-20260826.json` 與 `experiments/provider-activation/remaining-60-20260826.json`。

## 對目前 L3 缺口的實際影響

Catalog 已解決「系統不知道可以去哪裡找」與「global source 壓過台灣 source」兩個結構問題，也確保七標的 Required Data Contracts 的 43 個非 pipeline-derived metrics 都至少有 exact／derived candidate。

它尚未自動解決：

1. dataset／product／HS／company／peer identifier resolution。
2. XBRL、PDF 表格與跨來源單位／期間／幣別對齊。
3. 商業資料採購與 raw data 保存權限。
4. 每一條候選的 live health、quota、429 與 freshness monitor。
5. exact data 不存在時的 `derived`／`proxy`／`unavailable` release policy。

因此本次交付是可調用來源庫與 Broker discovery layer，不是把 110 個來源都假裝成已實跑 adapter。
