import {
  type IngestEnvelope,
  type MarketAlignmentEnvelope,
  type ResearchReportEnvelope,
  type TradingAgentsPlanEnvelope,
  PayloadValidationError,
  type RawItem,
  type SourceCheckpoint,
  type TopicSnapshot,
  validateIngestEnvelope,
  validateMarketAlignmentEnvelope,
  validateResearchReportEnvelope,
  validateTradingAgentsRunPlan,
  validateTradingAgentsPlanEnvelope,
  validateTopicSnapshot,
} from "./contracts";
import { canonicalJson } from "./canonical-json";


export class HttpError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: string[];

  constructor(status: number, code: string, details: string[] = []) {
    super(code);
    this.name = "HttpError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export interface IngestResult {
  run_id: string;
  snapshot_id: string;
  received_items: number;
  status: "staging" | "published";
  replayed: boolean;
}

export interface PublishResult {
  run_id: string;
  snapshot_id: string;
  topic_count: number;
  status: "published";
  replayed: boolean;
}

export interface MarketAlignmentResult {
  run_id: string;
  market_snapshot_id: string;
  alignment_id: string;
  instrument_count: number;
  topic_count: number;
  status: "published";
  replayed: boolean;
}

export interface TradingAgentsPlanResult {
  run_id: string;
  plan_id: string;
  alignment_id: string;
  topic_count: number;
  decision: "eligible" | "skipped";
  status: "published";
  replayed: boolean;
}

export interface ResearchReportResult {
  run_id: string;
  report_id: string;
  plan_id: string;
  alignment_id: string;
  topic_id: string;
  status: "published";
  replayed: boolean;
}

export async function ingestItems(
  env: Env,
  payload: unknown,
  now: Date,
): Promise<IngestResult> {
  const envelope = validateOrHttp(() => validateIngestEnvelope(payload));
  const payloadHash = await sha256Hex(canonicalJson(envelope));
  const replay = await reserveOrReplayIngest(env.DB, envelope, payloadHash, now);
  if (replay !== null) return replay;
  await persistRawObjects(env.RAW_OBJECTS, envelope.items);

  const happenedAt = now.toISOString();
  const audit = await buildAuditStatement(
    env.DB,
    envelope.run_id,
    "raw_collected",
    envelope.source_manifest_hash,
    happenedAt,
  );
  const statements: D1PreparedStatement[] = [];
  for (const item of envelope.items) {
    statements.push(rawItemStatement(env.DB, item, happenedAt));
    statements.push(
      env.DB.prepare(
        "INSERT OR IGNORE INTO run_items (run_id, item_id) VALUES (?, ?)",
      ).bind(envelope.run_id, item.item_id),
    );
  }
  for (const checkpoint of envelope.checkpoints) {
    statements.push(checkpointStatement(env.DB, envelope.snapshot_id, checkpoint, happenedAt));
  }
  statements.push(
    env.DB.prepare(
      `UPDATE runs SET item_count = (
        SELECT COUNT(*) FROM run_items WHERE run_id = ?
      ) WHERE run_id = ?`,
    ).bind(envelope.run_id, envelope.run_id),
  );
  statements.push(audit);
  statements.push(
    env.DB.prepare(
      `UPDATE ingest_receipts
       SET status = 'completed', completed_at = ?
       WHERE run_id = ? AND payload_sha256 = ?`,
    ).bind(happenedAt, envelope.run_id, payloadHash),
  );

  try {
    await env.DB.batch(statements);
  } catch (error) {
    logStorageFailure("d1_ingest_failed", envelope.run_id, error);
    throw new HttpError(503, "storage_write_failed");
  }
  return {
    run_id: envelope.run_id,
    snapshot_id: envelope.snapshot_id,
    received_items: envelope.items.length,
    status: "staging",
    replayed: false,
  };
}

