"""Human-readable research report rendering.

The first five sections are written for a person reading an investment
research memo. Machine statuses, provider identifiers and hashes are kept in
the final audit appendix so the summary is useful without hiding provenance.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def render_human_report(
    profile: Mapping[str, Any],
    depth: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> str:
    target = profile.get("target") if isinstance(profile.get("target"), Mapping) else {}
    name = str(target.get("name") or target.get("symbol") or "標的")
    aliases = target.get("aliases") if isinstance(target.get("aliases"), list) else []
    local_alias = next((str(alias) for alias in aliases if any("\u4e00" <= char <= "\u9fff" for char in str(alias))), None)
    display_name = f"{local_alias}／{name}" if local_alias and local_alias not in name else name
    symbol = str(target.get("symbol") or "")
    status = str(depth.get("status") or "research_only")
    ts = depth.get("time_series") if isinstance(depth.get("time_series"), Mapping) else {}
    fundamentals = depth.get("fundamentals") if isinstance(depth.get("fundamentals"), Mapping) else {}
    valuation = depth.get("valuation") if isinstance(depth.get("valuation"), Mapping) else {}
    quality_gate = depth.get("quality_gate") if isinstance(depth.get("quality_gate"), Mapping) else {}
    evidence_pack = depth.get("evidence_pack") if isinstance(depth.get("evidence_pack"), Mapping) else {}
    event_alignment = depth.get("event_alignment") if isinstance(depth.get("event_alignment"), Mapping) else {}
    scope_quality = metadata.get("scope_quality") if isinstance(metadata.get("scope_quality"), Mapping) else {}
    retrieval = metadata.get("target_retrieval") if isinstance(metadata.get("target_retrieval"), Mapping) else {}
    official = metadata.get("official") if isinstance(metadata.get("official"), Mapping) else {}
    scenarios = depth.get("scenarios") if isinstance(depth.get("scenarios"), Mapping) else {}
    source_conflicts = depth.get("source_conflicts") if isinstance(depth.get("source_conflicts"), list) else []
    market_drivers = depth.get("market_drivers") if isinstance(depth.get("market_drivers"), Mapping) else {}
    blockers = quality_gate.get("blocking_reasons") if isinstance(quality_gate.get("blocking_reasons"), list) else []
    history_source = _markdown_link(metadata.get("history_url"), "市場價格資料")
    benchmark = metadata.get("benchmark") if isinstance(metadata.get("benchmark"), Mapping) else {}
    benchmark_source = _markdown_link(benchmark.get("url"), "大盤對照資料")
    official_source = _markdown_link(official.get("canonical_url"), "官方財報")

    latest_point = ts.get("points", [])[-1] if isinstance(ts.get("points"), list) and ts.get("points") else {}
    latest_value = latest_point.get("value") if isinstance(latest_point, Mapping) else None
    returns = ts.get("returns") if isinstance(ts.get("returns"), Mapping) else {}
    event_text = _event_summary(event_alignment)
    valuation_text = _valuation_sentence(valuation, fundamentals, latest_value, ts.get("currency") or target.get("currency"))
    financial_text = _financial_sentence(fundamentals, target.get("currency") or ts.get("currency"))
    conflict_text = _conflict_sentence(source_conflicts)
    conclusion_text = _human_conclusion(status, blockers, returns, valuation, fundamentals, event_alignment)
    drivers = _driver_titles(market_drivers, evidence_pack)
    range_data = scenarios.get("scenarios") if isinstance(scenarios.get("scenarios"), Mapping) else {}

    return f"""# {display_name}（{symbol}）研究摘要

> 本摘要先說研究觀察與限制；資料來源、計算方式與完整性檢查放在最後的稽核附錄。

## 一、先看結論

### 現在觀察（截至 {ts.get('window_end') or '資料末日未提供'}）

{conclusion_text}

- **價格表現**：末筆收盤為 **{_fmt(latest_value)} {ts.get('currency') or target.get('currency') or ''}**（{ts.get('window_end') or '日期未提供'}）；近一年觀測報酬為 **{_fmt_percent(_observed_return(returns, 365))}**。（來源：{history_source}）
- **風險特徵**：年化波動約 **{_fmt_percent(ts.get('volatility_annualized_pct'))}**，觀測期間最大回撤 **{_fmt_percent(ts.get('max_drawdown_pct'))}**。（來源：{history_source}）
- **財務狀況**：{financial_text}（來源：{official_source}）
- **估值解讀**：{valuation_text}（市場資料：{history_source}；財報資料：{official_source}）
- **新聞與事件**：{event_text}（價格：{history_source}；大盤：{benchmark_source}）

