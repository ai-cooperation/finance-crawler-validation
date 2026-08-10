import asyncio
from pathlib import Path

import pytest

from finance_crawler_poc import cli
from finance_crawler_poc.models import Manifest, Outcome, ProbeResult, Source


class FakeAdapter:
    instances: list["FakeAdapter"] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.closed = False
        self.instances.append(self)

    async def close(self) -> None:
        self.closed = True


def probe_result(source: Source) -> ProbeResult:
    return ProbeResult(
        source_id=source.id,
        name=source.name,
        topic=source.topic,
        transport=source.transport,
        url=source.url,
        outcome=Outcome.SUCCESS,
        status_code=200,
        attempts=1,
        elapsed_ms=1,
        content_chars=10,
        content_sha256="a" * 64,
        preview="evidence",
        error="",
    )


def test_run_routes_all_transports_and_closes_adapters(monkeypatch, tmp_path: Path) -> None:
    sources = tuple(
        Source(
            id=f"source_{transport}",
            name=transport,
            topic="finance",
            transport=transport,
            url=f"https://example.com/{transport}",
        )
        for transport in ("json_api", "rss", "browser")
    )
    captured: dict[str, object] = {}

    async def fake_probe(
        source: Source, adapter: FakeAdapter, *, run_index: int
    ) -> ProbeResult:
        captured.setdefault(source.id, adapter)
        base = probe_result(source)
        return ProbeResult(**{**base.to_dict(), "outcome": Outcome.SUCCESS, "run_index": run_index})

    def fake_write(results, output, *, generated_at):
        captured["results"] = results
        captured["output"] = output
        captured["generated_at"] = generated_at

    async def no_sleep(_: float) -> None:
        return None

    FakeAdapter.instances = []
    monkeypatch.setattr(cli, "load_manifest", lambda _: Manifest(version=1, sources=sources))
    monkeypatch.setattr(cli, "HttpAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "Crawl4AIAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "probe_source", fake_probe)
    monkeypatch.setattr(cli, "write_reports", fake_write)
    monkeypatch.setattr(cli.asyncio, "sleep", no_sleep)

    results = asyncio.run(cli.run(Path("sources.yaml"), tmp_path, repetitions=2))

    assert [item.source_id for item in results] == [source.id for source in sources] * 2
    assert [item.run_index for item in results] == [1, 1, 1, 2, 2, 2]
    assert captured["output"] == tmp_path
    assert str(captured["generated_at"]).endswith("Z")
    assert len(FakeAdapter.instances) == 2
    assert all(adapter.closed for adapter in FakeAdapter.instances)
    assert captured["source_json_api"] is captured["source_rss"]
    assert captured["source_browser"] is not captured["source_json_api"]


def test_run_rejects_repetitions_outside_bounded_range(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="repetitions"):
        asyncio.run(cli.run(Path("sources.yaml"), tmp_path, repetitions=4))
