import {
  type ResearchAgentRequest,
  type ResearchClaim,
  type ResearchEvidenceNote,
  type ResearchReport,
  type RadarTopic,
  type TopicSnapshot,
  type TradingAgentsRunPlan,
  type ResearchTarget,
  validateResearchAgentRequest,
  validateTopicSnapshot,
  validateTradingAgentsRunPlan,
} from "./contracts";
import type { AuthContext } from "./auth";
import { HttpError, ingestResearchReport, type ResearchReportResult } from "./storage";


export const DEFAULT_RESEARCH_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
export const FALLBACK_RESEARCH_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8";
export const DETERMINISTIC_RESEARCH_MODEL = "deterministic-evidence-v1";
export const RESEARCH_AGENT_VERSION = "tradingagents-cloudflare-ai-v1";
const MAX_EVIDENCE_ITEMS = 6;
const MAX_EVIDENCE_CHARS = 1800;
const REPORT_TTL_MS = 24 * 60 * 60 * 1000;
const PRIMARY_MODEL_TIMEOUT_MS = 120_000;
const FALLBACK_MODEL_TIMEOUT_MS = 60_000;
const EVIDENCE_LOAD_TIMEOUT_MS = 90_000;
const RESEARCH_RESPONSE_SCHEMA = {
  type: "json_schema",
  json_schema: {
    type: "object",
    properties: {
      bull_case: { type: "array", minItems: 1, maxItems: 3, items: { "$ref": "#/$defs/claim" } },
      bear_case: { type: "array", minItems: 1, maxItems: 3, items: { "$ref": "#/$defs/claim" } },
      risk_view: { type: "array", minItems: 1, maxItems: 3, items: { "$ref": "#/$defs/claim" } },
      summary: { type: "string", minLength: 1, maxLength: 2000 },
      catalysts: { type: "array", minItems: 1, maxItems: 3, items: { "$ref": "#/$defs/claim" } },
      failure_conditions: { type: "array", minItems: 1, maxItems: 3, items: { "$ref": "#/$defs/claim" } },
      data_gaps: { type: "array", maxItems: 12, items: { "$ref": "#/$defs/evidence_note" } },
    },
    required: ["bull_case", "bear_case", "risk_view"],
    additionalProperties: false,
    $defs: {
      claim: {
        type: "object",
        properties: {
          text: { type: "string" },
          confidence: { type: "number", minimum: 0, maximum: 1 },
          evidence_ids: { type: "array", minItems: 1, items: { type: "string" } },
        },
        required: ["text", "confidence", "evidence_ids"],
        additionalProperties: false,
      },
      evidence_note: {
        type: "object",
        properties: {
          text: { type: "string", minLength: 1, maxLength: 500 },
          evidence_ids: { type: "array", minItems: 1, items: { type: "string" } },
        },
        required: ["text", "evidence_ids"],
        additionalProperties: false,
      },
    },
  },
} as const;

export type AiRunner = (
  env: Env,
  model: string,
  input: Record<string, unknown>,
) => Promise<unknown>;

export interface AiGenerationResult {
  output: unknown;
  model: string;
}

function modelForScope(request: ResearchAgentRequest): string {
  return request.model ?? DEFAULT_RESEARCH_MODEL;
}

/**
 * Workers AI can transiently stall on a large full-catalog prompt.  Keep the
 * research job bounded and retry once with the smaller model so a background
 * MCP job cannot remain `running` forever.  The report records the model that
 * actually produced it; this is part of the audit trail, not an implementation
 * detail.
 */
export async function runAiWithFallback(
  env: Env,
  runAi: AiRunner,
  model: string,
  input: Record<string, unknown>,
): Promise<AiGenerationResult> {
  const primaryInput = model === FALLBACK_RESEARCH_MODEL
    ? withoutResponseSchema(input)
    : input;
  try {
    return {
      output: await runWithTimeout(runAi(env, model, primaryInput), PRIMARY_MODEL_TIMEOUT_MS, model),
      model,
    };
  } catch (primaryError) {
    if (model === FALLBACK_RESEARCH_MODEL) throw primaryError;
    const fallbackInput = {
      ...withoutResponseSchema(input),
      max_tokens: Math.min(
        typeof input.max_tokens === "number" ? input.max_tokens : 800,
        800,
      ),
    };
    return {
      output: await runWithTimeout(
        runAi(env, FALLBACK_RESEARCH_MODEL, fallbackInput),
        FALLBACK_MODEL_TIMEOUT_MS,
        FALLBACK_RESEARCH_MODEL,
      ),
      model: FALLBACK_RESEARCH_MODEL,
    };
  }
}

function withoutResponseSchema(input: Record<string, unknown>): Record<string, unknown> {
  const { response_format: _responseFormat, ...inputWithoutSchema } = input;
  return inputWithoutSchema;
}

async function runWithTimeout(
  promise: Promise<unknown>,
  timeoutMs: number,
  model: string,
): Promise<unknown> {
  return await withTimeout(promise, timeoutMs, new HttpError(504, "model_timeout", [model]));
}

async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  timeoutError: Error,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(timeoutError), timeoutMs);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