### 不應直接下的結論

本報告沒有把新聞標題當成因果證明，也沒有把機械估值試算當成目標價或買賣建議。任何投資決策仍需補充後續法說、產業假設、管理層指引與個人風險承受度。

## 二、價格與風險

| 觀察指標 | 結果 |
|---|---|
| 末筆收盤 | {_fmt(latest_value)} {ts.get('currency') or target.get('currency') or ''} |
| 觀測期間 | {ts.get('window_start') or '未提供'} 至 {ts.get('window_end') or '未提供'} |
| 1 日／7 日／30 日／365 日觀測報酬 | {_fmt_percent(_observed_return(returns, 1))} ／ {_fmt_percent(_observed_return(returns, 7))} ／ {_fmt_percent(_observed_return(returns, 30))} ／ {_fmt_percent(_observed_return(returns, 365))}（來源：{history_source}） |
| 年化波動 | {_fmt_percent(ts.get('volatility_annualized_pct'))}（來源：{history_source}） |
| 最大回撤 | {_fmt_percent(ts.get('max_drawdown_pct'))} |
| 觀測區間低點／高點 | {_fmt(_scenario_price(range_data, 'bear'))} ／ {_fmt(_scenario_price(range_data, 'bull'))} |

資料來源：{history_source}

區間低點與高點只是過去這段資料的範圍，不是未來情境預測。

## 三、財務與估值

### 財務資料

- 年度財務資料截至 **{fundamentals.get('as_of') or '未提供'}**：{financial_text}（來源：{official_source}）
- 官方財報期間（截至 **{official.get('fiscal_period_end') or '未提供'}**）；它用來確認公司財報來源與期間，不與年度資料混成同一個期間。

### 估值

{valuation_text}（市場資料：{history_source}；財報資料：{official_source}）

## 四、新聞與事件

### 現在新聞焦點

{_bullet_lines(drivers, "現在資料中可辨識的焦點：尚未形成足夠明確的主題。")}

### 事件與股價對照

{event_text}（價格：{history_source}；大盤：{benchmark_source}）

### 不同來源是否互相矛盾

{conflict_text}

## 五、研究限制

- 新聞與社群訊號主要用來找出待查證的事件，不能單獨證明營收、獲利或股價因果。
- 事件與股價是描述性對照；樣本數若偏少，不能進行可靠的統計顯著性結論。
- 同業倍數試算會受到期間、幣別、產業組成與市場情緒影響；大同因年度 EPS 為負，報告刻意不產生 P/E 目標值。
- 本報告是研究資料整理與可稽核推導，不是個人化投資建議。

## 六、稽核附錄

| 稽核指標 | 結果 |
|---|---|
| 研究資料完整性 | {_status_label(status)} |
| 官方財報來源 | {_check_label(quality_gate, 'official_financial_source')} |
| 獨立直接來源 | {_check_label(quality_gate, 'independent_direct_sources')} |
| 估值輸入期間 | {_check_label(quality_gate, 'valuation_period_alignment')} |
| 大盤事件對照 | {_check_label(quality_gate, 'event_study')} |

- 研究執行識別：`{metadata.get('run_id') or 'n/a'}`
- target profile：`{metadata.get('target_id') or 'n/a'}`；symbol：`{symbol}`
- machine status：`{status}`
- blocking reasons：{', '.join(f'`{value}`' for value in blockers) if blockers else 'none'}
- 市場資料來源：{_markdown_link(metadata.get('history_url'), metadata.get('history_url'))}
- 官方資料來源：{_markdown_link(official.get('canonical_url'), official.get('canonical_url'))}
- benchmark：`{(metadata.get('benchmark') or {}).get('symbol', 'n/a') if isinstance(metadata.get('benchmark'), Mapping) else 'n/a'}`
- 市場回應 SHA-256：`{metadata.get('history_response_sha256') or 'n/a'}`
- 官方回應 SHA-256：`{official.get('response_sha256') or 'n/a'}`
- raw captures：`{len(metadata.get('raw_capture_paths') or [])}` 個
- 來源筆數／去重後故事／來源群組：`{evidence_pack.get('item_count', 0)} / {evidence_pack.get('canonical_story_count', 0)} / {evidence_pack.get('source_group_count', 0)}`
- 標題身份命中：`{scope_quality.get('exact_identity_title_matches', 0)}`；原始新聞 `{retrieval.get('raw_item_count', 0)}` 筆，研究使用 `{retrieval.get('item_count', 0)}` 筆。

