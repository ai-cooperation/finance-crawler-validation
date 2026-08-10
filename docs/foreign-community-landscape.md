# 國外財經社群來源全集與分層爬蟲評估契約

研究日期：2026-08-07

## 「全面」的操作定義

網路上不存在可證明封閉的「所有財經社群」名單。本專案把全面定義為：覆蓋所有主流資料取得形態、主要財經討論族群與代表性區域市場，並把無法匿名取得的主流平台保留在 catalog，而不是假裝不存在。

`foreign-community-sources.yaml` 目前包含 86 條獨立來源路徑：70 條無憑證結果記錄、16 條 catalog-only。前者分為 38 條 browser、18 條 JSON API、14 條 RSS/Atom；其中公開治理分母是 38 條 public web、14 條 public API 與 14 條 public feed，另有 4 條是無憑證認證邊界探測。38 條 browser 內有 7 條版本化 robots 禁止，Actions 會產生明確結果但不對目標發出 Browser 請求。

這不是「86 個互不重複網站」：公開路徑透過 `route_group` 合併為 47 個可解析社群／開源生態，其餘是認證或會員邊界。Reddit 的不同 subreddit、Telegram 頻道、Discord server 與 Facebook group 數量會持續變動，因此 catalog 以平台與代表性財經入口為單位。

## 涵蓋的討論類型

| 類型 | 代表來源 |
|---|---|
| 個人理財／FIRE | Bogleheads、MoneySavingExpert、Financial Wisdom、Mr. Money Mustache、Rational Reminder、White Coat Investor、Money Stack Exchange |
| 零售投資 | Reddit、InvestorsHub、HotCopper、ADVFN、Silicon Investor、RedFlagDeals、Rankia、Wertpapier、Moneycontrol |
| 主動交易 | Elite Trader、Trade2Win、Aussie Stock Forums、MQL5、Trading Q&A |
| 價值投資 | Value Investors Club、Corner of Berkshire and Fairfax、ValuePickr |
| 量化交易 | Quant Stack Exchange、QuantConnect、Lean、Backtrader、Jesse |
| 加密資產 | Bitcointalk、Bitcoin Stack Exchange、Ethereum Research、Ethereum Magicians、Hummingbot、CoinMarketCap Community |
| 社群投資 | TradingView、Stocktwits、Seeking Alpha、Investing.com、eToro、Mastodon、Bluesky、X、YouTube |
| 開源開發者生態 | OpenBB、Freqtrade、Lean、Hummingbot、Backtrader、Jesse、Hacker News |
| 專業金融職涯 | Wall Street Oasis、LinkedIn groups、Slack workspaces |

區域維度涵蓋 global、US、UK、AU、CA、IN、ES 與 DE；非英語代表包括 Rankia、西班牙語社群，Wertpapier、德語社群，以及印度的 Trading Q&A、ValuePickr、Moneycontrol。

選源規模證據包括：

