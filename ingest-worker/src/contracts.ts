import { Validator, type Schema } from "@cfworker/json-schema";

import ingestEnvelopeSchema from "../../schemas/ingest-envelope.schema.json";
import rawItemSchema from "../../schemas/raw-item.schema.json";
import topicSnapshotSchema from "../../schemas/topic-snapshot.schema.json";


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

const ingestValidator = new Validator(ingestEnvelopeSchema as Schema, "2020-12", false);
const rawItemValidator = new Validator(rawItemSchema as Schema, "2020-12", false);
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
