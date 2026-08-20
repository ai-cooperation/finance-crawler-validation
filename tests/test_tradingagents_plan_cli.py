from __future__ import annotations

import json
from pathlib import Path

from finance_crawler_poc.tradingagents_plan_cli import write_tradingagents_plan


def topic_snapshot() -> dict[str, object]:
    evidence_ids = ["a" * 64, "b" * 64, "c" * 64]
    topics = []
    for topic_id, label, score, evidence_id in (
        ("digital_assets", "Digital assets", 6, evidence_ids[0]),
        ("ai_semiconductors", "AI and semiconductors", 5, evidence_ids[1]),
        ("equities_earnings", "Equities and earnings", 4, evidence_ids[2]),
    ):
        topics.append({
            "topic_id": topic_id,
            "label": label,
            "score": score,
            "item_count": 1,
            "source_count": 1,
            "news_count": 1,
            "social_count": 0,
            "evidence_ids": [evidence_id],
            "divergence": {"direction": "insufficient_data", "magnitude": None},
        })
    return {
        "schema_version": 1,
        "snapshot_id": "radar_20260820t035848z",
        "run_id": "run_20260820t035848z",
        "as_of": "2026-08-20T03:58:48Z",
        "partial": False,
        "failed_sources": [],
        "input_item_ids": evidence_ids,
        "topics": topics,
    }


def alignment() -> dict[str, object]:
    topics = []
    for topic_id, label, score, evidence_id, direction in (
        ("digital_assets", "Digital assets", 6, "a" * 64, "positive"),
        ("ai_semiconductors", "AI and semiconductors", 5, "b" * 64, "not_covered"),
        ("equities_earnings", "Equities and earnings", 4, "c" * 64, "not_covered"),
    ):
        topics.append({
            "topic_id": topic_id,
            "label": label,
            "topic_score": score,
            "market_direction": direction,
            "instrument_count": 1 if direction == "positive" else 0,
            "symbols": ["BTC"] if direction == "positive" else [],
            "mean_change_24h_pct": 1.0 if direction == "positive" else None,
            "evidence_ids": [evidence_id],
        })
    return {
        "schema_version": 1,
        "alignment_id": "align_20260820t035900z",
        "topic_snapshot_id": "radar_20260820t035848z",
        "market_snapshot_id": "market_20260820t035900z",
        "generated_at": "2026-08-20T03:59:00Z",
        "partial": True,
        "coverage_ratio": 0.333333,
        "topics": topics,
    }


def test_plan_cli_writes_private_plan_and_public_summary(tmp_path: Path) -> None:
    topic_path = tmp_path / "topic.json"
    alignment_path = tmp_path / "alignment.json"
    output_path = tmp_path / "plan"
    topic_path.write_text(json.dumps(topic_snapshot()), encoding="utf-8")
    alignment_path.write_text(json.dumps(alignment()), encoding="utf-8")

    report = write_tradingagents_plan(
        topic_path,
        alignment_path,
        output_path,
        plan_id="plan_20260820t040000z",
        created_at="2026-08-20T04:00:00Z",
        max_topics=1,
        max_claims_per_topic=6,
        max_tokens=4000,
        max_usd=0.0,
        model="tradingagents-deferred",
    )

    assert report == {
        "schema_version": 1,
        "plan_id": "plan_20260820t040000z",
        "decision": "eligible",
        "skip_reason": "none",
        "topics_to_run": 1,
        "topics_considered": 3,
        "model": "tradingagents-deferred",
    }
    assert (output_path / "tradingagents-run-plan.json").exists()
    assert (output_path / "tradingagents-plan-report.json").exists()
    envelope = json.loads(
        (output_path / "tradingagents-plan-envelope.json").read_text(encoding="utf-8")
    )
    assert envelope["run_id"] == "run_20260820t035848z"
    assert envelope["workflow_run_id"] == "0"
