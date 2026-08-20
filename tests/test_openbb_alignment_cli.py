from __future__ import annotations

import json
from pathlib import Path

from finance_crawler_poc.openbb_alignment_cli import write_alignment_artifacts


def market_item(item_id: str) -> dict[str, object]:
    return {
        "item_id": item_id,
        "source_id": "coingecko_markets_api",
        "kind": "market_data",
        "content": (
            '{"current_price": 64000, "id": "bitcoin", '
            '"last_updated": "2026-08-20T03:55:20Z", "market_cap": 1000000, '
            '"name": "Bitcoin", "price_change_percentage_24h": 4.0, "symbol": "btc"}'
        ),
        "published_at": "2026-08-20T03:55:20Z",
        "collected_at": "2026-08-20T03:58:48Z",
    }


def topic_snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_id": "radar_20260820t035848z",
        "run_id": "run_20260820t035848z",
        "as_of": "2026-08-20T03:58:48Z",
        "partial": True,
        "failed_sources": ["bogleheads_investing_browser"],
        "input_item_ids": ["a" * 64],
        "topics": [
            {
                "topic_id": "digital_assets",
                "label": "Digital assets",
                "score": 6,
                "item_count": 1,
                "source_count": 1,
                "news_count": 1,
                "social_count": 0,
                "evidence_ids": ["a" * 64],
                "divergence": {"direction": "insufficient_data", "magnitude": None},
            },
            {
                "topic_id": "ai_semiconductors",
                "label": "AI and semiconductors",
                "score": 5,
                "item_count": 1,
                "source_count": 1,
                "news_count": 1,
                "social_count": 0,
                "evidence_ids": ["a" * 64],
                "divergence": {"direction": "insufficient_data", "magnitude": None},
            },
        ],
    }


def test_alignment_cli_writes_reproducible_artifacts(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw-items.json"
    topic_path = tmp_path / "topic-snapshot.json"
    output_path = tmp_path / "alignment"
    raw_path.write_text(
        json.dumps([market_item("a" * 64)]),
        encoding="utf-8",
    )
    topic_path.write_text(json.dumps(topic_snapshot()), encoding="utf-8")

    report = write_alignment_artifacts(
        raw_path,
        topic_path,
        output_path,
        provider="coingecko",
        generated_at="2026-08-20T03:59:00Z",
        target={"kind": "crypto", "symbol": "BTC"},
    )

    assert report == {
        "schema_version": 1,
        "market_snapshot_id": "market_20260820t035900z",
        "alignment_id": "align_20260820t035900z",
        "provider": "coingecko",
        "target": {"kind": "crypto", "symbol": "BTC"},
        "instruments": 1,
        "topics": 2,
        "coverage_ratio": 0.5,
        "partial": True,
    }
    assert (output_path / "market-snapshot.json").exists()
    assert (output_path / "market-topic-alignment.json").exists()
    envelope = json.loads((output_path / "market-alignment-envelope.json").read_text(encoding="utf-8"))
    assert envelope["run_id"] == "run_20260820t035848z"
    assert envelope["workflow_run_id"] == "0"


def test_alignment_cli_can_emit_not_requested_market_boundary(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw-items.json"
    topic_path = tmp_path / "topic-snapshot.json"
    output_path = tmp_path / "alignment"
    raw_path.write_text(json.dumps([]), encoding="utf-8")
    topic_path.write_text(json.dumps(topic_snapshot()), encoding="utf-8")

    report = write_alignment_artifacts(
        raw_path,
        topic_path,
        output_path,
        provider="coingecko",
        include_market_data=False,
        generated_at="2026-08-20T03:59:00Z",
        target={"kind": "equity", "symbol": "NVDA"},
    )

    assert report["provider"] == "not_requested"
    assert report["instruments"] == 0
    assert report["coverage_ratio"] == 0
    market = json.loads((output_path / "market-snapshot.json").read_text(encoding="utf-8"))
    assert market["instruments"] == []
