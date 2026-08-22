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
import {
  buildDeterministicResearchOutput,
  parseModelClaims,
  runAiWithFallback,
  selectResearchEvidenceIds,
} from "../src/research-agent";


const ITEM_ID = "a".repeat(64);
const CONTENT_HASH = "b".repeat(64);
const COMMIT_SHA = "d".repeat(40);
const RUN_ID = "run_20260820t035848z";
const SNAPSHOT_ID = "radar_20260820t035848z";
const ALIGNMENT_ID = "align_20260820t035900z";
const PLAN_ID = "plan_20260820t040000z";

describe("research model resilience", () => {
  it("bounds evidence loading to six traceable items per selected topic", () => {
    const ids = selectResearchEvidenceIds(
      [
        { topic_id: "topic_a", evidence_ids: ["a1", "a2", "a3", "a4", "a5", "a6", "a7"] },
        { topic_id: "topic_b", evidence_ids: ["b1", "b2", "b3", "b4", "b5", "b6", "b7"] },
        { topic_id: "topic_c", evidence_ids: ["c1", "c2", "c3", "c4", "c5", "c6", "c7"] },
      ],
      [],
      [],
    );
    expect(ids).toEqual([
      "a1", "a2", "a3", "a4", "a5", "a6",
      "b1", "b2", "b3", "b4", "b5", "b6",
      "c1", "c2", "c3", "c4", "c5", "c6",
    ]);
  });

  it("builds a traceable non-transactional report without a model call", () => {
    const output = buildDeterministicResearchOutput(
      {
        topic_id: "digital_assets",
        label: "Digital assets",
        score: 4,
        item_count: 2,
        source_count: 2,
        news_count: 1,
        social_count: 1,
        evidence_ids: [ITEM_ID],
        divergence: { direction: "social_leads", magnitude: 0.4 },
      },
      [ITEM_ID],
      "mixed",
    );
    expect(output.bull_case[0].evidence_ids).toEqual([ITEM_ID]);
    expect(output.bear_case[0].text).toContain("mixed");
  });

  it("falls back to the bounded model when the primary model fails", async () => {
    const models: string[] = [];
    const result = await runAiWithFallback(
      undefined as unknown as Parameters<typeof runAiWithFallback>[1],
      async (_env, model, input) => {
        models.push(model);
        if (models.length === 1) throw new Error("primary unavailable");
        expect(input).not.toHaveProperty("response_format");
        return { response: "{}" };
      },
      "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
      { messages: [] },
    );
    expect(models).toEqual([
      "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
      "@cf/meta/llama-3.1-8b-instruct-fp8",
    ]);
    expect(result.model).toBe("@cf/meta/llama-3.1-8b-instruct-fp8");
  });
});

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
      financial_depth: {
        schema_version: 1,
        status: "professional_ready",
        time_series: {
          schema_version: 1,
          status: "available",
          series_id: "BTC",
          provider: "fixture",
          currency: "USD",
          as_of: "2026-08-20T03:59:00Z",
          window_start: "2026-08-19T03:59:00Z",
          window_end: "2026-08-20T03:59:00Z",
          point_count: 2,
          points: [
            { observed_at: "2026-08-19T03:59:00Z", value: 60000 },
            { observed_at: "2026-08-20T03:59:00Z", value: 64000 },
          ],
          returns: { observed_pct: 6.666667 },
          volatility_annualized_pct: null,
          max_drawdown_pct: 0,
          source_item_ids: [ITEM_ID],
          missing_reason: null,
          source_ref: {
            url: "https://example.com/history",
            response_sha256: "e".repeat(64),
          },
        },
        fundamentals: { status: "unavailable", missing_reason: "provider_not_configured" },
        valuation: { status: "not_applicable", method: null, missing_fields: [], reason: "crypto" },
        scenarios: {
          schema_version: 1,
          status: "available",
          method: "observed_range",
          not_a_forecast: true,
          scenarios: {
            base: { price: 64000 },
            bull: { price: 64000 },
            bear: { price: 60000 },
          },
        },
        source_conflicts: [{
          schema_version: 1,
          topic_id: "target",
          status: "available",
          conflict_level: "low",
          counts: { positive: 1, negative: 0, neutral: 0, unknown: 0 },
          independent_source_count: 1,
          evidence_ids: [ITEM_ID],
        }],
      },
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
  it("fails closed on malformed or ungrounded model output", () => {
    const allowed = new Set([ITEM_ID]);
    expect(() => parseModelClaims("not json", allowed)).toThrow(/model_output_invalid/);
    expect(() => parseModelClaims("{bad}", allowed)).toThrow(/model_output_invalid/);
    expect(() => parseModelClaims({ response: "{}" }, allowed)).toThrow(/model_output_invalid/);
    expect(() => parseModelClaims({ response: JSON.stringify({ bull_case: [] }) }, allowed)).toThrow(/model_output_invalid/);
    expect(() => parseModelClaims({
      response: JSON.stringify({
        bull_case: [{ text: "x", confidence: 0.5, evidence_ids: ["e".repeat(64)] }],
        bear_case: [{ text: "x", confidence: 0.5, evidence_ids: [ITEM_ID] }],
        risk_view: [{ text: "x", confidence: 0.5, evidence_ids: [ITEM_ID] }],
      }),
    }, allowed)).toThrow(/model_output_invalid/);
  });

  it("accepts grounded JSON from the supported Workers AI response shapes", () => {
    const allowed = new Set([ITEM_ID]);
    const claims = {
      bull_case: [{ text: "bull", confidence: 0.5, evidence_ids: [ITEM_ID] }],
      bear_case: [{ text: "bear", confidence: 0.4, evidence_ids: [ITEM_ID] }],
      risk_view: [{ text: "risk", confidence: 0.8, evidence_ids: [ITEM_ID] }],
    };
    expect(parseModelClaims(JSON.stringify(claims), allowed).bull_case).toHaveLength(1);
    expect(parseModelClaims({ response: claims }, allowed).bull_case).toHaveLength(1);
    expect(parseModelClaims({ result: JSON.stringify(claims) }, allowed).bear_case).toHaveLength(1);
    expect(parseModelClaims({ result: { response: claims } }, allowed).bear_case).toHaveLength(1);
    expect(parseModelClaims({ text: "```json\n" + JSON.stringify(claims) + "\n```" }, allowed).risk_view)
      .toHaveLength(1);
    expect(() => parseModelClaims({ response: JSON.stringify({
      bull_case: [{ text: "x", confidence: 1.1, evidence_ids: [ITEM_ID] }],
      bear_case: claims.bear_case,
      risk_view: claims.risk_view,
    }) }, allowed)).toThrow(/model_output_invalid/);
    expect(() => parseModelClaims({ response: JSON.stringify({
      bull_case: [{ text: "x", confidence: 0.5, evidence_ids: [] }],
      bear_case: claims.bear_case,
      risk_view: claims.risk_view,
    }) }, allowed)).toThrow(/model_output_invalid/);
    expect(() => parseModelClaims({ response: JSON.stringify({
      bull_case: [{ text: "", confidence: 0.5, evidence_ids: [ITEM_ID] }],
      bear_case: claims.bear_case,
      risk_view: claims.risk_view,
    }) }, allowed)).toThrow(/model_output_invalid/);
  });

  it("binds report generation to the current GitHub OIDC workflow identity", async () => {
    await arrange();
    const payload = {
      schema_version: 1,
      operation: "generate_research_reports",
      run_id: RUN_ID,
      workflow_run_id: "32330093877",
      commit_sha: COMMIT_SHA,
      plan_id: PLAN_ID,
      alignment_id: ALIGNMENT_ID,
      authorize_model_execution: true,
    };
    const wrongWorkflow = createHandler({
      authenticate: async () => ({ workflowRunId: "other", commitSha: COMMIT_SHA }),
    });
    const workflowResponse = await wrongWorkflow.fetch(new Request(
      "https://ingest.example/v1/agent/research-reports",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
    ), env);
    expect(workflowResponse.status).toBe(403);
    const wrongCommit = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: "e".repeat(40) }),
    });
    const commitResponse = await wrongCommit.fetch(new Request(
      "https://ingest.example/v1/agent/research-reports",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
    ), env);
    expect(commitResponse.status).toBe(403);
  });

  it("fails closed when persisted plan, alignment, or evidence objects drift", async () => {
    await arrange();
    const requestPayload = (alignmentId = ALIGNMENT_ID) => ({
      schema_version: 1,
      operation: "generate_research_reports",
      run_id: RUN_ID,
      workflow_run_id: "32330093877",
      commit_sha: COMMIT_SHA,
      plan_id: PLAN_ID,
      alignment_id: alignmentId,
      authorize_model_execution: true,
    });
    const call = async (payload: unknown) => createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
    }).fetch(new Request("https://ingest.example/v1/agent/research-reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }), env);

    expect((await call(requestPayload("align_20260820t040001z"))).status).toBe(409);
    await env.DB.prepare(
      `INSERT INTO runs (
        run_id, workflow_run_id, commit_sha, snapshot_id, source_manifest_hash,
        status, collected_at, item_count
      ) VALUES (?, ?, ?, ?, ?, 'published', ?, 0)`,
    ).bind(
      "run_other",
      "32330093878",
      "f".repeat(40),
      "radar_20260820t040001z",
      "a".repeat(64),
      "2026-08-20T04:00:01Z",
    ).run();
    await env.DB.prepare(
      "UPDATE topic_market_alignments SET run_id = ? WHERE alignment_id = ?",
    ).bind("run_other", ALIGNMENT_ID).run();
    expect((await call(requestPayload())).status).toBe(409);

    await env.DB.prepare(
      "UPDATE topic_market_alignments SET run_id = ? WHERE alignment_id = ?",
    ).bind(RUN_ID, ALIGNMENT_ID).run();
    await env.DB.prepare(
      "UPDATE market_snapshots SET run_id = ? WHERE snapshot_id = ?",
    ).bind("run_other", "market_20260820t035900z").run();
    expect((await call(requestPayload())).status).toBe(409);
    await env.DB.prepare(
      "UPDATE market_snapshots SET run_id = ? WHERE snapshot_id = ?",
    ).bind(RUN_ID, "market_20260820t035900z").run();
    await env.RAW_OBJECTS.put(`alignments/${ALIGNMENT_ID}.json`, "{}", {
      httpMetadata: { contentType: "application/json" },
    });
    expect((await call(requestPayload())).status).toBe(503);

    await env.RAW_OBJECTS.put(
      `alignments/${ALIGNMENT_ID}.json`,
      JSON.stringify(alignmentEnvelope().alignment),
      { httpMetadata: { contentType: "application/json" } },
    );
    await env.RAW_OBJECTS.put("market/market_20260820t035900z.json", "{}", {
      httpMetadata: { contentType: "application/json" },
    });
    expect((await call(requestPayload())).status).toBe(503);

    await env.RAW_OBJECTS.put(
      "market/market_20260820t035900z.json",
      JSON.stringify(alignmentEnvelope().market_snapshot),
      { httpMetadata: { contentType: "application/json" } },
    );
    await env.RAW_OBJECTS.put(`raw/coingecko_markets_api/${ITEM_ID}.json`, "{}", {
      httpMetadata: { contentType: "application/json" },
    });
    expect((await call(requestPayload())).status).toBe(503);
  });

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

  it("runs a real-agent-shaped model response and persists the generated second opinion", async () => {
    await arrange();
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
      now: () => new Date("2026-08-20T04:10:00Z"),
      runAi: async (_env, model, input) => {
        expect(model).toBe("@cf/meta/llama-3.3-70b-instruct-fp8-fast");
        expect(input).toMatchObject({ temperature: 0 });
        expect((input.messages as Array<{ content: string }>)[0].content)
          .toContain("Do not give a buy or sell instruction");
        expect((input.messages as Array<{ content: string }>)[1].content).toContain("RESEARCH_TARGET=crypto:BTC");
        expect((input.messages as Array<{ content: string }>)[1].content).toContain("RESEARCH_QUESTION=What are the current drivers and risks for BTC?");
        expect((input.messages as Array<{ content: string }>)[1].content).toContain("FINANCIAL_DEPTH");
        return {
          response: JSON.stringify({
            bull_case: [{
              text: "The observed market data supports a constructive near-term case.",
              confidence: 0.6,
              evidence_ids: [ITEM_ID],
            }],
            bear_case: [{
              text: "A single market-data point is insufficient to rule out a reversal.",
              confidence: 0.55,
              evidence_ids: [ITEM_ID],
            }],
            risk_view: [{
              text: "The evidence set is narrow and should not be treated as a forecast.",
              confidence: 0.9,
              evidence_ids: [ITEM_ID],
            }],
            summary: "Bitcoin is supported by constructive momentum, but the evidence base is narrow.",
            catalysts: [{
              text: "Sustained activity would support the constructive case.",
              confidence: 0.55,
              evidence_ids: [ITEM_ID],
            }],
            failure_conditions: [{
              text: "A reversal with no confirming breadth would weaken the case.",
              confidence: 0.7,
              evidence_ids: [ITEM_ID],
            }],
            data_gaps: [{
              text: "Independent source confirmation is still needed.",
              evidence_ids: [ITEM_ID],
            }],
          }),
        };
      },
    });
    const response = await handler.fetch(new Request(
      "https://ingest.example/v1/agent/research-reports",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_version: 1,
          operation: "generate_research_reports",
          run_id: RUN_ID,
          workflow_run_id: "32330093877",
          commit_sha: COMMIT_SHA,
          plan_id: PLAN_ID,
          alignment_id: ALIGNMENT_ID,
          target: { kind: "crypto", symbol: "BTC" },
          research_question: "What are the current drivers and risks for BTC?",
          authorize_model_execution: true,
          max_reports: 1,
        }),
      },
    ), env);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      report_count: 1,
      reports: [{ report_id: `report_${RUN_ID}_digital_assets`, replayed: false }],
    });
    const storedReport = await env.RAW_OBJECTS.get(`reports/report_${RUN_ID}_digital_assets.json`);
    expect(storedReport).not.toBeNull();
    expect(await storedReport!.json()).toMatchObject({
      report_profile: "detailed_traceable",
      research_question: "What are the current drivers and risks for BTC?",
      target: { kind: "crypto", symbol: "BTC" },
      summary: "Bitcoin is supported by constructive momentum, but the evidence base is narrow.",
      catalysts: [{ text: "Sustained activity would support the constructive case." }],
      failure_conditions: [{ text: "A reversal with no confirming breadth would weaken the case." }],
      data_gaps: [{ text: "Independent source confirmation is still needed.", evidence_ids: [ITEM_ID] }],
      recommendation_status: "research_only",
      report_version: 2,
      professional_analysis: {
        status: "professional_ready",
        market_snapshot_id: "market_20260820t035900z",
        model_input_scope: { evidence_count: 1, market_depth_included: true },
      },
    });
    const row = await env.DB.prepare(
      "SELECT report_id, model, agent_version FROM research_reports",
    ).first();
    expect(row).toEqual({
      report_id: `report_${RUN_ID}_digital_assets`,
      model: "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
      agent_version: "tradingagents-cloudflare-ai-v1",
    });

    const replay = await handler.fetch(new Request(
      "https://ingest.example/v1/agent/research-reports",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_version: 1,
          operation: "generate_research_reports",
          run_id: RUN_ID,
          workflow_run_id: "32330093877",
          commit_sha: COMMIT_SHA,
          plan_id: PLAN_ID,
          alignment_id: ALIGNMENT_ID,
          authorize_model_execution: true,
          max_reports: 1,
        }),
      },
    ), env);
    expect(replay.status).toBe(200);
    expect(await replay.json()).toMatchObject({
      report_count: 1,
      reports: [{ replayed: true }],
    });
  });

  it("honors the compact report profile", async () => {
    await arrange();
    let aiCalls = 0;
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
      now: () => new Date("2026-08-20T04:10:00Z"),
      runAi: async (_env, _model, input) => {
        aiCalls += 1;
        const system = (input.messages as Array<{ content: string }>)[0].content;
        const user = (input.messages as Array<{ content: string }>)[1].content;
        expect(system).toContain("compact_traceable");
        expect(user).toContain("REPORT_PROFILE=compact_traceable");
        expect(input.max_tokens).toBe(800);
        return {
          response: JSON.stringify({
            bull_case: [{ text: "compact positive", confidence: 0.7, evidence_ids: [ITEM_ID] }],
            bear_case: [{ text: "compact negative", confidence: 0.6, evidence_ids: [ITEM_ID] }],
            risk_view: [{ text: "compact risk", confidence: 0.8, evidence_ids: [ITEM_ID] }],
          }),
        };
      },
    });
    const compactResponse = await handler.fetch(new Request(
      "https://ingest.example/v1/agent/research-reports",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_version: 1,
          operation: "generate_research_reports",
          run_id: RUN_ID,
          workflow_run_id: "32330093877",
          commit_sha: COMMIT_SHA,
          plan_id: PLAN_ID,
          alignment_id: ALIGNMENT_ID,
          target: { kind: "crypto", symbol: "BTC" },
          authorize_model_execution: true,
          report_profile: "compact_traceable",
          requested_outputs: ["quick_card", "evidence_appendix"],
          max_reports: 1,
        }),
      },
    ), env);
    expect(compactResponse.status).toBe(200);
    expect(await compactResponse.json()).toMatchObject({ report_count: 1 });
    expect(aiCalls).toBe(1);
    const compactReport = await env.DB.prepare("SELECT report_id, report_profile FROM research_reports").first();
    expect(compactReport).toEqual({
      report_id: `report_${RUN_ID}_digital_assets`,
      report_profile: "compact_traceable",
    });

    const conflictingProfileResponse = await handler.fetch(new Request(
      "https://ingest.example/v1/agent/research-reports",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_version: 1,
          operation: "generate_research_reports",
          run_id: RUN_ID,
          workflow_run_id: "32330093877",
          commit_sha: COMMIT_SHA,
          plan_id: PLAN_ID,
          alignment_id: ALIGNMENT_ID,
          target: { kind: "crypto", symbol: "BTC" },
          authorize_model_execution: true,
          report_profile: "detailed_traceable",
          requested_outputs: ["detailed_report", "evidence_appendix"],
          max_reports: 1,
        }),
      },
    ), env);
    expect(conflictingProfileResponse.status).toBe(409);

  });

  it("skips model execution when only the evidence appendix is requested", async () => {
    await arrange();
    let aiCalls = 0;
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "32330093877", commitSha: COMMIT_SHA }),
      now: () => new Date("2026-08-20T04:10:00Z"),
      runAi: async () => {
        aiCalls += 1;
        return {};
      },
    });
    const appendixOnlyResponse = await handler.fetch(new Request(
      "https://ingest.example/v1/agent/research-reports",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_version: 1,
          operation: "generate_research_reports",
          run_id: RUN_ID,
          workflow_run_id: "32330093877",
          commit_sha: COMMIT_SHA,
          plan_id: PLAN_ID,
          alignment_id: ALIGNMENT_ID,
          target: { kind: "crypto", symbol: "BTC" },
          authorize_model_execution: true,
          report_profile: "detailed_traceable",
          requested_outputs: ["evidence_appendix"],
          max_reports: 1,
        }),
      },
    ), env);
    expect(appendixOnlyResponse.status).toBe(200);
    expect(await appendixOnlyResponse.json()).toMatchObject({ report_count: 0, reports: [] });
    expect(aiCalls).toBe(0);
  });
});
