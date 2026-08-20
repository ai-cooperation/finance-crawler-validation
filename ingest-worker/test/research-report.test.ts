import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { createHandler } from "../src/handler";
import {
  ingestItems,
  ingestMarketAlignment,
  ingestResearchReport,
  ingestTradingAgentsPlan,
  publishSnapshot,
} from "../src/storage";


const ITEM_ID = "a".repeat(64);
const CONTENT_HASH = "b".repeat(64);
const COMMIT_SHA = "d".repeat(40);
const RUN_ID = "run_20260820t035848z";
const SNAPSHOT_ID = "radar_20260820t035848z";
const ALIGNMENT_ID = "align_20260820t035900z";
const PLAN_ID = "plan_20260820t040000z";

function envelope() {
  return {
    schema_version: 1,
    operation: "upsert_items",
    run_id: RUN_ID,
    workflow_run_id: "32330093877",
    commit_sha: COMMIT_SHA,
    snapshot_id: SNAPSHOT_ID,
    source_manifest_hash: "c".repeat(64),
    collected_at: "2026-08-20T03:58:48Z",
    items: [{
      schema_version: 1,
      item_id: ITEM_ID,
      source_id: "coingecko_markets_api",
      canonical_url: "https://www.coingecko.com/en/coins/bitcoin",
      title: "CoinGecko Top Markets: Bitcoin (BTC)",
      summary: "",
      content: "{\"current_price\":64000,\"id\":\"bitcoin\"}",
      published_at: "2026-08-20T03:55:20Z",
      collected_at: "2026-08-20T03:58:48Z",
      transport: "json_api",
      kind: "market_data",
      layer: "market",
      content_sha256: CONTENT_HASH,
      rights: { redistribution: "metadata_only", retention_days: 7, public_excerpt_chars: 0 },
      engagement: { score: null, comments: null, shares: null, likes: null },
      evidence: {
        route: "direct",
        status_code: 200,
        final_url: "https://api.coingecko.com/api/v3/coins/markets",
        extraction_method: "coingecko_markets",
      },
    }],
    checkpoints: [{
      source_id: "coingecko_markets_api",
      status: "success",
      last_successful_crawl: "2026-08-20T03:58:48Z",
      last_article_date: "2026-08-20T03:55:20Z",
      cursor: null,
    }],
  };
}

function topicSnapshot() {
  return {
    schema_version: 1,
    snapshot_id: SNAPSHOT_ID,
    run_id: RUN_ID,
    as_of: "2026-08-20T03:58:48Z",
    partial: true,
    failed_sources: ["bogleheads_investing_browser"],
    input_item_ids: [ITEM_ID],
    topics: [{
      topic_id: "digital_assets",
      label: "Digital assets",
      score: 6,
      item_count: 1,
      source_count: 1,
      news_count: 0,
      social_count: 1,
      evidence_ids: [ITEM_ID],
      divergence: { direction: "insufficient_data", magnitude: null },
    }, {
      topic_id: "ai_semiconductors",
      label: "AI semiconductors",
      score: 4,
      item_count: 1,
      source_count: 1,
      news_count: 1,
      social_count: 0,
      evidence_ids: [ITEM_ID],
      divergence: { direction: "insufficient_data", magnitude: null },
    }],
  };
}

function alignmentEnvelope() {
  return {
    schema_version: 1,
    operation: "upsert_market_alignment",
    run_id: RUN_ID,
    workflow_run_id: "32330093877",
    commit_sha: COMMIT_SHA,
    market_snapshot: {
      schema_version: 1,
      snapshot_id: "market_20260820t035900z",
      as_of: "2026-08-20T03:59:00Z",
      provider: "coingecko",
      instruments: [{
        symbol: "BTC",
        asset_type: "crypto",
        currency: "USD",
        price: 64000,
        observed_at: "2026-08-20T03:55:20Z",
        change_24h_pct: 4,
        market_cap: 1000000,
        source_item_ids: [ITEM_ID],
      }],
    },
    alignment: {
      schema_version: 1,
      alignment_id: ALIGNMENT_ID,
      topic_snapshot_id: SNAPSHOT_ID,
      market_snapshot_id: "market_20260820t035900z",
      generated_at: "2026-08-20T03:59:00Z",
      partial: true,
      coverage_ratio: 1,
      topics: [{
        topic_id: "digital_assets",
        label: "Digital assets",
        topic_score: 6,
        market_direction: "positive",
        instrument_count: 1,
        symbols: ["BTC"],
        mean_change_24h_pct: 4,
        evidence_ids: [ITEM_ID],
      }],
    },
  };
}

function planEnvelope() {
  return {
    schema_version: 1,
    operation: "upsert_tradingagents_plan",
    run_id: RUN_ID,
    workflow_run_id: "32330093877",
    commit_sha: COMMIT_SHA,
    plan: {
      schema_version: 1,
      plan_id: PLAN_ID,
      topic_snapshot_id: SNAPSHOT_ID,
      alignment_id: ALIGNMENT_ID,
      created_at: "2026-08-20T04:00:00Z",
      decision: "eligible",
      skip_reason: "none",
      budget: {
        max_topics: 3,
        max_claims_per_topic: 6,
        max_tokens: 12000,
        max_usd: 0,
        model: "tradingagents-deferred",
      },
      topics: [{
        topic_id: "digital_assets",
        label: "Digital assets",
        score: 6,
        decision: "run",
        reason: "top_ranked",
        market_direction: "positive",
        evidence_ids: [ITEM_ID],
      }],
    },
  };
}

