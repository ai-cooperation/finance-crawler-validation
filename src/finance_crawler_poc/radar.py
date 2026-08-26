from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from finance_crawler_poc.target_scope import select_target_items


@dataclass(frozen=True)
class TopicDefinition:
    topic_id: str
    label: str
    keywords: tuple[str, ...]


TOPICS = (
    TopicDefinition("monetary_policy", "Monetary policy and inflation", ("interest rate", "inflation", "policy rate", "rate cut", "rate hike", "monetary policy")),
    TopicDefinition("digital_assets", "Digital assets", ("bitcoin", "crypto", "ethereum", "stablecoin", "digital asset")),
    TopicDefinition("ai_semiconductors", "AI and semiconductors", ("artificial intelligence", "ai", "semiconductor", "nvidia", "chip")),
    TopicDefinition("market_risk", "Market and credit risk", ("recession", "volatility", "sell-off", "market risk", "credit risk", "crash")),
    TopicDefinition("equities_earnings", "Equities and earnings", ("stock", "equity", "earnings", "s&p", "nasdaq")),
    TopicDefinition("trade_policy", "Trade policy", ("tariff", "trade war", "sanction")),
    TopicDefinition("personal_finance", "Personal finance", ("retirement", "portfolio", "asset allocation", "etf", "saving")),
    TopicDefinition("banking_fintech", "Banking and fintech", ("banking", "commercial bank", "bank regulation", "bank earnings", "bank loan", "payments", "fintech", "lending")),
)


def build_topic_snapshot(
    items: Iterable[dict[str, Any]],
    *,
    run_id: str,
    snapshot_id: str,
    as_of: str,
    failed_sources: Iterable[str],
    target: Mapping[str, Any] | None = None,
    question: str | None = None,
) -> dict[str, Any]:
    all_items = list(items)
    item_list, target_scope = select_target_items(all_items, target=target, question=question)
    target_terms = _target_terms(target, question)
    ranked: list[dict[str, Any]] = []
    for definition in TOPICS:
        matches = [item for item in item_list if _matches(definition, item)]
        if not matches:
            continue
        evidence_ids = list(dict.fromkeys(str(item["item_id"]) for item in matches))
        source_count = len({str(item["source_id"]) for item in matches})
        layers = {str(item["layer"]) for item in matches}
        engagement = sum(_engagement_weight(item.get("engagement")) for item in matches)
        news_count = sum(item.get("layer") == "news" for item in matches)
        social_count = sum(item.get("layer") == "social" for item in matches)
        target_hits = sum(_target_hit_count(item, target_terms) for item in matches)
        target_topic_bonus = _target_topic_bonus(definition.topic_id, target)
        ranked.append(
            {
                "topic_id": definition.topic_id,
                "label": definition.label,
                "score": round(
                    len(matches)
                    + 0.25 * max(0, source_count - 1)
                    + 0.25 * max(0, len(layers) - 1)
                    + engagement
                    + min(3.0, target_hits * 1.0)
                    + target_topic_bonus,
                    4,
                ),
                "item_count": len(matches),
                "source_count": source_count,
                "news_count": news_count,
                "social_count": social_count,
                "evidence_ids": evidence_ids,
                "divergence": _divergence(news_count, social_count),
            }
        )
    ranked.sort(key=lambda topic: (-topic["score"], topic["topic_id"]))
    topics = ranked[:3]
    failures = sorted(set(failed_sources))
    minimum_topics = 1 if target is not None else 3
    return {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "run_id": run_id,
        "as_of": as_of,
        # The global radar contract needs three ranked topics. A target-scoped
        # research refresh has a narrower question and is complete when at
        # least one relevant topic is evidenced; zero topics still fails
        # closed. The collector/report gate records the same floor.
        "partial": bool(failures or len(topics) < minimum_topics),
        "failed_sources": failures,
        "input_item_ids": list(dict.fromkeys(str(item["item_id"]) for item in item_list)),
        "topics": topics,
        "target_scope": target_scope,
    }


def _matches(definition: TopicDefinition, item: dict[str, Any]) -> bool:
    # Topic membership must be derived from normalized editorial fields, not
    # the full raw RSS/HTML payload (which includes unrelated navigation and
    # recommendation text). Raw payload remains available via evidence IDs.
    text = f" {item.get('title', '')} {item.get('summary', '')} ".casefold()
    return any(_contains_keyword(text, keyword) for keyword in definition.keywords)


def _target_terms(target: Mapping[str, Any] | None, question: str | None) -> tuple[str, ...]:
    values: list[str] = []
    if target is not None:
        for key in ("symbol", "name", "market", "sector", "industry"):
            value = target.get(key)
            if isinstance(value, str):
                values.append(value)
    if question:
        values.extend(re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", question))
    stopwords = {"what", "are", "the", "and", "for", "with", "from", "this", "that", "current"}
    terms: list[str] = []
    for value in values:
        normalized = value.casefold().strip()
        if normalized and normalized not in stopwords and normalized not in terms:
            terms.append(normalized)
    return tuple(terms)


def _target_hit_count(item: Mapping[str, Any], terms: tuple[str, ...]) -> int:
    if not terms:
        return 0
    text = f" {item.get('title', '')} {item.get('summary', '')} ".casefold()
    return sum(_contains_keyword(text, term) for term in terms)


def _target_topic_bonus(topic_id: str, target: Mapping[str, Any] | None) -> float:
    if target is None:
        return 0.0
    expected = {
        "crypto": "digital_assets",
        "equity": "equities_earnings",
        "etf": "personal_finance",
    }.get(str(target.get("kind")))
    return 2.0 if expected == topic_id else 0.0


def _contains_keyword(text: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in text
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def _engagement_weight(raw: object) -> float:
    if not isinstance(raw, dict):
        return 0.0
    total = 0.0
    for key in ("score", "comments", "shares", "likes"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            total += float(value)
    return min(math.log1p(total) / 10, 0.5)


def _divergence(news_count: int, social_count: int) -> dict[str, object]:
    # This is a volume-balance screen only.  It must not be presented as
    # opposing sentiment or an information-quality judgment: no stance
    # classifier is run at the topic-radar stage.
    basis = "news_social_item_count_balance"
    if news_count == 0 or social_count == 0:
        return {"direction": "insufficient_data", "magnitude": None, "basis": basis}
    magnitude = abs(news_count - social_count) / (news_count + social_count)
    if magnitude <= 0.2:
        direction = "aligned"
    elif news_count > social_count:
        direction = "news_leads"
    else:
        direction = "social_leads"
    return {"direction": direction, "magnitude": round(magnitude, 4), "basis": basis}