export interface ResearchGenerationResult {
  run_id: string;
  plan_id: string;
  alignment_id: string;
  model: string;
  report_count: number;
  reports: ResearchReportResult[];
}

export interface ParsedResearchOutput {
  bull_case: ResearchClaim[];
  bear_case: ResearchClaim[];
  risk_view: ResearchClaim[];
  summary?: string;
  catalysts: ResearchClaim[];
  failure_conditions: ResearchClaim[];
  data_gaps: ResearchEvidenceNote[];
}

interface EvidenceIdTopic {
  topic_id: string;
  evidence_ids: string[];
}

interface StoredRow {
  object_key: string;
}

interface RunRow {
  snapshot_id: string;
  status: string;
}

interface PlanRow extends StoredRow {
  run_id: string;
  topic_snapshot_id: string;
  alignment_id: string;
}

interface AlignmentRow extends StoredRow {
  run_id: string;
  topic_snapshot_id: string;
  market_snapshot_id: string;
}

interface MarketRow extends StoredRow {
  run_id: string;
}

interface RawItemRow {
  item_id: string;
  source_id: string;
  object_key: string;
}

interface MarketInstrumentView {
  symbol: string;
  change_24h_pct?: number | null;
  price?: number | null;
}

interface MarketSnapshotView {
  snapshot_id: string;
  provider: string;
  as_of: string;
  instruments: MarketInstrumentView[];
  financial_depth?: Record<string, unknown>;
}

interface AlignedTopicView {
  topic_id: string;
  market_direction: "positive" | "negative" | "mixed" | "not_covered";
  symbols: string[];
  mean_change_24h_pct: number | null;
  evidence_ids: string[];
}

interface AlignmentView {
  alignment_id: string;
  topic_snapshot_id: string;
  market_snapshot_id: string;
  topics: AlignedTopicView[];
}

interface EvidenceView {
  item_id: string;
  title: string;
  summary: string;
  content: string;
  source_id: string;
  canonical_url: string;
  published_at: string | null;
}

/**
 * Keep model input bounded to the same six traceable items that each report
 * can cite. Full-catalog packs can contain hundreds of raw objects; loading
 * every R2 object for a three-topic report can exceed the Worker budget and
 * lets a model cite material it never received.
 */
export function selectResearchEvidenceIds(
  selectedTopics: ReadonlyArray<EvidenceIdTopic>,
  topicSnapshotTopics: ReadonlyArray<EvidenceIdTopic>,
  alignmentTopics: ReadonlyArray<EvidenceIdTopic>,
): string[] {
  const topicsById = new Map(topicSnapshotTopics.map((topic) => [topic.topic_id, topic]));
  const alignmentsById = new Map(alignmentTopics.map((topic) => [topic.topic_id, topic]));
  return uniqueStrings(selectedTopics.flatMap((plannedTopic) => topicEvidenceIds(
    plannedTopic,
    topicsById.get(plannedTopic.topic_id),
    alignmentsById.get(plannedTopic.topic_id),
  )));
}

function topicEvidenceIds(
  plannedTopic: EvidenceIdTopic,
  topic: EvidenceIdTopic | undefined,
  alignmentTopic: EvidenceIdTopic | undefined,
): string[] {
  return uniqueStrings([
    ...(topic?.evidence_ids ?? []),
    ...plannedTopic.evidence_ids,
    ...(alignmentTopic?.evidence_ids ?? []),
  ]).slice(0, MAX_EVIDENCE_ITEMS);
}

