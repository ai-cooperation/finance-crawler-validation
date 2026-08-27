export interface TargetEvidenceTarget {
  kind: string;
  symbol?: string;
  name?: string;
  market?: string;
  aliases?: unknown;
  ambiguous_aliases?: unknown;
  identity_context_terms?: unknown;
  identity_exclude_terms?: unknown;
}

export interface TargetEvidenceItem {
  title: string;
  summary?: string;
  content?: string;
  kind?: string;
  published_at?: string | null;
}

export const TARGET_MATCHER_VERSION = "target_identity_v3";

/**
 * Match evidence to an issuer identity, not to generic asset vocabulary.
 * News titles are the editorial identity field; raw feed fragments and
 * navigation text are deliberately excluded from the news match.
 */
export function matchesTargetEvidence(
  item: TargetEvidenceItem,
  target: TargetEvidenceTarget,
): boolean {
  const aliases = Array.isArray(target.aliases)
    ? target.aliases.filter((value): value is string => typeof value === "string")
    : [];
  const terms = [target.symbol, target.name, ...aliases]
    .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
    .map((value) => value.trim().toLowerCase());
  if (target.kind === "crypto") terms.push("bitcoin", "btc", "crypto", "cryptocurrency", "digital asset", "ethereum", "stablecoin");
  if (target.kind === "etf") terms.push("etf", "exchange traded fund", "fund");
  if (!terms.length) return false;
  const editorialOnly = new Set(["news", "community", "developer_community"]);
  const itemKind = String(item.kind ?? "").toLowerCase();
  if (itemKind === "news" && "published_at" in item && !item.published_at) return false;
  const text = editorialOnly.has(String(item.kind ?? "").toLowerCase())
    ? String(item.title ?? "").toLowerCase()
    : `${item.title ?? ""} ${item.summary ?? ""}`.toLowerCase();
  const ambiguousAliases = new Set(
    Array.isArray(target.ambiguous_aliases)
      ? target.ambiguous_aliases.filter((value): value is string => typeof value === "string").map((value) => value.trim().toLowerCase())
      : [],
  );
  const contextTerms = Array.isArray(target.identity_context_terms)
    ? target.identity_context_terms.filter((value): value is string => typeof value === "string").map((value) => value.trim().toLowerCase())
    : [];
  const excludeTerms = Array.isArray(target.identity_exclude_terms)
    ? target.identity_exclude_terms.filter((value): value is string => typeof value === "string").map((value) => value.trim().toLowerCase())
    : [];
  if (excludeTerms.some((term) => term.length > 0 && text.includes(term))) return false;
  return terms.some((term) => {
    if (ambiguousAliases.has(term) && contextTerms.length > 0 && !contextTerms.some((context) => text.includes(context))) return false;
    if (term.includes(" ")) return text.includes(term);
    if (/^\d+$/.test(term)) return new RegExp(`(^|[^a-z0-9.])${escapeRegExp(term)}([^a-z0-9.]|$)`).test(text);
    return new RegExp(`(^|[^a-z0-9])${escapeRegExp(term)}([^a-z0-9]|$)`).test(text);
  });
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