export async function publishSnapshot(
  env: Env,
  payload: unknown,
  now: Date,
): Promise<PublishResult> {
  const snapshot = validateOrHttp(() => validateTopicSnapshot(payload));
  const serialized = JSON.stringify(snapshot);
  const contentHash = await sha256Hex(serialized);
  const run = await env.DB.prepare(
    "SELECT snapshot_id, status FROM runs WHERE run_id = ?",
  ).bind(snapshot.run_id).first<{ snapshot_id: string; status: string }>();
  if (!run) throw new HttpError(409, "run_not_found");
  if (run.snapshot_id !== snapshot.snapshot_id) {
    throw new HttpError(409, "snapshot_run_conflict");
  }
  const existing = await env.DB.prepare(
    "SELECT run_id, content_sha256, topic_count FROM topic_snapshots WHERE snapshot_id = ?",
  ).bind(snapshot.snapshot_id).first<{
    run_id: string;
    content_sha256: string;
    topic_count: number;
  }>();
  if (existing !== null) {
    if (existing.run_id !== snapshot.run_id || existing.content_sha256 !== contentHash) {
      throw new HttpError(409, "snapshot_payload_conflict");
    }
    return {
      run_id: snapshot.run_id,
      snapshot_id: snapshot.snapshot_id,
      topic_count: Number(existing.topic_count),
      status: "published",
      replayed: true,
    };
  }
  if (run.status === "published") throw new HttpError(409, "published_snapshot_missing");
  await assertEvidenceBelongsToRun(env.DB, snapshot);

  const objectKey = `topics/${snapshot.snapshot_id}.json`;
  try {
    await env.RAW_OBJECTS.put(objectKey, serialized, {
      httpMetadata: { contentType: "application/json" },
      customMetadata: { content_sha256: contentHash, run_id: snapshot.run_id },
    });
  } catch (error) {
    logStorageFailure("r2_topic_write_failed", snapshot.run_id, error);
    throw new HttpError(503, "storage_write_failed");
  }

  const happenedAt = now.toISOString();
  const audit = await buildAuditStatement(
    env.DB,
    snapshot.run_id,
    "published",
    contentHash,
    happenedAt,
  );
  try {
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO topic_snapshots (
          snapshot_id, run_id, as_of, partial, failed_sources_json,
          object_key, content_sha256, topic_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_id) DO UPDATE SET
          as_of = excluded.as_of,
          partial = excluded.partial,
          failed_sources_json = excluded.failed_sources_json,
          object_key = excluded.object_key,
          content_sha256 = excluded.content_sha256,
          topic_count = excluded.topic_count`,
      ).bind(
        snapshot.snapshot_id,
        snapshot.run_id,
        snapshot.as_of,
        snapshot.partial ? 1 : 0,
        JSON.stringify(snapshot.failed_sources),
        objectKey,
        contentHash,
        snapshot.topics.length,
        happenedAt,
      ),
      env.DB.prepare(
        `INSERT INTO current_snapshot (singleton_id, snapshot_id, updated_at)
         VALUES (1, ?, ?)
         ON CONFLICT(singleton_id) DO UPDATE SET
           snapshot_id = excluded.snapshot_id,
           updated_at = excluded.updated_at`,
      ).bind(snapshot.snapshot_id, happenedAt),
      env.DB.prepare(
        "UPDATE runs SET status = 'published', published_at = ? WHERE run_id = ?",
      ).bind(happenedAt, snapshot.run_id),
      audit,
    ]);
  } catch (error) {
    logStorageFailure("d1_publish_failed", snapshot.run_id, error);
    throw new HttpError(503, "storage_write_failed");
  }
  return {
    run_id: snapshot.run_id,
    snapshot_id: snapshot.snapshot_id,
    topic_count: snapshot.topics.length,
    status: "published",
    replayed: false,
  };
}

export async function ingestMarketAlignment(
  env: Env,
  payload: unknown,
  now: Date,
): Promise<MarketAlignmentResult> {
  const envelope = validateOrHttp(() => validateMarketAlignmentEnvelope(payload));
  const run = await env.DB.prepare(
    "SELECT snapshot_id, status FROM runs WHERE run_id = ?",
  ).bind(envelope.run_id).first<{ snapshot_id: string; status: string }>();
  if (!run) throw new HttpError(409, "run_not_found");
  if (run.status !== "published") throw new HttpError(409, "run_not_published");
  if (envelope.alignment.topic_snapshot_id !== run.snapshot_id) {
    throw new HttpError(409, "alignment_topic_run_conflict");
  }
  await assertAlignmentEvidenceBelongsToRun(env.DB, envelope);

  const marketSerialized = canonicalJson(envelope.market_snapshot);
  const alignmentSerialized = canonicalJson(envelope.alignment);
  const marketHash = await sha256Hex(marketSerialized);
  const alignmentHash = await sha256Hex(alignmentSerialized);
  const existing = await env.DB.prepare(
    `SELECT run_id, market_snapshot_id, content_sha256
     FROM topic_market_alignments WHERE alignment_id = ?`,
  ).bind(envelope.alignment.alignment_id).first<{
    run_id: string;
    market_snapshot_id: string;
    content_sha256: string;
  }>();
  if (existing !== null) {
    if (
      existing.run_id !== envelope.run_id ||
      existing.market_snapshot_id !== envelope.market_snapshot.snapshot_id ||
      existing.content_sha256 !== alignmentHash
    ) {
      throw new HttpError(409, "alignment_payload_conflict");
    }
    return {
      run_id: envelope.run_id,
      market_snapshot_id: envelope.market_snapshot.snapshot_id,
      alignment_id: envelope.alignment.alignment_id,
      instrument_count: envelope.market_snapshot.instruments.length,
      topic_count: envelope.alignment.topics.length,
      status: "published",
      replayed: true,
    };
  }
  const marketExisting = await env.DB.prepare(
    "SELECT run_id, content_sha256 FROM market_snapshots WHERE snapshot_id = ?",
  ).bind(envelope.market_snapshot.snapshot_id).first<{
    run_id: string;
    content_sha256: string;
  }>();
  if (marketExisting !== null && (
    marketExisting.run_id !== envelope.run_id || marketExisting.content_sha256 !== marketHash
  )) {
    throw new HttpError(409, "market_snapshot_payload_conflict");
  }

  const marketObjectKey = `market/${envelope.market_snapshot.snapshot_id}.json`;
  const alignmentObjectKey = `alignments/${envelope.alignment.alignment_id}.json`;
  try {
    if (marketExisting === null) {
      await env.RAW_OBJECTS.put(marketObjectKey, marketSerialized, {
        httpMetadata: { contentType: "application/json" },
        customMetadata: { content_sha256: marketHash, run_id: envelope.run_id },
      });
    }
    await env.RAW_OBJECTS.put(alignmentObjectKey, alignmentSerialized, {
      httpMetadata: { contentType: "application/json" },
      customMetadata: { content_sha256: alignmentHash, run_id: envelope.run_id },
    });
  } catch (error) {
    logStorageFailure("r2_market_alignment_write_failed", envelope.run_id, error);
    throw new HttpError(503, "storage_write_failed");
  }

  const happenedAt = now.toISOString();
  const audit = await buildAuditStatement(
    env.DB,
    envelope.run_id,
    "openbb_normalized",
    alignmentHash,
    happenedAt,
  );
  const statements: D1PreparedStatement[] = [];
  if (marketExisting === null) {
    statements.push(
      env.DB.prepare(
        `INSERT INTO market_snapshots (
          snapshot_id, run_id, as_of, provider, object_key,
          content_sha256, instrument_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        envelope.market_snapshot.snapshot_id,
        envelope.run_id,
        envelope.market_snapshot.as_of,
        envelope.market_snapshot.provider,
        marketObjectKey,
        marketHash,
        envelope.market_snapshot.instruments.length,
        happenedAt,
      ),
    );
  }
  statements.push(
    env.DB.prepare(
      `INSERT INTO topic_market_alignments (
        alignment_id, run_id, topic_snapshot_id, market_snapshot_id,
        generated_at, partial, coverage_ratio, object_key,
        content_sha256, topic_count, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      envelope.alignment.alignment_id,
      envelope.run_id,
      envelope.alignment.topic_snapshot_id,
      envelope.alignment.market_snapshot_id,
      envelope.alignment.generated_at,
      envelope.alignment.partial ? 1 : 0,
      envelope.alignment.coverage_ratio,
      alignmentObjectKey,
      alignmentHash,
      envelope.alignment.topics.length,
      happenedAt,
    ),
  );
  statements.push(audit);
  try {
    await env.DB.batch(statements);
  } catch (error) {
    logStorageFailure("d1_market_alignment_failed", envelope.run_id, error);
    throw new HttpError(503, "storage_write_failed");
  }
  return {
    run_id: envelope.run_id,
    market_snapshot_id: envelope.market_snapshot.snapshot_id,
    alignment_id: envelope.alignment.alignment_id,
    instrument_count: envelope.market_snapshot.instruments.length,
    topic_count: envelope.alignment.topics.length,
    status: "published",
    replayed: false,
  };
}

export async function ingestTradingAgentsPlan(
  env: Env,
  payload: unknown,
  now: Date,
): Promise<TradingAgentsPlanResult> {
  const envelope = validateOrHttp(() => validateTradingAgentsPlanEnvelope(payload));
  const alignmentId = envelope.plan.alignment_id;
  if (alignmentId === null) throw new HttpError(422, "alignment_missing");
  const run = await env.DB.prepare(
    "SELECT snapshot_id, status FROM runs WHERE run_id = ?",
  ).bind(envelope.run_id).first<{ snapshot_id: string; status: string }>();
  if (!run) throw new HttpError(409, "run_not_found");
  if (run.status !== "published") throw new HttpError(409, "run_not_published");
  if (envelope.plan.topic_snapshot_id !== run.snapshot_id) {
    throw new HttpError(409, "plan_topic_run_conflict");
  }
  const alignment = await env.DB.prepare(
    `SELECT alignment_id FROM topic_market_alignments
     WHERE alignment_id = ? AND run_id = ? AND topic_snapshot_id = ?`,
  ).bind(alignmentId, envelope.run_id, run.snapshot_id)
    .first<{ alignment_id: string }>();
  if (!alignment) throw new HttpError(409, "alignment_not_found");
  await assertPlanEvidenceBelongsToRun(env.DB, envelope);

  let persistedPlan = envelope.plan;
  let serialized = canonicalJson(persistedPlan);
  let contentHash = await sha256Hex(serialized);
  const existing = await env.DB.prepare(
    `SELECT run_id, alignment_id, content_sha256, decision, topic_count
     FROM tradingagents_plans WHERE plan_id = ?`,
  ).bind(envelope.plan.plan_id).first<{
    run_id: string;
    alignment_id: string;
    content_sha256: string;
    decision: "eligible" | "skipped";
    topic_count: number;
  }>();
  if (existing !== null) {
    if (existing.run_id === envelope.run_id &&
        existing.alignment_id === alignmentId &&
        existing.content_sha256 === contentHash) {
      return {
        run_id: envelope.run_id,
        plan_id: envelope.plan.plan_id,
        alignment_id: alignmentId,
        topic_count: Number(existing.topic_count),
        decision: existing.decision,
        status: "published",
        replayed: true,
      };
    }

    // GitHub re-runs preserve GITHUB_RUN_ID, while this workflow's collector
    // intentionally creates a fresh run_id.  A plan keyed only by the former
    // therefore collides with the previous attempt.  Preserve the historical
    // row and materialize an attempt-scoped copy; completion resolves it by
    // the current run/alignment (see planForRun) instead of overwriting audit
    // history.  This is the only supported cross-run conflict recovery path.
    const attemptPlanId = `plan_${envelope.run_id}`;
    const attemptExisting = await env.DB.prepare(
      `SELECT run_id, alignment_id, content_sha256, decision, topic_count
       FROM tradingagents_plans WHERE plan_id = ?`,
    ).bind(attemptPlanId).first<{
      run_id: string;
      alignment_id: string;
      content_sha256: string;
      decision: "eligible" | "skipped";
      topic_count: number;
    }>();
    persistedPlan = { ...envelope.plan, plan_id: attemptPlanId };
    serialized = canonicalJson(persistedPlan);
    contentHash = await sha256Hex(serialized);
    if (attemptExisting !== null) {
      if (attemptExisting.run_id !== envelope.run_id ||
          attemptExisting.alignment_id !== alignmentId ||
          attemptExisting.content_sha256 !== contentHash) {
        throw new HttpError(409, "plan_payload_conflict");
      }
      return {
        run_id: envelope.run_id,
        plan_id: attemptPlanId,
        alignment_id: alignmentId,
        topic_count: Number(attemptExisting.topic_count),
        decision: attemptExisting.decision,
        status: "published",
        replayed: true,
      };
    }
  }

  const objectKey = `plans/${persistedPlan.plan_id}.json`;
  try {
    await env.RAW_OBJECTS.put(objectKey, serialized, {
      httpMetadata: { contentType: "application/json" },
      customMetadata: { content_sha256: contentHash, run_id: envelope.run_id },
    });
  } catch (error) {
    logStorageFailure("r2_tradingagents_plan_write_failed", envelope.run_id, error);
    throw new HttpError(503, "storage_write_failed");
  }
  const happenedAt = now.toISOString();
  const audit = await buildAuditStatement(
    env.DB,
    envelope.run_id,
    "tradingagents_planned",
    contentHash,
    happenedAt,
  );
  try {
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO tradingagents_plans (
          plan_id, run_id, topic_snapshot_id, alignment_id,
          decision, skip_reason, object_key, content_sha256,
          topic_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        persistedPlan.plan_id,
        envelope.run_id,
        persistedPlan.topic_snapshot_id,
        alignmentId,
        persistedPlan.decision,
        persistedPlan.skip_reason,
        objectKey,
        contentHash,
        persistedPlan.topics.length,
        happenedAt,
      ),
      audit,
    ]);
  } catch (error) {
    logStorageFailure("d1_tradingagents_plan_failed", envelope.run_id, error);
    throw new HttpError(503, "storage_write_failed");
  }
  return {
    run_id: envelope.run_id,
    plan_id: persistedPlan.plan_id,
    alignment_id: alignmentId,
    topic_count: persistedPlan.topics.length,
    decision: persistedPlan.decision,
    status: "published",
    replayed: false,
  };
}

export async function ingestResearchReport(
  env: Env,
  payload: unknown,
  now: Date,
): Promise<ResearchReportResult> {
  const envelope = validateOrHttp(() => validateResearchReportEnvelope(payload));
  const report = envelope.report;
  const run = await env.DB.prepare(
    "SELECT snapshot_id, status FROM runs WHERE run_id = ?",
  ).bind(envelope.run_id).first<{ snapshot_id: string; status: string }>();
  if (!run) throw new HttpError(409, "run_not_found");
  if (run.status !== "published") throw new HttpError(409, "run_not_published");
  if (report.topic_snapshot_id !== run.snapshot_id) {
    throw new HttpError(409, "report_topic_run_conflict");
  }

  const plan = await env.DB.prepare(
    `SELECT run_id, alignment_id, object_key
     FROM tradingagents_plans WHERE plan_id = ?`,
  ).bind(report.plan_id).first<{
    run_id: string;
    alignment_id: string;
    object_key: string;
  }>();
  if (!plan) throw new HttpError(409, "plan_not_found");
  if (plan.run_id !== envelope.run_id || plan.alignment_id !== report.alignment_id) {
    throw new HttpError(409, "report_plan_conflict");
  }

  const alignment = await env.DB.prepare(
    `SELECT market_snapshot_id FROM topic_market_alignments
     WHERE alignment_id = ? AND run_id = ? AND topic_snapshot_id = ?`,
  ).bind(report.alignment_id, envelope.run_id, run.snapshot_id)
    .first<{ market_snapshot_id: string }>();
  if (!alignment) throw new HttpError(409, "alignment_not_found");
  if (alignment.market_snapshot_id !== report.market_snapshot_id) {
    throw new HttpError(409, "report_market_conflict");
  }

  const snapshot = await env.DB.prepare(
    "SELECT object_key FROM topic_snapshots WHERE snapshot_id = ? AND run_id = ?",
  ).bind(report.topic_snapshot_id, envelope.run_id).first<{ object_key: string }>();
  if (!snapshot) throw new HttpError(409, "topic_snapshot_not_found");
  const snapshotPayload = await readPrivateJson(env.RAW_OBJECTS, snapshot.object_key, "topic_snapshot");
  const topicSnapshot = validateOrHttp(() => validateTopicSnapshot(snapshotPayload));
  if (!topicSnapshot.topics.some((topic) => topic.topic_id === report.topic_id)) {
    throw new HttpError(409, "topic_not_found");
  }

  const planPayload = await readPrivateJson(env.RAW_OBJECTS, plan.object_key, "tradingagents_plan");
  const runPlan = validateOrHttp(() => validateTradingAgentsRunPlan(planPayload));
  const plannedTopic = runPlan.topics.find((topic) => topic.topic_id === report.topic_id);
  if (!plannedTopic || plannedTopic.decision !== "run") {
    throw new HttpError(409, "topic_not_planned");
  }
  await assertResearchReportEvidenceBelongsToRun(env.DB, envelope);

  const serialized = canonicalJson(report);
  const contentHash = await sha256Hex(serialized);
  const existing = await env.DB.prepare(
    `SELECT run_id, plan_id, alignment_id, content_sha256, topic_id
     FROM research_reports WHERE report_id = ?`,
  ).bind(report.report_id).first<{
    run_id: string;
    plan_id: string;
    alignment_id: string;
    content_sha256: string;
    topic_id: string;
  }>();
  if (existing !== null) {
    if (
      existing.run_id !== envelope.run_id ||
      existing.plan_id !== report.plan_id ||
      existing.alignment_id !== report.alignment_id ||
      existing.topic_id !== report.topic_id ||
      existing.content_sha256 !== contentHash
    ) {
      throw new HttpError(409, "report_payload_conflict");
    }
    return {
      run_id: envelope.run_id,
      report_id: report.report_id,
      plan_id: report.plan_id,
      alignment_id: report.alignment_id,
      topic_id: report.topic_id,
      status: "published",
      replayed: true,
    };
  }

  const objectKey = `reports/${report.report_id}.json`;
  try {
    await env.RAW_OBJECTS.put(objectKey, serialized, {
      httpMetadata: { contentType: "application/json" },
      customMetadata: { content_sha256: contentHash, run_id: envelope.run_id },
    });
  } catch (error) {
    logStorageFailure("r2_research_report_write_failed", envelope.run_id, error);
    throw new HttpError(503, "storage_write_failed");
  }
  const happenedAt = now.toISOString();
  const audit = await buildAuditStatement(
    env.DB,
    envelope.run_id,
    "tradingagents_completed",
    contentHash,
    happenedAt,
  );
  try {
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO research_reports (
          report_id, run_id, topic_snapshot_id, plan_id, alignment_id,
          market_snapshot_id, topic_id, object_key, content_sha256,
          model, agent_version, report_profile, generated_at, expires_at, evidence_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        report.report_id,
        envelope.run_id,
        report.topic_snapshot_id,
        report.plan_id,
        report.alignment_id,
        report.market_snapshot_id,
        report.topic_id,
        objectKey,
        contentHash,
        report.model,
        report.agent_version,
        report.report_profile ?? "detailed_traceable",
        report.generated_at,
        report.expires_at,
        report.evidence_ids.length,
        happenedAt,
      ),
      audit,
    ]);
  } catch (error) {
    logStorageFailure("d1_research_report_failed", envelope.run_id, error);
    throw new HttpError(503, "storage_write_failed");
  }
  return {
    run_id: envelope.run_id,
    report_id: report.report_id,
    plan_id: report.plan_id,
    alignment_id: report.alignment_id,
    topic_id: report.topic_id,
    status: "published",
    replayed: false,
  };
}

async function reserveOrReplayIngest(
  db: D1Database,
  envelope: IngestEnvelope,
  payloadHash: string,
  now: Date,
): Promise<IngestResult | null> {
  const existing = await findExistingRun(db, envelope.run_id);
  if (existing !== null) return evaluateExistingRun(existing, envelope, payloadHash);

  const createdAt = now.toISOString();
  try {
    await db.batch([
      db.prepare(
        `INSERT INTO runs (
          run_id, workflow_run_id, commit_sha, snapshot_id, source_manifest_hash,
          status, collected_at, item_count
        ) VALUES (?, ?, ?, ?, ?, 'staging', ?, 0)`,
      ).bind(
        envelope.run_id,
        envelope.workflow_run_id,
        envelope.commit_sha,
        envelope.snapshot_id,
        envelope.source_manifest_hash,
        envelope.collected_at,
      ),
      db.prepare(
        `INSERT INTO ingest_receipts (
          run_id, payload_sha256, status, created_at, completed_at
        ) VALUES (?, ?, 'started', ?, NULL)`,
      ).bind(envelope.run_id, payloadHash, createdAt),
    ]);
    return null;
  } catch {
    const raced = await findExistingRun(db, envelope.run_id);
    if (raced !== null) return evaluateExistingRun(raced, envelope, payloadHash);
    throw new HttpError(503, "storage_write_failed");
  }
}

interface ExistingRun {
  workflow_run_id: string;
  commit_sha: string;
  snapshot_id: string;
  source_manifest_hash: string;
  status: string;
  payload_sha256: string | null;
  receipt_status: string | null;
}

async function findExistingRun(db: D1Database, runId: string): Promise<ExistingRun | null> {
  const existing = await db.prepare(
    `SELECT
      runs.workflow_run_id, runs.commit_sha, runs.snapshot_id,
      runs.source_manifest_hash, runs.status,
      ingest_receipts.payload_sha256,
      ingest_receipts.status AS receipt_status
    FROM runs
    LEFT JOIN ingest_receipts ON ingest_receipts.run_id = runs.run_id
    WHERE runs.run_id = ?`,
  ).bind(runId).first<ExistingRun>();
  return existing ?? null;
}

function evaluateExistingRun(
  existing: ExistingRun,
  envelope: IngestEnvelope,
  payloadHash: string,
): IngestResult | null {
  if (
    existing.workflow_run_id !== envelope.workflow_run_id ||
    existing.commit_sha !== envelope.commit_sha ||
    existing.snapshot_id !== envelope.snapshot_id ||
    existing.source_manifest_hash !== envelope.source_manifest_hash
  ) {
    throw new HttpError(409, "run_identity_conflict");
  }
  if (existing.payload_sha256 === null || existing.receipt_status === null) {
    throw new HttpError(409, "run_receipt_missing");
  }
  if (existing.payload_sha256 !== payloadHash) {
    throw new HttpError(409, "run_payload_conflict");
  }
  if (existing.receipt_status === "started") return null;
  if (existing.receipt_status !== "completed") {
    throw new HttpError(409, "run_receipt_invalid");
  }
  if (existing.status !== "staging" && existing.status !== "published") {
    throw new HttpError(409, "run_not_replayable");
  }
  return {
    run_id: envelope.run_id,
    snapshot_id: envelope.snapshot_id,
    received_items: envelope.items.length,
    status: existing.status,
    replayed: true,
  };
}

async function persistRawObjects(bucket: R2Bucket, items: RawItem[]): Promise<void> {
  for (const item of items) {
    const objectKey = rawObjectKey(item);
    try {
      await bucket.put(objectKey, JSON.stringify(item), {
        httpMetadata: { contentType: "application/json" },
        customMetadata: {
          content_sha256: item.content_sha256,
          source_id: item.source_id,
        },
      });
    } catch (error) {
      logStorageFailure("r2_raw_write_failed", item.source_id, error);
      throw new HttpError(503, "storage_write_failed");
    }
  }
}

function rawItemStatement(
  db: D1Database,
  item: RawItem,
  createdAt: string,
): D1PreparedStatement {
  return db.prepare(
    `INSERT INTO raw_items (
      item_id, source_id, canonical_url, title, summary, published_at,
      collected_at, transport, kind, layer, content_sha256, object_key,
      rights_json, evidence_json, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source_id, canonical_url, content_sha256) DO NOTHING`,
  ).bind(
    item.item_id,
    item.source_id,
    item.canonical_url,
    item.title,
    item.summary,
    item.published_at,
    item.collected_at,
    item.transport,
    item.kind,
    item.layer,
    item.content_sha256,
    rawObjectKey(item),
    JSON.stringify(item.rights),
    JSON.stringify(item.evidence),
    createdAt,
  );
}

function checkpointStatement(
  db: D1Database,
  snapshotId: string,
  checkpoint: SourceCheckpoint,
  updatedAt: string,
): D1PreparedStatement {
  return db.prepare(
    `INSERT INTO source_state (
      source_id, status, last_successful_crawl, last_article_date,
      cursor, last_snapshot_id, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source_id) DO UPDATE SET
      status = excluded.status,
      last_successful_crawl = COALESCE(
        excluded.last_successful_crawl,
        source_state.last_successful_crawl
      ),
      last_article_date = COALESCE(excluded.last_article_date, source_state.last_article_date),
      cursor = COALESCE(excluded.cursor, source_state.cursor),
      last_snapshot_id = excluded.last_snapshot_id,
      updated_at = excluded.updated_at`,
  ).bind(
    checkpoint.source_id,
    checkpoint.status,
    checkpoint.last_successful_crawl,
    checkpoint.last_article_date,
    checkpoint.cursor,
    snapshotId,
    updatedAt,
  );
}

async function assertEvidenceBelongsToRun(
  db: D1Database,
  snapshot: TopicSnapshot,
): Promise<void> {
  const results = await db.prepare(
    "SELECT item_id FROM run_items WHERE run_id = ?",
  ).bind(snapshot.run_id).all<{ item_id: string }>();
  const runItemIds = new Set(results.results.map((row) => row.item_id));
  if (runItemIds.size === 0) throw new HttpError(409, "run_has_no_items");
  for (const itemId of snapshot.input_item_ids) {
    if (!runItemIds.has(itemId)) {
      throw new HttpError(422, "input_item_not_in_run", [itemId]);
    }
  }
}

async function assertAlignmentEvidenceBelongsToRun(
  db: D1Database,
  envelope: MarketAlignmentEnvelope,
): Promise<void> {
  const results = await db.prepare(
    "SELECT item_id FROM run_items WHERE run_id = ?",
  ).bind(envelope.run_id).all<{ item_id: string }>();
  const runItemIds = new Set(results.results.map((row) => row.item_id));
  const evidenceIds = new Set<string>();
  for (const instrument of envelope.market_snapshot.instruments) {
    instrument.source_item_ids.forEach((itemId) => evidenceIds.add(itemId));
  }
  for (const topic of envelope.alignment.topics) {
    topic.evidence_ids.forEach((itemId) => evidenceIds.add(itemId));
  }
  for (const evidenceId of evidenceIds) {
    if (!runItemIds.has(evidenceId)) {
      throw new HttpError(422, "evidence_not_in_run", [evidenceId]);
    }
  }
}

async function assertPlanEvidenceBelongsToRun(
  db: D1Database,
  envelope: TradingAgentsPlanEnvelope,
): Promise<void> {
  const results = await db.prepare(
    "SELECT item_id FROM run_items WHERE run_id = ?",
  ).bind(envelope.run_id).all<{ item_id: string }>();
  const runItemIds = new Set(results.results.map((row) => row.item_id));
  for (const topic of envelope.plan.topics) {
    for (const evidenceId of topic.evidence_ids) {
      if (!runItemIds.has(evidenceId)) {
        throw new HttpError(422, "evidence_not_in_run", [evidenceId]);
      }
    }
  }
}

async function assertResearchReportEvidenceBelongsToRun(
  db: D1Database,
  envelope: ResearchReportEnvelope,
): Promise<void> {
  const results = await db.prepare(
    "SELECT item_id FROM run_items WHERE run_id = ?",
  ).bind(envelope.run_id).all<{ item_id: string }>();
  const runItemIds = new Set(results.results.map((row) => row.item_id));
  const evidenceIds = new Set(envelope.report.evidence_ids);
  for (const claim of [
    ...envelope.report.bull_case,
    ...envelope.report.bear_case,
    ...envelope.report.risk_view,
  ]) {
    claim.evidence_ids.forEach((evidenceId) => evidenceIds.add(evidenceId));
  }
  for (const evidenceId of evidenceIds) {
    if (!runItemIds.has(evidenceId)) {
      throw new HttpError(422, "evidence_not_in_run", [evidenceId]);
    }
  }
}

export async function readPrivateJson(
  bucket: R2Bucket,
  objectKey: string,
  kind: string,
): Promise<unknown> {
  let object: R2ObjectBody | null;
  try {
    object = await bucket.get(objectKey);
  } catch (error) {
    logStorageFailure(`r2_${kind}_read_failed`, objectKey, error);
    throw new HttpError(503, "storage_read_failed");
  }
  if (object === null) throw new HttpError(503, "storage_object_missing");
  try {
    return await object.json();
  } catch (error) {
    logStorageFailure(`r2_${kind}_parse_failed`, objectKey, error);
    throw new HttpError(503, "storage_object_invalid");
  }
}

export async function buildAuditStatement(
  db: D1Database,
  runId: string,
  stage: string,
  payloadHash: string,
  happenedAt: string,
): Promise<D1PreparedStatement> {
  const previous = await db.prepare(
    "SELECT event_hash FROM audit_events ORDER BY happened_at DESC, event_id DESC LIMIT 1",
  ).first<{ event_hash: string }>();
  const previousHash = previous?.event_hash ?? null;
  const eventId = crypto.randomUUID();
  const eventHash = await sha256Hex(
    JSON.stringify({ eventId, runId, stage, payloadHash, happenedAt, previousHash }),
  );
  return db.prepare(
    `INSERT OR IGNORE INTO audit_events (
      event_id, run_id, stage, status, happened_at, payload_sha256,
      previous_event_hash, event_hash
    ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?)`,
  ).bind(eventId, runId, stage, happenedAt, payloadHash, previousHash, eventHash);
}

function validateOrHttp<T>(validate: () => T): T {
  try {
    return validate();
  } catch (error) {
    if (error instanceof PayloadValidationError) {
      throw new HttpError(422, "invalid_payload", error.details);
    }
    throw error;
  }
}

function rawObjectKey(item: RawItem): string {
  return `raw/${item.source_id}/${item.item_id}.json`;
}

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function logStorageFailure(event: string, correlationId: string, error: unknown): void {
  console.error(JSON.stringify({
    event,
    correlation_id: correlationId,
    error: error instanceof Error ? error.message : String(error),
  }));
}
