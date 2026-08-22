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
