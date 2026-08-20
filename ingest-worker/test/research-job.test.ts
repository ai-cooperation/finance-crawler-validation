import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { createHandler } from "../src/handler";
import {
  buildEvidenceGraph,
  executeResearchJob,
  dispatchActionsResearchJob,
  completeResearchJob,
  failResearchJob,
  readEvidenceAppendix,
  readResearchPack,
  readResearchReport,
  readResearchJob,
  readResearchJobByRequestId,
  retryResearchJob,
  submitResearchJob,
} from "../src/research-jobs";
import { validateResearchPack, type ResearchReport } from "../src/contracts";


const ITEM_ID = "a".repeat(64);
const COMMIT_SHA = "d".repeat(40);
const RUN_ID = "run_20260820t035848z";
const SNAPSHOT_ID = "radar_20260820t035848z";
const ALIGNMENT_ID = "align_20260820t035900z";
const PLAN_ID = "plan_20260820t040000z";

function researchRequest(idempotencyKey = "target-btc-20260820") {
  return {
    schema_version: 1,
    operation: "submit_research_job",
    idempotency_key: idempotencyKey,
    target: {
      kind: "crypto",
      symbol: "BTC",
      name: "Bitcoin",
      market: "global",
    },
    requirements: {
      question: "What are the strongest current drivers and risks for BTC?",
      source_strategy: "latest_published",
      include_market_data: true,
      include_topic_radar: true,
      report_profile: "detailed_traceable",
      requested_outputs: ["detailed_report", "evidence_appendix"],
      max_sources: 12,
    },
  };
}

function rawItem() {
  return {
    schema_version: 1,
    item_id: ITEM_ID,
    source_id: "coingecko_markets_api",
    canonical_url: "https://www.coingecko.com/en/coins/bitcoin",
    title: "CoinGecko Bitcoin market data",
    summary: "Synthetic for testing only",
    content: "{\"current_price\":64000,\"id\":\"bitcoin\"}",
    published_at: "2026-08-20T03:55:20Z",
    collected_at: "2026-08-20T03:58:48Z",
    transport: "json_api",
    kind: "market_data",
    layer: "market",
    content_sha256: "b".repeat(64),
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

function seedInputPayloads() {
  return {
    ingest: {
      schema_version: 1,
      operation: "upsert_items",
      run_id: RUN_ID,
      workflow_run_id: "32330093877",
      commit_sha: COMMIT_SHA,
      snapshot_id: SNAPSHOT_ID,
      source_manifest_hash: "c".repeat(64),
      collected_at: "2026-08-20T03:58:48Z",
      items: [rawItem()],
      checkpoints: [{
        source_id: "coingecko_markets_api",
        status: "success",
        last_successful_crawl: "2026-08-20T03:58:48Z",
        last_article_date: "2026-08-20T03:55:20Z",
        cursor: null,
      }],
    },
    topicSnapshot: {
      schema_version: 1,
      snapshot_id: SNAPSHOT_ID,
      run_id: RUN_ID,
      as_of: "2026-08-20T03:58:48Z",
      partial: true,
      failed_sources: [],
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
    },
    alignment: {
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
    },
    plan: {
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
          max_topics: 1,
          max_claims_per_topic: 3,
          max_tokens: 1200,
          max_usd: 0,
          model: "test-model",
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
    },
  };
}

async function arrange(): Promise<void> {
  const payloads = seedInputPayloads();
  const { ingestItems, ingestMarketAlignment, ingestTradingAgentsPlan, publishSnapshot } = await import("../src/storage");
  await ingestItems(env, payloads.ingest, new Date("2026-08-20T03:58:50Z"));
  await publishSnapshot(env, payloads.topicSnapshot, new Date("2026-08-20T03:58:55Z"));
  await ingestMarketAlignment(env, payloads.alignment, new Date("2026-08-20T03:59:05Z"));
  await ingestTradingAgentsPlan(env, payloads.plan, new Date("2026-08-20T04:00:00Z"));
}

const auth = { subject: "opencode-test", scopes: ["research:submit", "research:read"] };

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM research_packs"),
    env.DB.prepare("DELETE FROM research_jobs"),
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
    env.DB.prepare("DELETE FROM runs"),
  ]);
  const objects = await env.RAW_OBJECTS.list();
  if (objects.objects.length > 0) await env.RAW_OBJECTS.delete(objects.objects.map((object) => object.key));
});

