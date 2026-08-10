# 財經爬蟲來源選擇與能力邊界

研究日期：2026-08-07

## 驗證問題

這個 PoC 驗證的是 GitHub-hosted runner 加 Crawl4AI/HTTP client 的資料取得能力，不是驗證內容正確性，也不構成投資建議。

成功契約如下：

1. runner 能完成 DNS、TLS、HTTP 與必要的 Chromium 渲染；
2. 回應不是登入、API key、robots 或反機器人攔截頁；
3. 內容達最低長度並包含來源專屬必要詞；
4. 每個來源跑兩次時，報表保留每輪結果，不能用一次成功掩蓋另一次失敗。

## 擴大後的代表來源

| 類型 | 代表來源 | 選擇理由 | 主要能力假設 |
|---|---|---|---|
| 台灣大眾社群 | Dcard 理財、Mobile01 投資理財、PTT Stock | 涵蓋年輕族群、長篇論壇與 BBS | JavaScript、反機器人與站方政策會形成主要邊界 |
| 長期投資社群 | Bogleheads | 官方 About 頁稱社群超過 13 萬會員、700 萬篇貼文 | 傳統論壇 HTML 是否能由資料中心 IP 穩定取得 |
| 加密社群 | Bitcointalk、Stocktwits | 涵蓋長篇論壇與即時個股/幣圈情緒流 | 公開頁與強反機器人頁的差異 |
| 投資社群平台 | Reddit r/investing、TradingView Ideas | 大型討論與交易觀點社群 | 公開 HTML 與匿名 JSON/API 的權限可能不同 |
| 專業問答與開源 | Quant Stack Exchange、OpenBB Discussions、HN Search API | 涵蓋量化問答、金融開源專案與開發者社群 | 一般 HTML 與官方社群 API 的可重現性 |
| 官方資料與消息 | TWSE、MOPS、央行、金管會、Fed RSS、World Bank API | 結構化資料、法規/政策消息及舊式入口 | 官方機器介接通常比互動頁穩定，但仍受 robots 與使用規範約束 |
| 市場資料 | Yahoo Finance、CoinMarketCap、Finviz、CoinGecko | 延續 Crawl4AI 範例並加入公開 JSON API | 動態頁、Cloudflare 與公開 API 的差異 |
| 新聞與聚合 | BBC RSS、Google News RSS、Reuters、FT、MarketWatch、NBC | RSS、聚合器、一般新聞與付費媒體 | RSS 通常較穩；付費牆或 bot 防護不得繞過 |
| 認證邊界 | FRED、Alpha Vantage 無金鑰請求 | 官方文件明載 API key 要求 | 系統應回報 `auth_required`，不能誤判為成功或一般 HTTP 錯誤 |

## 熱門度與選源證據

熱門只用來挑選有代表性的壓力測試來源，不代表推薦或內容品質排名。

- [Bogleheads About](https://www.bogleheads.org/forum/about) 公開其會員、貼文及流量級距；論壇首頁也提供主題與貼文總數。
- [Mobile01 投資理財分類](https://www.mobile01.com/forumlist.php?f=37) 在研究日顯示「投資理財綜合」約 11 萬主題、251 萬篇回覆/文章量級。
- [Bitcointalk 首頁](https://bitcointalk.org/) 在研究日顯示 Economics 分類超過 300 萬篇貼文。
- [Dcard 理財板](https://www.dcard.tw/f/money) 持續提供熱門、最新與精華等討論入口。
- [TradingView 社群說明](https://www.tradingview.com/support/solutions/43000761245-tradingview-social-network/) 列出 Ideas、Community Scripts、Minds 等公開社群內容。
- [OpenBB GitHub repository](https://github.com/OpenBB-finance/OpenBB) 在研究日由 GitHub API 觀察到約 7.15 萬 stars、7,337 forks，並有持續更新的 issues/discussions。

每個經研究選入的來源，都在 `sources.yaml` 保存 `provenance` 與 `selection_evidence`，使選源理由可追溯。

## 合規與技術邊界

- 僅讀取公開 URL；不登入、不供應假身分、不繞過 CAPTCHA、付費牆或 API key。
- browser 路徑開啟 robots.txt 檢查；被拒絕時記為 `robots_denied`。
- Reddit JSON 是匿名存取邊界測試；正式資料產品應依 [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms) 與官方開發者流程取得授權。
- FRED 與 Alpha Vantage 只驗證「缺少金鑰」的分類，不在 repository 保存任何 secret。
- 僅保存狀態、耗時、字數、SHA-256、錯誤摘要與最多 500 字預覽；不建立完整內容鏡像。
- 目前 workflow 僅能手動觸發。若日後加排程，必須同時加入失敗通知與資料新鮮度反向檢查。
- 同一結果只代表該 GitHub runner、時間點與設定下的系統量測；站方規則、地區、IP 信譽與網頁結構改變後都需重測。

## 結果語意

| 結果 | 意義 |
|---|---|
| `success` | HTTP/瀏覽器取得、內容長度與必要詞全部通過 |
| `auth_required` | 回應要求登入、API key 或其他認證 |
| `blocked` | 403 或可辨識的反機器人/CAPTCHA 攔截 |
| `robots_denied` | robots 規則拒絕 browser 抓取 |
| `rate_limited` | 429 限流 |
| `invalid_content` | 有回應但缺必要詞、內容太短或拿到非預期頁面 |
| `http_error` / `tls_error` / `timeout` / `error` | 其他傳輸或執行錯誤 |

機器可讀報表 schema v3 同時提供 `by_transport`、`by_kind`、`by_community_type`、`by_region`、`by_access_tier` 與 `source_stability`，用來判斷來源類型差異、存取層級與重複觀察的一致性。