export async function generateResearchReports(
  env: Env,
  payload: unknown,
  auth: AuthContext,
  now: Date,
  runAi: AiRunner,
): Promise<ResearchGenerationResult> {
  const request = validateRequest(payload);
  if (request.workflow_run_id !== auth.workflowRunId) {
    throw new HttpError(403, "workflow_run_mismatch");
  }
  if (request.commit_sha !== auth.commitSha) {
    throw new HttpError(403, "commit_sha_mismatch");
  }

  const run = await env.DB.prepare(
    "SELECT snapshot_id, status FROM runs WHERE run_id = ?",
  ).bind(request.run_id).first<RunRow>();
  if (!run) throw new HttpError(409, "run_not_found");
  if (run.status !== "published") throw new HttpError(409, "run_not_published");

  const planRow = await env.DB.prepare(
    `SELECT run_id, topic_snapshot_id, alignment_id, object_key
     FROM tradingagents_plans WHERE plan_id = ?`,
  ).bind(request.plan_id).first<PlanRow>();
  if (!planRow) throw new HttpError(409, "plan_not_found");
  if (
    planRow.run_id !== request.run_id ||
    planRow.topic_snapshot_id !== run.snapshot_id ||
    planRow.alignment_id !== request.alignment_id
  ) {
    throw new HttpError(409, "plan_request_conflict");
  }

  const reportProfile = request.report_profile ?? "detailed_traceable";
  const requestedOutputs = request.requested_outputs ?? ["detailed_report", "evidence_appendix"];
  const reportRequested = requestedOutputs.includes("quick_card") || requestedOutputs.includes("detailed_report");

  const alignmentRow = await env.DB.prepare(
    `SELECT run_id, topic_snapshot_id, market_snapshot_id, object_key
     FROM topic_market_alignments WHERE alignment_id = ?`,
  ).bind(request.alignment_id).first<AlignmentRow>();
  if (!alignmentRow) throw new HttpError(409, "alignment_not_found");
  if (
    alignmentRow.run_id !== request.run_id ||
    alignmentRow.topic_snapshot_id !== run.snapshot_id
  ) {
    throw new HttpError(409, "alignment_request_conflict");
  }

  const marketRow = await env.DB.prepare(
    `SELECT run_id, object_key FROM market_snapshots WHERE snapshot_id = ?`,
  ).bind(alignmentRow.market_snapshot_id).first<MarketRow>();
  if (!marketRow || marketRow.run_id !== request.run_id) {
    throw new HttpError(409, "market_snapshot_not_found");
  }
  const snapshotRow = await env.DB.prepare(
    "SELECT object_key FROM topic_snapshots WHERE snapshot_id = ? AND run_id = ?",
  ).bind(run.snapshot_id, request.run_id).first<StoredRow>();
  if (!snapshotRow) throw new HttpError(409, "topic_snapshot_not_found");

  const topicSnapshot = asTopicSnapshot(
    await readJson(env.RAW_OBJECTS, snapshotRow.object_key, "topic_snapshot"),
  );
  const plan = asRunPlan(await readJson(env.RAW_OBJECTS, planRow.object_key, "tradingagents_plan"));
  const alignment = asAlignment(
    await readJson(env.RAW_OBJECTS, alignmentRow.object_key, "market_alignment"),
    request.alignment_id,
    run.snapshot_id,
    alignmentRow.market_snapshot_id,
  );
  const market = asMarketSnapshot(
    await readJson(env.RAW_OBJECTS, marketRow.object_key, "market_snapshot"),
    alignmentRow.market_snapshot_id,
  );

  if (plan.decision !== "eligible") throw new HttpError(409, "plan_not_eligible");
  if (!reportRequested) {
    return {
      run_id: request.run_id,
      plan_id: request.plan_id,
      alignment_id: request.alignment_id,
      model: request.model ?? DEFAULT_RESEARCH_MODEL,
      report_count: 0,
      reports: [],
    };
  }

  const targetTopicId = request.target?.kind === "crypto"
    ? "digital_assets"
    : request.target?.kind === "equity"
      ? "equities_earnings"
      : request.target?.kind === "etf"
        ? "personal_finance"
        : undefined;
  const plannedTopics = targetTopicId === undefined
    ? plan.topics
    : plan.topics.filter((topic) => topic.topic_id === targetTopicId);
  const selectedTopics = plannedTopics
    .filter((topic) => topic.decision === "run")
    .slice(0, request.max_reports ?? plan.budget.max_topics);
  if (selectedTopics.length === 0) throw new HttpError(409, "no_topics_to_research");

  const evidenceIdsToLoad = selectResearchEvidenceIds(
    selectedTopics,
    topicSnapshot.topics,
    alignment.topics,
  );
  const rawItems = await withTimeout(
    loadEvidence(env, request.run_id, evidenceIdsToLoad),
    EVIDENCE_LOAD_TIMEOUT_MS,
    new HttpError(504, "evidence_load_timeout"),
  );
  const reports: ResearchReportResult[] = [];
  const model = modelForScope(request);
  for (const plannedTopic of selectedTopics) {
    const topic = topicSnapshot.topics.find((candidate) => candidate.topic_id === plannedTopic.topic_id);
    if (!topic) throw new HttpError(409, "topic_not_found", [plannedTopic.topic_id]);
    const alignmentTopic = alignment.topics.find((candidate) => candidate.topic_id === topic.topic_id);
    const evidenceIds = topicEvidenceIds(topic, topic, alignmentTopic);
    const evidence = evidenceIds
      .map((itemId) => rawItems.get(itemId))
      .filter((item): item is EvidenceView => item !== undefined)
      .filter((item) => targetEvidenceMatch(item, request.target))
      .slice(0, MAX_EVIDENCE_ITEMS);
    if (evidence.length === 0) throw new HttpError(409, "research_evidence_missing", [topic.topic_id]);
    const scopedEvidenceIds = evidence.map((item) => item.item_id);

    const generationMode = request.generation_mode
      ?? (model === DETERMINISTIC_RESEARCH_MODEL ? "deterministic_baseline" : "ai_enrichment");
    const reportSuffix = request.report_instance_id === undefined
      ? ""
      : `_${request.report_instance_id}`;
    const reportId = request.generation_mode === undefined && reportSuffix === ""
      ? `report_${request.run_id}_${topic.topic_id}`
      : `report_${request.run_id}_${topic.topic_id}_${generationMode === "ai_enrichment" ? "ai" : "baseline"}${reportSuffix}`;
    const existing = await env.DB.prepare(
      "SELECT object_key FROM research_reports WHERE report_id = ?",
    ).bind(reportId).first<StoredRow>();
    if (existing) {
      const stored = await readJson(env.RAW_OBJECTS, existing.object_key, "research_report");
      const storedProfile = isRecord(stored) &&
        (stored.report_profile === "compact_traceable" || stored.report_profile === "detailed_traceable")
        ? stored.report_profile
        : "detailed_traceable";
      if (storedProfile !== reportProfile) {
        throw new HttpError(409, "report_profile_conflict", [reportId]);
      }
      const replayEnvelope = {
        schema_version: 1,
        operation: "upsert_research_report",
        run_id: request.run_id,
        workflow_run_id: request.workflow_run_id,
        commit_sha: request.commit_sha,
        report: stored,
      };
      reports.push(await ingestResearchReport(env, replayEnvelope, now));
      continue;
    }

    let parsedOutput: ParsedResearchOutput;
    let reportModel = model;
    if (model === DETERMINISTIC_RESEARCH_MODEL) {
      parsedOutput = buildDeterministicResearchOutput(topic, scopedEvidenceIds, plannedTopic.market_direction);
    } else {
      const generation = await runAiWithFallback(env, runAi, model, {
        messages: [
          {
            role: "system",
            content: `You are a cautious financial research second-opinion assistant. The requested report profile is ${reportProfile}. Do not give a buy or sell instruction. Return only valid JSON with bull_case, bear_case, risk_view, and optional summary, catalysts, failure_conditions, data_gaps. Use FINANCIAL_DEPTH for observed returns, volatility, drawdown, valuation status, scenarios, and source conflicts; distinguish observed facts from mechanical non-forecast scenarios and state missing data explicitly. Each claim array has 1 to 3 objects with text, confidence (0 to 1), and evidence_ids. Every evidence_id must be copied exactly from the supplied evidence. Keep each claim text under ${reportProfile === "compact_traceable" ? 240 : 400} characters.`,
          },
          {
            role: "user",
            content: buildPrompt(
              topic,
              plannedTopic.market_direction,
              alignmentTopic,
              market,
              evidence,
              request.target,
              request.research_question,
              reportProfile,
            ),
          },
        ],
        max_tokens: Math.min(plan.budget.max_tokens, reportProfile === "compact_traceable" ? 800 : 1200),
        temperature: 0,
        seed: 42,
        response_format: RESEARCH_RESPONSE_SCHEMA,
      });
      parsedOutput = parseResearchOutput(generation.output, new Set(scopedEvidenceIds));
      reportModel = generation.model;
    }
    const generatedAt = now.toISOString();
    const professionalAnalysis = buildProfessionalAnalysis(
      market,
      evidence.length,
      alignmentRow.market_snapshot_id,
    );
    const report: ResearchReport = {
      schema_version: 1,
      report_id: reportId,
      topic_snapshot_id: topicSnapshot.snapshot_id,
      plan_id: request.plan_id,
      alignment_id: request.alignment_id,
      market_snapshot_id: alignmentRow.market_snapshot_id,
      topic_id: topic.topic_id,
      generated_at: generatedAt,
      expires_at: new Date(now.getTime() + REPORT_TTL_MS).toISOString(),
      model: reportModel,
      agent_version: RESEARCH_AGENT_VERSION,
      report_version: 2,
      report_profile: reportProfile,
      generation_mode: generationMode,
      ...(request.research_question === undefined ? {} : { research_question: request.research_question }),
      ...(request.target === undefined ? {} : { target: request.target }),
      as_of: topicSnapshot.as_of,
      summary: parsedOutput.summary ?? buildFallbackSummary(topic.label),
      second_opinion: true,
      evidence_ids: scopedEvidenceIds,
      bull_case: parsedOutput.bull_case,
      bear_case: parsedOutput.bear_case,
      risk_view: parsedOutput.risk_view,
      catalysts: parsedOutput.catalysts,
      failure_conditions: parsedOutput.failure_conditions,
      data_gaps: addProfessionalDataGaps(parsedOutput.data_gaps, market, scopedEvidenceIds),
      recommendation_status: "research_only",
      professional_analysis: professionalAnalysis,
    };
    reports.push(await ingestResearchReport(env, {
      schema_version: 1,
      operation: "upsert_research_report",
      run_id: request.run_id,
      workflow_run_id: request.workflow_run_id,
      commit_sha: request.commit_sha,
      report,
    }, now));
  }

  return {
    run_id: request.run_id,
    plan_id: request.plan_id,
    alignment_id: request.alignment_id,
    model,
    report_count: reports.length,
    reports,
  };
}

