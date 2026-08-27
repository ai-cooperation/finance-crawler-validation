import { describe, expect, it } from "vitest";

import { targetMatchesEvidence } from "../src/research-jobs";
import { targetEvidenceMatch } from "../src/research-agent";

const equity = { kind: "equity" as const, symbol: "2330.TW", name: "Taiwan Semiconductor Manufacturing Company Limited" };
const crypto = { kind: "crypto" as const, symbol: "BTC", name: "Bitcoin", market: "global" };

function item(title: string, summary = "", content = "") {
  return {
    title,
    summary,
    content,
    kind: "news",
  } as never;
}

describe("target evidence identity policy", () => {
  it("does not treat generic equity terms as issuer evidence", () => {
    const generic = item("Tesla stock jumps after earnings", "Shares rise as earnings beat estimates");
    expect(targetMatchesEvidence(generic, equity)).toBe(false);
    expect(targetEvidenceMatch(generic, equity)).toBe(false);
  });

  it("accepts an issuer alias in the editorial title", () => {
    const issuer = item("TSMC expands advanced packaging capacity");
    const aliasTarget = { ...equity, name: "TSMC" };
    expect(targetMatchesEvidence(issuer, aliasTarget)).toBe(true);
    expect(targetEvidenceMatch(issuer, aliasTarget)).toBe(true);
  });

  it("does not use an unrelated raw payload fragment for news identity", () => {
    const page = item("Market update", "", "Related links mention TSMC and other companies");
    expect(targetMatchesEvidence(page, equity)).toBe(false);
    expect(targetEvidenceMatch(page, equity)).toBe(false);
  });

  it("does not treat the generic global market label as BTC evidence", () => {
    const unrelated = item("Global AI policy outlook for financial markets");
    const bitcoin = item("Bitcoin market structure and ETF flows");
    expect(targetMatchesEvidence(unrelated, crypto)).toBe(false);
    expect(targetEvidenceMatch(unrelated, crypto)).toBe(false);
    expect(targetMatchesEvidence(bitcoin, crypto)).toBe(true);
    expect(targetEvidenceMatch(bitcoin, crypto)).toBe(true);
  });
});
