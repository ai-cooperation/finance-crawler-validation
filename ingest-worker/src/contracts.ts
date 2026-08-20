import { Validator, type Schema } from "@cfworker/json-schema";

import ingestEnvelopeSchema from "../../schemas/ingest-envelope.schema.json";
import marketAlignmentEnvelopeSchema from "../../schemas/market-alignment-envelope.schema.json";
import marketSnapshotSchema from "../../schemas/market-snapshot.schema.json";
import marketTopicAlignmentSchema from "../../schemas/market-topic-alignment.schema.json";
import rawItemSchema from "../../schemas/raw-item.schema.json";
import researchReportEnvelopeSchema from "../../schemas/research-report-envelope.schema.json";
import researchReportSchema from "../../schemas/research-report.schema.json";
import researchAgentRequestSchema from "../../schemas/research-agent-request.schema.json";
import topicSnapshotSchema from "../../schemas/topic-snapshot.schema.json";
import tradingAgentsPlanEnvelopeSchema from "../../schemas/tradingagents-plan-envelope.schema.json";
import tradingAgentsRunPlanSchema from "../../schemas/tradingagents-run-plan.schema.json";


export interface RightsPolicy {
  redistribution: "metadata_only" | "excerpt" | "full";
  retention_days: number;
  public_excerpt_chars: number;
}

export interface Engagement {
  score: number | null;
  comments: number | null;
  shares: number | null;
  likes: number | null;
}

export interface Evidence {
  route: string;
  status_code: number | null;
  final_url: string;
  extraction_method: string;
}

export interface RawItem {
  schema_version: 1;
  item_id: string;
  source_id: string;
  canonical_url: string;
  title: string;
  summary: string;
  content: string;
  published_at: string | null;
  collected_at: string;
  transport: "browser" | "json_api" | "rss" | "static_html";
  kind:
    | "aggregator"
    | "community"
    | "developer_community"
    | "market_data"
    | "news"
    | "official_data"
    | "official_news";
  layer: "market" | "news" | "official" | "social";
  content_sha256: string;
  rights: RightsPolicy;
  engagement: Engagement;
  evidence: Evidence;
}

export interface SourceCheckpoint {
  source_id: string;
  status: "success" | "partial" | "failed";
  last_successful_crawl: string | null;
  last_article_date: string | null;
  cursor: string | null;
}

export interface IngestEnvelope {
  schema_version: 1;
  operation: "upsert_items";
  run_id: string;
  workflow_run_id: string;
  commit_sha: string;
  snapshot_id: string;
  source_manifest_hash: string;
  collected_at: string;
  items: RawItem[];
  checkpoints: SourceCheckpoint[];
}

export interface TopicDivergence {
  direction: "aligned" | "news_leads" | "social_leads" | "insufficient_data";
  magnitude: number | null;
}

export interface RadarTopic {
  topic_id: string;
  label: string;
  score: number;
  item_count: number;
  source_count: number;
  news_count: number;
  social_count: number;
  evidence_ids: string[];
  divergence: TopicDivergence;
}

export interface TopicSnapshot {
  schema_version: 1;
  snapshot_id: string;
  run_id: string;
  as_of: string;
  partial: boolean;
  failed_sources: string[];
  input_item_ids: string[];
  topics: RadarTopic[];
}

export interface MarketInstrument {
  symbol: string;
  asset_type: string;
  currency: string | null;
  price: number | null;
  observed_at: string;
  change_24h_pct?: number | null;
  market_cap?: number | null;
  source_item_ids: string[];
}

export interface MarketSnapshot {
  schema_version: 1;
  snapshot_id: string;
  as_of: string;
  provider: string;
  instruments: MarketInstrument[];
}

export interface AlignedTopic {
  topic_id: string;
  label: string;
  topic_score: number;
  market_direction: "positive" | "negative" | "mixed" | "not_covered";
  instrument_count: number;
  symbols: string[];
  mean_change_24h_pct: number | null;
  evidence_ids: string[];
}

export interface MarketTopicAlignment {
  schema_version: 1;
  alignment_id: string;
  topic_snapshot_id: string;
  market_snapshot_id: string;
  generated_at: string;
  partial: boolean;
  coverage_ratio: number;
  topics: AlignedTopic[];
}

export interface MarketAlignmentEnvelope {
  schema_version: 1;
  operation: "upsert_market_alignment";
  run_id: string;
  workflow_run_id: string;
  commit_sha: string;
  market_snapshot: MarketSnapshot;
  alignment: MarketTopicAlignment;
}

