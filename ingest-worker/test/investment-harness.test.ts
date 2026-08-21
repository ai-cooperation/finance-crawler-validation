import { describe, expect, it } from "vitest";

import { buildInvestmentHarnessArtifacts } from "../src/investment-harness";

describe("investment H3 MVP harness", () => {
  it("emits replayable signals and only internal action tasks", async () => {
    const artifacts = await buildInvestmentHarnessArtifacts({
      target: { kind: "crypto", symbol: "BTC" },
      snapshot_id: "radar_20260821t010000z",
      generated_at: "2026-08-21T01:00:00Z",
      collection_scope: "full_catalog",
      input_item_count: 200,
      input_source_count: 100,
      topics: [{
        topic_id: "digital_assets",
        score: 8,
        item_count: 12,
        source_count: 8,
        evidence_ids: ["a".repeat(64)],
        divergence: { direction: "social_leads", magnitude: 0.4 },
      }],
    });

    expect(artifacts.harness.collection_scope).toBe("full_catalog");
    expect(artifacts.signals.signals).toHaveLength(2);
    expect(artifacts.signals.signals.every((signal) => signal.evidence_ids.length > 0)).toBe(true);
    expect(artifacts.action_tasks.map((task) => task.action_type)).toEqual(["build_research_pack", "open_review"]);
    expect(artifacts.action_tasks.every((task) => task.side_effect_level === "internal_write")).toBe(true);
    expect(artifacts.action_receipts.every((receipt) => /^[a-f0-9]{64}$/.test(receipt.input_sha256))).toBe(true);
  });
});
