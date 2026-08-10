import {
  type IngestEnvelope,
  PayloadValidationError,
  type RawItem,
  type SourceCheckpoint,
  type TopicSnapshot,
  validateIngestEnvelope,
  validateTopicSnapshot,
} from "./contracts";


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
  status: "staging";
}

export interface PublishResult {
  run_id: string;
  snapshot_id: string;
  topic_count: number;
  status: "published";
}

export async function ingestItems(
  env: Env,
  payload: unknown,
  now: Date,
): Promise<IngestResult> {
  const envelope = validateOrHttp(() => validateIngestEnvelope(payload));
  await assertRunCompatible(env.DB, envelope);
  await persistRawObjects(env.RAW_OBJECTS, envelope.items);

  const happenedAt = now.toISOString();
  const audit = await buildAuditStatement(
    env.DB,
    envelope.run_id,
    "raw_collected",
    envelope.source_manifest_hash,
    happenedAt,
  );
  const statements: D1PreparedStatement[] = [
    env.DB.prepare(
      `INSERT INTO runs (
        run_id, workflow_run_id, commit_sha, snapshot_id, source_manifest_hash,
        status, collected_at, item_count
      ) VALUES (?, ?, ?, ?, ?, 'staging', ?, 0)
      ON CONFLICT(run_id) DO NOTHING`,
    ).bind(
      envelope.run_id,
      envelope.workflow_run_id,
      envelope.commit_sha,
      envelope.snapshot_id,
      envelope.source_manifest_hash,
      envelope.collected_at,
    ),
  ];
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
  };
}

export async function publishSnapshot(
  env: Env,
  payload: unknown,
  now: Date,
): Promise<PublishResult> {
  const snapshot = validateOrHttp(() => validateTopicSnapshot(payload));
  const run = await env.DB.prepare(
    "SELECT snapshot_id, status FROM runs WHERE run_id = ?",
  ).bind(snapshot.run_id).first<{ snapshot_id: string; status: string }>();
  if (!run) throw new HttpError(409, "run_not_found");
  if (run.snapshot_id !== snapshot.snapshot_id) {
    throw new HttpError(409, "snapshot_run_conflict");
  }
  await assertEvidenceBelongsToRun(env.DB, snapshot);

  const serialized = JSON.stringify(snapshot);
  const contentHash = await sha256Hex(serialized);
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
  };
}

async function assertRunCompatible(db: D1Database, envelope: IngestEnvelope): Promise<void> {
  const existing = await db.prepare(
    `SELECT workflow_run_id, commit_sha, snapshot_id, source_manifest_hash
     FROM runs WHERE run_id = ?`,
  ).bind(envelope.run_id).first<{
    workflow_run_id: string;
    commit_sha: string;
    snapshot_id: string;
    source_manifest_hash: string;
  }>();
  if (!existing) return;
  if (
    existing.workflow_run_id !== envelope.workflow_run_id ||
    existing.commit_sha !== envelope.commit_sha ||
    existing.snapshot_id !== envelope.snapshot_id ||
    existing.source_manifest_hash !== envelope.source_manifest_hash
  ) {
    throw new HttpError(409, "run_identity_conflict");
  }
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

async function buildAuditStatement(
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

async function sha256Hex(value: string): Promise<string> {
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