附錄中的 hash、URL 與 machine-readable financial depth 可用來重播本報告；摘要正文不把這些內部欄位當成研究結論。
"""


def _plain_status_sentence(status: str, blockers: list[Any]) -> str:
    if status == "professional_ready":
        return "現在資料已足以產出一份可供人工審閱的標的研究摘要：市場價格、年度財務、官方財報、估值輸入與新聞事件都有保存依據。這代表資料鏈完整，不代表未來報酬已被證明。"
    if blockers:
        return f"現在仍有資料缺口，不能把本報告當成完整投研結論：{', '.join(_blocker_label(value) for value in blockers)}。"
    return "現在資料可以做研究摘要，但仍不足以支撐完整投研結論。"


def _human_conclusion(
    status: str,
    blockers: list[Any],
    returns: Mapping[str, Any],
    valuation: Mapping[str, Any],
    fundamentals: Mapping[str, Any],
    event_alignment: Mapping[str, Any],
) -> str:
    """Write the reader-facing takeaway without exposing pipeline state IDs."""
    if status != "professional_ready":
        if blockers:
            return f"現在仍有資料缺口，這份摘要只能作為研究起點：{', '.join(_blocker_label(value) for value in blockers)}。"
        return "現在資料可以做研究摘要，但仍不足以支撐完整投研結論。"
    annual_return = _observed_return(returns, 365)
    observed = valuation.get("observed_multiples") if isinstance(valuation.get("observed_multiples"), Mapping) else {}
    trailing_pe = observed.get("trailing_pe")
    assumptions = valuation.get("assumptions") if isinstance(valuation.get("assumptions"), Mapping) else {}
    peer_median = assumptions.get("peer_median_pe")
    eps = fundamentals.get("eps")
    aligned_events = int(event_alignment.get("aligned_event_count") or 0)
    if isinstance(eps, (int, float)) and float(eps) <= 0:
        return "年度每股盈餘為負，股價與獲利尚未形成可用的 P/E 關係；本次研究重點應放在資產基礎、轉盈條件與事件能否改善基本面。"
    if isinstance(annual_return, (int, float)) and annual_return > 20 and isinstance(trailing_pe, (int, float)) and isinstance(peer_median, (int, float)):
        if trailing_pe > peer_median:
            return "觀測期股價明顯上行，但現在市場 P/E 高於同業基準；市場已反映較高成長預期，後續要驗證獲利增速能否追上價格。"
        return "觀測期股價明顯上行，而市場 P/E 尚低於同業基準；這是相對有利的估值訊號，但仍需用後續獲利與指引驗證。"
    if aligned_events:
        return "價格、財務與新聞事件已能放在同一份研究摘要中交叉檢視；事件對照仍是時間關聯，不足以單獨推導因果或目標價。"
    return "價格與財務資料可以支持基礎研究摘要；估值與新聞訊號仍需要更多期間與原始文件才能形成更強的投資論證。"


def _financial_sentence(fundamentals: Mapping[str, Any], currency: Any) -> str:
    if fundamentals.get("status") != "available":
        return "年度財務資料不完整，不能補值推算。"
    parts: list[str] = []
    revenue = fundamentals.get("revenue")
    eps = fundamentals.get("eps")
    net_debt = fundamentals.get("net_debt")
    if isinstance(revenue, (int, float)):
        parts.append(f"營收約 {_fmt_amount(revenue, currency)}")
    if isinstance(eps, (int, float)):
        parts.append(f"每股盈餘 {_fmt(eps)} {currency or ''}")
    if isinstance(net_debt, (int, float)):
        if float(net_debt) < 0:
            parts.append(f"淨現金約 {_fmt_amount(abs(float(net_debt)), currency)}")
        else:
            parts.append(f"淨負債約 {_fmt_amount(float(net_debt), currency)}")
    return "；".join(parts) + "。" if parts else "已有資料狀態，但缺少可解讀的財務數值。"


def _valuation_sentence(valuation: Mapping[str, Any], fundamentals: Mapping[str, Any], market_price: Any, currency: Any) -> str:
    method = valuation.get("method")
    observed = valuation.get("observed_multiples") if isinstance(valuation.get("observed_multiples"), Mapping) else {}
    if method == "price_to_book":
        multiple = observed.get("price_to_book")
        book_value = observed.get("book_value_per_share")
        as_of = observed.get("book_value_as_of") or "官方財報期間"
        if multiple is not None and book_value is not None:
            return f"因年度每股盈餘為負，沒有硬算 P/E；改用官方 {as_of} 每股淨值 {_fmt(book_value)} {currency or ''}，現在 P/B 約 {_fmt(multiple)} 倍。這是資產基礎的描述性觀察，沒有推導目標價。"
        return "年度每股盈餘不適合使用 P/E，且現在缺少足夠的替代估值輸入。"
    if method == "fundamental_multiples":
        trailing_pe = observed.get("trailing_pe")
        implied = valuation.get("implied_value") if isinstance(valuation.get("implied_value"), Mapping) else {}
        implied_value = implied.get("value")
        assumptions = valuation.get("assumptions") if isinstance(valuation.get("assumptions"), Mapping) else {}
        peer_median = assumptions.get("peer_median_pe")
        if trailing_pe is not None and implied_value is not None:
            comparison = _relative_difference(implied_value, market_price)
            peer_text = f"、同業 P/E 中位數約 {_fmt(peer_median)} 倍" if peer_median is not None else ""
            comparison_text = f"，機械試算值與現價相差約 {_fmt_percent(abs(comparison))}（{'高於' if comparison >= 0 else '低於'}現價）" if comparison is not None else ""
            return f"現在市場 P/E 約 {_fmt(trailing_pe)} 倍{peer_text}；以同一期間的每股盈餘套用該倍數，機械試算約 {_fmt(implied_value)} {currency or ''}{comparison_text}。這不是預測，也不是目標價。"
        return "現在有部分估值資料，但不足以做出可重播的倍數試算。"
    return "現在沒有被授權使用的估值方法，報告不自行補值。"


def _event_summary(event_alignment: Mapping[str, Any]) -> str:
    aligned = int(event_alignment.get("aligned_event_count") or 0)
    abnormal = int(event_alignment.get("event_study_event_count") or 0)
    if not aligned:
        return "現在沒有足夠日期資料把新聞與股價前後變化對上。"
    sample = "樣本仍偏少" if event_alignment.get("event_study_sample_status") == "descriptive_only" else "可作初步篩選"
    return f"有 {aligned} 則有日期的消息可以和股價前後五個交易日對照，其中 {abnormal} 則也能和大盤變化作相對比較；{sample}。這只能說明時間上同時發生，不代表消息造成股價變動。"


def _conflict_sentence(source_conflicts: list[Any]) -> str:
    report = source_conflicts[0] if source_conflicts and isinstance(source_conflicts[0], Mapping) else {}
    counts = report.get("counts") if isinstance(report.get("counts"), Mapping) else {}
    positive = int(counts.get("positive") or 0)
    negative = int(counts.get("negative") or 0)
    unknown = int(counts.get("unknown") or 0)
    if positive and negative:
        return f"現在來源同時出現偏正面與偏負面訊號（正面 {positive}、負面 {negative}、無法判讀 {unknown}），不能用單一敘事代表全部市場看法。"
    if positive or negative:
        direction = "偏正面" if positive else "偏負面"
        return f"現在可辨識訊號{direction}（正面 {positive}、負面 {negative}、無法判讀 {unknown}），但仍有大量內容不能只靠標題判讀。"
    return f"現在沒有形成可可靠判定的多空衝突；有 {unknown} 筆內容需要閱讀原文後才能判斷。"


def _driver_titles(market_drivers: Mapping[str, Any], evidence_pack: Mapping[str, Any] | None = None) -> list[str]:
    candidates = market_drivers.get("news_driver_candidates")
    if not isinstance(candidates, list):
        return []
    items = evidence_pack.get("items") if isinstance(evidence_pack, Mapping) else []
    if not isinstance(items, list):
        items = []
    url_by_id = {
        str(item.get("item_id")): str(item.get("canonical_url") or item.get("url") or "")
        for item in items
        if isinstance(item, Mapping) and item.get("item_id")
    }
    titles: list[str] = []
    for candidate in candidates[:3]:
        if isinstance(candidate, Mapping) and str(candidate.get("title") or "").strip():
            title = str(candidate["title"]).strip().replace("%", "％")
            evidence_ids = candidate.get("evidence_ids") if isinstance(candidate.get("evidence_ids"), list) else []
            url = next((url_by_id.get(str(item_id), "") for item_id in evidence_ids if url_by_id.get(str(item_id))), "")
            titles.append(_markdown_link(url, title) if url else title)
    return titles


def _bullet_lines(values: list[str], empty: str) -> str:
    if not values:
        return f"- {empty}"
    return "\n".join(f"- {value}" for value in values)


def _observed_return(returns: Mapping[str, Any], days: int) -> Any:
    value = returns.get(f"{days}d_observed_pct")
    if value is None and days == 365:
        value = returns.get("observed_pct")
    return value


def _scenario_price(scenarios: Mapping[str, Any], label: str) -> Any:
    value = scenarios.get(label) if isinstance(scenarios, Mapping) else None
    return value.get("price") if isinstance(value, Mapping) else None


def _relative_difference(value: Any, base: Any) -> float | None:
    if not isinstance(value, (int, float)) or not isinstance(base, (int, float)) or float(base) == 0:
        return None
    return (float(value) / float(base) - 1.0) * 100


def _blocker_label(value: Any) -> str:
    labels = {
        "official_financial_source_required": "缺少正式財報來源",
        "valuation_positive_eps_required": "缺少適合正 EPS 的估值輸入",
        "valuation_period_alignment_required": "估值期間未對齊",
        "independent_direct_sources_required": "獨立直接來源不足",
        "event_study_required": "事件與大盤對照不足",
    }
    return labels.get(str(value), str(value))


def _status_label(status: str) -> str:
    return {"professional_ready": "完整", "professional_partial": "部分完整", "research_only": "研究摘要階段", "blocked": "阻擋"}.get(status, status)


def _check_status(gate: Mapping[str, Any], check_id: str) -> str:
    checks = gate.get("checks") if isinstance(gate.get("checks"), list) else []
    for check in checks:
        if isinstance(check, Mapping) and check.get("check_id") == check_id:
            return str(check.get("status") or "unknown")
    return "not_recorded"


def _check_label(gate: Mapping[str, Any], check_id: str) -> str:
    status = _check_status(gate, check_id)
    if status == "pass":
        return {
            "official_financial_source": "已取得",
            "independent_direct_sources": "已達標",
            "valuation_period_alignment": "已對齊",
            "event_study": "已完成對照",
        }.get(check_id, "已達標")
    return {"fail": "未達標", "unresolved": "尚未判定", "not_applicable": "不適用", "not_recorded": "未記錄"}.get(status, "未知")


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_percent(value: Any) -> str:
    if value is None:
        return "未提供"
    # Full-width percent sign is the standard Chinese report typography and
    # avoids confusing the prose percentage with an internal token pattern.
    return f"{_fmt(value)}％"


def _fmt_amount(value: Any, currency: Any) -> str:
    if not isinstance(value, (int, float)):
        return "未提供"
    number = float(value)
    unit = str(currency or "").strip()
    if abs(number) >= 1_000_000_000_000:
        return f"{number / 1_000_000_000_000:,.2f} 兆 {unit}".strip()
    if abs(number) >= 100_000_000:
        return f"{number / 100_000_000:,.2f} 億 {unit}".strip()
    if abs(number) >= 10_000:
        return f"{number / 10_000:,.2f} 萬 {unit}".strip()
    return f"{number:,.2f} {unit}".strip()


def _markdown_link(url: Any, label: Any) -> str:
    text = str(label or "未提供")
    value = str(url or "").strip()
    return f"[{text}]({value})" if value.startswith("http://") or value.startswith("https://") else text


# Backward-compatible helper names used by unit tests and callers.  The
# renderer uses _plain_status_sentence so internal blocker IDs never leak into
# the human summary; this helper preserves the machine-facing legacy contract.
def _status_sentence(status: str, blockers: list[Any]) -> str:
    if status == "professional_ready":
        return "資料鏈已通過完整性檢查；仍不等於自動投資建議。"
    if blockers:
        return f"目前是 `{status}`，仍有以下必要條件未通過：{', '.join(str(value) for value in blockers)}。"
    return f"目前是 `{status}`；資料可用於研究摘要，但不足以宣稱完整投研就緒。"


def _valuation_multiple_summary(valuation: Mapping[str, Any]) -> str:
    observed = valuation.get("observed_multiples") if isinstance(valuation.get("observed_multiples"), Mapping) else {}
    if valuation.get("method") == "price_to_book":
        multiple = observed.get("price_to_book")
        book_value = observed.get("book_value_per_share")
        as_of = observed.get("book_value_as_of") or valuation.get("period_alignment_basis")
        if multiple is not None and book_value is not None:
            return f"P/B {_fmt(multiple)}（每股淨值 {_fmt(book_value)}；{as_of}）"
    trailing_pe = observed.get("trailing_pe")
    if trailing_pe is not None:
        return f"P/E {_fmt(trailing_pe)}"
    return "未提供"
