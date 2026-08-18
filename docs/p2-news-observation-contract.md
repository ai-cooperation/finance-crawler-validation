# P2 120 品牌 observation 契約

更新日期：2026-08-14

## 問題與成功標準

P2 驗證 Browser＋API＋RSS 分層策略能否在隔離帳號下，對 120 個不重複財經新聞品牌產生品牌級、可追溯、可續跑的 observation。Endpoint 是取得路徑，不增加品牌分母。

- Catalog 固定為 120 個唯一品牌；品牌 ID 與 canonical domain 均不可重複。
- 每個完整 baseline 必須記錄 120/120 個品牌，不允許靜默缺列。
- 同一 observation 中，任一合規 endpoint 通過內容契約即算該品牌成功，分子只加一。
- 90% 門檻為 108/120；95% 門檻為 114/120。
- 子集 batch 只報本批成功數與 target=120，不得將子集成功率寫成整體成功率。
- 後續 batch 只更新本批品牌的 latest observation；未重驗品牌保留 baseline 結果。
- 合併器必須校驗 baseline 與 catalog 品牌 ID 完全一致，batch result ID 必須與其 explicit selection 完全一致。
- `robots_denied`、`auth_required`、`paywall` 是合規終點，不以 Browser、換 IP 或 commercial unlocker 規避。
- 某個 Browser URL 被 robots 禁止不會啟動該 URL 的其他 executor；但同品牌主動公開、可獨立識別的 API／RSS（包含財經 podcast RSS）可作為不同發佈介面，且 endpoint ID 必須明確標示類型。
- 商業 proxy／residential egress／web unlocker 未授權，不列入候選 executor 或推估成功率。

## 分批與額度契約

`news_120` workflow 只接受明確的 comma-separated `brand_ids`，並以 `max_brands` 作硬 admission cap。預設上限 30；只有有意的完整 baseline 才可明確設定 120。GitHub Actions schedule 保持關閉。

Public repository 的標準 GitHub-hosted runner 分鐘依 GitHub 官方文件不計費；本專案仍限制 batch、artifact retention 與重驗範圍，以降低儲存、cache、外站流量與誤操作。Cloudflare 免費層的 Worker request、D1 rows written 與 R2 operations 另設獨立上限，不因 GitHub runner 免費而放寬。

## 內容與 transport 契約

- HTTP 200 不是成功；仍需最低 300 字元與至少一個財經語意詞。
- JSON API 必須可解析 JSON。
- RSS 必須可解析 XML，root local name 只能是 `rss`、`feed` 或 `rdf`。Feed URL 若重導向 HTML，必須記為 `invalid_content`。
- Static HTML 只計可見文字，排除 script、style、template 與 noscript。
- Browser 使用 Crawl4AI markdown，並執行 robots preflight 與 Crawl4AI robots check。
- 預設 capability report 只保存 metadata、SHA-256、final URL、content type 與 500 字元 preview，不保存完整正文。
- 需要驗證「真實內容已抓到」時，`news_120` workflow 以 `capture_raw=true` 啟用 raw capture：每個 endpoint attempt 的實際 response body 以 `news-raw/<brand>/<endpoint>.raw` 保存，並由 `news-raw-manifest.json` 記錄 URL、transport、HTTP、bytes、SHA-256、outcome 與 hash match。raw capture 是 GitHub Actions artifact，不寫入 Git history；失敗 response 若有 body 也保存，timeout／連線失敗則只留 manifest evidence。
- relay 成功的 endpoint 必須同時保存 GitHub 直連與 Cloudflare relay 兩段 delivery attempts，不得只保存最終 200。

## 邊界條件

1. 未知 brand ID 拒絕執行。
2. 同一 batch 重複 brand ID 拒絕執行。
3. batch 大於 `max_brands` 拒絕執行。
4. 空的 brand ID input 拒絕執行。
5. 子集結果的 `target_brands` 仍為 120。
6. RSS URL 回 HTML 時不得成功。
7. XML malformed 時不得成功。
8. Atom namespace 的 `feed` root 可接受。
9. RDF namespace 的 `RDF` root 可接受。
10. 同品牌 fallback 成功只計一個品牌成功。
11. 過去 observation 的 endpoint 數不得被新版 catalog 回溯改寫。
12. Robots 終點不得進入 executor fallback。
13. 未配置商業憑證時 residential／unlocker 維持 blocked。
14. workflow failure 仍需上傳已產生的診斷 artifact。

## Given／When／Then

- Given 120 品牌 baseline，When 完成 probe，Then report 恰有 120 個唯一 brand IDs。
- Given 失敗品牌清單，When 指定 bounded brand batch，Then 只發出該批品牌的 endpoint requests。
- Given 子批次成功，When 合併 baseline 101/120，Then 必須按被替換的品牌結果計算，不得直接相加重複品牌。
- Given RSS endpoint 回 HTTP 200 HTML，When adapter 驗證格式，Then 回 `invalid_content`。
- Given explicit robots disallow，When route planner 評估 fallback，Then停止且不啟動 Browser 或商業 executor。

## 實驗證據版本

