from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from finance_crawler_poc.contracts import build_item_id, validate_contract
from finance_crawler_poc.radar_cli import build_radar_artifacts
from finance_crawler_poc.radar_collect import RadarCollection
from finance_crawler_poc.radar_manifest import load_radar_manifest


ROOT = Path(__file__).resolve().parents[1]


def item(source_id: str, title: str, layer: str) -> dict[str, object]:
    content_hash = hashlib.sha256(title.encode()).hexdigest()
    url = f"https://example.com/{source_id}"
    return {
        "schema_version": 1,
        "item_id": build_item_id(source_id, url, content_hash),
        "source_id": source_id,
        "canonical_url": url,
        "title": title,
        "summary": "Synthetic for testing only",
        "content": title,
        "published_at": "2026-08-10T02:00:00Z",
        "collected_at": "2026-08-10T02:05:00Z",
        "transport": "rss",
        "kind": "news",
        "layer": layer,
        "content_sha256": content_hash,
        "rights": {"redistribution": "metadata_only", "retention_days": 1, "public_excerpt_chars": 0},
        "engagement": {"score": None, "comments": None, "shares": None, "likes": None},
        "evidence": {"route": "synthetic_test", "status_code": 200, "final_url": url, "extraction_method": "synthetic_test"},
    }


def test_artifact_builder_outputs_ingest_ready_contracts() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    items = (
        item("news_a", "Fed inflation interest rate", "news"),
        item("social_a", "Bitcoin crypto rally", "social"),
        item("news_b", "Nvidia AI semiconductor", "news"),
    )
    checkpoints = tuple(
        {
            "source_id": source.source_id,
            "status": "success",
            "last_successful_crawl": "2026-08-10T02:05:00Z",
            "last_article_date": None,
            "cursor": None,
        }
        for source in manifest.sources
    )
    results = tuple(
        {
            "source_id": source.source_id,
            "transport": source.transport,
            "status": "success",
            "status_code": 200,
            "route": "synthetic_test",
            "item_count": 1,
            "request_url": source.canonical_url,
            "catchup_strategy": source.catchup_strategy,
            "published_since": None,
            "error": "",
        }
        for source in manifest.sources
    )
    collection = RadarCollection(items=items, checkpoints=checkpoints, source_results=results)

    envelope, snapshot, report = build_radar_artifacts(
        manifest,
        collection,
        workflow_run_id="31309377786",
        commit_sha="d" * 40,
        now=datetime(2026, 8, 10, 2, 5, tzinfo=timezone.utc),
        manifest_sha256="a" * 64,
    )

    validate_contract("ingest-envelope", envelope)
    validate_contract("topic-snapshot", snapshot)
    assert report["accepted"] is True
    assert report["successful_sources"] == 15
    assert report["topics"] == 3
    assert len(report["checkpoints"]) == 15
    assert report["checkpoints"][0]["source_id"] == manifest.sources[0].source_id
    assert envelope["snapshot_id"] == snapshot["snapshot_id"]
    assert envelope["commit_sha"] == "d" * 40


def test_artifact_report_separates_transport_success_from_empty_windows() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")
    items = (
        item("news_a", "Fed inflation interest rate", "news"),
        item("social_a", "Bitcoin crypto rally", "social"),
        item("news_b", "Nvidia AI semiconductor", "news"),
    )
    checkpoints = tuple(
        {
            "source_id": source.source_id,
            "status": "success",
            "last_successful_crawl": "2026-08-10T02:05:00Z",
            "last_article_date": None,
            "cursor": None,
        }
        for source in manifest.sources
    )
    results = tuple(
        {
            "source_id": source.source_id,
            "transport": source.transport,
            "status": "success",
            "status_code": 200,
            "route": "synthetic_test",
            "item_count": 0 if source is manifest.sources[0] else 1,
            "request_url": source.canonical_url,
            "catchup_strategy": source.catchup_strategy,
            "published_since": "2026-08-10T02:00:00Z",
            "error": "",
        }
        for source in manifest.sources
    )
    _, _, report = build_radar_artifacts(
        manifest,
        RadarCollection(items=items, checkpoints=checkpoints, source_results=results),
        workflow_run_id="31309377786",
        commit_sha="d" * 40,
        now=datetime(2026, 8, 10, 2, 5, tzinfo=timezone.utc),
        manifest_sha256="a" * 64,
    )

    assert report["successful_sources"] == 15
    assert report["content_sources"] == 14
    assert report["empty_sources"] == [manifest.sources[0].source_id]
