# P1 TradingAgents run-plan gate

更新日期：2026-08-20

這一層先產生可稽核的 agent run plan，不執行 TradingAgents、不使用付費模型，也不產生 bull／bear 結論。只有 plan 的 `decision=eligible` 且後續 budget gate 放行時，才可建立 `research-report` 第二意見。研究報告的私有 R2／D1 ingest 邊界已在本機完成 TDD，但尚未部署到驗證帳號。

## 啟動規則

- 輸入必須包含已驗證的 `topic-snapshot` 與同一 snapshot 的 `market-topic-alignment`。
- 預設候選為議題雷達前三名；每輪最多 3 個 topic。
- 使用者明確指定的 topic 可優先於排名，但仍受 `max_topics` 限制。
- divergence `magnitude >= 0.5` 且 direction 不是 `insufficient_data` 時，記錄 `reason=divergence`。
- 沒有 market alignment、沒有 topic 或 budget 為零時，plan fail closed 並保存 skip reason。
- 每個 topic 都保留 topic evidence IDs 與 market evidence IDs；plan 不包含 raw content。

## Budget boundary

`max_tokens`、`max_claims_per_topic`、`max_topics` 與 `max_usd` 都必須進入 plan。`model=tradingagents-deferred`、`max_usd=0` 只代表目前完成規劃與 evidence assembly，不代表已執行模型。真正執行前必須另設 provider／模型與費用批准。

真正產出的 `research-report` 必須同時引用 `plan_id`、`alignment_id`、`market_snapshot_id` 與根級 `evidence_ids`；bull／bear／risk claim 內的 evidence IDs 不能取代這些輸入關聯欄位。

## Test matrix

| ID | 驗證 | 落點 |
|---|---|---|
| REQ-1 | 沒有 market alignment 時全部 skip，保存 `missing_market_alignment` | `tests/test_tradingagents_plan.py` |
| REQ-2 | 預設最多前三名，輸出 deterministic topic decisions | `tests/test_tradingagents_plan.py` |
| REQ-3 | 使用者指定 topic 可優先，但不能突破 `max_topics` | `tests/test_tradingagents_plan.py` |
| REQ-4 | budget cap 造成 skip 時保存 `budget_cap`；全零 budget 保存 `no_budget` | `tests/test_tradingagents_plan.py` |
| REQ-5 | plan schema、alignment ID、evidence IDs 可驗證 | schema contract + CI |
| INV-1 | plan 不含 raw content、不產生投資結論 | schema／CLI report shape |
| INV-2 | research report 不得缺少 plan、market alignment、market snapshot 與 root evidence links | `tests/test_contracts.py` |
| DET-1 | 真實 Actions run 的 plan、provider budget 與後續 report 必須可在私有目的端查回 | production Worker/D1/R2；CI 不代替 |

## Current evidence

- Python contract／plan／CLI／workflow guard 測試已通過。
- 冷凍雷達 run `run_20260818t145842z`：3 topics、market alignment coverage `1/3`；plan `plan_20260818t150000z` 判定 `eligible`，3 topics 可規劃，model 為 `tradingagents-deferred`、`max_usd=0`。
- 這是 agent 執行前 gate，不是 TradingAgents 模型成功率或投資績效證據。
- `/v1/ingest/research-report` 的本機測試覆蓋：私有 R2 物件、D1 索引、`tradingagents_completed` audit、冪等 replay、未選議題、過期時間、外部 evidence 與未標記 second opinion 均 fail closed；目前 5 個 Worker test files、73 tests 通過，branch coverage 80%。這些測試使用 synthetic fixture，不代表模型已執行或已產生真實投資意見。
- Actions run `32333213987` 已遠端寫入 `plan_32333213987`，D1 顯示 `decision=eligible`、`topic_count=3`，R2 `plans/plan_32333213987.json` hash `dd22e56769175b9f8eda1d51c70d0a7ec37c2fe01fd169a819a18564d3cbd954` 與 audit 一致；本輪沒有呼叫模型，因此 `research_reports=0` 是預期的 budget gate 結果。
