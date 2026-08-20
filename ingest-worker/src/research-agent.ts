import {
  type ResearchAgentRequest,
  type ResearchClaim,
  type ResearchReport,
  type RadarTopic,
  type TopicSnapshot,
  type TradingAgentsRunPlan,
  validateResearchAgentRequest,
  validateTopicSnapshot,
  validateTradingAgentsRunPlan,
} from "./contracts";
import type { AuthContext } from "./auth";
import { HttpError, ingestResearchReport, type ResearchReportResult } from "./storage";


export const DEFAULT_RESEARCH_MODEL = "@cf/meta/llama-3.2-3b-instruct";
export const RESEARCH_AGENT_VERSION = "tradingagents-cloudflare-ai-v1";
const MAX_EVIDENCE_ITEMS = 6;
const MAX_EVIDENCE_CHARS = 1800;
const REPORT_TTL_MS = 24 * 60 * 60 * 1000;

export type AiRunner = (
  env: Env,
  model: string,
  input: Record<string, unknown>,
) => Promise<unknown>;

export interface ResearchGenerationResult {
  run_id: string;
  plan_id: string;
  alignment_id: string;
  model: string;
  report_count: number;
  reports: ResearchReportResult[];
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
  const selectedTopics = plan.topics
    .filter((topic) => topic.decision === "run")
    .slice(0, request.max_reports ?? plan.budget.max_topics);
  if (selectedTopics.length === 0) throw new HttpError(409, "no_topics_to_research");

  const rawItems = await loadEvidence(env, request.run_id);
  const reports: ResearchReportResult[] = [];
  const model = request.model ?? DEFAULT_RESEARCH_MODEL;
  for (const plannedTopic of selectedTopics) {
    const topic = topicSnapshot.topics.find((candidate) => candidate.topic_id === plannedTopic.topic_id);
    if (!topic) throw new HttpError(409, "topic_not_found", [plannedTopic.topic_id]);
    const alignmentTopic = alignment.topics.find((candidate) => candidate.topic_id === topic.topic_id);
    const evidenceIds = uniqueStrings([
      ...topic.evidence_ids,
      ...plannedTopic.evidence_ids,
      ...(alignmentTopic?.evidence_ids ?? []),
    ]);
    const evidence = evidenceIds
      .map((itemId) => rawItems.get(itemId))
      .filter((item): item is EvidenceView => item !== undefined)
      .slice(0, MAX_EVIDENCE_ITEMS);
    if (evidence.length === 0) throw new HttpError(409, "research_evidence_missing", [topic.topic_id]);

    const reportId = `report_${request.run_id}_${topic.topic_id}`;
    const existing = await env.DB.prepare(
      "SELECT object_key FROM research_reports WHERE report_id = ?",
    ).bind(reportId).first<StoredRow>();
    if (existing) {
      const stored = await readJson(env.RAW_OBJECTS, existing.object_key, "research_report");
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

    const output = await runAi(env, model, {
      messages: [
        {
          role: "system",
          content: "You are a cautious financial research second-opinion assistant. Do not give a buy or sell instruction. Return only valid JSON with exactly three arrays: bull_case, bear_case, risk_view. Each array has 1 to 3 objects with text, confidence (0 to 1), and evidence_ids. Every evidence_id must be copied exactly from the supplied evidence. Keep each text under 400 characters.",
        },
        {
          role: "user",
          content: buildPrompt(topic, plannedTopic.market_direction, alignmentTopic, market, evidence),
        },
      ],
      max_tokens: Math.min(plan.budget.max_tokens, 1200),
      temperature: 0,
      response_format: { type: "json_object" },
    });
    const claims = parseModelClaims(output, new Set(evidenceIds));
    const generatedAt = now.toISOString();
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
      model,
      agent_version: RESEARCH_AGENT_VERSION,
      second_opinion: true,
      evidence_ids: evidenceIds,
      bull_case: claims.bull_case,
      bear_case: claims.bear_case,
      risk_view: claims.risk_view,
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

export function parseModelClaims(
  output: unknown,
  allowedEvidenceIds: Set<string>,
): Pick<ResearchReport, "bull_case" | "bear_case" | "risk_view"> {
  const text = modelText(output);
  const parsed = parseJsonObject(text);
  const result: Record<string, unknown> = isRecord(parsed) ? parsed : {};
  return {
    bull_case: parseClaims(result.bull_case, "bull_case", allowedEvidenceIds),
    bear_case: parseClaims(result.bear_case, "bear_case", allowedEvidenceIds),
    risk_view: parseClaims(result.risk_view, "risk_view", allowedEvidenceIds),
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

async function loadEvidence(env: Env, runId: string): Promise<Map<string, EvidenceView>> {
  const rows = await env.DB.prepare(
    `SELECT raw_items.item_id, raw_items.source_id, raw_items.object_key
     FROM raw_items JOIN run_items ON run_items.item_id = raw_items.item_id
     WHERE run_items.run_id = ?`,
  ).bind(runId).all<RawItemRow>();
  const evidence = new Map<string, EvidenceView>();
  for (const row of rows.results) {
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
    evidence.set(row.item_id, {
      item_id: item.item_id,
      title: item.title,
      summary: item.summary,
      content: item.content,
      source_id: row.source_id,
      canonical_url: item.canonical_url,
      published_at: item.published_at,
    });
  }
  return evidence;
}

function buildPrompt(
  topic: RadarTopic,
  plannedDirection: string,
  alignment: AlignedTopicView | undefined,
  market: MarketSnapshotView,
  evidence: EvidenceView[],
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
  return [
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
    "EVIDENCE:",
    evidenceLines,
    "Use only the exact EVIDENCE_ID values above. State uncertainty when evidence is sparse or contradictory.",
  ].join("\n");
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
  return { snapshot_id: snapshotId, provider: value.provider, as_of: value.as_of, instruments };
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
      if (typeof output[key] === "string") return output[key] as string;
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
