from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from finance_crawler_poc.news_probe import NewsBrandResult, NewsEndpointAttempt


_SAFE_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


def write_news_raw_artifacts(
    results: list[NewsBrandResult],
    output_dir: Path,
    *,
    generated_at: str | None = None,
) -> Path:
    """Write exact response bodies plus a machine-readable capture manifest.

    This is opt-in because ordinary capability reports intentionally contain
    only hashes and short previews.  Every attempted endpoint with a non-empty
    response gets a raw file, including blocked/invalid responses, while
    timeout and connection failures remain manifest-only records.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_root = output_dir / "news-raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    payload_records: list[dict[str, object]] = []

    for brand in results:
        _validate_component(brand.brand_id, "brand_id")
        for attempt in brand.endpoint_attempts:
            _validate_component(attempt.endpoint_id, "endpoint_id")
            content = attempt.content
            payload_path: str | None = None
            computed_hash = ""
            if content:
                relative = Path("news-raw") / brand.brand_id / f"{attempt.endpoint_id}.raw"
                target = output_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(target, content)
                payload_path = relative.as_posix()
                computed_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

            payload_records.append(
                {
                    "brand_id": brand.brand_id,
                    "brand_class": brand.brand_class,
                    "region": brand.region,
                    "endpoint_id": attempt.endpoint_id,
                    "transport": attempt.transport,
                    "url": attempt.url,
                    "final_url": attempt.final_url,
                    "executor_id": attempt.executor_id,
                    "outcome": attempt.outcome,
                    "status_code": attempt.status_code,
                    "content_chars": len(content),
                    "content_sha256": computed_hash,
                    "reported_content_sha256": attempt.content_sha256,
                    "hash_matches_report": bool(
                        content and computed_hash == attempt.content_sha256
                    ),
                    "content_type": attempt.content_type,
                    "payload_path": payload_path,
                    "error": attempt.error,
                }
            )

    written = sum(record["payload_path"] is not None for record in payload_records)
    manifest = {
        "schema_version": 1,
        "capture_type": "news_endpoint_raw_payload",
        "generated_at": generated_at,
        "summary": {
            "brands": len(results),
            "endpoint_attempts": len(payload_records),
            "payloads_written": written,
            "payloads_missing": len(payload_records) - written,
        },
        "payloads": payload_records,
    }
    manifest_path = output_dir / "news-raw-manifest.json"
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest_path


def _validate_component(value: str, field: str) -> None:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"unsafe {field}: {value!r}")


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
