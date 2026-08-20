import { Validator, type Schema } from "@cfworker/json-schema";

import {
  type ResearchJobRequest,
  type ResearchRequirements,
  type ResearchTarget,
  validateResearchJobRequest,
} from "./contracts";
import { PayloadValidationError } from "./contracts";
import researchRequirementSchema from "../../schemas/research-requirement.schema.json";
import sourceBundleManifestSchema from "../../schemas/source-bundle-manifest.schema.json";


const PLANNER_VERSION = "research-requirement-planner-v1";
const MIN_RADAR_SOURCES = 12;
const MAX_RADAR_SOURCES = 20;
const DEFAULT_FRESHNESS_HOURS = 24;
const MARKET_FRESHNESS_HOURS = 6;
const requirementValidator = new Validator(researchRequirementSchema as Schema, "2020-12", false);
const sourceBundleValidator = new Validator(sourceBundleManifestSchema as Schema, "2020-12", false);

const SOURCE_LAYERS: Record<string, "market" | "news" | "official" | "social"> = {
  federal_reserve_press_rss: "official",
  ecb_press_rss: "official",
  bbc_business_rss: "news",
  cnbc_top_news_rss: "news",
  marketwatch_topstories_rss: "news",
  hacker_news_finance_api: "social",
  money_stackexchange_api: "social",
  quant_stackexchange_api: "social",
  coingecko_markets_api: "market",
  world_bank_growth_api: "official",
  openbb_github_issues_api: "social",
  tradingagents_github_issues_api: "social",
  openbb_github_discussions_browser: "social",
  tradingview_ideas_browser: "social",
  bogleheads_investing_browser: "social",
};

const SOURCE_GROUPS: Record<string, readonly string[]> = {
  official: ["federal_reserve_press_rss", "ecb_press_rss", "world_bank_growth_api"],
  news: ["bbc_business_rss", "cnbc_top_news_rss", "marketwatch_topstories_rss"],
  market: ["coingecko_markets_api"],
  social: [
    "hacker_news_finance_api",
    "money_stackexchange_api",
    "quant_stackexchange_api",
    "openbb_github_issues_api",
    "tradingagents_github_issues_api",
    "openbb_github_discussions_browser",
    "tradingview_ideas_browser",
    "bogleheads_investing_browser",
  ],
};

export interface ResearchRequirement {
  schema_version: 1;
  requirement_id: string;
  target: ResearchTarget;
  question: string;
  objective: "screen" | "research" | "monitor" | "compare" | "due_diligence" | "meeting_battle_card" | "decision_support";
  as_of: string;
  horizon: "intraday" | "days" | "months" | "years" | "transaction";
  constraints: {
    currency?: string;
    risk_tolerance?: string;
    jurisdiction?: string;
    portfolio_context_ref?: string;
  };
  requested_outputs: Array<"quick_card" | "detailed_report" | "evidence_appendix">;
  include_market_data: boolean;
  include_topic_radar: boolean;
  max_sources: number;
  source_strategy: "latest_published" | "actions";
}

export interface SnapshotSourceState {
  status: "success" | "partial" | "failed";
  last_successful_crawl: string | null;
}

export interface SnapshotSufficiencyInput {
  snapshot_id: string | null;
  run_id: string | null;
  published_at: string | null;
  partial: boolean;
  now: string;
  source_states: Record<string, SnapshotSourceState>;
}

export interface SnapshotSufficiency {
  status: "sufficient" | "refresh_required" | "blocked";
  coverage_ratio: number;
  reasons: string[];
}

export interface SourceBundleManifest {
  schema_version: 1;
  manifest_id: string;
  requirement_id: string;
  strategy: "reuse" | "refresh" | "blocked";
  source_ids: string[];
  source_count: number;
  layers: Array<"market" | "news" | "official" | "social">;
  reused_snapshot_id: string | null;
  sufficiency: SnapshotSufficiency;
  missing_data: string[];
  planner_version: string;
  generated_at: string;
  reason?:
    | "explicit_refresh"
    | "sufficiency_refresh_required"
    | "reuse_latest_published"
    | "document_engine_required"
    | "source_budget_too_low"
    | "market_target_not_supported";
}