- 舊帳號 baseline `31309377786`：99/120，只作失敗分群起點。
- 隔離帳號 baseline `31677822771`：101/120、120/120 有結果、141 endpoint attempts；artifact `news-report.json` SHA-256 為 `077f95616657fb1c9d3f38c9a3b07cbf57ab7dcc2fa3830d1904388977679cf4`。
- RSS fallback batch `31679578795`：12 個 baseline 失敗品牌中 9 個成功；artifact `news-report.json` SHA-256 為 `42198ebba221070dad28898f52bf1024cc438785ac9df17e426684b63be8559c`。按品牌 ID 合併後為 110/120（91.67%），不是將 9 直接加到任意其他分母。
- Cloudflare relay staging version `7d9fc09e-710d-44b9-8393-909017b8a75d`：Private Banker International 與 AdvisorHub 的官方 RSS 實際回 200、XML root=`rss` 且通過財經語意檢查；Benzinga 實際為 timeout／upstream 500 截斷 XML，不列成功。
- Cloudflare relay 只能實際證明該出口可讀；仍必須由 `ai-cooperation` GitHub runner 完成「直連失敗 → relay 成功」的小批次證據。
- Relay batch `31680725396`：Private Banker International 與 AdvisorHub 在 GitHub runner 上都完整記錄 `direct 403 → cloudflare_relay 200`；Benzinga 是 `direct 403 → relay timeout`，不列成功。artifact JSON SHA-256 為 `f28b8a0903e7c327845c6e6fb8308bc3e8491b2c1273d40d097cdc44f1cc3d04`。
- 第一方財經 podcast RSS batch `31681280554`：Reuters Morning Bid 與 Barron's Streetwise 皆成功，同時保留 Reuters／Barron's 網頁 Browser robots 禁止的獨立邊界。artifact JSON SHA-256 為 `962e7ae35692df6d65d2e1d4123afaa5806a46a3881c347e6249e8075fbcf221`。
- 以程式重新下載四個 artifact 並按 brand ID 合併後，結果為 120 個唯一品牌、114 成功、6 失敗、95.00%；已達 P2 95% 驗收門檻。
- 2026-08-14 端點修路前的公開 probe：Private Banker International 的正式新聞 feed 改為 `/news/feed/`、AdvisorHub 改為 `/feed/`；前者在同一台本機先回 200 後又回 403，因此仍保留為 relay 候選，不把單次 200 外推為成功。Citywire 的官方 Advice Show 訂閱頁、Livewire Markets 的官方 podcast 索引、Sifted Startup Europe 的官方訂閱頁均明示其活躍 RSS；三個外部 feed 均實際通過 XML、300 字與財經語意契約。先以未設定 relay 的本機完整 120 品牌 run 得 114/120，再只替換六個失敗品牌的 bounded batch，其中 Sifted 成功、其他五個仍失敗；按品牌 ID 合併為 115/120（95.83%）。機器摘要在 `local-20260814t031220z-summary.json` 與 `local-20260814t032008z-sifted-repair-summary.json`，兩者都明確標為非 acceptance：待 `ai-cooperation` GitHub runner 重驗，未回填 2026-08-13 的 acceptance 數字。
- 新版正式 baseline `31768400476`：GitHub-hosted runner 在 163 endpoint catalog 對 120 個唯一品牌產生完整 result，112 成功、8 失敗；artifact SHA-256 為 `b3778895c1d61ab230c2c2d9f4520408c519540dc10fef181b37d5c2653b45d1`。這一輪揭露四個先前 HTML-only 來源可轉用公開 RSS：International Banker、LeapRate、BusinessLine Markets、Bankless Daily。
- 官方 RSS recovery batch `31768848318`：只選取前述四個品牌，四個都成功，artifact SHA-256 為 `07b991abf2df401d11f9d2fb78dfd74fa6ae24c94c06319dc98da00a557a17bf`。用合併器按 brand ID 覆寫後為 120 個唯一品牌、116 成功、4 失敗、96.67%；詳見 `p2-acceptance-20260814.json`。四個失敗為 Benzinga、Financial Advisor Magazine、ETF Stream（anti-bot／Cloudflare challenge）與 Financial Express（GitHub runner 403，官方 syndication RSS 候選 redirect 到 HTML 或 410）；未使用 commercial proxy、residential egress 或 web unlocker。
- Raw content acceptance run `32112055769`（驗證 branch `agent/p2-news-catalog-revalidation`）：GitHub-hosted runner 實際抓取 120/120 品牌、144 endpoint attempts，113 品牌通過內容契約（94.17%）；raw capture 產生 139 個 payload、8,225,522 bytes，5 個 endpoint 無 body（timeout／連線或空回應），139/139 payload hash 與 probe 回報一致，hash mismatch=0。`news-report.json` SHA-256=`7a88fbe70b047f8b2c733cd5d5cb236aabacefe2c223c8f4d7d5fd7031b1802e`，`news-raw-manifest.json` SHA-256=`725470265bf22d7388aa8cb2c13d8ef37cc427cfc32ebb111e6efbfdcda0bf74`；artifact ID `9315334853`。本輪與 8 月 14 日的 116/120 不同，是同一來源目錄在不同時間的即時 observation，不合併成單一成功率。
- Raw recovery runs `32116022731`、`32116635535`：只選取四個未通過品牌，最新一輪 3/4 成功，5 個 endpoint payload 全部保存且 hash mismatch=0；Financial Advisor Magazine 與 Financial Express 均為 GitHub 直連 403 後由固定 Cloudflare relay 取得 200，The TRADE 直連取得 200。ETF Stream 的直連與 relay 都是 403 Cloudflare JS challenge，Crawl4AI 也未能通過；因此 raw-content 合併 observation 為 119/120，並非宣稱 120/120。