export interface TradingAgentsTopicPlan {
  topic_id: string;
  label: string;
  score: number;
  decision: "run" | "skip";
  reason: "top_ranked" | "divergence" | "user_requested" | "budget_cap" | "not_requested";
  market_direction: "positive" | "negative" | "mixed" | "not_covered";
  evidence_ids: string[];
}

export interface TradingAgentsRunPlan {
  schema_version: 1;
  plan_id: string;
  topic_snapshot_id: string;
  alignment_id: string | null;
  created_at: string;
  decision: "eligible" | "skipped";
  skip_reason: "none" | "no_topics" | "missing_market_alignment" | "no_budget";
  budget: {
    max_topics: number;
    max_claims_per_topic: number;
    max_tokens: number;
    max_usd: number;
    model: string;
  };
  topics: TradingAgentsTopicPlan[];
}

export interface TradingAgentsPlanEnvelope {
  schema_version: 1;
  operation: "upsert_tradingagents_plan";
  run_id: string;
  workflow_run_id: string;
  commit_sha: string;
  plan: TradingAgentsRunPlan;
}

export interface ResearchClaim {
  text: string;
  confidence: number;
  evidence_ids: string[];
}

export interface ResearchReport {
  schema_version: 1;
  report_id: string;
  topic_snapshot_id: string;
  plan_id: string;
  alignment_id: string;
  market_snapshot_id: string;
  topic_id: string;
  generated_at: string;
  expires_at: string;
  model: string;
  agent_version: string;
  second_opinion: true;
  evidence_ids: string[];
  bull_case: ResearchClaim[];
  bear_case: ResearchClaim[];
  risk_view: ResearchClaim[];
}

export interface ResearchReportEnvelope {
  schema_version: 1;
  operation: "upsert_research_report";
  run_id: string;
  workflow_run_id: string;
  commit_sha: string;
  report: ResearchReport;
}

export interface ResearchAgentRequest {
  schema_version: 1;
  operation: "generate_research_reports";
  run_id: string;
  workflow_run_id: string;
  commit_sha: string;
  plan_id: string;
  alignment_id: string;
  authorize_model_execution: true;
  model?: string;
  max_reports?: number;
}

const ingestValidator = new Validator(ingestEnvelopeSchema as Schema, "2020-12", false);
const marketSnapshotValidator = new Validator(marketSnapshotSchema as Schema, "2020-12", false);
const marketTopicAlignmentValidator = new Validator(
  marketTopicAlignmentSchema as Schema,
  "2020-12",
  false,
);
const marketAlignmentEnvelopeValidator = new Validator(
  marketAlignmentEnvelopeSchema as Schema,
  "2020-12",
  false,
);
const tradingAgentsRunPlanValidator = new Validator(
  tradingAgentsRunPlanSchema as Schema,
  "2020-12",
  false,
);
const tradingAgentsPlanEnvelopeValidator = new Validator(
  tradingAgentsPlanEnvelopeSchema as Schema,
  "2020-12",
  false,
);
const rawItemValidator = new Validator(rawItemSchema as Schema, "2020-12", false);
const researchReportValidator = new Validator(researchReportSchema as Schema, "2020-12", false);
const researchReportEnvelopeValidator = new Validator(
  researchReportEnvelopeSchema as Schema,
  "2020-12",
  false,
);
const researchAgentRequestValidator = new Validator(
  researchAgentRequestSchema as Schema,
  "2020-12",
  false,
);
const topicSnapshotValidator = new Validator(
  topicSnapshotSchema as Schema,
  "2020-12",
  false,
);

export class PayloadValidationError extends Error {
  readonly details: string[];

  constructor(contract: string, details: string[]) {
    super(`${contract} validation failed`);
    this.name = "PayloadValidationError";
    this.details = details;
  }
}

function assertValid<T>(
  contract: string,
  validator: Validator,
  payload: unknown,
): asserts payload is T {
  const result = validator.validate(payload);
  if (result.valid) return;
  const details = result.errors.map(
    (error) => `${error.instanceLocation || "$"}: ${error.error}`,
  );
  throw new PayloadValidationError(contract, details);
}

export function validateIngestEnvelope(payload: unknown): IngestEnvelope {
  assertValid<IngestEnvelope>("ingest-envelope", ingestValidator, payload);
  for (const item of payload.items) {
    assertValid<RawItem>("raw-item", rawItemValidator, item);
  }
  assertEnvelopeInvariants(payload);
  return payload;
}