export interface PersistedResearchPlan {
  requirement: ResearchRequirement;
  source_bundle: SourceBundleManifest;
  snapshot: SnapshotSufficiencyInput;
}

export async function buildPersistedResearchPlan(
  db: D1Database,
  payload: unknown,
  requestId: string,
  now: Date,
): Promise<PersistedResearchPlan> {
  const requirement = buildResearchRequirement(payload, requestId, now);
  const preliminary = planSourceBundle(requirement, undefined, now);
  if (preliminary.strategy === "blocked") {
    return {
      requirement,
      source_bundle: preliminary,
      snapshot: emptySnapshot(now),
    };
  }
  const selected = preliminary.source_ids;
  const current = await db.prepare(
    `SELECT current_snapshot.snapshot_id, topic_snapshots.run_id,
            topic_snapshots.as_of, topic_snapshots.partial, runs.published_at
     FROM current_snapshot
     JOIN topic_snapshots ON topic_snapshots.snapshot_id = current_snapshot.snapshot_id
     JOIN runs ON runs.run_id = topic_snapshots.run_id
     WHERE runs.status = 'published' AND current_snapshot.singleton_id = 1`,
  ).first<{
    snapshot_id: string;
    run_id: string;
    as_of: string;
    partial: number;
    published_at: string | null;
  }>();
  const placeholders = selected.map(() => "?").join(", ");
  const states = selected.length === 0
    ? []
    : (await db.prepare(
      `SELECT source_id, status, last_successful_crawl
       FROM source_state WHERE source_id IN (${placeholders})`,
    ).bind(...selected).all<{
      source_id: string;
      status: "success" | "partial" | "failed";
      last_successful_crawl: string | null;
    }>()).results;
  const sourceStates = Object.fromEntries(states.map((state) => [state.source_id, {
    status: state.status,
    last_successful_crawl: state.last_successful_crawl,
  }]));
  const snapshot: SnapshotSufficiencyInput = {
    snapshot_id: current?.snapshot_id ?? null,
    run_id: current?.run_id ?? null,
    published_at: current?.published_at ?? null,
    partial: current?.partial === 1,
    now: now.toISOString(),
    source_states: sourceStates,
  };
  const sufficiency = evaluateSnapshotSufficiency(requirement, snapshot);
  const sourceBundle = planSourceBundle(requirement, sufficiency, now);
  const normalized: PersistedResearchPlan = {
    requirement,
    source_bundle: {
      ...sourceBundle,
      reused_snapshot_id: sufficiency.status === "sufficient" ? snapshot.snapshot_id : null,
    },
    snapshot,
  };
  validatePlannerPayload(requirementValidator, "research-requirement", normalized.requirement);
  validatePlannerPayload(sourceBundleValidator, "source-bundle-manifest", normalized.source_bundle);
  return normalized;
}

export function buildResearchRequirement(
  payload: unknown,
  requestId: string,
  now: Date,
): ResearchRequirement {
  let request: ResearchJobRequest;
  try {
    request = validateResearchJobRequest(payload);
  } catch (error) {
    if (error instanceof PayloadValidationError) throw error;
    throw new PayloadValidationError("research-requirement", [
      error instanceof Error ? error.message : "invalid research request",
    ]);
  }
  const requirements = request.requirements as ResearchRequirements & {
    objective?: ResearchRequirement["objective"];
    as_of?: string;
    horizon?: ResearchRequirement["horizon"];
    constraints?: ResearchRequirement["constraints"];
    requested_outputs?: ResearchRequirement["requested_outputs"];
  };
  const target = normalizeTarget(request.target);
  const requestedOutputs = requirements.requested_outputs
    ?? (requirements.report_profile === "detailed_traceable"
      ? ["detailed_report", "evidence_appendix"]
      : ["quick_card", "evidence_appendix"]);
  const normalized: ResearchRequirement = {
    schema_version: 1,
    requirement_id: `req_${safeId(requestId)}`,
    target,
    question: requirements.question.trim(),
    objective: requirements.objective ?? "research",
    as_of: requirements.as_of ?? "latest",
    horizon: requirements.horizon ?? "months",
    constraints: { ...(requirements.constraints ?? {}) },
    requested_outputs: [...new Set(requestedOutputs)],
    include_market_data: requirements.include_market_data,
    include_topic_radar: requirements.include_topic_radar,
    max_sources: requirements.max_sources,
    source_strategy: requirements.source_strategy,
  };
  validatePlannerPayload(requirementValidator, "research-requirement", normalized);
  return normalized;
}

