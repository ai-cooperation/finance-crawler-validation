# P2 120 品牌 observation 契約

更新日期：2026-08-13

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
- 報表只保存 metadata、SHA-256、final URL、content type 與 500 字元 preview，不保存完整正文。
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
