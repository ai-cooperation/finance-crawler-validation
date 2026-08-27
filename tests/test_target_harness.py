from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from finance_crawler_poc.contracts import build_item_id
from finance_crawler_poc import target_harness
from finance_crawler_poc.target_harness import (
    artifact_prefix,
    build_target_question,
    build_target_official_source,
    build_target_run_id,
)
from finance_crawler_poc.target_profiles import get_target_profile


def test_artifact_prefix_is_target_scoped() -> None:
    assert artifact_prefix(get_target_profile("tsmc")) == "tsmc-2330tw"
    assert artifact_prefix(get_target_profile("delta")) == "delta-2308tw"
    assert artifact_prefix(get_target_profile("tatung")) == "tatung-2371tw"


def test_run_id_and_question_are_target_scoped() -> None:
    profile = get_target_profile("delta")
    assert build_target_run_id(profile, "20260822") == "20260822-delta-2308tw"
    assert build_target_question(profile) == "台達電的標的研究"


def test_official_source_adapter_contract_is_not_tsmc_specific() -> None:
    source = build_target_official_source(get_target_profile("delta"))
    assert source["kind"] == "twse_financial_statement"
    assert source["source_id"] == "twse_delta_financial_statement"
    assert "tsmc" not in str(source).casefold()


def test_run_target_research_completes_with_replayable_provider_fixtures(monkeypatch, tmp_path: Path) -> None:
    profile = get_target_profile("delta")
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / "raw-items.json").write_text("[]", encoding="utf-8")
    (frozen / "topic-snapshot.json").write_text(json.dumps({"as_of": "2026-08-21T00:00:00Z"}), encoding="utf-8")
    (frozen / "full-catalog-report.json").write_text(json.dumps({"normalized_item_count": 0}), encoding="utf-8")

    points = [
        {"observed_at": "2026-08-19T00:00:00Z", "value": 100.0},
        {"observed_at": "2026-08-21T00:00:00Z", "value": 110.0},
    ]

    def history(target, **kwargs):
        if target["symbol"] == "^TWII":
            return points, "yahoo_finance", "https://example.test/twii", "b" * 64
        return points, "yahoo_finance", "https://example.test/2308", "a" * 64

    def fundamentals(target, **kwargs):
        return {
            "status": "available",
            "provider": "fixture",
            "symbol": target["symbol"],
            "as_of": "2025-12-31",
            "currency": "TWD",
            "eps": 10.0,
            "revenue": 1000.0,
            "total_debt": 100.0,
            "cash": 50.0,
            "net_debt": 50.0,
            "source_ref": {"url": "https://example.test/fundamentals", "response_sha256": "c" * 64},
        }

    def peers(target, **kwargs):
        return {
            "status": "available",
            "provider": "fixture",
            "selection_rule": "fixture_peers_v1",
            "peer_set": [{"symbol": "2301.TW", "trailing_pe": 10.0}] * 3,
            "usable_peer_count": 3,
            "median_pe": 10.0,
            "period_alignment_status": "aligned",
            "period_alignment_basis": "fiscal_year_label",
            "target_period_key": "2025",
            "assumptions": {"selection_rule": "fixture_peers_v1", "minimum_usable_peers": 3},
        }

    item_id = build_item_id("google_news_delta_rss", "https://example.test/story", "d" * 64)
    news_item = {
        "item_id": item_id,
        "source_id": "google_news_delta_rss",
        "canonical_url": "https://example.test/story",
        "content_sha256": "d" * 64,
        "title": "Delta Electronics expands power systems",
        "summary": "Delta Electronics update",
        "published_at": "2026-08-20T00:00:00Z",
        "kind": "news",
        "evidence": {"publisher_verified": True, "publisher_id": "example_wire", "publisher_url": "https://example.test"},
    }
    official_body = b"{\"company\":\"Delta Electronics\"}"
    official = {
        "item_id": build_item_id("twse_delta_company_profile", "https://example.test/twse", hashlib.sha256(official_body).hexdigest()),
        "source_id": "twse_delta_company_profile",
        "publisher_id": "twse_openapi",
        "source_tier": "official",
        "independence_group": "twse_openapi",
        "transport": "json_api",
        "canonical_url": "https://example.test/twse",
        "content_sha256": hashlib.sha256(official_body).hexdigest(),
        "title": "Delta Electronics TWSE profile",
        "summary": "Official profile",
        "published_at": "2026-08-20T00:00:00Z",
        "raw_bytes": official_body,
    }

    monkeypatch.setattr(target_harness, "fetch_market_history", history)
    monkeypatch.setattr(target_harness, "fetch_yahoo_fundamentals", fundamentals)
    monkeypatch.setattr(target_harness, "fetch_yahoo_peer_valuation", peers)
    monkeypatch.setattr(target_harness, "fetch_yahoo_volume", lambda *args, **kwargs: {"status": "available", "provider": "fixture"})
    monkeypatch.setattr(target_harness, "fetch_yahoo_target_news", lambda *args, **kwargs: {
        "status": "available", "items": [news_item], "all_items": [news_item], "noise_item_count": 0, "attempts": [],
    })
    monkeypatch.setattr(target_harness, "_fetch_official", lambda *args, **kwargs: dict(official))
    monkeypatch.setattr(target_harness.httpx, "get", lambda url, **kwargs: httpx.Response(200, content=b"fixture", request=httpx.Request("GET", url)))

    result = target_harness.run_target_research(profile, frozen_dir=frozen, output_root=tmp_path / "output", days=365)

    assert result["metadata"]["run_id"] == "20260821-delta-2308tw"
    assert result["depth"]["time_series"]["point_count"] == 2
    assert result["depth"]["fundamentals"]["status"] == "available"
    assert (result["output_dir"] / "delta-2308tw-financial-depth.json").exists()
    assert (result["output_dir"] / "delta-2308tw-official.raw.json").exists()


