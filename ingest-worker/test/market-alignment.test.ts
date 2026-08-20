import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { createHandler } from "../src/handler";
import { ingestItems, publishSnapshot } from "../src/storage";


const ITEM_ID = "a".repeat(64);
const CONTENT_HASH = "b".repeat(64);
const COMMIT_SHA = "d".repeat(40);
const RUN_ID = "run_20260820t035848z";
const SNAPSHOT_ID = "radar_20260820t035848z";

function marketItem() {
  return {
    schema_version: 1,
    item_id: ITEM_ID,
    source_id: "coingecko_markets_api",
    canonical_url: "https://www.coingecko.com/en/coins/bitcoin",
    title: "CoinGecko Top Markets: Bitcoin (BTC)",
    summary: "",
    content: '{"current_price":64000,"id":"bitcoin","last_updated":"2026-08-20T03:55:20Z","market_cap":1000000,"name":"Bitcoin","price_change_percentage_24h":4,"symbol":"btc"}',
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
  };
}

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
    items: [marketItem()],
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
    }],
  };
}

function marketSnapshot() {
  return {
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
  };
}

function alignment() {
  return {
    schema_version: 1,
    alignment_id: "align_20260820t035900z",
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
  };
}

function ingestPayload() {
  return {
    schema_version: 1,
    operation: "upsert_market_alignment",
    run_id: RUN_ID,
    workflow_run_id: "32330093877",
    commit_sha: COMMIT_SHA,
    market_snapshot: marketSnapshot(),
    alignment: alignment(),
  };
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM research_reports"),
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

describe("market alignment ingest", () => {
  async function arrangePublishedRun() {
    await ingestItems(env, envelope(), new Date("2026-08-20T03:58:50Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-20T03:58:55Z"));
  }

  function handler() {
    return createHandler({
      authenticate: async () => ({
        workflowRunId: "32330093877",
        commitSha: COMMIT_SHA,
        eventName: "workflow_dispatch",
      }),
      now: () => new Date("2026-08-20T04:00:00Z"),
    });
  }

  it("persists market snapshot and alignment, then replays idempotently", async () => {
    await arrangePublishedRun();
    const request = () => new Request("https://ingest.example/v1/ingest/market-alignment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ingestPayload()),
    });

    const first = await handler().fetch(request(), env);
    expect(first.status).toBe(201);
    expect(await first.json()).toMatchObject({ replayed: false, alignment_id: "align_20260820t035900z" });
    const replay = await handler().fetch(request(), env);
    expect(replay.status).toBe(200);
    expect(await replay.json()).toMatchObject({ replayed: true });
    const rows = await env.DB.prepare(
      "SELECT (SELECT COUNT(*) FROM market_snapshots) AS markets, (SELECT COUNT(*) FROM topic_market_alignments) AS alignments",
    ).first<{ markets: number; alignments: number }>();
    expect(rows).toEqual({ markets: 1, alignments: 1 });
    expect(await env.RAW_OBJECTS.get("market/market_20260820t035900z.json")).not.toBeNull();
    expect(await env.RAW_OBJECTS.get("alignments/align_20260820t035900z.json")).not.toBeNull();
  });

  it("rejects alignment evidence that is not in the published run", async () => {
    await arrangePublishedRun();
    const invalid = ingestPayload();
    invalid.market_snapshot.instruments[0].source_item_ids = ["e".repeat(64)];
    invalid.alignment.topics[0].evidence_ids = ["e".repeat(64)];
    const response = await handler().fetch(new Request(
      "https://ingest.example/v1/ingest/market-alignment",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(invalid),
      },
    ), env);
    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ error: "evidence_not_in_run" });
  });
});