describe("research report generator job contract", () => {
  it("keeps evidence graph generation compatible with legacy reports and packs", () => {
    const legacyReport = {
      schema_version: 1,
      report_id: "report_legacy_20260820",
      topic_snapshot_id: SNAPSHOT_ID,
      plan_id: PLAN_ID,
      alignment_id: ALIGNMENT_ID,
      market_snapshot_id: "market_20260820t035900z",
      topic_id: "digital_assets",
      generated_at: "2026-08-20T03:59:00Z",
      expires_at: "2026-08-21T03:59:00Z",
      model: "legacy-model",
      agent_version: "legacy-agent",
      second_opinion: true,
      evidence_ids: [ITEM_ID],
      bull_case: [{ text: "positive", confidence: 0.7, evidence_ids: [ITEM_ID] }],
      bear_case: [{ text: "negative", confidence: 0.4, evidence_ids: [ITEM_ID] }],
      risk_view: [{ text: "uncertain", confidence: 0.5, evidence_ids: [ITEM_ID] }],
    } satisfies ResearchReport;
    expect(buildEvidenceGraph([legacyReport]).claims.map((claim) => claim.category)).toEqual([
      "bull_case",
      "bear_case",
      "risk_view",
    ]);
  });

  it("submits an idempotent job with target and research requirements", async () => {
    const first = await submitResearchJob(env, researchRequest(), auth, new Date("2026-08-20T04:10:00Z"));
    expect(first).toMatchObject({
      status: "queued",
      stage: "queued",
      progress: 0,
      retryable: true,
      next_action: "poll_job_status",
      replayed: false,
      request_id: expect.any(String),
    });
    expect(first.job_id).toMatch(/^research_/);
    const replay = await submitResearchJob(env, researchRequest(), auth, new Date("2026-08-20T04:11:00Z"));
    expect(replay).toMatchObject({ job_id: first.job_id, status: "queued", replayed: true });
  });

  it("freezes the normalized target identity before dispatch", async () => {
    const request = researchRequest("target-normalization-20260820");
    request.target.symbol = "btc";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    expect(submitted.target).toMatchObject({ kind: "crypto", symbol: "BTC" });
    expect(submitted.planner?.requirement.target).toMatchObject({ kind: "crypto", symbol: "BTC" });
  });

  it("exposes every persisted job state with an explicit next action", async () => {
    const submitted = await submitResearchJob(
      env,
      researchRequest("job-state-metadata-20260820"),
      auth,
      new Date("2026-08-20T04:10:00Z"),
      "request-state-metadata",
    );
    const cases: Array<{
      status: string;
      errorCode: string | null;
      dispatchId?: string | null;
      expected: Record<string, unknown>;
    }> = [
      { status: "queued", errorCode: null, expected: { stage: "queued", progress: 0, next_action: "poll_job_status" } },
      { status: "queued", errorCode: null, dispatchId: "workflow:topic-radar.yml:test", expected: { stage: "dispatching", next_action: "wait_for_actions" } },
      { status: "running", errorCode: null, expected: { stage: "processing", progress: 0.5, next_action: "poll_job_status" } },
      { status: "completed", errorCode: null, expected: { stage: "published", progress: 1, retryable: false, next_action: null } },
      { status: "partial", errorCode: null, expected: { stage: "published", progress: 1, retryable: false, next_action: null } },
      { status: "stale", errorCode: "stale_snapshot", expected: { stage: "stale", progress: null, next_action: "request_refresh" } },
      { status: "failed", errorCode: "provider_failed", expected: { stage: "failed", progress: null, retryable: true, next_action: "retry_research_job" } },
      { status: "blocked", errorCode: "document_engine_required", expected: { stage: "blocked", retryable: false, next_action: "provide_document_engine" } },
      { status: "blocked", errorCode: "source_budget_too_low", expected: { stage: "blocked", retryable: false, next_action: "increase_source_budget" } },
      { status: "blocked", errorCode: "market_target_not_supported", expected: { stage: "blocked", retryable: false, next_action: "review_error" } },
      { status: "blocked", errorCode: "actions_dispatch_not_configured", expected: { stage: "blocked", retryable: true, next_action: "configure_actions_dispatch_and_retry" } },
      { status: "blocked", errorCode: "actions_admission_denied", expected: { stage: "blocked", retryable: true, next_action: "retry_research_job" } },
      { status: "blocked", errorCode: "research_inputs_unavailable", expected: { stage: "blocked", retryable: true, next_action: "retry_research_job" } },
    ];
    for (const [index, testCase] of cases.entries()) {
      await env.DB.prepare(
        "UPDATE research_jobs SET status = ?, error_code = ?, dispatch_id = ?, updated_at = ? WHERE job_id = ?",
      ).bind(
        testCase.status,
        testCase.errorCode,
        testCase.dispatchId ?? null,
        `2026-08-20T04:${String(20 + index).padStart(2, "0")}:00Z`,
        submitted.job_id,
      ).run();
      const status = await readResearchJob(env, submitted.job_id);
      expect(status).toMatchObject(testCase.expected);
    }
  });

  it("reads jobs by request id and fails closed for missing or conflicting dispatch state", async () => {
    const submitted = await submitResearchJob(
      env,
      researchRequest("job-request-id-20260820"),
      auth,
      new Date("2026-08-20T04:10:00Z"),
      "request-by-id",
    );
    expect(await readResearchJobByRequestId(env, "request-by-id")).toMatchObject({ job_id: submitted.job_id });
    await expect(readResearchJob(env, "research_missing")).rejects.toMatchObject({ code: "research_job_not_found" });
    await expect(readResearchJobByRequestId(env, "request_missing")).rejects.toMatchObject({ code: "research_job_not_found" });
    await expect(dispatchActionsResearchJob(env, submitted.job_id, new Date("2026-08-20T04:11:00Z")))
      .rejects.toMatchObject({ code: "research_job_strategy_conflict" });
  });

  it("does not requeue completed jobs or running latest-published jobs", async () => {
    const completed = await submitResearchJob(env, researchRequest("retry-completed-20260820"), auth, new Date("2026-08-20T04:10:00Z"));
    await env.DB.prepare("UPDATE research_jobs SET status = 'completed' WHERE job_id = ?").bind(completed.job_id).run();
    expect(await retryResearchJob(env, completed.job_id, new Date("2026-08-20T04:11:00Z"))).toMatchObject({ status: "completed" });

    const running = await submitResearchJob(env, researchRequest("retry-running-20260820"), auth, new Date("2026-08-20T04:10:00Z"));
    await env.DB.prepare("UPDATE research_jobs SET status = 'running' WHERE job_id = ?").bind(running.job_id).run();
    expect(await retryResearchJob(env, running.job_id, new Date("2026-08-20T04:11:00Z"))).toMatchObject({ status: "running" });
  });

  it("closes missing pipeline inputs with stage-specific retry states", async () => {
    const actions = researchRequest("execute-actions-without-run-20260820");
    actions.requirements.source_strategy = "actions";
    const actionsJob = await submitResearchJob(env, actions, auth, new Date("2026-08-20T04:10:00Z"));
    await expect(executeResearchJob(env, actionsJob.job_id, { runAi: async () => ({ response: "{}" }) }, new Date("2026-08-20T04:11:00Z")))
      .resolves.toMatchObject({ status: "blocked", error_code: "actions_dispatch_not_configured" });

    const latestJob = await submitResearchJob(env, researchRequest("execute-no-published-run-20260820"), auth, new Date("2026-08-20T04:10:00Z"));
    await expect(executeResearchJob(env, latestJob.job_id, { runAi: async () => ({ response: "{}" }) }, new Date("2026-08-20T04:11:00Z")))
      .resolves.toMatchObject({ status: "blocked", error_code: "research_inputs_unavailable" });
  });

  it("marks a job failed when its persisted plan is unavailable", async () => {
    await arrange();
    const missingPlan = await submitResearchJob(env, researchRequest("execute-missing-plan-20260820"), auth, new Date("2026-08-20T04:10:00Z"));
    await env.DB.prepare("DELETE FROM tradingagents_plans").run();
    await expect(executeResearchJob(env, missingPlan.job_id, { runAi: async () => ({ response: "{}" }) }, new Date("2026-08-20T04:11:00Z")))
      .resolves.toMatchObject({ status: "failed", error_code: "research_plan_unavailable" });
  });

  it("marks a job failed when its persisted alignment is unavailable", async () => {
    await arrange();
    const missingAlignment = await submitResearchJob(env, researchRequest("execute-missing-alignment-20260820"), auth, new Date("2026-08-20T04:10:00Z"));
    await env.DB.prepare(
      `INSERT INTO topic_snapshots (
        snapshot_id, run_id, as_of, partial, failed_sources_json,
        object_key, content_sha256, topic_count, created_at
      ) VALUES (?, ?, ?, 0, '[]', ?, ?, 0, ?)`,
    ).bind(
      "snapshot_other",
      RUN_ID,
      "2026-08-20T04:00:00Z",
      "topics/snapshot_other.json",
      "e".repeat(64),
      "2026-08-20T04:00:00Z",
    ).run();
    await env.DB.prepare("UPDATE topic_market_alignments SET topic_snapshot_id = ? WHERE alignment_id = ?")
      .bind("snapshot_other", ALIGNMENT_ID).run();
    await expect(executeResearchJob(env, missingAlignment.job_id, { runAi: async () => ({ response: "{}" }) }, new Date("2026-08-20T04:11:00Z")))
      .resolves.toMatchObject({ status: "failed", error_code: "research_alignment_unavailable" });
  });

  it("records generic model failures and protects private pack reads", async () => {
    await arrange();
    const submitted = await submitResearchJob(env, researchRequest("execute-model-failure-20260820"), auth, new Date("2026-08-20T04:10:00Z"));
    await expect(executeResearchJob(env, submitted.job_id, {
      runAi: async () => { throw new Error("synthetic model failure"); },
    }, new Date("2026-08-20T04:11:00Z"))).resolves.toMatchObject({
      status: "failed",
      error_code: "research_job_failed",
    });
    await expect(readResearchPack(env, "research_not_ready")).rejects.toMatchObject({ code: "research_pack_not_ready" });
  });

  it("rejects idempotency conflicts and blocks dispatch when the planner is missing", async () => {
    const first = await submitResearchJob(env, researchRequest("idempotency-conflict-20260820"), auth, new Date("2026-08-20T04:10:00Z"));
    const changed = researchRequest("idempotency-conflict-20260820");
    changed.requirements.question = "A different frozen research question for the same key";
    await expect(submitResearchJob(env, changed, auth, new Date("2026-08-20T04:11:00Z")))
      .rejects.toMatchObject({ code: "idempotency_conflict" });

    const actions = researchRequest("dispatch-planner-missing-20260820");
    actions.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, actions, auth, new Date("2026-08-20T04:10:00Z"));
    await env.DB.prepare("UPDATE research_jobs SET requirement_json = '{}', source_bundle_json = '{}' WHERE job_id = ?")
      .bind(submitted.job_id).run();
    const blocked = await dispatchActionsResearchJob(env, submitted.job_id, new Date("2026-08-20T04:11:00Z"));
    expect(blocked).toMatchObject({ status: "blocked", error_code: "research_plan_blocked", next_action: "review_error" });
  });

  it("executes against the latest published pipeline, persists a private Research Pack and report read-back", async () => {
    await arrange();
    const submitted = await submitResearchJob(env, researchRequest(), auth, new Date("2026-08-20T04:10:00Z"));
    const completed = await executeResearchJob(env, submitted.job_id, {
      runAi: async () => ({
        response: JSON.stringify({
          bull_case: [{ text: "positive momentum", confidence: 0.7, evidence_ids: [ITEM_ID] }],
          bear_case: [{ text: "data coverage is partial", confidence: 0.6, evidence_ids: [ITEM_ID] }],
          risk_view: [{ text: "single-source risk", confidence: 0.8, evidence_ids: [ITEM_ID] }],
          catalysts: [{ text: "inflows accelerate", confidence: 0.5, evidence_ids: [ITEM_ID] }],
          failure_conditions: [{ text: "liquidity reverses", confidence: 0.5, evidence_ids: [ITEM_ID] }],
          data_gaps: [{ text: "needs broader source coverage", evidence_ids: [ITEM_ID] }],
        }),
      }),
    }, new Date("2026-08-20T04:12:00Z"));
    expect(completed.status).toBe("partial");
    const status = await readResearchJob(env, submitted.job_id);
    expect(status).toMatchObject({
      job_id: submitted.job_id,
      status: "partial",
      stage: "published",
      progress: 1,
      retryable: false,
      next_action: null,
      report_count: 1,
    });
    const pack = await readResearchPack(env, submitted.job_id);
    expect(pack).toMatchObject({
      schema_version: 1,
      job_id: submitted.job_id,
      target: { symbol: "BTC" },
      requirement: { requirement_id: expect.stringMatching(/^req_/) },
      source_bundle_plan: { source_count: expect.any(Number) },
      quality: { partial: true },
      reports: [{ report_id: expect.stringMatching(/^report_/), report_profile: "detailed_traceable" }],
      evidence_graph: {
        schema_version: 1,
        claims: expect.arrayContaining([
          expect.objectContaining({
            claim_id: expect.stringMatching(/^report_.*:bull_case:0$/),
            category: "bull_case",
            evidence_ids: [ITEM_ID],
          }),
          expect.objectContaining({
            claim_id: expect.stringMatching(/^report_.*:bear_case:0$/),
            category: "bear_case",
            evidence_ids: [ITEM_ID],
          }),
          expect.objectContaining({
            claim_id: expect.stringMatching(/^report_.*:risk_view:0$/),
            category: "risk_view",
            evidence_ids: [ITEM_ID],
          }),
          expect.objectContaining({ category: "catalyst", evidence_ids: [ITEM_ID] }),
          expect.objectContaining({ category: "failure_condition", evidence_ids: [ITEM_ID] }),
          expect.objectContaining({ category: "data_gap", evidence_ids: [ITEM_ID] }),
        ]),
      },
    });
    const evidenceIds = new Set(pack.evidence.map((item) => item.evidence_id));
    expect(pack.evidence_graph.claims.every((claim) => claim.evidence_ids.every((id) => evidenceIds.has(id)))).toBe(true);
    const legacyPack = structuredClone(pack);
    delete legacyPack.evidence_graph;
    expect(validateResearchPack(legacyPack)).toEqual(legacyPack);
    const invalidGraph = structuredClone(pack);
    invalidGraph.evidence_graph!.claims[0].report_id = "report_missing";
    try {
      validateResearchPack(invalidGraph);
      throw new Error("expected invalid evidence graph");
    } catch (error) {
      expect((error as { details?: string[] }).details).toEqual(expect.arrayContaining([
        expect.stringContaining("report_id is absent"),
      ]));
    }
    const reports = await readResearchReport(env, submitted.job_id);
    expect(reports).toHaveLength(1);
    const appendix = await readEvidenceAppendix(env, submitted.job_id);
    expect(appendix).toMatchObject({ evidence: [{ evidence_id: ITEM_ID, source_id: "coingecko_markets_api" }] });
  });

  it("keeps actions-backed jobs queued until a matching GitHub OIDC completion callback", async () => {
    await arrange();
    const request = researchRequest("actions-callback-20260820");
    request.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    expect(submitted.status).toBe("queued");
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:10:30Z"),
      { dispatchFetch: async () => new Response(null, { status: 204 }) },
    );
    const completed = await completeResearchJob(
      env,
      {
        schema_version: 1,
        operation: "complete_research_job",
        job_id: submitted.job_id,
        run_id: RUN_ID,
        plan_id: PLAN_ID,
        alignment_id: ALIGNMENT_ID,
        research_target: request.target,
        research_requirement_id: submitted.planner!.requirement.requirement_id,
        research_source_ids: submitted.planner!.source_bundle.source_ids,
        workflow_run_id: "32330093877",
        commit_sha: COMMIT_SHA,
      },
      { workflowRunId: "32330093877", commitSha: COMMIT_SHA },
      new Date("2026-08-20T04:12:00Z"),
      {
        runAi: async () => ({ response: JSON.stringify({
          bull_case: [{ text: "positive", confidence: 0.7, evidence_ids: [ITEM_ID] }],
          bear_case: [{ text: "negative", confidence: 0.6, evidence_ids: [ITEM_ID] }],
          risk_view: [{ text: "risk", confidence: 0.8, evidence_ids: [ITEM_ID] }],
        }) }),
      },
    );
    expect(completed.status).toBe("partial");
    expect(completed.report_count).toBe(1);
  });

  it("rejects a callback for a different planner requirement", async () => {
    await arrange();
    const request = researchRequest("actions-callback-mismatch-20260820");
    request.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:10:30Z"),
      { dispatchFetch: async () => new Response(null, { status: 204 }) },
    );
    await expect(completeResearchJob(
      env,
      {
        schema_version: 1,
        operation: "complete_research_job",
        job_id: submitted.job_id,
        run_id: RUN_ID,
        plan_id: PLAN_ID,
        alignment_id: ALIGNMENT_ID,
        research_target: request.target,
        research_requirement_id: "req_different_job",
        research_source_ids: submitted.planner!.source_bundle.source_ids,
        workflow_run_id: "32330093877",
        commit_sha: COMMIT_SHA,
      },
      { workflowRunId: "32330093877", commitSha: COMMIT_SHA },
      new Date("2026-08-20T04:12:00Z"),
      { runAi: async () => ({ response: "{}" }) },
    )).rejects.toMatchObject({ code: "research_requirement_mismatch" });
  });

  it("rejects a callback whose target differs from the submitted research job", async () => {
    await arrange();
    const request = researchRequest("actions-target-mismatch-20260820");
    request.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:10:30Z"),
      { dispatchFetch: async () => new Response(null, { status: 204 }) },
    );
    await expect(completeResearchJob(
      env,
      {
        schema_version: 1,
        operation: "complete_research_job",
        job_id: submitted.job_id,
        run_id: RUN_ID,
        plan_id: PLAN_ID,
        alignment_id: ALIGNMENT_ID,
        research_target: { kind: "crypto", symbol: "ETH" },
        research_requirement_id: submitted.planner!.requirement.requirement_id,
        research_source_ids: submitted.planner!.source_bundle.source_ids,
        workflow_run_id: "32330093877",
        commit_sha: COMMIT_SHA,
      },
      { workflowRunId: "32330093877", commitSha: COMMIT_SHA },
      new Date("2026-08-20T04:12:00Z"),
      { runAi: async () => ({ response: "{}" }) },
    )).rejects.toMatchObject({ code: "research_target_mismatch" });
  });

  it("moves an actions-backed job out of queued when the workflow reports failure", async () => {
    await arrange();
    const request = researchRequest("actions-failure-callback-20260820");
    request.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:10:30Z"),
      { dispatchFetch: async () => new Response(null, { status: 204 }) },
    );

    const failed = await failResearchJob(
      env,
      {
        schema_version: 1,
        operation: "fail_research_job",
        job_id: submitted.job_id,
        research_target: request.target,
        research_requirement_id: submitted.planner!.requirement.requirement_id,
        error_code: "actions_workflow_failed",
        workflow_run_id: "32330093877",
        commit_sha: COMMIT_SHA,
      },
      { workflowRunId: "32330093877", commitSha: COMMIT_SHA },
      new Date("2026-08-20T04:13:00Z"),
    );
    expect(failed).toMatchObject({
      job_id: submitted.job_id,
      status: "failed",
      stage: "failed",
      error_code: "actions_workflow_failed",
      retryable: true,
      next_action: "retry_research_job",
    });
    expect(await env.DB.prepare(
      "SELECT stage, status FROM audit_events WHERE run_id = ? ORDER BY happened_at DESC LIMIT 1",
    ).bind(submitted.job_id).first()).toMatchObject({
      stage: "research_job_failed",
      status: "completed",
    });
  });

  it("rejects a failure callback whose target or planner requirement is not frozen", async () => {
    await arrange();
    const request = researchRequest("actions-failure-callback-mismatch-20260820");
    request.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:10:30Z"),
      { dispatchFetch: async () => new Response(null, { status: 204 }) },
    );
    const baseFailure = {
      schema_version: 1,
      operation: "fail_research_job" as const,
      job_id: submitted.job_id,
      research_target: request.target,
      research_requirement_id: submitted.planner!.requirement.requirement_id,
      error_code: "actions_admission_denied" as const,
      workflow_run_id: "32330093877",
      commit_sha: COMMIT_SHA,
    };
    await expect(failResearchJob(
      env,
      { ...baseFailure, research_target: { kind: "crypto", symbol: "ETH" } },
      { workflowRunId: "32330093877", commitSha: COMMIT_SHA },
      new Date("2026-08-20T04:13:00Z"),
    )).rejects.toMatchObject({ code: "research_target_mismatch" });
    await expect(failResearchJob(
      env,
      { ...baseFailure, research_requirement_id: "req_different_job" },
      { workflowRunId: "32330093877", commitSha: COMMIT_SHA },
      new Date("2026-08-20T04:13:00Z"),
    )).rejects.toMatchObject({ code: "research_requirement_mismatch" });
  });

  it("routes the failure callback through the OIDC handler boundary", async () => {
    const request = researchRequest("actions-failure-handler-20260820");
    request.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:10:30Z"),
      { dispatchFetch: async () => new Response(null, { status: 204 }) },
    );
    const handler = createHandler({ authenticate: async () => ({
      workflowRunId: "32330093877",
      commitSha: COMMIT_SHA,
    }), });
    const response = await handler.fetch(new Request("https://ingest.example/v1/research/jobs/fail", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer oidc" },
      body: JSON.stringify({
        schema_version: 1,
        operation: "fail_research_job",
        job_id: submitted.job_id,
        research_target: request.target,
        research_requirement_id: submitted.planner!.requirement.requirement_id,
        error_code: "actions_workflow_failed",
        workflow_run_id: "32330093877",
        commit_sha: COMMIT_SHA,
      }),
    }), env);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      job_id: submitted.job_id,
      status: "failed",
      error_code: "actions_workflow_failed",
    });
  });

  it("rejects a callback whose source bundle differs from the frozen planner", async () => {
    await arrange();
    const request = researchRequest("actions-source-bundle-mismatch-20260820");
    request.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:10:30Z"),
      { dispatchFetch: async () => new Response(null, { status: 204 }) },
    );
    const mismatchedSources = [...submitted.planner!.source_bundle.source_ids];
    mismatchedSources[0] = "unapproved_source";
    await expect(completeResearchJob(
      env,
      {
        schema_version: 1,
        operation: "complete_research_job",
        job_id: submitted.job_id,
        run_id: RUN_ID,
        plan_id: PLAN_ID,
        alignment_id: ALIGNMENT_ID,
        research_target: request.target,
        research_requirement_id: submitted.planner!.requirement.requirement_id,
        research_source_ids: mismatchedSources,
        workflow_run_id: "32330093877",
        commit_sha: COMMIT_SHA,
      },
      { workflowRunId: "32330093877", commitSha: COMMIT_SHA },
      new Date("2026-08-20T04:12:00Z"),
      { runAi: async () => ({ response: "{}" }) },
    )).rejects.toMatchObject({ code: "research_source_bundle_mismatch" });
  });

  it("rejects a published run containing an unapproved source", async () => {
    await arrange();
    const request = researchRequest("actions-run-source-mismatch-20260820");
    request.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:10:30Z"),
      { dispatchFetch: async () => new Response(null, { status: 204 }) },
    );
    await env.DB.prepare("UPDATE raw_items SET source_id = ? WHERE item_id = ?")
      .bind("unapproved_source", ITEM_ID)
      .run();
    await expect(completeResearchJob(
      env,
      {
        schema_version: 1,
        operation: "complete_research_job",
        job_id: submitted.job_id,
        run_id: RUN_ID,
        plan_id: PLAN_ID,
        alignment_id: ALIGNMENT_ID,
        research_target: request.target,
        research_requirement_id: submitted.planner!.requirement.requirement_id,
        research_source_ids: submitted.planner!.source_bundle.source_ids,
        workflow_run_id: "32330093877",
        commit_sha: COMMIT_SHA,
      },
      { workflowRunId: "32330093877", commitSha: COMMIT_SHA },
      new Date("2026-08-20T04:12:00Z"),
      { runAi: async () => ({ response: "{}" }) },
    )).rejects.toMatchObject({ code: "research_run_source_unapproved" });
  });

  it("requeues a latest-published job for bounded background retry", async () => {
    const submitted = await submitResearchJob(
      env,
      researchRequest("latest-retry-20260820"),
      auth,
      new Date("2026-08-20T04:10:00Z"),
    );
    const retried = await retryResearchJob(env, submitted.job_id, new Date("2026-08-20T04:11:00Z"));
    expect(retried).toMatchObject({
      status: "queued",
      stage: "queued",
      next_action: "poll_job_status",
      execute_job_id: submitted.job_id,
    });
  });

  it("fails an actions refresh closed when the dispatch credential is absent", async () => {
    const request = researchRequest("actions-dispatch-missing-20260820");
    request.requirements.source_strategy = "actions";
    const noDispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    noDispatchEnv.GITHUB_DISPATCH_TOKEN = "";
    const submitted = await submitResearchJob(noDispatchEnv, request, auth, new Date("2026-08-20T04:10:00Z"));
    const blocked = await dispatchActionsResearchJob(
      noDispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:11:00Z"),
      { dispatchFetch: async () => new Response(null, { status: 204 }) },
    );
    expect(blocked).toMatchObject({
      status: "blocked",
      stage: "blocked",
      retryable: true,
      next_action: "configure_actions_dispatch_and_retry",
      error_code: "actions_dispatch_not_configured",
    });
    expect(blocked.planner?.source_bundle.strategy).toBe("refresh");
  });

  it("dispatches only the approved source bundle and records the dispatch id", async () => {
    const request = researchRequest("actions-dispatch-success-20260820");
    request.requirements.source_strategy = "actions";
    const submitted = await submitResearchJob(env, request, auth, new Date("2026-08-20T04:10:00Z"));
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    let dispatchBody: Record<string, unknown> | null = null;
    const dispatched = await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:11:00Z"),
      {
        dispatchFetch: async (_input, init) => {
          dispatchBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
          return new Response(null, { status: 204 });
        },
      },
    );
    expect(dispatched).toMatchObject({
      status: "queued",
      stage: "dispatching",
      progress: 0,
      retryable: true,
      next_action: "wait_for_actions",
    });
    expect(dispatched.error_code).toBeNull();
    expect(await env.DB.prepare("SELECT dispatch_id FROM research_jobs WHERE job_id = ?").bind(submitted.job_id).first()).toMatchObject({
      dispatch_id: `workflow:topic-radar.yml:${submitted.job_id}`,
    });
    expect(dispatchBody).toMatchObject({
      ref: "main",
      inputs: {
        research_job_id: submitted.job_id,
        research_requirement_id: expect.stringMatching(/^req_/),
      },
    });
    expect(JSON.stringify(dispatchBody)).not.toContain("private");
    const replayedDispatch = await dispatchActionsResearchJob(
      dispatchEnv,
      submitted.job_id,
      new Date("2026-08-20T04:12:00Z"),
      {
        dispatchFetch: async () => {
          throw new Error("duplicate dispatch must not be attempted");
        },
      },
    );
    expect(replayedDispatch).toMatchObject({
      status: "queued",
      stage: "dispatching",
      next_action: "wait_for_actions",
    });
  });

  it("exposes MCP initialize, tools/list, submit, status and private pack read-back", async () => {
    await arrange();
    const handler = createHandler({
      authenticateMcp: async () => auth,
      runAi: async (_env, _model, input) => {
        expect((input.messages as Array<{ content: string }>)[1].content).toContain("RESEARCH_TARGET=crypto:BTC");
        expect((input.messages as Array<{ content: string }>)[1].content).toContain("RESEARCH_QUESTION=What are the strongest current drivers and risks for BTC?");
        return {
          response: JSON.stringify({
            bull_case: [{ text: "positive", confidence: 0.7, evidence_ids: [ITEM_ID] }],
            bear_case: [{ text: "negative", confidence: 0.6, evidence_ids: [ITEM_ID] }],
            risk_view: [{ text: "risk", confidence: 0.8, evidence_ids: [ITEM_ID] }],
          }),
        };
      },
    });
    const call = async (body: unknown) => handler.fetch(new Request("https://ingest.example/mcp", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer test-token" },
      body: JSON.stringify(body),
    }), env);
    const stream = await handler.fetch(new Request("https://ingest.example/mcp", {
      method: "GET",
      headers: { Authorization: "Bearer test-token" },
    }), env);
    expect(stream.status).toBe(200);
    expect(stream.headers.get("content-type")).toContain("text/event-stream");
    const streamText = await stream.text();
    expect(streamText).toContain("event: endpoint");
    expect(streamText).toContain("/mcp?sessionId=");
    const initialize = await call({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} });
    expect(initialize.status).toBe(200);
    expect(await initialize.json()).toMatchObject({ result: { capabilities: { tools: {} } } });
    expect(await (await call({ jsonrpc: "2.0", id: 1.1, method: "ping" })).json()).toMatchObject({ result: {} });
    expect(await (await call({ jsonrpc: "2.0", method: "notifications/initialized" })).json()).toMatchObject({ jsonrpc: "2.0" });
    expect(await (await call({ jsonrpc: "2.0", id: 1.2, method: "unknown/method" })).json()).toMatchObject({ error: { code: -32601, message: "method_not_found" } });
    expect(await (await call({ jsonrpc: "2.0", id: 1.3, method: "tools/call", params: {} })).json()).toMatchObject({ error: { code: -32602, message: "tool_name_required" } });
    expect(await (await call({ jsonrpc: "2.0", id: 1.4, method: "tools/call", params: { name: "not-a-tool" } })).json()).toMatchObject({ error: { code: -32602, message: "tool_not_found" } });
    expect(await (await call({ jsonrpc: "2.0", id: 1.45, method: "tools/call", params: null })).json()).toMatchObject({ error: "invalid_jsonrpc_request" });
    expect(await (await call({ jsonrpc: "2.0", id: 1.5, method: "tools/call", params: { name: "get_job_status", arguments: { job_id: "a", request_id: "b" } } })).json()).toMatchObject({ result: { isError: true, structuredContent: { error: "job_id_or_request_id_exclusive" } } });
    expect(await (await call({ jsonrpc: "2.0", id: 1.6, method: "tools/call", params: { name: "get_job_status", arguments: {} } })).json()).toMatchObject({ result: { isError: true, structuredContent: { error: "job_id_required" } } });
    const resolved = await call({ jsonrpc: "2.0", id: 1.7, method: "tools/call", params: {
      name: "resolve_target",
      arguments: { kind: "equity", symbol: "nvda", name: "NVIDIA", market: "NASDAQ" },
    } });
    expect(await resolved.json()).toMatchObject({ result: { structuredContent: { target: { symbol: "NVDA", name: "NVIDIA", market: "NASDAQ" } } } });
    const badUrl = await call({ jsonrpc: "2.0", id: 1.8, method: "tools/call", params: {
      name: "resolve_target",
      arguments: { kind: "url", url: "not a url" },
    } });
    expect(await badUrl.json()).toMatchObject({ result: { isError: true, structuredContent: { error: "target_url_invalid" } } });
    const listed = await call({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
    const listedBody = await listed.json<{ result: { tools: Array<{ name: string; inputSchema: Record<string, unknown> }> } }>();
    expect(listedBody).toMatchObject({ result: { tools: expect.arrayContaining([
      expect.objectContaining({ name: "submit_research_job" }),
      expect.objectContaining({ name: "retry_research_job" }),
      expect.objectContaining({ name: "get_job_status" }),
      expect.objectContaining({ name: "get_research_pack" }),
    ]) } });
    const plannerTool = listedBody.result.tools.find((tool) => tool.name === "plan_research_sources");
    expect(plannerTool?.inputSchema).toMatchObject({
      required: ["target", "requirements"],
      properties: {
        target: { required: ["kind"] },
        requirements: { required: ["question", "source_strategy", "include_market_data", "include_topic_radar", "report_profile", "max_sources"] },
      },
    });
    const resolveTool = listedBody.result.tools.find((tool) => tool.name === "resolve_target");
    expect(resolveTool?.inputSchema).toMatchObject({ required: ["kind"] });
    const statusTool = listedBody.result.tools.find((tool) => tool.name === "get_job_status");
    expect(statusTool?.inputSchema).toMatchObject({
      anyOf: [
        { required: ["job_id"] },
        { required: ["request_id"] },
      ],
    });
    const malformedPlan = await call({ jsonrpc: "2.0", id: 2.25, method: "tools/call", params: {
      name: "plan_research_sources",
      arguments: {
        target: { asset: "BTC", asset_class: "crypto" },
        requirements: researchRequest("planner-malformed-20260820").requirements,
      },
    } });
    expect(await malformedPlan.json()).toMatchObject({
      result: { isError: true, structuredContent: { error: "invalid_payload" } },
    });
    const invalidTarget = await call({ jsonrpc: "2.0", id: 2.2, method: "tools/call", params: {
      name: "resolve_target",
      arguments: { kind: "not-a-target", symbol: "BTC" },
    } });
    expect(await invalidTarget.json()).toMatchObject({
      result: { isError: true, structuredContent: { error: "target_kind_invalid" } },
    });
    const missingUrl = await call({ jsonrpc: "2.0", id: 2.21, method: "tools/call", params: {
      name: "resolve_target",
      arguments: { kind: "url" },
    } });
    expect(await missingUrl.json()).toMatchObject({
      result: { isError: true, structuredContent: { error: "target_url_required" } },
    });
    const stringVersionSubmit = await call({ jsonrpc: "2.0", id: 2.3, method: "tools/call", params: {
      name: "submit_research_job",
      arguments: { ...researchRequest("mcp-string-version-20260820"), schema_version: "1" },
    } });
    expect(await stringVersionSubmit.json()).toMatchObject({
      result: { structuredContent: { status: "queued", stage: "queued" } },
    });
    const planned = await call({ jsonrpc: "2.0", id: 2.5, method: "tools/call", params: {
      name: "plan_research_sources",
      arguments: {
        target: researchRequest("planner-mcp-20260820").target,
        requirements: researchRequest("planner-mcp-20260820").requirements,
      },
    } });
    expect(await planned.json()).toMatchObject({
      result: {
        structuredContent: {
          source_bundle: {
            source_count: expect.any(Number),
            strategy: expect.any(String),
          },
          requirement: { target: { symbol: "BTC" } },
        },
      },
    });
    const submitted = await call({ jsonrpc: "2.0", id: 3, method: "tools/call", params: {
      name: "submit_research_job",
      arguments: researchRequest("mcp-test-20260820"),
    } });
    const submittedBody = await submitted.json<{ result: { structuredContent: { job_id: string; request_id: string } } }>();
    expect(submittedBody.result.structuredContent.job_id).toMatch(/^research_/);
    await executeResearchJob(env, submittedBody.result.structuredContent.job_id, {
      runAi: async () => ({ response: JSON.stringify({
        bull_case: [{ text: "positive", confidence: 0.7, evidence_ids: [ITEM_ID] }],
        bear_case: [{ text: "negative", confidence: 0.6, evidence_ids: [ITEM_ID] }],
        risk_view: [{ text: "risk", confidence: 0.8, evidence_ids: [ITEM_ID] }],
      }) }),
    }, new Date("2026-08-20T04:12:00Z"));
    const packed = await call({ jsonrpc: "2.0", id: 4, method: "tools/call", params: {
      name: "get_research_pack",
      arguments: { job_id: submittedBody.result.structuredContent.job_id },
    } });
    expect(await packed.json()).toMatchObject({ result: { structuredContent: { schema_version: 1 } } });
  });

  it("runs the latest-published job through handler waitUntil and reads all App A outputs via MCP", async () => {
    await arrange();
    const waits: Promise<unknown>[] = [];
    const handler = createHandler({
      authenticateMcp: async () => auth,
      runAi: async () => ({
        response: JSON.stringify({
          bull_case: [{ text: "positive", confidence: 0.7, evidence_ids: [ITEM_ID] }],
          bear_case: [{ text: "negative", confidence: 0.6, evidence_ids: [ITEM_ID] }],
          risk_view: [{ text: "risk", confidence: 0.8, evidence_ids: [ITEM_ID] }],
        }),
      }),
    });
    const call = async (body: unknown, context?: ExecutionContext) => handler.fetch(new Request("https://ingest.example/mcp", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer test-token" },
      body: JSON.stringify(body),
    }), env, context);
    const context = {
      waitUntil(promise: Promise<unknown>) { waits.push(promise); },
    } as ExecutionContext;
    const submitted = await call({ jsonrpc: "2.0", id: 30, method: "tools/call", params: {
      name: "submit_research_job",
      arguments: researchRequest("mcp-handler-e2e-20260820"),
    } }, context);
    const submittedBody = await submitted.json<{ result: { structuredContent: { job_id: string; request_id: string; status: string } } }>();
    expect(submittedBody.result.structuredContent).toMatchObject({ status: "queued" });
    expect(waits).toHaveLength(1);
    await Promise.all(waits);

    const jobId = submittedBody.result.structuredContent.job_id;
    const status = await call({ jsonrpc: "2.0", id: 31, method: "tools/call", params: {
      name: "get_job_status",
      arguments: { job_id: jobId },
    } });
    expect(await status.json()).toMatchObject({
      result: { structuredContent: { job_id: jobId, status: "partial", stage: "published", report_count: 1 } },
    });
    const statusByRequestId = await call({ jsonrpc: "2.0", id: 31.5, method: "tools/call", params: {
      name: "get_job_status",
      arguments: { request_id: submittedBody.result.structuredContent.request_id },
    } });
    expect(await statusByRequestId.json()).toMatchObject({
      result: { structuredContent: { job_id: jobId, request_id: submittedBody.result.structuredContent.request_id, status: "partial" } },
    });
    const pack = await call({ jsonrpc: "2.0", id: 32, method: "tools/call", params: {
      name: "get_research_pack",
      arguments: { job_id: jobId },
    } });
    expect(await pack.json()).toMatchObject({
      result: { structuredContent: { job_id: jobId, reports: [{ report_id: expect.stringMatching(/^report_/) }] } },
    });
    const report = await call({ jsonrpc: "2.0", id: 33, method: "tools/call", params: {
      name: "get_research_report",
      arguments: { job_id: jobId },
    } });
    expect(await report.json()).toMatchObject({
      result: { structuredContent: { job_id: jobId, reports: [{ evidence_ids: [ITEM_ID] }] } },
    });
    const appendix = await call({ jsonrpc: "2.0", id: 34, method: "tools/call", params: {
      name: "get_evidence_appendix",
      arguments: { job_id: jobId },
    } });
    expect(await appendix.json()).toMatchObject({
      result: { structuredContent: { job_id: jobId, evidence: [{ evidence_id: ITEM_ID }] } },
    });
  });

  it("uses the retry MCP tool to dispatch a previously blocked actions job", async () => {
    const handler = createHandler({
      authenticateMcp: async () => auth,
      dispatchFetch: async () => new Response(null, { status: 204 }),
      runAi: async () => ({ response: "{}" }),
    });
    const call = async (body: unknown, requestEnv: Env) => handler.fetch(new Request("https://ingest.example/mcp", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer test-token" },
      body: JSON.stringify(body),
    }), requestEnv);
    const request = researchRequest("mcp-retry-actions-20260820");
    request.requirements.source_strategy = "actions";
    const noDispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    noDispatchEnv.GITHUB_DISPATCH_TOKEN = "";
    const submitted = await call({ jsonrpc: "2.0", id: 20, method: "tools/call", params: {
      name: "submit_research_job",
      arguments: request,
    } }, noDispatchEnv);
    const submittedBody = await submitted.json<{ result: { structuredContent: { job_id: string; status: string } } }>();
    expect(submittedBody.result.structuredContent).toMatchObject({ status: "blocked" });
    const dispatchEnv = Object.create(env) as Env & { GITHUB_DISPATCH_TOKEN?: string };
    dispatchEnv.GITHUB_DISPATCH_TOKEN = "dispatch-test-token";
    const retried = await call({ jsonrpc: "2.0", id: 21, method: "tools/call", params: {
      name: "retry_research_job",
      arguments: { job_id: submittedBody.result.structuredContent.job_id },
    } }, dispatchEnv);
    expect(await retried.json()).toMatchObject({
      result: { structuredContent: { status: "queued", stage: "dispatching", next_action: "wait_for_actions" } },
    });
  });
});