export function validateTopicSnapshot(payload: unknown): TopicSnapshot {
  assertValid<TopicSnapshot>("topic-snapshot", topicSnapshotValidator, payload);
  if (!payload.partial && payload.topics.length !== 3) {
    throw new PayloadValidationError("topic-snapshot", [
      "$.topics: a complete snapshot must contain exactly three topics",
    ]);
  }
  const inputIds = new Set(payload.input_item_ids);
  for (const topic of payload.topics) {
    for (const evidenceId of topic.evidence_ids) {
      if (!inputIds.has(evidenceId)) {
        throw new PayloadValidationError("topic-snapshot", [
          `$.topics.${topic.topic_id}: evidence id is absent from input_item_ids`,
        ]);
      }
    }
  }
  return payload;
}

export function validateMarketAlignmentEnvelope(payload: unknown): MarketAlignmentEnvelope {
  assertValid<MarketAlignmentEnvelope>(
    "market-alignment-envelope",
    marketAlignmentEnvelopeValidator,
    payload,
  );
  assertValid<MarketSnapshot>("market-snapshot", marketSnapshotValidator, payload.market_snapshot);
  assertValid<MarketTopicAlignment>(
    "market-topic-alignment",
    marketTopicAlignmentValidator,
    payload.alignment,
  );
  if (payload.alignment.market_snapshot_id !== payload.market_snapshot.snapshot_id) {
    throw new PayloadValidationError("market-alignment-envelope", [
      "$.alignment.market_snapshot_id: must match market_snapshot.snapshot_id",
    ]);
  }
  return payload;
}

export function validateTradingAgentsPlanEnvelope(payload: unknown): TradingAgentsPlanEnvelope {
  assertValid<TradingAgentsPlanEnvelope>(
    "tradingagents-plan-envelope",
    tradingAgentsPlanEnvelopeValidator,
    payload,
  );
  assertValid<TradingAgentsRunPlan>(
    "tradingagents-run-plan",
    tradingAgentsRunPlanValidator,
    payload.plan,
  );
  if (payload.plan.alignment_id === null) {
    throw new PayloadValidationError("tradingagents-plan-envelope", [
      "$.plan.alignment_id: persisted plan requires a market alignment",
    ]);
  }
  if (payload.plan.topic_snapshot_id.length < 1) {
    throw new PayloadValidationError("tradingagents-plan-envelope", [
      "$.plan.topic_snapshot_id: is required",
    ]);
  }
  return payload;
}

export function validateTradingAgentsRunPlan(payload: unknown): TradingAgentsRunPlan {
  assertValid<TradingAgentsRunPlan>("tradingagents-run-plan", tradingAgentsRunPlanValidator, payload);
  return payload;
}

export function validateResearchReportEnvelope(payload: unknown): ResearchReportEnvelope {
  assertValid<ResearchReportEnvelope>(
    "research-report-envelope",
    researchReportEnvelopeValidator,
    payload,
  );
  assertValid<ResearchReport>("research-report", researchReportValidator, payload.report);
  if (!payload.report.second_opinion) {
    throw new PayloadValidationError("research-report", [
      "$.second_opinion: research reports must be marked as a second opinion",
    ]);
  }
  const generatedAt = Date.parse(payload.report.generated_at);
  const expiresAt = Date.parse(payload.report.expires_at);
  if (!Number.isFinite(generatedAt) || !Number.isFinite(expiresAt) || expiresAt <= generatedAt) {
    throw new PayloadValidationError("research-report", [
      "$.expires_at: must be later than generated_at",
    ]);
  }
  return payload;
}

export function validateResearchAgentRequest(payload: unknown): ResearchAgentRequest {
  assertValid<ResearchAgentRequest>(
    "research-agent-request",
    researchAgentRequestValidator,
    payload,
  );
  return payload;
}

function assertEnvelopeInvariants(envelope: IngestEnvelope): void {
  const itemIds = new Set<string>();
  const checkpointIds = new Set<string>();
  for (const checkpoint of envelope.checkpoints) {
    if (checkpointIds.has(checkpoint.source_id)) {
      throw new PayloadValidationError("ingest-envelope", [
        `$.checkpoints: duplicate source ${checkpoint.source_id}`,
      ]);
    }
    checkpointIds.add(checkpoint.source_id);
    if (checkpoint.status === "success" && checkpoint.last_successful_crawl === null) {
      throw new PayloadValidationError("ingest-envelope", [
        `$.checkpoints.${checkpoint.source_id}: success requires last_successful_crawl`,
      ]);
    }
  }
  for (const item of envelope.items) {
    if (itemIds.has(item.item_id)) {
      throw new PayloadValidationError("ingest-envelope", [
        `$.items: duplicate item_id ${item.item_id}`,
      ]);
    }
    itemIds.add(item.item_id);
    if (!checkpointIds.has(item.source_id)) {
      throw new PayloadValidationError("ingest-envelope", [
        `$.items.${item.item_id}: source has no checkpoint`,
      ]);
    }
  }
}
