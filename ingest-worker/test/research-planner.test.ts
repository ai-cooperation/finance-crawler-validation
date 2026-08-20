import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import {
  buildResearchRequirement,
  buildPersistedResearchPlan,
  evaluateSnapshotSufficiency,
  planSourceBundle,
  type ResearchRequirement,
  type SnapshotSufficiencyInput,
} from "../src/research-planner";
import { buildWorkflowDispatchRequest, dispatchResearchWorkflow } from "../src/github-dispatch";


function requirement(overrides: Partial<ResearchRequirement> = {}): ResearchRequirement {
  return {
    schema_version: 1,
    requirement_id: "req_20260820_btc",
    target: { kind: "crypto", symbol: "BTC", name: "Bitcoin", market: "global" },
    question: "What are the current drivers and risks for BTC?",
    objective: "research",
    as_of: "latest",
    horizon: "months",
    constraints: {},
    requested_outputs: ["detailed_report", "evidence_appendix"],
    include_market_data: true,
    include_topic_radar: true,
    max_sources: 12,
    source_strategy: "actions",
    ...overrides,
  };
}

function snapshot(overrides: Partial<SnapshotSufficiencyInput> = {}): SnapshotSufficiencyInput {
  return {
    snapshot_id: "radar_20260820t040000z",
    run_id: "run_20260820t040000z",
    published_at: "2026-08-20T04:00:00Z",
    partial: false,
    now: "2026-08-20T04:30:00Z",
    source_states: {
      coingecko_markets_api: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      bbc_business_rss: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      cnbc_top_news_rss: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      marketwatch_topstories_rss: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      hacker_news_finance_api: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      money_stackexchange_api: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      quant_stackexchange_api: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      openbb_github_issues_api: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      tradingagents_github_issues_api: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      openbb_github_discussions_browser: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      tradingview_ideas_browser: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      bogleheads_investing_browser: { status: "success", last_successful_crawl: "2026-08-20T04:20:00Z" },
      ...overrides.source_states,
    },
    ...overrides,
  };
}

