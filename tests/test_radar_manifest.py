from __future__ import annotations

from pathlib import Path

import pytest

from finance_crawler_poc.radar_manifest import RadarManifestError, load_radar_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_vertical_slice_manifest_has_balanced_unique_sources() -> None:
    manifest = load_radar_manifest(ROOT / "radar-sources.yaml")

    assert len(manifest.sources) == 15
    assert len({source.source_id for source in manifest.sources}) == 15
    assert manifest.minimum_successful_sources == 12
    assert manifest.maximum_items_per_run == 60
    assert {
        transport: sum(source.transport == transport for source in manifest.sources)
        for transport in ("rss", "json_api", "browser")
    } == {"rss": 5, "json_api": 7, "browser": 3}


def test_manifest_rejects_private_network_targets(tmp_path: Path) -> None:
    manifest_path = tmp_path / "radar.yaml"
    valid_manifest = (ROOT / "radar-sources.yaml").read_text(encoding="utf-8")
    manifest_path.write_text(
        valid_manifest.replace(
            "https://www.federalreserve.gov/feeds/press_all.xml",
            "https://127.0.0.1/admin",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RadarManifestError, match="public host"):
        load_radar_manifest(manifest_path)
