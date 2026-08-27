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

# Keep this version in the scope payload and in the Worker matcher.  It is a
# compatibility boundary: changing lexical identity rules without changing
# the version would make a frozen Research Pack impossible to replay.
MATCHER_VERSION = "target_identity_v3"

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
    kind = str(target.get("kind") or "").casefold()
    aliases = target.get("aliases")
    if isinstance(aliases, list):
        values.extend(value for value in aliases if isinstance(value, str))
    symbol = target.get("symbol")
    name = target.get("name")
    if isinstance(symbol, str):
        values.append(symbol)
    if isinstance(name, str):
        values.append(name)
    if kind not in {"equity", "company"}:
        # Crypto/ETF headlines commonly omit the ticker, so their controlled
        # asset-family vocabulary is useful.  Market, sector and industry
        # metadata are routing context, not lexical identity: values such as
        # ``global`` would otherwise select unrelated headlines.
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
        selected = [item for item in item_list if _matches_terms(item, terms, target)]
    selected_ids = list(dict.fromkeys(str(item["item_id"]) for item in selected if item.get("item_id")))
    source_ids = list(dict.fromkeys(str(item["source_id"]) for item in selected if item.get("source_id")))
    return selected, {
        "policy": "exact_identity_or_crypto_asset_family_v3" if target else "catalog_unscoped_v1",
        "matcher_version": MATCHER_VERSION if target else None,
        "target": dict(target) if target else None,
        "question": question,
        "identity_terms": list(terms),
        "input_item_count": len(item_list),
        "relevant_item_count": len(selected),
        "identity_match_item_count": sum(1 for item in selected if _matches_terms(item, terms, target)),
        "relevant_source_group_count": len(source_ids),
        "input_item_ids": selected_ids,
        "source_ids": source_ids,
    }


def _matches_terms(
    item: Mapping[str, Any],
    terms: tuple[str, ...],
    target: Mapping[str, Any] | None = None,
) -> bool:
    # The raw payload is retained for audit/replay, but it is not a relevance
    # field. RSS feeds and browser captures commonly contain navigation,
    # related-story rails, and footer links; matching those would turn a
    # generic homepage into false evidence for the requested asset.
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    kind = str(item.get("kind") or "").casefold()
    # A browser/HTML capture without an article timestamp is a page snapshot,
    # not a dated evidence item. Its summary often contains the site's global
    # navigation and must not make it target-relevant. Market/official data is
    # exempt because the provider timestamp is the data observation itself.
    if kind == "news" and not item.get("published_at"):
        return False
    if kind == "news" and re.search(r"\b(?:browser|html)\s+capture\b", title.casefold()):
        return False
    # News feeds are normalized to one item per editorial headline. Restrict
    # their identity match to the headline; feed descriptions frequently
    # concatenate several unrelated stories. Other typed data can use its
    # short summary because the provider controls that field.
    text_fields = (title,) if kind in {"news", "community", "developer_community"} else (title, summary)
    text = " ".join(text_fields).casefold()
    ambiguous_aliases = {
        str(value).casefold().strip()
        for value in (target or {}).get("ambiguous_aliases", [])
        if isinstance(value, str) and value.strip()
    }
    context_terms = tuple(
        str(value).casefold().strip()
        for value in (target or {}).get("identity_context_terms", [])
        if isinstance(value, str) and value.strip()
    )
    exclude_terms = tuple(
        str(value).casefold().strip()
        for value in (target or {}).get("identity_exclude_terms", [])
        if isinstance(value, str) and value.strip()
    )
    if exclude_terms and any(term in text for term in exclude_terms):
        return False
    for term in terms:
        if not term:
            continue
        if term in ambiguous_aliases and context_terms and not any(context in text for context in context_terms):
            continue
        if " " in term:
            if term in text:
                return True
        elif term.isdigit():
            # A ticker such as 2371 must not match a decimal, price, or
            # larger numeric token embedded in an unrelated headline.
            if re.search(rf"(?<![a-z0-9.]){re.escape(term)}(?![a-z0-9.])", text):
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            return True
    return False
