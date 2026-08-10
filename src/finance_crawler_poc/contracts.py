from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


CONTRACT_NAMES = frozenset(
    {
        "audit-event",
        "ingest-envelope",
        "market-snapshot",
        "raw-item",
        "research-report",
        "source-record",
        "topic-snapshot",
    }
)
SCHEMA_DIRECTORY = Path(__file__).resolve().parents[2] / "schemas"
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class ContractValidationError(ValueError):
    """Raised when a versioned boundary payload violates its JSON Schema."""


@lru_cache(maxsize=len(CONTRACT_NAMES))
def load_contract(name: str) -> dict[str, Any]:
    if name not in CONTRACT_NAMES:
        raise ContractValidationError(f"unknown contract: {name}")
    path = SCHEMA_DIRECTORY / f"{name}.schema.json"
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"cannot load contract {name}: {exc}") from exc
    Draft202012Validator.check_schema(schema)
    return schema


def validate_contract(name: str, payload: object) -> None:
    validator = Draft202012Validator(
        load_contract(name),
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "$"
    raise ContractValidationError(f"{name} {location}: {error.message}")


def build_item_id(
    source_id: str,
    canonical_url: str,
    content_sha256: str,
) -> str:
    values = (source_id.strip(), canonical_url.strip(), content_sha256.strip())
    if not values[0] or not values[1]:
        raise ValueError("source_id and canonical_url are required for item id")
    if not SHA256_PATTERN.fullmatch(values[2]):
        raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
    material = "\0".join(values).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
