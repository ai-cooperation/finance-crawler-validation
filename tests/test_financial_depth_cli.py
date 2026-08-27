from __future__ import annotations

import json
from pathlib import Path

from finance_crawler_poc import financial_depth_cli


def test_build_depth_artifact_preserves_market_snapshot_and_contract(tmp_path: Path, monkeypatch) -> None:
    item_id = "a" * 64
    market_path = tmp_path / "market.json"
    raw_path = tmp_path / "raw.json"
    market_path.write_text(json.dumps({
        "schema_version": 1,
        "snapshot_id": "market_20260101t000000z",
        "as_of": "2026-01-02T00:00:00Z",
        "provider": "coingecko",
        "instruments": [{
            "symbol": "BTC",
            "asset_type": "crypto",
            "currency": "USD",
            "price": 110,
            "observed_at": "2026-01-02T00:00:00Z",
            "source_item_ids": [item_id],
        }],
    }), encoding="utf-8")
    raw_path.write_text(json.dumps([{
        "item_id": item_id,
        "source_id": "news",
        "title": "Bitcoin rises",
        "summary": "",
    }]), encoding="utf-8")
    monkeypatch.setattr(
        financial_depth_cli,
        "fetch_market_history",
        lambda target, days: (
            [
                {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
                {"observed_at": "2026-01-02T00:00:00Z", "value": 110},
            ],
            "coingecko",
            "https://example.com/history",
        ),
    )

    enriched, depth = financial_depth_cli.build_depth_artifact(
        market_path,
        raw_path,
        target={"kind": "crypto", "symbol": "BTC"},
        history_days=365,
    )

    assert enriched["financial_depth"] == depth
    assert depth["status"] == "professional_partial"
    assert depth["time_series"]["point_count"] == 2


def test_write_depth_artifact_splits_large_depth_from_alignment_envelope(tmp_path: Path, monkeypatch) -> None:
    item_id = "a" * 64
    market_path = tmp_path / "market.json"
    raw_path = tmp_path / "raw.json"
    output_dir = tmp_path / "depth"
    market_path.write_text(json.dumps({
        "schema_version": 1,
        "snapshot_id": "market_20260101t000000z",
        "as_of": "2026-01-02T00:00:00Z",
        "provider": "coingecko",
        "instruments": [{
            "symbol": "BTC", "asset_type": "crypto", "currency": "USD", "price": 110,
            "observed_at": "2026-01-02T00:00:00Z", "source_item_ids": [item_id],
        }],
    }), encoding="utf-8")
    raw_path.write_text(json.dumps([{
        "item_id": item_id, "source_id": "news", "title": "Bitcoin rises", "summary": "",
    }]), encoding="utf-8")
    (tmp_path / "market-alignment-envelope.json").write_text(json.dumps({
        "schema_version": 1,
        "operation": "upsert_market_alignment",
        "run_id": "run_20260101t000000z",
        "workflow_run_id": "12345",
        "commit_sha": "b" * 40,
        "market_snapshot": json.loads(market_path.read_text()),
        "alignment": {
            "schema_version": 1, "alignment_id": "align_20260101t000000z",
            "topic_snapshot_id": "radar_20260101t000000z", "market_snapshot_id": "market_20260101t000000z",
            "generated_at": "2026-01-02T00:00:00Z", "partial": True, "coverage_ratio": 0,
            "topics": [],
        },
    }), encoding="utf-8")
    monkeypatch.setattr(
        financial_depth_cli,
        "fetch_market_history",
        lambda target, days: ([
            {"observed_at": "2026-01-01T00:00:00Z", "value": 100},
            {"observed_at": "2026-01-02T00:00:00Z", "value": 110},
        ], "coingecko", "https://example.com/history"),
    )

    financial_depth_cli.write_depth_artifact(
        market_path, raw_path, output_dir,
        target={"kind": "crypto", "symbol": "BTC"}, history_days=365,
    )

    alignment = json.loads((tmp_path / "market-alignment-envelope.json").read_text())
    depth_envelope = json.loads((tmp_path / "financial-depth-envelope.json").read_text())
    assert "financial_depth" not in alignment["market_snapshot"]
    assert depth_envelope["operation"] == "upsert_financial_depth"
    assert depth_envelope["market_snapshot_id"] == "market_20260101t000000z"
    assert depth_envelope["financial_depth"]["time_series"]["point_count"] == 2
