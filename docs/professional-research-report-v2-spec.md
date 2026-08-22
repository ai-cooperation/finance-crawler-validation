# Professional Research Report v2 SDD

## 目的

把目前只整理新聞證據的 `research_report.v1` 升級為可稽核的標的研究報告。報告仍然不是自動下單指令；它必須把可觀測資料、計算結果、來源衝突與模型推理分開，讓讀者知道哪一段是事實、哪一段是計算、哪一段是情境假設。

## 範圍

### 這一版要做

1. 目標標的的市場快照與歷史時間序列比較。
2. 基本面與估值資料的明確狀態：`available`、`insufficient_data`、`not_applicable`。
3. 基於明確假設的 base／bull／bear 情境；情境不是預測，也不產生 broker order。
4. 跨來源的支持／反駁／未知與來源衝突矩陣。
5. 高階模型只讀取 Research Pack 的結構化資料與 evidence appendix，輸出每個可驗證句子的 evidence IDs。
6. 報告品質閘門：資料缺口、partial、stale、來源衝突、模型重播與模型實際生成必須分開標示。

### 這一版不做

- 不自動交易、不產生下單 payload。
- 不把歷史波動區間冒充目標價或保證報酬。
- 沒有基本面來源時，不以零或模型常識補值。
- 沒有可反查 evidence 的句子不得進入結論區。

## 交接契約

```text
Research Requirement
  -> source bundle / market-depth request
  -> market_snapshot + financial_depth + topic_snapshot
  -> source_conflict_report + scenario_analysis
  -> Research Pack v2
  -> high-level model second opinion
  -> professional research report + evidence appendix + audit receipt
```

### `financial_depth`

- `time_series`: 價格點、觀察時間、視窗、報酬、年化波動、最大回撤、provider response hash（測試 fixture 若沒有原始 response，只能標成 `normalized_points`）。
- `fundamentals`: revenue／earnings／margin／cash／debt／guidance；缺值保留 `null` 與缺口原因。
- `valuation`: method、period、peer set、assumptions、輸入 evidence IDs；不適用時必須是 `not_applicable`。
- `scenarios`: base／bull／bear、horizon、assumptions、mechanical output、`not_a_forecast=true`。

### `source_conflict_report`

- 每一個 topic／target 都列出 source group、stance（positive／negative／neutral／unknown）、樣本數、獨立來源數、conflict level。
- 樣本不足、來源集中或疑似重複轉載時，只能輸出 `unknown`／`insufficient_data`。
- 結論必須指向 evidence IDs；不能把來源數量直接當成市場共識。

## 發布閘門

1. target identity、as-of、currency、timezone 與 provider response hash 齊全。
2. 時間序列至少有兩個有效觀測點才能計算報酬；不足則 `insufficient_data`。
3. 估值輸入缺失時，狀態只能是 `insufficient_data` 或 `not_applicable`。
4. 每一個 model claim 的 evidence IDs 都存在於同一 Pack appendix。
5. partial／stale／來源衝突未解決時，`recommendation_status` 不得升級為投資建議。
6. 模型 timeout／fallback／replay 要記錄實際模型，不得宣稱高階模型已重新推理。

## Test matrix

| REQ | 驗證方式 | 測試類型 | 落點 |
|---|---|---|---|
| PR-1 歷史點依時間排序且不重複 | unsorted／duplicate fixture | unit + invariant | CI |
| PR-2 少於兩點不得計算報酬 | one-point fixture returns `insufficient_data` | unit | CI |
| PR-3 報酬、波動、回撤使用同一視窗 | known three-point fixture truth table | fixture truth-table | CI |
| PR-4 估值缺欄不補零 | missing fundamentals fixture | unit + invariant | CI |
| PR-5 情境輸出標記非預測並保留假設 | scenario fixture | unit | CI |
| PR-6 來源衝突須保留支持／反駁／未知 | mixed stance fixture | unit + invariant | CI |
| PR-7 模型不可取得時保留 last-good 與失敗原因 | timeout/fallback fixture | integration | CI + production audit |
| PR-8 claim evidence 必須存在於 appendix | dangling evidence fixture | contract | CI |
| PR-9 Research Pack v2 可由既有 v1 replay | v1 pack round-trip | compatibility | CI |
| PR-10 provider／時間序列在真實 Actions 中可回讀 | full-catalog run + MCP read-back | detector | production + TG |

## 可接受狀態

- `professional_ready`: depth、衝突矩陣、market confirmation providers 與 evidence graph 通過閘門。
- `professional_partial`: 有可用資料但仍有缺口，報告可以讀取但必須標示限制。
- `research_only`: 只有新聞／市場快照，沒有足夠時間序列或基本面。
- `blocked`: target identity、provider 或 evidence path 無法驗證。

`lexical_stance_v1` 仍只能作為舊快照的篩選器，不能通過 ready gate。新版本 `lexical_stance_v2_calibrated` 提交 frozen calibration set、classifier version、precision／recall 與 calibration-set hash；只有校準狀態為 `calibrated`、至少兩個獨立來源、時間序列可用、且 volume／ETF flows／derivatives／on-chain provider 均有真實 response hash 時，才可標成 `professional_ready`。因果歸因仍必須在報告中標為 observed association 或 unresolved，不得把新聞標題直接寫成因果結論。
