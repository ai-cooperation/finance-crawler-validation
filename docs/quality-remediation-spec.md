# 六項品質修正規格（QFIX）

本規格把來源可達性、標的相關性與研究需求覆蓋分成三個不同維度。任何一個維度失敗，都必須保留可稽核狀態，不得以來源數量或模型文字升級 L3。

## Requirements

| ID | Requirement | 驗收條件 |
|---|---|---|
| QFIX-01 | 健康 transport 與空增量視窗分離 | HTTP 2xx／可解析 payload 但 `empty_window` 時 checkpoint 為 `success`，source result 保留 `content_status=empty_window`，不得進 `failed_sources` |
| QFIX-02 | Python／Worker 標的 matcher 同版 | 兩端輸出 `matcher_version=target_identity_v3`；只使用 symbol／name／aliases／受控 asset 詞，`global`、sector、industry 等路由 metadata 不得成為 identity |
| QFIX-03 | 需求缺口驅動回補 | 需求未 complete 時才建立 gap；依 requirement 的 missing metrics、roles、geography 排路徑；已嘗試 route 不得重跑，沒有合法 route 時保留 provider handoff／阻擋原因 |
| QFIX-04 | 覆蓋以 requirement 計算 | `coverage_ratio` 只由必要 requirement 的 complete 狀態計算；成功來源數、raw 筆數、模型自評不得單獨提高 coverage 或 L3 |
| QFIX-05 | 受阻品牌有可審計替代路徑 | 五個受阻品牌各有同出版者 alternative endpoint；primary 全失敗後才嘗試 fallback，fallback 不增加品牌分母或 independence group |
| QFIX-06 | L3 fail-closed | 只要任一必要 requirement、官方／監管、期間對齊、事件研究、社群／地區或質性 gate 未通過，狀態不得為 `professional_ready` |

## Test matrix

| Test ID | Covered by |
|---|---|
| QFIX-01-T1 | `tests/test_radar_extraction.py::test_collector_does_not_mark_healthy_empty_window_as_partial` |
| QFIX-02-T1 | `tests/test_target_scope.py::test_crypto_scope_does_not_use_generic_global_market_as_identity` |
| QFIX-02-T2 | `ingest-worker/test/target-evidence.test.ts` crypto/global parity case |
| QFIX-03-T1 | `tests/test_research_loop.py` gap plan and controller tests |
| QFIX-03-T2 | `tests/test_research_loop.py::test_gap_broker_plans_declared_alternative_route_without_inflating_independence` |
| QFIX-04-T1 | `tests/test_standard_pipeline.py::test_requirement_coverage_blocks_l3_even_when_source_count_is_high` |
| QFIX-05-T1 | `tests/test_news_catalog.py::test_known_blocked_brands_have_same_publisher_alternative_routes` |
| QFIX-05-T2 | `tests/test_news_probe.py::test_brand_probe_tries_declared_alternative_after_all_primary_routes_fail` |
| QFIX-06-T1 | shared quality-gate and context-coverage tests |

## Release gate

先通過 Python／Worker 單元與契約測試，再部署 Worker。部署後需以新版本執行一次真實 BTC job，回讀 source result、target scope、requirement coverage、Research Pack 與 report 的 hash／status；若仍有 missing requirement 或失敗來源，交付狀態必須是 partial，而不是宣稱 L3。
