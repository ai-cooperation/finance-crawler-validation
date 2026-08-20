# P1 OpenBB-compatible market/topic alignment

更新日期：2026-08-20

本單元把議題雷達產出的 `topic-snapshot` 與 market raw items 組成可重跑、可溯源的市場快照與對齊結果。這是 OpenBB／TradingAgents 的輸入邊界，不宣稱已在此步驟執行 OpenBB 套件或產生投資結論。

## Scope

- market raw item 以既有 `kind=market_data`、JSON content 與 `item_id` 為輸入。
- `finance-openbb-align` 產出 `market-snapshot.json`、`market-topic-alignment.json` 與不含原文的 report。
- market snapshot 使用 OpenBB-facing provider-neutral shape；目前 live adapter 是 CoinGecko normalization，後續 OpenBB provider 只替換 adapter，不改 topic／agent contract。
- alignment 只描述市場涵蓋與 24 小時方向（positive／negative／mixed／not_covered），不把方向解讀成 bullish／bearish 或交易建議。
- 對齊每一列保留 topic evidence IDs 與 market source item IDs；不複製 raw content。

## Requirements and invariants

| ID | Requirement / invariant | Acceptance |
|---|---|---|
| REQ-1 | 每個 market instrument 必須有 symbol、asset type、currency、price、observed_at 與至少一個 source item ID。 | `market-snapshot.schema.json` validation passes; missing/invalid fields fail closed. |
| REQ-2 | 同一 symbol 的多筆 market evidence 必須 deterministic deduplicate，保留最新 observed_at 與全部 source item IDs。 | fixture truth-table checks sorted instruments, latest value, unique IDs. |
| REQ-3 | alignment 只能引用 topic snapshot 已有 topic 與 market source item IDs，不得創造未驗證 evidence ID。 | schema + evidence subset assertion in CI. |
| REQ-4 | 未覆蓋的 topic 必須明確標示 `not_covered`，不得以零值假裝市場中性。 | fixture with crypto-only market data yields not_covered for non-crypto topics. |
| REQ-5 | `coverage_ratio` 與 `partial` 必須揭露 topic snapshot partial 或市場未覆蓋。 | deterministic unit test and report assertion. |
| REQ-6 | 輸出可在相同輸入、provider、generated_at 下重跑得到相同 IDs 與 JSON values。 | CLI fixture rerun hash equality. |
| INV-1 | 不把市場方向描述成投資建議或因果結論。 | schema enum excludes bull/bear/recommend; code comment and review gate. |
| INV-2 | raw content 不進 alignment report。 | report shape test and artifact inspection. |
| DET-1 | 外部 provider／來源的可用性、freshness 與 coverage 必須在 production run report 觀測。 | GitHub Actions artifact + Worker status; CI fixture 不代替 live detector. |

## Test matrix（本表就是 TDD RED 清單）

| Requirement | Test type | Test / evidence | 落點 |
|---|---|---|---|
| REQ-1 | unit + invariant | malformed JSON、missing symbol、non-numeric price、schema validation | CI `tests/test_openbb_alignment.py` |
| REQ-2 | fixture truth-table | duplicate BTC/ETH rows and out-of-order inputs | CI `tests/test_openbb_alignment.py` |
| REQ-3 | invariant | alignment evidence IDs are union of declared topic/market IDs | CI `tests/test_openbb_alignment.py` |
| REQ-4 | fixture truth-table | crypto-only market input against digital assets, AI semiconductors, equities | CI `tests/test_openbb_alignment.py` |
| REQ-5 | unit | partial topic snapshot and uncovered topic produce ratio/flag | CI `tests/test_openbb_alignment.py` |
| REQ-6 | integration | CLI output IDs and hashes repeat for frozen fixture | CI `tests/test_openbb_alignment_cli.py` + local replay |
| INV-1 | invariant | enum/schema review rejects bull/bear/recommendation fields | CI schema contract + code review |
| INV-2 | invariant | report contains counts/IDs only; no raw `content` field | CI `tests/test_openbb_alignment_cli.py` |
| DET-1 | detector | live 15-source run report, Worker status and source failure list | production Actions/Worker; not claimed by unit tests |

## Current evidence and boundary

- Targeted Python tests for the new contract, CLI, contract registry and workflow guard: 21 passed.
- Frozen local radar run `run_20260818t145842z` produced 3 market instruments (BTC, ETH, USDT), 3 topics, and alignment coverage `1/3`; digital assets was `positive` at mean 24h change `0.366667%`, while AI／semiconductors and equities were correctly `not_covered`.
- The same 15-source flow was remotely published by Actions run [32330093877](https://github.com/ai-cooperation/finance-crawler-validation/actions/runs/32330093877): 14/15 sources, 26 items, 3 topics, Worker status HTTP 200. The run's public artifact intentionally contains source-health metadata only; full raw and processed evidence remain private by the existing boundary.
- The narrow OIDC route, D1/R2 index/object receipts and `0005_market_alignment.sql` are now implemented and locally verified. Cloudflare remote migration inspection still reports `0005_market_alignment.sql`, `0006_tradingagents_plans.sql` and `0007_research_reports.sql` as pending; until the reviewed deployment and a new Actions run read them back, this unit remains **locally verified, remotely not yet persisted for P1**.
