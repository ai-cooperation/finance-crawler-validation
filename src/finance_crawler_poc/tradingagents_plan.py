from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from finance_crawler_poc.contracts import validate_contract


def build_tradingagents_run_plan(
    topic_snapshot: Mapping[str, Any],
    alignment: Mapping[str, Any] | None,
    *,
    plan_id: str,
    created_at: str,
    max_topics: int,
    max_claims_per_topic: int,
    max_tokens: int,
    max_usd: float,
    model: str,
    requested_topic_ids: Iterable[str] = (),
    divergence_threshold: float = 0.5,
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a bounded, evidence-linked TradingAgents execution plan.

    This function only decides eligibility. It never invokes a model. A plan
    may be persisted and replayed before an agent run is authorized.
    """

    validate_contract("topic-snapshot", dict(topic_snapshot))
    if alignment is not None:
        validate_contract("market-topic-alignment", dict(alignment))
        if alignment["topic_snapshot_id"] != topic_snapshot["snapshot_id"]:
            raise ValueError("market alignment belongs to a different topic snapshot")
    if not model.strip():
        raise ValueError("model is required")
    if max_topics < 0 or max_topics > 3:
        raise ValueError("max_topics must be between 0 and 3")
    if max_claims_per_topic < 1:
        raise ValueError("max_claims_per_topic must be positive")
    if max_tokens < 0 or max_usd < 0:
        raise ValueError("budget values cannot be negative")
    if not 0 <= divergence_threshold <= 1:
        raise ValueError("divergence_threshold must be between 0 and 1")

    requested = {topic_id.strip() for topic_id in requested_topic_ids if topic_id.strip()}
    topic_ids = {topic["topic_id"] for topic in topic_snapshot["topics"]}
    unknown_requested = sorted(requested - topic_ids)
    if unknown_requested:
        raise ValueError(f"requested topic is absent from snapshot: {unknown_requested[0]}")
    alignment_by_id = {
        topic["topic_id"]: topic for topic in (alignment["topics"] if alignment else [])
    }
    budget = {
        "max_topics": max_topics,
        "max_claims_per_topic": max_claims_per_topic,
        "max_tokens": max_tokens,
        "max_usd": max_usd,
        "model": model.strip(),
    }
    topics = list(topic_snapshot["topics"])
    if not topics:
        return _build_plan(
            topic_snapshot,
            alignment,
            plan_id,
            created_at,
            "skipped",
            "no_topics",
            budget,
            [],
        )
    if alignment is None:
        return _build_plan(
            topic_snapshot,
            alignment,
            plan_id,
            created_at,
            "skipped",
            "missing_market_alignment",
            budget,
            [_topic_entry(topic, None, "skip", "not_requested") for topic in topics],
        )
    if target is not None:
        target_scope = topic_snapshot.get("target_scope")
        relevant_count = int(target_scope.get("relevant_item_count") or 0) if isinstance(target_scope, Mapping) else 0
        target_kind = str(target.get("kind") or "").casefold()
        identity_count = int(target_scope.get("identity_match_item_count") or 0) if isinstance(target_scope, Mapping) else relevant_count
        if relevant_count < 1 or (target_kind in {"equity", "company"} and identity_count < 1):
            return _build_plan(
                topic_snapshot,
                alignment,
                plan_id,
                created_at,
                "skipped",
                "target_evidence_insufficient",
                budget,
                [],
            )
    if max_topics == 0 or max_tokens == 0:
        return _build_plan(
            topic_snapshot,
            alignment,
            plan_id,
            created_at,
            "skipped",
            "no_budget",
            budget,
            [
                _topic_entry(
                    topic,
                    alignment_by_id.get(topic["topic_id"]),
                    "skip",
                    "budget_cap",
                )
                for topic in topics
            ],
        )

    target_topic = {
        "crypto": "digital_assets",
        "equity": "equities_earnings",
        "etf": "personal_finance",
    }.get(str((target or {}).get("kind")))
    ranked = sorted(
        topics,
        key=lambda topic: (
            0 if topic["topic_id"] in requested else 1,
            0 if target_topic is not None and topic["topic_id"] == target_topic else 1,
            0 if _is_significant_divergence(topic, divergence_threshold) else 1,
            -float(topic["score"]),
            topic["topic_id"],
        ),
    )
    selected_ids = {topic["topic_id"] for topic in ranked[:max_topics]}
    if target_topic is not None and target_topic in topic_ids and max_topics > 0:
        selected_ids = {target_topic}
    entries = []
    for topic in topics:
        topic_id = topic["topic_id"]
        if topic_id not in selected_ids:
            entries.append(
                _topic_entry(
                    topic,
                    alignment_by_id[topic_id],
                    "skip",
                    "budget_cap",
                )
            )
            continue
        reason = (
            "user_requested"
            if topic_id in requested
            else "divergence"
            if _is_significant_divergence(topic, divergence_threshold)
            else "top_ranked"
        )
        entries.append(_topic_entry(topic, alignment_by_id[topic_id], "run", reason))
    entries.sort(key=lambda topic: (-next(item["score"] for item in topics if item["topic_id"] == topic["topic_id"]), topic["topic_id"]))
    return _build_plan(
        topic_snapshot,
        alignment,
        plan_id,
        created_at,
        "eligible",
        "none",
        budget,
        entries,
    )


def _build_plan(
    topic_snapshot: Mapping[str, Any],
    alignment: Mapping[str, Any] | None,
    plan_id: str,
    created_at: str,
    decision: str,
    skip_reason: str,
    budget: Mapping[str, Any],
    topics: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    plan = {
        "schema_version": 1,
        "plan_id": plan_id,
        "topic_snapshot_id": topic_snapshot["snapshot_id"],
        "alignment_id": alignment["alignment_id"] if alignment else None,
        "created_at": created_at,
        "decision": decision,
        "skip_reason": skip_reason,
        "budget": dict(budget),
        "topics": list(topics),
    }
    validate_contract("tradingagents-run-plan", plan)
    return plan


def _topic_entry(
    topic: Mapping[str, Any],
    aligned_topic: Mapping[str, Any] | None,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    evidence_ids = set(topic["evidence_ids"])
    if aligned_topic is not None:
        evidence_ids.update(aligned_topic["evidence_ids"])
    return {
        "topic_id": topic["topic_id"],
        "label": topic["label"],
        "score": topic["score"],
        "decision": decision,
        "reason": reason,
        "market_direction": aligned_topic["market_direction"] if aligned_topic else "not_covered",
        "evidence_ids": sorted(evidence_ids),
    }


def _is_significant_divergence(topic: Mapping[str, Any], threshold: float) -> bool:
    divergence = topic["divergence"]
    magnitude = divergence["magnitude"]
    return (
        divergence["direction"] != "insufficient_data"
        and isinstance(magnitude, (int, float))
        and magnitude >= threshold
    )