- [InvestorsHub About](https://investorshub.advfn.com/boards/about.aspx) 公開會員、訊息與 boards 數量；
- [HotCopper About](https://hotcopper.com.au/help/about/) 說明其澳洲投資論壇定位；
- [Rankia Forums](https://www.rankia.com/foros) 提供西語股票、基金與個人理財論壇的公開活動量；
- [Bogleheads About](https://www.bogleheads.org/forum/about) 公開會員、貼文與流量級距；
- [ADVFN discussion forums](https://uk.advfn.com/welcome/discussion-forums) 說明免費與 premium boards 的界線；
- [QuantConnect forum docs](https://www.quantconnect.com/docs/v2/cloud-platform/community/forum) 說明量化策略、實作與發文資格。

完整證據 URL 保存在每筆來源的 `selection_evidence`，不依賴本文件的二手摘要。

## Browser＋API＋RSS 分層假設

```text
公開社群
  ├─ 官方 API / 公開 JSON：優先，結構化且容易驗證
  ├─ RSS / Atom：GitHub 直連 → 固定白名單 Cloudflare relay
  └─ Browser / Crawl4AI：只補沒有機器介面或需要渲染的公開頁

受限社群
  ├─ OAuth / API key / bot installation：取得授權後走 API
  ├─ 會員或付費內容：不以 browser 繞過
  └─ 無合法匯出路徑：標記 catalog-only
```

同站多路徑的關鍵對照組：

| 社群家族 | Browser | JSON/API | RSS/Atom |
|---|---:|---:|---:|
| Stack Exchange：Money／Bitcoin／Quant | ✓ | ✓ | ✓ |
| Discourse：Trading Q&A／ValuePickr | ✓ | ✓ | ✓ |
| Rational Reminder | 會員 | 會員 | 會員 |
| Ethereum Research／Magicians | — | ✓ | ✓ |
| XenForo：Elite Trader／Trade2Win／Aussie Stock Forums | ✓ | — | ✓ |
| Bogleheads／MoneySavingExpert／Financial Wisdom | ✓ | — | ✓（含邊界探測） |
| Reddit | ✓ | 匿名 JSON 邊界＋OAuth catalog | — |

若 HTML 被 Cloudflare、Akamai 或 robots 擋住但官方 JSON/feed 通過，分層策略成立；若三條路徑都拒絕，則必須取得正式授權或放棄該來源，不能靠更激進的 browser 偽裝。

Cloudflare relay 不是通用 proxy：只允許 manifest 內七個 feed ID、不接受 URL 參數、不追隨重導、宣告超過 2 MB 的回應會 fail closed。這是用不同合法出口排除 GitHub Runner IP/CDN 差異，不是繞過會員、驗證或 robots 邊界。

## 認證與合規邊界

- Reddit 正式路徑依 [Data API Terms](https://redditinc.com/policies/data-api-terms) 與 OAuth；匿名 JSON 只用來量測邊界。
- X 官方文件目前描述為 [pay-per-use API](https://docs.x.com/overview)；無 token 請求只驗證 `auth_required` 分類。
- YouTube 留言應走 [`commentThreads.list`](https://developers.google.com/youtube/v3/docs/commentThreads/list) 並使用 API key；不爬 rendered comments 來規避配額。
- Discord 需要 bot/OAuth token 與 `READ_MESSAGE_HISTORY` 等權限，依 [Discord API reference](https://docs.discord.com/developers/reference)。
- Mastodon public timeline 是否要求 token 由 instance 的 public-preview 設定決定，依 [官方 timeline 文件](https://docs.joinmastodon.org/methods/timelines/)。
- Bluesky 多數 public AppView endpoints 可匿名呼叫，但 `searchPosts` 的[lexicon](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/searchPosts.json) 明記可由服務提供者要求認證，因此本矩陣將它列為 auth boundary。
- GitHub 公開 repository issues 可無認證讀取，但受較低 rate limit；依 [GitHub REST issues 文件](https://docs.github.com/en/rest/issues/issues)。

Rational Reminder 目前將社群首頁與 RSS 重導到 `/login`，`latest.json` 也回傳 `not_logged_in`，因此三條都改列 member-only catalog。Facebook／LinkedIn groups、WhatsApp communities、Slack workspaces、Public.com、Webull、Substack Chat／comments 等會員資料也保持停用。Telegram 完整頻道歷史不以 `t.me` 網頁替代正式 MTProto 權限。

## 2026-08-08 測量更正與 Cloudflare 實測

舊報表的 50%、59% 與 87.5% 不是同一層級的系統可行率，不得並列解讀：

- RSS 50% 是舊 GitHub job 中 7/14 條直連路徑兩次都成功；分母還錯把已轉為登入制的 Rational Reminder 算成 public feed。移除該會員來源後分母是 13；新增並驗證 Financial Wisdom Forum 公開 Atom 後，現行分母為 14。
- Browser 59% 是 23/39 條在同一 job 兩次都成功；不是可抓率。兩個舊 Actions run 的首抓結果都是 28/39（71.8%）；現行單輪契約移除登入制 Rational Reminder 後，實測為 26/38（68.4%）。失敗分為 4 條 robots 明確拒絕與 8 條 anti-bot／CDN 403，不能以重試或偽裝灌高成功率。
- Public API 87.5% 是 14/16；兩個失敗端點中，Rational Reminder 是會員限制，Bluesky `searchPosts` 的官方 lexicon 明寫服務提供者可要求認證。兩者都不應留在 public API 分母，更正後是 14/14。

[GitHub Actions run 31231582994](https://github.com/AlanChen75/finance-crawler-poc/actions/runs/31231582994) 在 commit `df7cd01` 完成最終單輪驗收：公開 API 14/14（100%）；RSS 直連 8/14（57.1%），經固定 Cloudflare relay 恢復四條後為 12/14（85.7%）；Browser 26/38（68.4%）；整體公開路徑由直連 48/66（72.7%）提升至分層解析 52/66（78.8%）。47 個公開 `route_group` 中有 37 個至少一條路徑成功（78.7%）。Financial Wisdom Forum 的 Browser 在 GitHub 出口為 403，但 Atom 直連為 200，驗證了同社群多路徑降級；Bogleheads 與 Mr. Money Mustache 的 feed 在 GitHub 與 Cloudflare 出口都為 403，保留為真實邊界。

依據：[Bluesky `searchPosts` lexicon](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/searchPosts.json)、[Stack Exchange API 匿名限制](https://api.stackexchange.com/docs)、[Cloudflare Workers 錯誤索引](https://developers.cloudflare.com/workers/observability/errors/)、[Crawl4AI CrawlResult 重導欄位](https://docs.crawl4ai.com/api/crawl-result/)。

## GitHub Actions 驗收與成本上限

手動 workflow 的 `scope` 是固定 choice，只能選 `core` 或 `foreign_communities`，避免把任意路徑注入 runner。全面矩陣每個啟用來源：

- 最多 20 秒；
- 不自動重試；
- 來源間隔 1 秒；
- 預設一輪；手動要求 2–3 輪時只解讀為 burst repeatability；
- browser 一律檢查 robots.txt；
- 每輪都必須為 86 條來源產生結果，停用項目也必須明確記錄。

預設一輪應產生 86 筆結果，其中 63 筆最多會實際發出無憑證探測、7 筆為已驗證 robots 排除、16 筆為明確停用紀錄。理論最壞探測時間約 24.5 分鐘，再加環境安裝仍應落在 workflow 的 60 分鐘上限內；最終 run `31287504043` 的主探測步驟為 3 分 2 秒，Crawlee 處理組為 2 分 13 秒，整個 job 為 6 分 40 秒。

報表 schema v4 必須提供：`by_transport`、`by_kind`、`by_community_type`、`by_region`、`by_access_tier`、`direct_first_pass`、`resolved_first_pass`、`community_resolution` 與 `path_repeatability`。`success` 只代表該公開入口通過當時的 transport、最低長度與必要詞契約，不代表可完整回溯歷史、取得留言、合法再發布或內容可信。

## 2026-08-09 Browser 處理組更新

最終同批 URL 實驗已將 Crawl4AI、Crawlee、Cloudflare Browser Run 分開測量，並統一 7 條 robots 排除與 31 條技術分母。Run `31287504043` 中 Crawl4AI 為 23/31（74.19%）、Crawlee 為 25/31（80.65%）、當輪聯集 26/31（83.87%）；三輪穩定聯集為 25/31（80.65%）。完整方法、失敗來源、Cloudflare 1/7 pilot 與商業出口 A/B 驗收契約見 [`browser-treatment-experiment.md`](browser-treatment-experiment.md)。
