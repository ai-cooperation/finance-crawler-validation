"""Target-scoped evidence selection.

The public catalogue remains complete, but downstream research must not treat
every finance headline as evidence for the requested instrument.  This module
keeps that boundary deterministic and auditable: identity aliases and the
target's asset family are used, while generic question words are never used as
relevance keys.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


STOPWORDS = frozenset({
    "what", "are", "the", "and", "for", "with", "from", "this", "that",
    "current", "recent", "latest", "drivers", "driver", "risks", "risk",
    "market", "structure", "signals", "analysis", "price", "outlook",
})

ASSET_TERMS: dict[str, tuple[str, ...]] = {
    "crypto": ("bitcoin", "btc", "crypto", "cryptocurrency", "digital asset", "ethereum", "stablecoin"),
    "equity": ("stock", "equity", "shares", "earnings"),
    "etf": ("etf", "exchange traded fund", "fund"),
    "company": ("company",),
}


def target_identity_terms(target: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not target:
        return ()
    values: list[str] = []
    for key in ("symbol", "name", "market", "sector", "industry"):
        value = target.get(key)
        if isinstance(value, str):
            values.append(value)
            values.extend(re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", value))
    kind = str(target.get("kind") or "").casefold()
    values.extend(ASSET_TERMS.get(kind, ()))
    # BTC/ETH and other short tickers need an exact token boundary.  Preserve
    # longer aliases as phrases; all values are de-duplicated deterministically.
    terms: list[str] = []
    for value in values:
        normalized = value.casefold().strip()
        if normalized and normalized not in terms:
            terms.append(normalized)
    return tuple(terms)


def select_target_items(
    items: Iterable[Mapping[str, Any]],
    *,
    target: Mapping[str, Any] | None = None,
    question: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return target evidence and an auditable scope summary.

    No target means the catalogue is intentionally unscoped.  With a target,
    identity aliases are required; asset-family terms are accepted for broad
    instruments such as BTC because market reporting commonly omits the
    ticker while naming the asset.  ``question`` is recorded for provenance
    but its generic words do not make an item relevant.
    """

    item_list = [dict(item) for item in items]
    terms = target_identity_terms(target)
    if target is None:
        selected = item_list
    else:
        selected = [item for item in item_list if _matches_terms(item, terms)]
    selected_ids = list(dict.fromkeys(str(item["item_id"]) for item in selected if item.get("item_id")))
    source_ids = list(dict.fromkeys(str(item["source_id"]) for item in selected if item.get("source_id")))
    return selected, {
        "policy": "target_identity_or_asset_family_v1" if target else "catalog_unscoped_v1",
        "target": dict(target) if target else None,
        "question": question,
        "identity_terms": list(terms),
        "input_item_count": len(item_list),
        "relevant_item_count": len(selected),
        "relevant_source_group_count": len(source_ids),
        "input_item_ids": selected_ids,
        "source_ids": source_ids,
    }


def _matches_terms(item: Mapping[str, Any], terms: tuple[str, ...]) -> bool:
    text = " ".join(str(item.get(field) or "") for field in ("title", "summary", "content")).casefold()
    for term in terms:
        if not term:
            continue
        if " " in term:
            if term in text:
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            return True
    return False