def test_target_harness_validation_and_capture_helpers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="eight-digit"):
        build_target_run_id(get_target_profile("delta"), "2026-08")
    with pytest.raises(ValueError, match="official source"):
        build_target_official_source({"target_id": "broken"})
    assert build_target_question({"target": {"symbol": "2308.TW"}}) == "2308.TW 的標的研究"

    captures = [("https://example.test/raw", b"raw", 200, "application/json")]
    paths, metadata = target_harness._persist_captures(captures, tmp_path / "captures", root=tmp_path)
    assert paths and metadata[0]["status_code"] == 200
    official = {
        "canonical_url": "https://example.test/official",
        "source_id": "twse_delta_company_profile",
    }
    official_path, response_hash = target_harness._persist_official_capture(
        official,
        None,
        tmp_path,
        "delta-2308tw",
        [("https://example.test/official", b"official", 200, "application/json")],
        root=tmp_path,
    )
    assert official_path and response_hash


def test_target_harness_records_official_and_benchmark_failures(monkeypatch, tmp_path: Path) -> None:
    profile = get_target_profile("delta")
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / "raw-items.json").write_text("[]", encoding="utf-8")
    (frozen / "topic-snapshot.json").write_text(json.dumps({"as_of": "2026-08-21T00:00:00Z"}), encoding="utf-8")
    (frozen / "full-catalog-report.json").write_text("{}", encoding="utf-8")

    def history(target, **kwargs):
        if target["symbol"] == "^TWII":
            raise RuntimeError("benchmark unavailable")
        return ([{"observed_at": "2026-08-20T00:00:00Z", "value": 100.0}, {"observed_at": "2026-08-21T00:00:00Z", "value": 101.0}], "fixture", "https://example.test/target", "a" * 64)

    monkeypatch.setattr(target_harness, "fetch_market_history", history)
    monkeypatch.setattr(target_harness, "fetch_yahoo_fundamentals", lambda *args, **kwargs: {"status": "unavailable", "missing_fields": []})
    monkeypatch.setattr(target_harness, "fetch_yahoo_peer_valuation", lambda *args, **kwargs: {"status": "insufficient_data", "peer_set": []})
    monkeypatch.setattr(target_harness, "fetch_yahoo_volume", lambda *args, **kwargs: {"status": "unavailable"})
    monkeypatch.setattr(target_harness, "fetch_yahoo_target_news", lambda *args, **kwargs: {"status": "insufficient_data", "items": [], "all_items": [], "attempts": []})
    monkeypatch.setattr(target_harness, "fetch_target_community", lambda *args, **kwargs: {"status": "insufficient_data", "items": [], "all_items": [], "attempts": [], "coverage": {"status": "partial"}})
    monkeypatch.setattr(target_harness, "_fetch_official", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("official unavailable")))

    result = target_harness.run_target_research(profile, frozen_dir=frozen, output_root=tmp_path / "output")

    assert result["metadata"]["official"]["status"] == "unavailable"
    assert result["metadata"]["benchmark"]["error"]
    assert result["depth"]["status"] == "research_only"


def test_target_harness_rejects_missing_target_symbol(monkeypatch, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="target.symbol"):
        target_harness.run_target_research({"target_id": "broken", "target": {}}, frozen_dir=tmp_path, output_root=tmp_path)