export function planSourceBundle(
  requirement: ResearchRequirement,
  sufficiency: SnapshotSufficiency = {
    status: "refresh_required",
    coverage_ratio: 0,
    reasons: ["sufficiency_not_checked"],
  },
  now = new Date(),
): SourceBundleManifest {
  const manifestId = `bundle_${safeId(requirement.requirement_id)}`;
  if (requirement.target.kind === "url") {
    return finalizeBundle({
      schema_version: 1,
      manifest_id: manifestId,
      requirement_id: requirement.requirement_id,
      strategy: "blocked",
      source_ids: [],
      source_count: 0,
      layers: [],
      reused_snapshot_id: null,
      sufficiency: { status: "blocked", coverage_ratio: 0, reasons: ["document_engine_required"] },
      missing_data: ["document_content", "document_provenance"],
      planner_version: PLANNER_VERSION,
      generated_at: now.toISOString(),
      reason: "document_engine_required",
    });
  }
  if (requirement.include_market_data && requirement.target.kind !== "crypto") {
    return finalizeBundle({
      schema_version: 1,
      manifest_id: manifestId,
      requirement_id: requirement.requirement_id,
      strategy: "blocked",
      source_ids: [],
      source_count: 0,
      layers: [],
      reused_snapshot_id: null,
      sufficiency: {
        status: "blocked",
        coverage_ratio: 0,
        reasons: ["market_target_not_supported"],
      },
      missing_data: ["target_market_provider"],
      planner_version: PLANNER_VERSION,
      generated_at: now.toISOString(),
      reason: "market_target_not_supported",
    });
  }
  const sourceIds = selectSourceIds(requirement);
  if (sourceIds.length < MIN_RADAR_SOURCES) {
    return finalizeBundle({
      schema_version: 1,
      manifest_id: manifestId,
      requirement_id: requirement.requirement_id,
      strategy: "blocked",
      source_ids: sourceIds,
      source_count: sourceIds.length,
      layers: uniqueLayers(sourceIds),
      reused_snapshot_id: null,
      sufficiency: { status: "blocked", coverage_ratio: 0, reasons: ["source_budget_too_low"] },
      missing_data: ["minimum_radar_source_count"],
      planner_version: PLANNER_VERSION,
      generated_at: now.toISOString(),
      reason: "source_budget_too_low",
    });
  }
  const explicitRefresh = requirement.source_strategy === "actions";
  const strategy = explicitRefresh || sufficiency.status === "refresh_required" ? "refresh" : "reuse";
  return finalizeBundle({
    schema_version: 1,
    manifest_id: manifestId,
    requirement_id: requirement.requirement_id,
    strategy,
    source_ids: sourceIds,
    source_count: sourceIds.length,
    layers: uniqueLayers(sourceIds),
    reused_snapshot_id: null,
    sufficiency,
    missing_data: sufficiency.status === "sufficient" ? [] : sufficiency.reasons,
    planner_version: PLANNER_VERSION,
    generated_at: now.toISOString(),
    reason: explicitRefresh
      ? "explicit_refresh"
      : sufficiency.status === "refresh_required"
        ? "sufficiency_refresh_required"
        : "reuse_latest_published",
  });
}

