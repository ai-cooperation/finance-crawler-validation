from __future__ import annotations

from finance_crawler_poc.contracts import validate_contract
from finance_crawler_poc.tradingagents_plan import build_tradingagents_run_plan


def topic_snapshot(*, partial: bool = False) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_id": "radar_20260820t035848z",
        "run_id": "run_20260820t035848z",
        "as_of": "2026-08-20T03:58:48Z",
        "partial": partial,
        "failed_sources": ["bogleheads_investing_browser"] if partial else [],
        "input_item_ids": ["a" * 64, "b" * 64, "c" * 64],
        "topics": [
            {
                "topic_id": "digital_assets",
                "label": "Digital assets",
                "score": 6,
                "item_count": 2,
                "source_count": 2,
                "news_count": 1,
                "social_count": 1,
                "evidence_ids": ["a" * 64],
                "divergence": {"direction": "aligned", "magnitude": 0.1},
            },
            {
                "topic_id": "ai_semiconductors",
                "label": "AI and semiconductors",
                "score": 5,
                "item_count": 2,
                "source_count": 2,
                "news_count": 2,
                "social_count": 0,
                "evidence_ids": ["b" * 64],
                "divergence": {"direction": "insufficient_data", "magnitude": None},
            },
            {
                "topic_id": "equities_earnings",
                "label": "Equities and earnings",
                "score": 4,
                "item_count": 1,
                "source_count": 1,
                "news_count": 1,
                "social_count": 0,
                "evidence_ids": ["c" * 64],
                "divergence": {"direction": "insufficient_data", "magnitude": None},
            },
        ],
    }


def alignment() -> dict[str, object]:
    return {
        "schema_version": 1,
        "alignment_id": "align_20260820t035900z",
        "topic_snapshot_id": "radar_20260820t035848z",
        "market_snapshot_id": "market_20260820t035900z",
        "generated_at": "2026-08-20T03:59:00Z",
        "partial": True,
        "coverage_ratio": 0.333333,
        "topics": [
            {
                "topic_id": "digital_assets",
                "label": "Digital assets",
                "topic_score": 6,
                "market_direction": "positive",
                "instrument_count": 2,
                "symbols": ["BTC", "ETH"],
                "mean_change_24h_pct": 1.2,
                "evidence_ids": ["a" * 64],
            },
            {
                "topic_id": "ai_semiconductors",
                "label": "AI and semiconductors",
                "topic_score": 5,
                "market_direction": "not_covered",
                "instrument_count": 0,
                "symbols": [],
                "mean_change_24h_pct": None,
                "evidence_ids": ["b" * 64],
            },
            {
                "topic_id": "equities_earnings",
                "label": "Equities and earnings",
                "topic_score": 4,
                "market_direction": "not_covered",
                "instrument_count": 0,
                "symbols": [],
                "mean_change_24h_pct": None,
                "evidence_ids": ["c" * 64],
            },
        ],
    }


def test_plan_selects_top_three_with_budget_and_traceability() -> None:
    plan = build_tradingagents_run_plan(
        topic_snapshot(),
        alignment(),
        plan_id="plan_20260820t040000z",
        created_at="2026-08-20T04:00:00Z",
        max_topics=3,
        max_claims_per_topic=6,
        max_tokens=12000,
        max_usd=0.5,
        model="tradingagents-deferred",
    )

    validate_contract("tradingagents-run-plan", plan)
    assert plan["decision"] == "eligible"
    assert plan["skip_reason"] == "none"
    assert [topic["decision"] for topic in plan["topics"]] == ["run", "run", "run"]
    assert all(topic["reason"] == "top_ranked" for topic in plan["topics"])


def test_plan_enforces_budget_cap_and_keeps_skip_reasons() -> None:
    plan = build_tradingagents_run_plan(
        topic_snapshot(),
        alignment(),
        plan_id="plan_20260820t040000z",
        created_at="2026-08-20T04:00:00Z",
        max_topics=1,
        max_claims_per_topic=6,
        max_tokens=4000,
        max_usd=0.1,
        model="tradingagents-deferred",
    )

    assert plan["decision"] == "eligible"
    assert [topic["decision"] for topic in plan["topics"]] == ["run", "skip", "skip"]
    assert [topic["reason"] for topic in plan["topics"]] == [
        "top_ranked",
        "budget_cap",
        "budget_cap",
    ]


def test_plan_fails_closed_when_market_alignment_is_missing() -> None:
    plan = build_tradingagents_run_plan(
        topic_snapshot(partial=True),
        None,
        plan_id="plan_20260820t040000z",
        created_at="2026-08-20T04:00:00Z",
        max_topics=3,
        max_claims_per_topic=6,
        max_tokens=12000,
        max_usd=0.5,
        model="tradingagents-deferred",
    )

    validate_contract("tradingagents-run-plan", plan)
    assert plan["decision"] == "skipped"
    assert plan["skip_reason"] == "missing_market_alignment"
    assert all(topic["decision"] == "skip" for topic in plan["topics"])


def test_plan_fails_closed_when_topic_or_token_budget_is_zero() -> None:
    plan = build_tradingagents_run_plan(
        topic_snapshot(),
        alignment(),
        plan_id="plan_20260820t040000z",
        created_at="2026-08-20T04:00:00Z",
        max_topics=0,
        max_claims_per_topic=6,
        max_tokens=12000,
        max_usd=0.0,
        model="tradingagents-deferred",
    )

    assert plan["decision"] == "skipped"
    assert plan["skip_reason"] == "no_budget"
    assert all(topic["reason"] == "budget_cap" for topic in plan["topics"])


def test_user_request_overrides_rank_reason_but_not_budget() -> None:
    plan = build_tradingagents_run_plan(
        topic_snapshot(),
        alignment(),
        plan_id="plan_20260820t040000z",
        created_at="2026-08-20T04:00:00Z",
        requested_topic_ids=["equities_earnings"],
        max_topics=1,
        max_claims_per_topic=6,
        max_tokens=4000,
        max_usd=0.1,
        model="tradingagents-deferred",
    )

    by_id = {topic["topic_id"]: topic for topic in plan["topics"]}
    assert by_id["equities_earnings"]["reason"] == "user_requested"
    assert by_id["equities_earnings"]["decision"] == "run"
    assert sum(topic["decision"] == "run" for topic in plan["topics"]) == 1


def test_plan_skips_when_target_scope_has_no_evidence() -> None:
    snapshot = topic_snapshot()
    snapshot["target_scope"] = {
        "policy": "exact_identity_or_crypto_asset_family_v2",
        "input_item_count": 3,
        "relevant_source_group_count": 0,
        "input_item_ids": ["a" * 64, "b" * 64, "c" * 64],
        "target": {"kind": "equity", "symbol": "2330.TW"},
        "relevant_item_count": 0,
        "identity_match_item_count": 0,
    }
    plan = build_tradingagents_run_plan(
        snapshot,
        alignment(),
        plan_id="plan_20260820t040000z",
        created_at="2026-08-20T04:00:00Z",
        max_topics=3,
        max_claims_per_topic=6,
        max_tokens=12000,
        max_usd=0.5,
        model="tradingagents-deferred",
        target={"kind": "equity", "symbol": "2330.TW"},
    )

    assert plan["decision"] == "skipped"
    assert plan["skip_reason"] == "target_evidence_insufficient"