function reportEnvelope() {
  const claim = {
    text: "Synthetic for storage contract testing only",
    confidence: 0.5,
    evidence_ids: [ITEM_ID],
  };
  return {
    schema_version: 1,
    operation: "upsert_research_report",
    run_id: RUN_ID,
    workflow_run_id: "32330093877",
    commit_sha: COMMIT_SHA,
    report: {
      schema_version: 1,
      report_id: "report_20260820t040500z",
      topic_snapshot_id: SNAPSHOT_ID,
      plan_id: PLAN_ID,
      alignment_id: ALIGNMENT_ID,
      market_snapshot_id: "market_20260820t035900z",
      topic_id: "digital_assets",
      generated_at: "2026-08-20T04:05:00Z",
      expires_at: "2026-08-21T04:05:00Z",
      model: "synthetic-test-model",
      agent_version: "tradingagents-deferred-v1",
      second_opinion: true,
      evidence_ids: [ITEM_ID],
      bull_case: [claim],
      bear_case: [claim],
      risk_view: [claim],
    },
  };
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM research_reports"),
    env.DB.prepare("DELETE FROM tradingagents_plans"),
    env.DB.prepare("DELETE FROM topic_market_alignments"),
    env.DB.prepare("DELETE FROM market_snapshots"),
    env.DB.prepare("DELETE FROM current_snapshot"),
    env.DB.prepare("DELETE FROM topic_snapshots"),
    env.DB.prepare("DELETE FROM run_items"),
    env.DB.prepare("DELETE FROM raw_items"),
    env.DB.prepare("DELETE FROM source_state"),
    env.DB.prepare("DELETE FROM audit_events"),
    env.DB.prepare("DELETE FROM ingest_receipts"),
    env.DB.prepare("DELETE FROM operational_alerts"),
    env.DB.prepare("DELETE FROM soak_observations"),
    env.DB.prepare("DELETE FROM run_admissions"),
    env.DB.prepare("DELETE FROM runs"),
  ]);
  const objects = await env.RAW_OBJECTS.list();
  if (objects.objects.length > 0) {
    await env.RAW_OBJECTS.delete(objects.objects.map((object) => object.key));
  }
});

describe("research report ingest", () => {
  async function arrange() {
    await ingestItems(env, envelope(), new Date("2026-08-20T03:58:50Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-20T03:58:55Z"));
    await ingestMarketAlignment(env, alignmentEnvelope(), new Date("2026-08-20T03:59:05Z"));
    await ingestTradingAgentsPlan(env, planEnvelope(), new Date("2026-08-20T04:00:00Z"));
  }

  it("persists a private report and replays it idempotently", async () => {
    await arrange();
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
      now: () => new Date("2026-08-20T04:05:00Z"),
    });
    const request = () => new Request("https://ingest.example/v1/ingest/research-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reportEnvelope()),
    });
    const first = await handler.fetch(request(), env);
    expect(first.status).toBe(201);
    expect(await first.json()).toMatchObject({ report_id: "report_20260820t040500z", replayed: false });
    const replay = await handler.fetch(request(), env);
    expect(replay.status).toBe(200);
    expect(await replay.json()).toMatchObject({ replayed: true });
    const row = await env.DB.prepare("SELECT report_id, topic_id FROM research_reports").first();
    expect(row).toEqual({ report_id: "report_20260820t040500z", topic_id: "digital_assets" });
    expect(await env.RAW_OBJECTS.get("reports/report_20260820t040500z.json")).not.toBeNull();
  });

  it("rejects a report topic that was not selected by the plan", async () => {
    await arrange();
    const invalid = reportEnvelope();
    invalid.report.topic_id = "ai_semiconductors";
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
    });
    const response = await handler.fetch(new Request(
      "https://ingest.example/v1/ingest/research-report",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(invalid),
      },
    ), env);
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ error: "topic_not_planned" });
  });

  it("fails closed when report expiry is not later than generation", async () => {
    const invalid = reportEnvelope();
    invalid.report.expires_at = invalid.report.generated_at;
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
    });
    const response = await handler.fetch(new Request(
      "https://ingest.example/v1/ingest/research-report",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(invalid),
      },
    ), env);
    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ error: "invalid_payload" });
  });

  it("rejects evidence that is outside the published run", async () => {
    await arrange();
    const invalid = reportEnvelope();
    invalid.report.evidence_ids = ["e".repeat(64)];
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
    });
    const response = await handler.fetch(new Request(
      "https://ingest.example/v1/ingest/research-report",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(invalid),
      },
    ), env);
    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ error: "evidence_not_in_run" });
  });

  it("rejects a report that is not explicitly marked as a second opinion", async () => {
    const invalid = reportEnvelope();
    invalid.report.second_opinion = false;
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
    });
    const response = await handler.fetch(new Request(
      "https://ingest.example/v1/ingest/research-report",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(invalid),
      },
    ), env);
    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ error: "invalid_payload" });
  });

  it("rejects a report whose plan has not been persisted", async () => {
    await arrange();
    const invalid = reportEnvelope();
    invalid.report.plan_id = "plan_20260820t040001z";
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
    });
    const response = await handler.fetch(new Request(
      "https://ingest.example/v1/ingest/research-report",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(invalid),
      },
    ), env);
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ error: "plan_not_found" });
  });
});