export function evaluateSnapshotSufficiency(
  requirement: ResearchRequirement,
  snapshot: SnapshotSufficiencyInput,
): SnapshotSufficiency {
  if (requirement.target.kind === "url") {
    return { status: "blocked", coverage_ratio: 0, reasons: ["document_engine_required"] };
  }
  const selected = selectSourceIds(requirement);
  if (selected.length < MIN_RADAR_SOURCES) {
    return { status: "blocked", coverage_ratio: 0, reasons: ["source_budget_too_low"] };
  }
  const reasons: string[] = [];
  if (!snapshot.snapshot_id || !snapshot.run_id || !snapshot.published_at) reasons.push("no_published_snapshot");
  if (snapshot.partial) reasons.push("snapshot_partial");
  const nowMs = Date.parse(snapshot.now);
  const maxAgeMs = (requirement.include_market_data ? MARKET_FRESHNESS_HOURS : DEFAULT_FRESHNESS_HOURS) * 3_600_000;
  let healthy = 0;
  for (const sourceId of selected) {
    const state = snapshot.source_states[sourceId];
    if (!state) {
      reasons.push(`source_missing:${sourceId}`);
      continue;
    }
    if (state.status !== "success") {
      reasons.push(`source_${state.status}:${sourceId}`);
      continue;
    }
    const crawledMs = state.last_successful_crawl ? Date.parse(state.last_successful_crawl) : Number.NaN;
    if (!Number.isFinite(crawledMs) || !Number.isFinite(nowMs) || nowMs - crawledMs > maxAgeMs) {
      reasons.push(`source_stale:${sourceId}`);
      continue;
    }
    healthy += 1;
  }
  const coverageRatio = selected.length === 0 ? 0 : healthy / selected.length;
  return {
    status: reasons.length === 0 ? "sufficient" : "refresh_required",
    coverage_ratio: coverageRatio,
    reasons: [...new Set(reasons)],
  };
}

function selectSourceIds(requirement: ResearchRequirement): string[] {
  const requiredLayers = requirement.target.kind === "crypto"
    ? (requirement.include_market_data ? ["market", "news", "social"] : ["news", "social"])
    : requirement.target.kind === "industry" || requirement.target.kind === "topic"
      ? ["news", "official", "social"]
      : requirement.include_market_data
        ? ["market", "news", "official", "social"]
        : ["news", "official", "social"];
  const ordered: string[] = [];
  for (const layer of requiredLayers) {
    const candidate = SOURCE_GROUPS[layer]?.[0];
    if (candidate && !ordered.includes(candidate)) ordered.push(candidate);
  }
  for (const layer of requiredLayers) {
    for (const sourceId of SOURCE_GROUPS[layer] ?? []) {
      if (!ordered.includes(sourceId)) ordered.push(sourceId);
    }
  }
  for (const sourceId of Object.keys(SOURCE_LAYERS)) {
    if (!ordered.includes(sourceId)) ordered.push(sourceId);
  }
  return ordered.slice(0, Math.min(MAX_RADAR_SOURCES, requirement.max_sources));
}

function uniqueLayers(sourceIds: string[]): Array<"market" | "news" | "official" | "social"> {
  return [...new Set(sourceIds.map((sourceId) => SOURCE_LAYERS[sourceId]).filter(Boolean))] as Array<"market" | "news" | "official" | "social">;
}

function normalizeTarget(target: ResearchTarget): ResearchTarget {
  return {
    ...target,
    ...(target.symbol ? { symbol: target.symbol.toUpperCase() } : {}),
  };
}

function safeId(value: string): string {
  const normalized = value.toLowerCase().replace(/[^a-z0-9_:-]+/g, "_");
  return normalized.slice(0, 120).replace(/[_:-]+$/, "") || "unknown";
}

function emptySnapshot(now: Date): SnapshotSufficiencyInput {
  return {
    snapshot_id: null,
    run_id: null,
    published_at: null,
    partial: false,
    now: now.toISOString(),
    source_states: {},
  };
}

function finalizeBundle(bundle: SourceBundleManifest): SourceBundleManifest {
  validatePlannerPayload(sourceBundleValidator, "source-bundle-manifest", bundle);
  return bundle;
}

function validatePlannerPayload(
  validator: Validator,
  contract: string,
  payload: unknown,
): void {
  const result = validator.validate(payload);
  if (result.valid) return;
  throw new PayloadValidationError(
    contract,
    result.errors.map((error) => `${error.instanceLocation || "$"}: ${error.error}`),
  );
}
