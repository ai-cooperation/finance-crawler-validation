from __future__ import annotations

import json
from pathlib import Path

from finance_crawler_poc.h3_assemble import assemble_h3_artifacts


def _item(item_id: str, source_id: str, title: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "item_id": item_id,
        "source_id": source_id,
        "canonical_url": f"https://example.com/{item_id}",
        "title": title,
        "summary": title,
        "content": title,
        "published_at": None,
        "collected_at": "2026-08-21T01:00:00Z",
        "transport": "rss",
        "kind": "news",
        "layer": "news",
        "content_sha256": "a" * 64,
        "rights": {"redistribution": "metadata_only", "retention_days": 365, "public_excerpt_chars": 0},
        "engagement": {"score": None, "comments": None, "shares": None, "likes": None},
        "evidence": {"route": "rss", "status_code": 200, "final_url": f"https://example.com/{item_id}", "extraction_method": "test"},
    }


def test_assemble_merges_news_and_radar_without_dropping_full_catalog_metadata(tmp_path: Path) -> None:
    news = tmp_path / "news-ingest.json"
    news.write_text(json.dumps({
        "run_id": "run_20260821t010000z",
        "snapshot_id": "radar_20260821t010000z",
        "source_manifest_hash": "b" * 64,
        "items": [_item("1" * 64, "bloomberg", "Bitcoin market update")],
        "checkpoints": [{"source_id": "bloomberg", "status": "success", "last_successful_crawl": "2026-08-21T01:00:00Z", "last_article_date": None, "cursor": None}],
        "endpoint_attempt_count": 166,
        "collection_source_group_count": 120,
    }), encoding="utf-8")
    radar_dir = tmp_path / "radar"
    radar_dir.mkdir()
    (radar_dir / "ingest-envelope.json").write_text(json.dumps({
        "run_id": "run_20260821t010000z",
        "snapshot_id": "radar_20260821t010000z",
        "source_manifest_hash": "c" * 64,
        "collected_at": "2026-08-21T01:00:00Z",
        "items": [_item("2" * 64, "coingecko_markets_api", "Bitcoin price")],
        "checkpoints": [{"source_id": "coingecko_markets_api", "status": "success", "last_successful_crawl": "2026-08-21T01:00:00Z", "last_article_date": None, "cursor": None}],
    }), encoding="utf-8")
    (radar_dir / "topic-snapshot.json").write_text(json.dumps({"failed_sources": []}), encoding="utf-8")

    result = assemble_h3_artifacts(news, radar_dir, tmp_path / "out", target={"kind": "crypto", "symbol": "BTC"})

    assert result["collection_scope"] == "full_catalog"
    assert result["collection_source_group_count"] == 2
    assert result["endpoint_attempt_count"] == 167
    assert result["normalized_item_count"] == 2
    assert len(result["items"]) == 2
    assert result["checkpoints"][-1]["source_id"] == "coingecko_markets_api"
    persisted_envelope = json.loads((tmp_path / "out" / "ingest-envelope.json").read_text(encoding="utf-8"))
    assert "collection_scope" not in persisted_envelope
    assert set(persisted_envelope) == {
        "schema_version", "operation", "run_id", "workflow_run_id", "commit_sha",
        "snapshot_id", "source_manifest_hash", "collected_at", "items", "checkpoints",
    }
