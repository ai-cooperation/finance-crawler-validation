export interface TargetEvidenceTarget {
  kind: string;
  symbol?: string;
  name?: string;
  market?: string;
  aliases?: unknown;
}

export interface TargetEvidenceItem {
  title: string;
  summary?: string;
  content?: string;
  kind?: string;
}

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
  const text = editorialOnly.has(String(item.kind ?? "").toLowerCase())
    ? String(item.title ?? "").toLowerCase()
    : `${item.title ?? ""} ${item.summary ?? ""}`.toLowerCase();
  return terms.some((term) => {
    if (term.includes(" ")) return text.includes(term);
    return new RegExp(`(^|[^a-z0-9])${escapeRegExp(term)}([^a-z0-9]|$)`).test(text);
  });
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
