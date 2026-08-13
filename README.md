# Finance Crawler Capability Probe

這個 repository 用可重現的 GitHub Actions 實驗，回答四個問題：

1. Crawl4AI 在 GitHub-hosted Ubuntu runner 能否正常啟動 Chromium？
2. 財經來源的 browser、JSON API、RSS 三條路徑，直連與合規備援後各自能成功到哪裡？
3. 社群、新聞、官方資料、市場資料與聚合器的能力邊界是否不同？
4. 失敗能否被正確分為封鎖、需認證、限流、TLS、逾時、robots 或內容不足，而不是誤報成功？

## 2026-08-10 雲端實接驗證

- GitHub public repo：[`ai-cooperation/finance-crawler-validation`](https://github.com/ai-cooperation/finance-crawler-validation)
- 單次手動 Actions：[run 31369726174](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31369726174)，耗時 1 分 52 秒，沒有建立 schedule 或重複 run。
- 15 個唯一來源中 14 個成功：RSS 5/5、Public API 7/7、Browser 2/3；Bogleheads 仍被 Cloudflare challenge 阻擋。
- GitHub OIDC 已成功通過 Ingest Worker 寫入 38 筆 raw items，並發布 1 個含 3 個議題的 partial current snapshot。
- D1：`finance-crawler-validation-core`，database ID `476bd84f-e924-4b9b-a9d9-dfca9ea29a1a`。
- Private R2：`finance-crawler-validation-raw`；已直接讀回 topic object 與 raw object，topic SHA-256 與 D1 索引一致。
- Ingest Worker：`finance-crawler-validation-ingest`，只接受固定 repository、owner、workflow、`main` ref、event、run ID 與 commit SHA 的 GitHub OIDC。

這次證明手動垂直切片可行，不代表長期穩定性驗收已完成。checkpoint catch-up、D1 原子 admission、遠端重放／last-good、staleness watchdog、外部 webhook transport 與去重已實作與遠端注入驗證；schedule-only soak observation、私有每日用量 collector 與七日 fail-closed 驗收器已完成 TDD，仍待 Analytics 唯讀 token、真人 primary/fallback 收件與七日實跑。正式排程保持關閉。操作契約見 [`docs/p0-alert-and-soak-runbook.md`](docs/p0-alert-and-soak-runbook.md)。

## 驗收契約

- 每個啟用來源必須產生一筆結果，不可靜默遺失。
- HTTP 200 不等於成功；內容長度及必要關鍵字也必須通過。
- 同一 job 重複 1–3 次只表示「短時間重抓耐受度」，不稱為長期穩定性；預設只抓一次。
- 報表必須分開 `direct_first_pass` 與 `resolved_first_pass`，不得把直連阻擋與備援救回混為同一百分比。
- 同一社群的 Browser、API、RSS 以 `route_group` 合併，另外報告最終可取得率。
- 結果只保存 metadata、SHA-256 與最多 500 字元預覽，不鏡像完整受版權保護內容。
- browser 路徑必須啟用 robots.txt 檢查。
- workflow 目前只支援手動觸發；接上通知以前不建立排程。
- SEC EDGAR 需要帶姓名與聯絡 email 的 User-Agent，未配置前保持停用。

## 來源矩陣

新版新聞架構把來源單位改為唯一品牌／機構，`news-sources.yaml` 已固定 120 家（100 家財經專業媒體、20 家綜合媒體財經部門）與四類 162 個 endpoints；RSS、API、靜態 HTML、Browser 只算同品牌的取得路徑。平台依每個工作的能力、時間、回應大小、成本、配額及憑證即時選擇；GitHub Actions 手動 scope `news_120` 必須指定 bounded `brand_ids`，並產生品牌級 `news-report.json`。契約與遷移狀態見 [`docs/resource-aware-news-architecture.md`](docs/resource-aware-news-architecture.md) 與 [`docs/p2-news-observation-contract.md`](docs/p2-news-observation-contract.md)，executor policy 見 `resource-executors.yaml`。

資料保存、Crawl4AI＋OpenBB＋TradingAgents 背景分析、Cloudflare 權限分離、MCP 與 GitHub Actions 失效恢復的後續工作，統一追蹤於 [`docs/cf-gh-mcp-implementation-plan.md`](docs/cf-gh-mcp-implementation-plan.md)。

第一個議題雷達垂直切片定義在 `radar-sources.yaml`：15 個唯一來源分為 RSS 5、Public API 7、Browser 3。2026-08-10 本機實測 14/15 成功、正規化 38 筆資料並產出三個可溯源議題；Bogleheads Browser 路徑被 Cloudflare JS challenge／HTTP 403 阻擋，因此 snapshot 正確標為 partial。這次沒有觸發 GitHub Actions，也沒有部署 Cloudflare 遠端資源。

第一個嚴格單輪實測是 [Actions run 31309377786](https://github.com/AlanChen75/finance-crawler-poc/actions/runs/31309377786)：99/120 個品牌成功（82.5%），其中財經專業媒體 79/100、綜合媒體財經部門 20/20。這一輪只有 GitHub Actions executor 可用；結果不可外推為加入 Cloudflare 或商業出口後的成功率。

隔離帳號 baseline 是 [Actions run 31677822771](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/31677822771)：120/120 品牌都有紀錄，101/120 成功（84.17%）。後續只以 explicit brand batch 重驗失敗或 endpoint 變更者，再按 brand ID 合併 observation；batch 內成功率不得冒充 120 品牌整體成功率。

P2 擴大驗收已由 baseline 與三個 bounded batches 合併完成：120 個唯一品牌中 114 個成功（95.00%），達成契約上緣。實驗 run、artifact 雜湊、合併規則及剩餘 6 個邊界見 [`experiments/news-120/p2-acceptance-20260813.json`](experiments/news-120/p2-acceptance-20260813.json)。

來源定義在 `sources.yaml`，目前涵蓋台灣與國際社群、開發者社群、新聞、RSS、官方資料 API、市場資料 API，以及 Crawl4AI 財經範例網站。每個來源都聲明 topic、kind、transport、最低內容門檻、必要詞、來源脈絡與選源證據。

熱門社群的選擇依據、能力假設與合規邊界見 [`docs/source-selection.md`](docs/source-selection.md)。實際可用性以 GitHub Actions 產出的 `report.json` 為準，不以本文件或單次本機請求推定。

國外社群的全面矩陣獨立放在 [`foreign-community-sources.yaml`](foreign-community-sources.yaml)，避免官方資料探測與社群平台邊界互相稀釋。它包含可匿名實跑路徑及需要 OAuth、API key、會員或商業授權的 catalog-only 路徑；範圍定義與分層策略見 [`docs/foreign-community-landscape.md`](docs/foreign-community-landscape.md)，同批 Browser URL 的 Crawl4AI、Crawlee、Cloudflare Browser Run 實測與商業 unlocker A/B 契約見 [`docs/browser-treatment-experiment.md`](docs/browser-treatment-experiment.md)。GitHub Actions 手動觸發時可選 `core` 或 `foreign_communities` scope。

`worker/` 是限定十個 feed ID 的 Cloudflare RSS relay：只在 GitHub 直連遇到 403、429 或 5xx 時啟用，不接受任意目標 URL，也不追隨上游重導。工作流程透過 repository variable `CF_RELAY_BASE_URL` 注入部署 URL；未設定時仍可重現純 GitHub 直連邊界。

## 本機開發

```bash
python -m pip install -e '.[test]'
python -m playwright install chromium
pytest --cov --cov-report=term-missing
finance-topic-radar --manifest radar-sources.yaml --output /tmp/finance-topic-radar
finance-crawler-probe --manifest sources.yaml --output artifacts --repeat 2
CF_RELAY_BASE_URL=https://your-worker.workers.dev \
  finance-crawler-probe --manifest foreign-community-sources.yaml --output artifacts --repeat 1
```

本專案將 Crawl4AI 明確列為核心依賴並保留上游歸屬；Crawl4AI 專案採 Apache 2.0 加額外 attribution 條款，公開使用時應依其 LICENSE 要求標示。