describe("research requirement planner", () => {
  it("normalizes a target request into an auditable requirement", () => {
    const normalized = buildResearchRequirement({
      schema_version: 1,
      operation: "submit_research_job",
      idempotency_key: "planner-test-20260820",
      target: { kind: "crypto", symbol: "btc", name: "Bitcoin" },
      requirements: {
        question: "What are the current drivers and risks for BTC?",
        source_strategy: "actions",
        include_market_data: true,
        include_topic_radar: true,
        report_profile: "detailed_traceable",
        max_sources: 12,
      },
    }, "request-1", new Date("2026-08-20T04:30:00Z"));
    expect(normalized).toMatchObject({
      schema_version: 1,
      requirement_id: "req_request-1",
      target: { symbol: "BTC" },
      as_of: "latest",
      requested_outputs: ["detailed_report", "evidence_appendix"],
    });
  });

  it("selects a bounded heterogeneous bundle for a crypto target", () => {
    const planned = planSourceBundle(requirement());
    expect(planned.strategy).toBe("refresh");
    expect(planned.source_ids.length).toBeGreaterThanOrEqual(12);
    expect(planned.source_ids.length).toBeLessThanOrEqual(20);
    expect(planned.layers).toEqual(expect.arrayContaining(["market", "news", "social"]));
    expect(planned.source_ids).toContain("coingecko_markets_api");
    expect(planned.source_ids).toContain("bbc_business_rss");
  });

  it("does not claim a snapshot is sufficient when it is partial or stale", () => {
    const complete = evaluateSnapshotSufficiency(requirement(), snapshot());
    expect(complete.status).toBe("sufficient");

    const partial = evaluateSnapshotSufficiency(requirement(), snapshot({ partial: true }));
    expect(partial.status).toBe("refresh_required");
    expect(partial.reasons).toContain("snapshot_partial");

    const stale = evaluateSnapshotSufficiency(requirement(), snapshot({
      source_states: {
        ...snapshot().source_states,
        coingecko_markets_api: { status: "success", last_successful_crawl: "2026-08-19T00:00:00Z" },
      },
    }));
    expect(stale.status).toBe("refresh_required");
    expect(stale.reasons).toContain("source_stale:coingecko_markets_api");
  });

  it("requires a fresh complete snapshot and names every missing source condition", () => {
    const result = evaluateSnapshotSufficiency(requirement(), {
      snapshot_id: null,
      run_id: null,
      published_at: null,
      partial: false,
      now: "not-a-date",
      source_states: {
        coingecko_markets_api: { status: "failed", last_successful_crawl: null },
        bbc_business_rss: { status: "partial", last_successful_crawl: "2026-08-20T04:20:00Z" },
        marketwatch_topstories_rss: { status: "success", last_successful_crawl: "not-a-date" },
      },
    });

    expect(result.status).toBe("refresh_required");
    expect(result.reasons).toEqual(expect.arrayContaining([
      "no_published_snapshot",
      "source_failed:coingecko_markets_api",
      "source_partial:bbc_business_rss",
      "source_missing:cnbc_top_news_rss",
      "source_stale:marketwatch_topstories_rss",
    ]));
    expect(result.coverage_ratio).toBe(0);
  });

  it("uses the correct source layers for non-crypto target branches", () => {
    const industry = planSourceBundle(requirement({
      target: { kind: "industry", name: "semiconductors" },
      include_market_data: false,
    }));
    const topic = planSourceBundle(requirement({
      target: { kind: "topic", name: "inflation" },
      include_market_data: false,
    }));
    const equity = planSourceBundle(requirement({
      target: { kind: "equity", symbol: "NVDA", market: "NASDAQ" },
      include_market_data: false,
    }));

    expect(industry.layers).toEqual(expect.arrayContaining(["news", "official", "social"]));
    expect(industry.layers).not.toContain("market");
    expect(topic.layers).toEqual(expect.arrayContaining(["news", "official", "social"]));
    expect(equity.layers).toEqual(expect.arrayContaining(["news", "official", "social"]));
  });

  it("persists a blocked plan before querying snapshot state", async () => {
    const plan = await buildPersistedResearchPlanForTest({
      target: { kind: "url", url: "https://example.com/filing" },
      include_market_data: false,
    });
    expect(plan.source_bundle).toMatchObject({
      strategy: "blocked",
      reason: "document_engine_required",
      source_ids: [],
    });
    expect(plan.snapshot).toMatchObject({ snapshot_id: null, run_id: null, source_states: {} });
  });

  it("fails closed for unsupported document refresh instead of inventing sources", () => {
    const planned = planSourceBundle(requirement({
      target: { kind: "url", url: "https://example.com/research" },
    }));
    expect(planned.strategy).toBe("blocked");
    expect(planned.reason).toBe("document_engine_required");
    expect(planned.source_ids).toEqual([]);
  });

  it("fails closed when the current market provider cannot serve a requested equity target", () => {
    const planned = planSourceBundle(requirement({
      target: { kind: "equity", symbol: "NVDA", market: "NASDAQ" },
    }));

    expect(planned.strategy).toBe("blocked");
    expect(planned.reason).toBe("market_target_not_supported");
    expect(planned.sufficiency.reasons).toContain("market_target_not_supported");
  });

  it("does not attach an unrelated crypto market source when market data is not requested", () => {
    const planned = planSourceBundle(requirement({
      target: { kind: "equity", symbol: "NVDA", market: "NASDAQ" },
      include_market_data: false,
    }));

    expect(planned.strategy).toBe("refresh");
    expect(planned.source_ids).not.toContain("coingecko_markets_api");
    expect(planned.layers).not.toContain("market");
    expect(planned.layers).toEqual(expect.arrayContaining(["news", "official", "social"]));
  });

  it("builds a bounded workflow dispatch request without putting private content in the inputs", () => {
    const request = buildWorkflowDispatchRequest({
      job_id: "research_20260820_abc12345",
      source_ids: ["coingecko_markets_api", "bbc_business_rss"],
      target: { kind: "crypto", symbol: "BTC" },
      requirement_id: "req_request-1",
    });
    expect(request).toMatchObject({
      ref: "main",
      inputs: {
        research_job_id: "research_20260820_abc12345",
        research_source_ids: "[\"coingecko_markets_api\",\"bbc_business_rss\"]",
        research_requirement_id: "req_request-1",
        research_target: "{\"kind\":\"crypto\",\"symbol\":\"BTC\"}",
        research_include_market_data: "true",
      },
    });
    expect(JSON.stringify(request)).not.toContain("content");

    const noMarketRequest = buildWorkflowDispatchRequest({
      job_id: "research_20260820_abc12346",
      source_ids: ["bbc_business_rss", "hacker_news_finance_api"],
      target: { kind: "equity", symbol: "NVDA" },
      requirement_id: "req_request-2",
      include_market_data: false,
    });
    expect(noMarketRequest.inputs.research_include_market_data).toBe("false");
  });

  it("rejects malformed workflow dispatch identities and source budgets", () => {
    const base = {
      job_id: "research_20260820_abc12345",
      source_ids: ["bbc_business_rss"],
      target: { kind: "crypto", symbol: "BTC" },
      requirement_id: "req_request-1",
    };
    expect(() => buildWorkflowDispatchRequest({ ...base, job_id: "bad-job" })).toThrow(/invalid_research_job_id/);
    expect(() => buildWorkflowDispatchRequest({ ...base, source_ids: [] })).toThrow(/invalid_research_source_ids/);
    expect(() => buildWorkflowDispatchRequest({ ...base, source_ids: Array.from({ length: 21 }, (_, i) => `source_${i}`) })).toThrow(/invalid_research_source_ids/);
    expect(() => buildWorkflowDispatchRequest({ ...base, requirement_id: "bad-requirement" })).toThrow(/invalid_research_requirement_id/);
  });

  it("normalizes Actions transport failures into bounded HttpErrors", async () => {
    const envWithToken = { GITHUB_DISPATCH_TOKEN: "dispatch-secret" } as Env;
    const job = {
      job_id: "research_20260820_abc12345",
      source_ids: ["bbc_business_rss"],
      target: { kind: "crypto", symbol: "BTC" },
      requirement: requirement({ include_market_data: false }),
      source_bundle: planSourceBundle(requirement({ include_market_data: false })),
    };
    await expect(dispatchResearchWorkflow(envWithToken, job, async () => {
      throw new Error("synthetic transport failure");
    })).rejects.toMatchObject({ status: 503, code: "actions_dispatch_failed" });
    await expect(dispatchResearchWorkflow(envWithToken, job, async () => new Response(null, { status: 500 })))
      .rejects.toMatchObject({ status: 502, code: "actions_dispatch_failed" });
    await expect(dispatchResearchWorkflow(envWithToken, job, async () => new Response(null, { status: 401 })))
      .rejects.toMatchObject({ status: 503, code: "actions_dispatch_failed" });
    await expect(dispatchResearchWorkflow({} as Env, job, async () => new Response(null, { status: 204 })))
      .rejects.toMatchObject({ status: 503, code: "actions_dispatch_not_configured" });
  });
});

async function buildPersistedResearchPlanForTest(
  overrides: Partial<ResearchJobRequestForTest>,
) {
  const request = {
    schema_version: 1,
    operation: "submit_research_job" as const,
    idempotency_key: "persisted-plan-test-20260820",
    target: overrides.target ?? { kind: "crypto", symbol: "BTC" },
    requirements: {
      question: "Check the target",
      source_strategy: "latest_published" as const,
      include_market_data: overrides.include_market_data ?? true,
      include_topic_radar: true,
      report_profile: "detailed_traceable" as const,
      max_sources: 12,
    },
  };
  return buildPersistedResearchPlan(
    env.DB,
    request,
    "persisted-plan-request",
    new Date("2026-08-20T04:30:00Z"),
  );
}

interface ResearchJobRequestForTest {
  target?: { kind: "crypto" | "equity" | "industry" | "topic" | "url"; symbol?: string; name?: string; market?: string; url?: string };
  include_market_data?: boolean;
}