function addProfessionalDataGaps(
  existing: ResearchEvidenceNote[],
  market: MarketSnapshotView,
  evidenceIds: string[],
): ResearchEvidenceNote[] {
  const anchor = evidenceIds[0];
  if (!anchor) return existing;
  const depth = market.financial_depth;
  const additions: ResearchEvidenceNote[] = [];
  if (!depth) {
    additions.push({
      text: "Financial depth was not supplied; historical comparison, valuation, and scenario analysis remain unresolved.",
      evidence_ids: [anchor],
    });
  } else {
    const timeSeries = isRecord(depth.time_series) ? depth.time_series : {};
    const valuation = isRecord(depth.valuation) ? depth.valuation : {};
    if (timeSeries.status !== "available") {
      additions.push({
        text: "Historical time-series data is unavailable or insufficient for a stable comparison.",
        evidence_ids: [anchor],
      });
    }
    if (valuation.status !== "available" && valuation.status !== "not_applicable") {
      additions.push({
        text: "Valuation inputs are incomplete; no implied value is published.",
        evidence_ids: [anchor],
      });
    }
    const conflicts = Array.isArray(depth.source_conflicts) ? depth.source_conflicts : [];
    if (conflicts.some((conflict) => isRecord(conflict)
      && ["source_conflict_screen_v2", "lexical_stance_v1"].includes(String(conflict.method))
      && String(conflict.calibration_status ?? "unresolved") !== "calibrated")) {
      additions.push({
        text: "Source conflict detection is a calibrated-screening gap; independent stance calibration remains unresolved.",
        evidence_ids: [anchor],
      });
    }
  }
  const merged = [...existing, ...additions];
  const seen = new Set<string>();
  return merged.filter((note) => {
    const key = `${note.text}:${note.evidence_ids.join(",")}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 12);
}

export function parseModelClaims(
  output: unknown,
  allowedEvidenceIds: Set<string>,
): Pick<ResearchReport, "bull_case" | "bear_case" | "risk_view"> {
  const parsed = parseResearchOutput(output, allowedEvidenceIds);
  return {
    bull_case: parsed.bull_case,
    bear_case: parsed.bear_case,
    risk_view: parsed.risk_view,
  };
}

function parseResearchOutput(
  output: unknown,
  allowedEvidenceIds: Set<string>,
): ParsedResearchOutput {
  const text = modelText(output);
  const parsed = parseJsonObject(text);
  const result: Record<string, unknown> = isRecord(parsed) ? parsed : {};
  return {
    bull_case: parseClaims(result.bull_case, "bull_case", allowedEvidenceIds),
    bear_case: parseClaims(result.bear_case, "bear_case", allowedEvidenceIds),
    risk_view: parseClaims(result.risk_view, "risk_view", allowedEvidenceIds),
    summary: optionalText(result.summary, "summary"),
    catalysts: optionalClaims(result.catalysts, "catalysts", allowedEvidenceIds),
    failure_conditions: optionalClaims(result.failure_conditions, "failure_conditions", allowedEvidenceIds),
    data_gaps: optionalEvidenceNotes(result.data_gaps, "data_gaps", allowedEvidenceIds),
  };
}

function optionalText(value: unknown, field: string): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string" || value.trim().length === 0 || value.length > 2000) {
    throw new HttpError(502, "model_output_invalid", [`${field}: expected non-empty text`]);
  }
  return value.trim();
}

function optionalClaims(
  value: unknown,
  field: string,
  allowedEvidenceIds: Set<string>,
): ResearchClaim[] {
  if (value === undefined) return [];
  return parseClaims(value, field, allowedEvidenceIds);
}

function optionalEvidenceNotes(
  value: unknown,
  field: string,
  allowedEvidenceIds: Set<string>,
): ResearchEvidenceNote[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 12) {
    throw new HttpError(502, "model_output_invalid", [`${field}: expected up to 12 evidence notes`]);
  }
  return value.map((item, index) => {
    if (!isRecord(item) || typeof item.text !== "string" || item.text.trim().length === 0 || item.text.length > 500 || !Array.isArray(item.evidence_ids) || item.evidence_ids.length < 1 || item.evidence_ids.some((id) => typeof id !== "string" || !allowedEvidenceIds.has(id))) {
      throw new HttpError(502, "model_output_invalid", [`${field}[${index}]`]);
    }
    return {
      text: item.text.trim(),
      evidence_ids: uniqueStrings(item.evidence_ids as string[]),
    };
  });
}

function buildFallbackSummary(topicLabel: string): string {
  return `Evidence-linked second opinion for ${topicLabel}; this is research-only and not a buy or sell instruction.`;
}

export function buildDeterministicResearchOutput(
  topic: RadarTopic,
  evidenceIds: string[],
  plannedDirection: string,
): ParsedResearchOutput {
  const refs = evidenceIds.slice(0, 3);
  const confidence = Math.min(0.8, 0.35 + refs.length * 0.1);
  const divergence = topic.divergence.direction;
  return {
    bull_case: [{
      text: `${topic.label} has observable activity across ${topic.source_count} source(s); the constructive case is limited to the collected evidence.`,
      confidence,
      evidence_ids: refs,
    }],
    bear_case: [{
      text: `${topic.label} remains exposed to contradictory or incomplete information; the planned market direction is ${plannedDirection}.`,
      confidence,
      evidence_ids: refs,
    }],
    risk_view: [{
      text: `Evidence divergence is ${divergence}; this signal is a research lead, not a forecast or transaction instruction.`,
      confidence: Math.max(0.5, confidence),
      evidence_ids: refs,
    }],
    summary: buildFallbackSummary(topic.label),
    catalysts: [{
      text: `Monitor new evidence linked to ${topic.label} and whether source breadth increases.`,
      confidence: 0.5,
      evidence_ids: refs,
    }],
    failure_conditions: [{
      text: "Treat the signal as unresolved if cited sources become stale, unavailable, or mutually contradictory.",
      confidence: 0.75,
      evidence_ids: refs,
    }],
    data_gaps: [],
  };
}

function parseClaims(
  value: unknown,
  field: string,
  allowedEvidenceIds: Set<string>,
): ResearchClaim[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 6) {
    throw new HttpError(502, "model_output_invalid", [`${field}: expected 1-6 claims`]);
  }
  return value.map((candidate, index) => {
    if (!isRecord(candidate)) throw new HttpError(502, "model_output_invalid", [`${field}[${index}]`]);
    const text = candidate.text;
    const confidence = candidate.confidence;
    const evidenceIds = candidate.evidence_ids;
    if (
      typeof text !== "string" || text.trim().length === 0 || text.length > 5000 ||
      typeof confidence !== "number" || !Number.isFinite(confidence) || confidence < 0 || confidence > 1 ||
      !Array.isArray(evidenceIds) || evidenceIds.length < 1 ||
      evidenceIds.some((id) => typeof id !== "string" || !allowedEvidenceIds.has(id))
    ) {
      throw new HttpError(502, "model_output_invalid", [`${field}[${index}]`]);
    }
    return {
      text: text.trim(),
      confidence,
      evidence_ids: uniqueStrings(evidenceIds as string[]),
    };
  });
}

function validateRequest(payload: unknown): ResearchAgentRequest {
  try {
    return validateResearchAgentRequest(payload);
  } catch (error) {
    const details = error instanceof Error ? [error.message] : [];
    throw new HttpError(422, "invalid_payload", details);
  }
}

async function loadEvidence(
  env: Env,
  runId: string,
  itemIds?: string[],
): Promise<Map<string, EvidenceView>> {
  if (itemIds !== undefined && itemIds.length === 0) return new Map();
  const filter = itemIds && itemIds.length > 0
    ? ` AND raw_items.item_id IN (${itemIds.map(() => "?").join(", ")})`
    : "";
  const rows = await env.DB.prepare(
    `SELECT raw_items.item_id, raw_items.source_id, raw_items.object_key
     FROM raw_items JOIN run_items ON run_items.item_id = raw_items.item_id
     WHERE run_items.run_id = ?${filter}`,
  ).bind(runId, ...(itemIds ?? [])).all<RawItemRow>();
  const loaded = await mapWithConcurrency(rows.results, 16, async (row) => {
    const raw = await readJson(env.RAW_OBJECTS, row.object_key, "raw_item");
    if (!isRecord(raw)) throw new HttpError(503, "raw_item_invalid", [row.item_id]);
    const item = raw as Record<string, unknown>;
    if (
      typeof item.item_id !== "string" || typeof item.title !== "string" ||
      typeof item.summary !== "string" || typeof item.content !== "string" ||
      typeof item.canonical_url !== "string" ||
      (item.published_at !== null && typeof item.published_at !== "string")
    ) {
      throw new HttpError(503, "raw_item_invalid", [row.item_id]);
    }
    return [row.item_id, {
      item_id: item.item_id,
      title: item.title,
      summary: item.summary,
      content: item.content,
      source_id: row.source_id,
      canonical_url: item.canonical_url,
      published_at: item.published_at,
    }] as const;
  });
  return new Map(loaded);
}

async function mapWithConcurrency<T, R>(
  values: T[],
  concurrency: number,
  mapper: (value: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(values.length);
  let nextIndex = 0;
  const worker = async (): Promise<void> => {
    while (true) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= values.length) return;
      results[index] = await mapper(values[index]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, values.length) }, worker));
  return results;
}

function buildPrompt(
  topic: RadarTopic,
  plannedDirection: string,
  alignment: AlignedTopicView | undefined,
  market: MarketSnapshotView,
  evidence: EvidenceView[],
  target?: ResearchTarget,
  researchQuestion?: string,
  reportProfile: "detailed_traceable" | "compact_traceable" = "detailed_traceable",
): string {
  const marketLines = market.instruments.slice(0, 8).map((instrument) =>
    `${instrument.symbol}: price=${instrument.price ?? "n/a"}, 24h_change=${instrument.change_24h_pct ?? "n/a"}`,
  ).join("\n");
  const evidenceLines = evidence.map((item) => [
    `EVIDENCE_ID=${item.item_id}`,
    `SOURCE=${item.source_id}`,
    `TITLE=${item.title}`,
    `PUBLISHED_AT=${item.published_at ?? "n/a"}`,
    `URL=${item.canonical_url}`,
    `SUMMARY=${item.summary}`,
    `CONTENT=${item.content.slice(0, MAX_EVIDENCE_CHARS)}`,
  ].join("\n")).join("\n---\n");
  const depth = summarizeFinancialDepth(market.financial_depth);
  return [
    `RESEARCH_TARGET=${target ? targetLabel(target) : "not_provided"}`,
    `RESEARCH_QUESTION=${researchQuestion ?? "not_provided"}`,
    `REPORT_PROFILE=${reportProfile}`,
    `TOPIC_ID=${topic.topic_id}`,
    `TOPIC_LABEL=${topic.label}`,
    `TOPIC_SCORE=${topic.score}`,
    `TOPIC_DIVERGENCE=${topic.divergence.direction}`,
    `PLANNED_MARKET_DIRECTION=${plannedDirection}`,
    `ALIGNED_MARKET_DIRECTION=${alignment?.market_direction ?? "not_covered"}`,
    `ALIGNED_SYMBOLS=${alignment?.symbols.join(",") ?? "none"}`,
    `ALIGNED_MEAN_24H_CHANGE=${alignment?.mean_change_24h_pct ?? "n/a"}`,
    `MARKET_PROVIDER=${market.provider}`,
    `MARKET_AS_OF=${market.as_of}`,
    "MARKET_INSTRUMENTS:",
    marketLines || "none",
    `FINANCIAL_DEPTH=${JSON.stringify(depth)}`,
    "EVIDENCE:",
    evidenceLines,
    "Use only the exact EVIDENCE_ID values above. State uncertainty when evidence is sparse or contradictory.",
  ].join("\n");
}

function summarizeFinancialDepth(depth: Record<string, unknown> | undefined): Record<string, unknown> {
  if (!depth) return { status: "research_only", reason: "financial_depth_not_present" };
  const timeSeries = isRecord(depth.time_series) ? depth.time_series : {};
  const valuation = isRecord(depth.valuation) ? depth.valuation : {};
  const scenarios = isRecord(depth.scenarios) ? depth.scenarios : {};
  const marketDrivers = isRecord(depth.market_drivers) ? depth.market_drivers : {};
  const conflicts = Array.isArray(depth.source_conflicts)
    ? depth.source_conflicts.map((conflict) => {
      if (!isRecord(conflict)) return { status: "unknown" };
      return {
        topic_id: conflict.topic_id,
        status: conflict.status,
        conflict_level: conflict.conflict_level,
        counts: conflict.counts,
        independent_source_count: conflict.independent_source_count,
      };
    })
    : [];
  return {
    status: depth.status,
    time_series: {
      status: timeSeries.status,
      window_start: timeSeries.window_start,
      window_end: timeSeries.window_end,
      point_count: timeSeries.point_count,
      returns: timeSeries.returns,
      volatility_annualized_pct: timeSeries.volatility_annualized_pct,
      max_drawdown_pct: timeSeries.max_drawdown_pct,
      source_ref: timeSeries.source_ref,
    },
    valuation: {
      status: valuation.status,
      method: valuation.method,
      missing_fields: valuation.missing_fields,
      reason: valuation.reason,
    },
    scenarios: {
      status: scenarios.status,
      method: scenarios.method,
      not_a_forecast: scenarios.not_a_forecast,
      scenarios: scenarios.scenarios,
    },
    market_drivers: marketDrivers,
    source_conflicts: conflicts,
  };
}

function buildProfessionalAnalysis(
  market: MarketSnapshotView,
  evidenceCount: number,
  marketSnapshotId: string,
): NonNullable<ResearchReport["professional_analysis"]> {
  const depth = market.financial_depth;
  const conflicts = Array.isArray(depth?.source_conflicts)
    ? depth.source_conflicts.find((conflict) => isRecord(conflict) && conflict.topic_id === "target")
    : undefined;
  return {
    schema_version: 1,
    status: depth && isProfessionalStatus(depth.status) ? depth.status : "research_only",
    market_snapshot_id: marketSnapshotId,
    financial_depth: depth && isFinancialDepth(depth) ? depth as unknown as NonNullable<ResearchReport["professional_analysis"]>["financial_depth"] : null,
    source_conflict_summary: isRecord(conflicts) ? conflicts : { status: "unknown", reason: "source_conflict_report_not_present" },
    model_input_scope: {
      evidence_count: evidenceCount,
      market_depth_included: depth !== undefined,
    },
  };
}

function isProfessionalStatus(value: unknown): value is "professional_ready" | "professional_partial" | "research_only" | "blocked" {
  return value === "professional_ready" || value === "professional_partial" || value === "research_only" || value === "blocked";
}

function isFinancialDepth(value: Record<string, unknown>): boolean {
  return isProfessionalStatus(value.status) && isRecord(value.time_series) && isRecord(value.valuation) && isRecord(value.scenarios);
}

function targetLabel(target: ResearchTarget): string {
  const identifier = target.symbol ?? target.name ?? target.url ?? "unidentified";
  return `${target.kind}:${identifier}`;
}

function targetEvidenceMatch(item: EvidenceView, target: ResearchTarget | undefined): boolean {
  if (target === undefined) return true;
  const text = `${item.title} ${item.summary} ${item.content}`.toLowerCase();
  const terms = [target.symbol, target.name, target.market].filter(
    (value): value is string => typeof value === "string" && value.trim().length > 0,
  ).map((value) => value.toLowerCase());
  if (target.kind === "crypto") terms.push("bitcoin", "btc", "crypto", "cryptocurrency", "digital asset");
  if (target.kind === "equity") terms.push("stock", "equity", "shares", "earnings");
  if (target.kind === "etf") terms.push("etf", "exchange traded fund");
  return terms.some((term) => term.includes(" ") ? text.includes(term) : new RegExp(`(^|[^a-z0-9])${escapeRegExp(term)}([^a-z0-9]|$)`).test(text));
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function asTopicSnapshot(value: unknown): TopicSnapshot {
  try {
    return validateTopicSnapshot(value);
  } catch {
    throw new HttpError(503, "topic_snapshot_invalid");
  }
}

function asRunPlan(value: unknown): TradingAgentsRunPlan {
  try {
    return validateTradingAgentsRunPlan(value);
  } catch {
    throw new HttpError(503, "tradingagents_plan_invalid");
  }
}

function asAlignment(
  value: unknown,
  alignmentId: string,
  topicSnapshotId: string,
  marketSnapshotId: string,
): AlignmentView {
  if (!isRecord(value) || value.alignment_id !== alignmentId || value.topic_snapshot_id !== topicSnapshotId || value.market_snapshot_id !== marketSnapshotId || !Array.isArray(value.topics)) {
    throw new HttpError(503, "market_alignment_invalid");
  }
  const topics: AlignedTopicView[] = [];
  for (const candidate of value.topics) {
    if (!isRecord(candidate) || typeof candidate.topic_id !== "string" || !isMarketDirection(candidate.market_direction) || !Array.isArray(candidate.symbols) || candidate.symbols.some((symbol) => typeof symbol !== "string") || !Array.isArray(candidate.evidence_ids) || candidate.evidence_ids.some((itemId) => typeof itemId !== "string") || (candidate.mean_change_24h_pct !== null && typeof candidate.mean_change_24h_pct !== "number")) {
      throw new HttpError(503, "market_alignment_invalid");
    }
    topics.push({
      topic_id: candidate.topic_id,
      market_direction: candidate.market_direction,
      symbols: candidate.symbols as string[],
      mean_change_24h_pct: candidate.mean_change_24h_pct as number | null,
      evidence_ids: candidate.evidence_ids as string[],
    });
  }
  return { alignment_id: alignmentId, topic_snapshot_id: topicSnapshotId, market_snapshot_id: marketSnapshotId, topics };
}

function asMarketSnapshot(value: unknown, snapshotId: string): MarketSnapshotView {
  if (!isRecord(value) || value.snapshot_id !== snapshotId || typeof value.provider !== "string" || typeof value.as_of !== "string" || !Array.isArray(value.instruments)) {
    throw new HttpError(503, "market_snapshot_invalid");
  }
  const instruments: MarketInstrumentView[] = [];
  for (const candidate of value.instruments) {
    if (!isRecord(candidate) || typeof candidate.symbol !== "string" || (candidate.price !== null && typeof candidate.price !== "number") || (candidate.change_24h_pct !== null && typeof candidate.change_24h_pct !== "number")) {
      throw new HttpError(503, "market_snapshot_invalid");
    }
    instruments.push({
      symbol: candidate.symbol,
      price: candidate.price as number | null,
      change_24h_pct: candidate.change_24h_pct as number | null,
    });
  }
  return {
    snapshot_id: snapshotId,
    provider: value.provider,
    as_of: value.as_of,
    instruments,
    ...(isRecord(value.financial_depth) ? { financial_depth: value.financial_depth } : {}),
  };
}

async function readJson(bucket: R2Bucket, objectKey: string, kind: string): Promise<unknown> {
  let object: R2ObjectBody | null;
  try {
    object = await bucket.get(objectKey);
  } catch {
    throw new HttpError(503, "storage_read_failed", [kind]);
  }
  if (object === null) throw new HttpError(503, "storage_object_missing", [kind]);
  try {
    return await object.json();
  } catch {
    throw new HttpError(503, "storage_object_invalid", [kind]);
  }
}

function modelText(output: unknown): string {
  if (typeof output === "string") return output;
  if (isRecord(output)) {
    for (const key of ["response", "result", "text"]) {
      const value = output[key];
      if (typeof value === "string") return value;
      // Workers AI JSON mode may return the parsed object under response.
      // Convert only that structured response back to JSON for the strict
      // contract parser; never accept arbitrary metadata as model content.
      if (key === "response" && isRecord(value)) return JSON.stringify(value);
      if (key === "result" && isRecord(value)) {
        const nested = value.response;
        if (typeof nested === "string") return nested;
        if (isRecord(nested)) return JSON.stringify(nested);
      }
    }
  }
  throw new HttpError(502, "model_output_invalid", ["response_text"]);
}

function parseJsonObject(text: string): unknown {
  const trimmed = text.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start < 0 || end <= start) throw new HttpError(502, "model_output_invalid", ["json"]);
  try {
    return JSON.parse(trimmed.slice(start, end + 1)) as unknown;
  } catch {
    throw new HttpError(502, "model_output_invalid", ["json"]);
  }
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values)];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isMarketDirection(value: unknown): value is AlignedTopicView["market_direction"] {
  return value === "positive" || value === "negative" || value === "mixed" || value === "not_covered";
}
