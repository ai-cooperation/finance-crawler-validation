# Browser 引擎、Cloudflare 與商業 unlocker 實驗報告

實驗日期：2026-08-09

## 結論

在同一批 38 條 Browser URL 上，7 條因已驗證的 `robots.txt` 禁止而只記錄、不發出 Browser 請求；可比較的技術分母為 31 條。最終 [GitHub Actions run 31287504043](https://github.com/AlanChen75/finance-crawler-poc/actions/runs/31287504043) 中：

| 處理組 | 成功 | 31 條可測 URL 成功率 | 38 條治理分母取得率 |
|---|---:|---:|---:|
| Crawl4AI，GitHub 資料中心出口 | 23 | 74.19% | 60.53% |
| Crawlee PlaywrightCrawler，同一 GitHub 出口 | 25 | 80.65% | 65.79% |
| 當輪兩引擎聯集 | 26 | 83.87% | 68.42% |

Crawlee 本輪比 Crawl4AI 多 2 條，但三輪中單引擎成功數分別介於 23–24 與 24–25，差異不足以支持「換 library 就能穩定達到 90%」。兩引擎在每一輪都至少一個成功的穩定聯集是 25/31（80.65%）。

Cloudflare Browser Run 針對 8 條當時的 Browser 難例做 pilot；Quant StackExchange 依 robots 排除後，只救回 Investing.com 1/7（14.29%）。Cloudflare Browser Run 可以執行 JavaScript，但不是 residential proxy 或 web unlocker，不能用來合理推估 90–95%。

Firecrawl Hosted 與 Browserless Residential 尚未執行：GitHub repository 與當前執行環境均沒有對應 API token。因此目前沒有商業出口成功率，也不把廠商行銷說法當成實驗結果。

## 同批 URL 與同一成功契約

固定 cohort 是 `foreign-community-sources.yaml` 中 38 條已啟用 Browser 入口。兩處理組同在 `ubuntu-latest` job 中順序執行，各 URL 只有一次 client attempt、並行度 1、無 proxy、無 session rotation。Crawlee 不會對失敗請求重試；Crawl4AI manifest 同樣設 `retries: 0`。

成功不只是 HTTP 200，還必須同時通過：

- 無 401、403、429 或其他 HTTP error；
- 無 Cloudflare、DataDome、CAPTCHA、`Just a moment` 等 block marker；
- 無登入重導；
- 內容長度達到該來源的 `min_content_chars`；
- 內容或標題包含該來源的必要關鍵詞。

完整頁面不落盤；只保存長度、SHA-256、最多 500 字預覽、狀態與錯誤類別。

## robots 分母更正

前兩輪曾出現 Crawl4AI 在 GitHub runner 漏掉 StackExchange 非標準 HTTP 418 robots 回應、Crawlee 卻正確排除的不一致。最終契約將 2026-08-09 驗證的 7 條禁止與證據 URL 版本化在 manifest，兩引擎都在啟動 Chromium 前排除：

- Reddit HTML
- Quant、Money、Bitcoin StackExchange HTML
- Value Investors Club
- X Finance Search Web
- Quora Investing

這 7 條仍屬於 38 條治理 cohort，但不屬於 31 條技術成功率分母。如把 robots 禁止當成技術失敗，任何合規 Browser 處理組的上限只有 31/38（81.58%），所以不可能在 38 條治理分母上聲稱 90–95%。

## 三輪實測與波動

| Actions run | Crawl4AI | Crawlee | 當輪聯集 |
|---|---:|---:|---:|
| [31286395957](https://github.com/AlanChen75/finance-crawler-poc/actions/runs/31286395957) | 23/31 | 24/31 | 25/31 |
| [31286721255](https://github.com/AlanChen75/finance-crawler-poc/actions/runs/31286721255) | 24/31 | 24/31 | 27/31 |
| [31287504043](https://github.com/AlanChen75/finance-crawler-poc/actions/runs/31287504043) | 23/31 | 25/31 | 26/31 |

第一輪 Crawlee 原始報表曾把只出現在 HTML `<title>` 的必要詞誤判為缺少；表中依後續固定的「標題＋body」同一契約校正，不改變原始回應證據。同樣，前兩輪 Crawl4AI 數字已在統一排除 7 條 robots 後重算。

三輪中：

- 穩定 Crawl4AI 成功：23/31（74.19%）。
- 穩定 Crawlee 成功：23/31（74.19%）。
- 每輪都至少有一個引擎成功：25/31（80.65%）。
- 三輪都同時失敗：ADVFN、Bogleheads、Mr. Money Mustache、White Coat Investor，共4/31。
- eToro 只在中間一輪被聯集救回；HotCopper 在後兩輪被 Crawlee 救回，因此不算穩定。

最終一輪的差集為：Crawl4AI-only 是 CoinMarketCap Community；Crawlee-only 是 Financial Wisdom、HotCopper、Investing.com。這證明兩 library 有引擎互補，但未解決共同的出口與 anti-bot 邊界。

## Browser＋API＋RSS 分層結果

最終單輪中，Crawl4AI 基準系統的公開路徑是：Browser 23/38，API 14/14，RSS 直連 8/14，固定 Cloudflare feed relay 救回 4 條後為 12/14；合計 49/66（74.24%）。排除 7 條不應發請求的 Browser URL 後，合規技術分母為 49/59（83.05%）。

若在 Browser 層將本輪 Crawlee 當合法 fallback，Browser 聯集是 26/31，全系統為 52/66（78.79%），或合規技術分母 52/59（88.14%）；47 個公開 `route_group` 中有 38 個至少一條合法路徑可取得（80.85%）。

API 與 RSS 成功率之所以顯著高於 Browser，是因為分層系統把官方機器介面放在優先順序，並在同社群入口間做 fallback；不是降低成功判定標準。

## Cloudflare 能做與不能做的事

本專案用了兩種 Cloudflare 能力：

1. Workers feed relay：只允許 7 個固定 RSS route，成功救回 4 條 GitHub 直連失敗 feed。這是可控制的合法備援出口，不是通用 proxy。
2. Browser Run Quick Action `markdown`：對 8 條當時難例使用 `networkidle2` 與額外 5 秒等待，1 條 robots 排除、7 條實際執行，只救回 1 條。六條失敗中，五條是 `Just a moment`，一條是 403；Bogleheads 額外延長到 15 秒仍是 challenge。

Cloudflare [Automatic request headers](https://developers.cloudflare.com/browser-run/reference/automatic-request-headers/) 文件明確說明 Browser Run 請求始終可被識別，自訂 User-Agent 也不會繞過 bot protection。因此「開啟 Cloudflare 商業出口」不是這個產品的正確描述；Cloudflare Browser Run 不等於 Browserless Residential 或 Firecrawl Enhanced。實測證據保存在 `experiments/cloudflare-browser-run/results/networkidle2-wait5s-run1.json`。

## 類似專案的社群反饋能推論什麼

公開 issue 沒有可比分母，只能用來建立失敗機制假說：

- Crawl4AI [issue #225](https://github.com/unclecode/crawl4ai/issues/225) 記錄 Cloudflare block，維護者建議使用已有合法使用者狀態的 managed browser，並建議對不希望被爬的網站放棄。
- Crawlee [issue #3506](https://github.com/apify/crawlee/issues/3506) 把「Playwright 在資料中心 IP 仍快速被 Cloudflare 擋」當成需要 residential-browser fallback 的背景。
- Firecrawl [issue #1129](https://github.com/firecrawl/firecrawl/issues/1129) 有自建版使用者回報單一 server IP 在重複爬取後出現 403；[issue #2257](https://github.com/firecrawl/firecrawl/issues/2257) 則有用戶回報同一 host 上 self-hosted Firecrawl 失敗、Browserless 成功。

這些反饋共同指向「出口 IP、browser fingerprint、session 與站方政策」比 library 名稱更關鍵；但 issue 有選擇偏差、版本與目標網站不同，不可從中計算任何廠商的成功率。本專案的 74.19%、80.65% 與 83.87% 才是同 URL、同時段、同成功契約下可比較的數字。

## 下一個 90–95% 商業出口 A/B

供應商文件顯示，Firecrawl Hosted `/v2/scrape` 支援 `proxy: basic|enhanced|auto`，`auto` 會在 basic 失敗時於供應商內部升級 enhanced；[Enhanced Mode](https://docs.firecrawl.dev/features/enhanced-mode) 列的請求成本是 basic 1 credit、enhanced 5 credits。Browserless [`/unblock`](https://docs.browserless.io/rest-apis/unblock) 可搭配 `proxy=residential`；[proxy 文件](https://docs.browserless.io/rest-apis/proxies) 與 [unit consumption](https://docs.browserless.io/overview/unit-consumption) 列出 residential 6 units/MB，browser time 每 30 秒 1 unit，成功的 CAPTCHA solve 每次 10 units。

取得 `FIRECRAWL_API_KEY` 與 `BROWSERLESS_API_TOKEN` 後，實驗應直接用同一 GitHub Actions job 執行：

| Arm | 固定設定 |
|---|---|
| Crawl4AI | GitHub runner，無 proxy，0 retry |
| Crawlee | GitHub runner，無 proxy，0 retry，0 session rotation |
| Firecrawl Hosted | 每 URL 一次 `/v2/scrape`，`proxy:auto`，`formats:[markdown]`，`storeInCache:false`；供應商內部 basic→enhanced 視為此 arm 的產品行為 |
| Browserless Residential | 每 URL 一次 `/unblock?proxy=residential`，只回傳 content，固定 timeout |

執行契約：

1. cohort 仍固定 38 條，7 條 robots 在四個 arm 都只產生排除記錄，不發送供應商請求；每 arm 實際分母都是同一 31 條。
2. 三個獨立時段各跑一輪，共 `31 × 4 × 3 = 372` 筆 provider-URL observation。
3. 每輪使用 Latin-square 更換 arm 順序，每 arm 並行度 1，避免固定先後順序與短期限流混淆。
4. 四 arm 共用本報告的 status、block marker、登入重導、必要詞與最低長度契約；同時記錄 elapsed time、response bytes、provider credits/units 與錯誤類別。
5. 主結果同時報單輪成功率、3/3 穩定成功率、四 arm 聯集、成本/成功 URL；同 URL 配對差異用 exact McNemar test，不用各家不同來源的行銷數字互比。

對當前穩定聯集 25/31，再穩定救回 3 條會到 28/31（90.32%），救回 4 條會到 29/31（93.55%），救回 5 條會到 30/31（96.77%）。由於 31 的離散分母沒有剛好 95%，若驗收條件是「至少 95%」，必須是 30/31，不是 29/31。

這個實驗能證明固定 31 條可測 cohort 是否達標；若要對更大網路母體聲稱 90–95%，還必須擴張 domain 數、時間切片與地區出口。
