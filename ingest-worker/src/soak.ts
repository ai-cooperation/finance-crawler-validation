import type { AuthContext } from "./auth";
import { parseFreshnessPolicy, readStatus, type StatusResponse } from "./status";
import { HttpError } from "./storage";


const MAX_R2_OBJECT_CHECKS = 4;
const MAX_RAW_OBJECT_CHECKS = MAX_R2_OBJECT_CHECKS - 1;

interface SoakRequest {
  schema_version: 1;
  workflow_run_id: string;
  run_attempt: number;
  commit_sha: string;
}

interface AdmissionRow {
  decision: "admitted" | "denied";
  reason: "admitted" | "daily_run_limit" | "minimum_interval";
  requested_at: string;
}

interface CountRow {
  runs: number;
  published_runs: number;
  raw_items: number;
  topic_snapshots: number;
  audit_events: number;
  run_admissions: number;
  operational_alerts: number;
  open_alerts: number;
}

interface ObjectRow {
  kind: "topic" | "raw";
  object_key: string;
  content_sha256: string;
}

interface ScheduledRunRow {
  run_id: string;
  snapshot_id: string;
  status: "staging" | "published" | "failed";
  item_count: number;
  published_at: string | null;
}

interface ScheduledRunEvidence {
  state: "published" | "not_admitted" | "not_started" | "incomplete";
  run_id: string | null;
  snapshot_id: string | null;
  item_count: number | null;
  published_at: string | null;
  current_snapshot_matches: boolean;
}

interface ReceiptRow {
  commit_sha: string;
  evidence_json: string;
}

interface IntegritySample extends ObjectRow {
  size: number;
}

export interface SoakObservation {
  schema_version: 1;
  workflow_run_id: string;
  run_attempt: number;
  commit_sha: string;
  observed_at: string;
  replayed: boolean;
  admission: AdmissionRow;
  scheduled_run: ScheduledRunEvidence;
  status: StatusResponse;
  d1_counts: CountRow;
  r2_integrity: {
    checked_objects: number;
    max_checked_objects: 4;
    all_metadata_match: true;
    samples: IntegritySample[];
  };
}

export async function observeScheduledSoak(
  env: Env,
  payload: unknown,
  identity: AuthContext,
  now: Date,
): Promise<SoakObservation> {
  const request = parseSoakRequest(payload);
  assertScheduledIdentity(request, identity);

  const existing = await findReceipt(env.DB, request.workflow_run_id, request.run_attempt);
  if (existing !== null) return replayReceipt(existing, request);

  const [admission, status, counts, objectRows] = await Promise.all([
    readAdmission(env.DB, request.workflow_run_id),
    readStatus(env.DB, now, parseFreshnessPolicy(env)),
    readCounts(env.DB),
    readObjectSampleRows(env.DB),
  ]);
  const scheduledRun = await readScheduledRun(
    env.DB,
    request.workflow_run_id,
    admission,
    status,
  );
  const samples = await verifyR2Integrity(env.RAW_OBJECTS, objectRows);
  const observation: SoakObservation = {
    schema_version: 1,
    workflow_run_id: request.workflow_run_id,
    run_attempt: request.run_attempt,
    commit_sha: request.commit_sha,
    observed_at: now.toISOString(),
    replayed: false,
    admission,
    scheduled_run: scheduledRun,
    status,
    d1_counts: counts,
    r2_integrity: {
      checked_objects: samples.length,
      max_checked_objects: MAX_R2_OBJECT_CHECKS,
      all_metadata_match: true,
      samples,
    },
  };

  try {
    await env.DB.prepare(
      `INSERT INTO soak_observations (
        workflow_run_id, run_attempt, commit_sha, observed_at, evidence_json
      ) VALUES (?, ?, ?, ?, ?)`,
    ).bind(
      request.workflow_run_id,
      request.run_attempt,
      request.commit_sha,
      observation.observed_at,
      JSON.stringify(observation),
    ).run();
  } catch {
    const raced = await findReceipt(env.DB, request.workflow_run_id, request.run_attempt);
    if (raced !== null) return replayReceipt(raced, request);
    throw new HttpError(503, "soak_write_failed");
  }
  return observation;
}

function parseSoakRequest(payload: unknown): SoakRequest {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new HttpError(422, "invalid_soak_request");
  }
  const record = payload as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  const expected = ["commit_sha", "run_attempt", "schema_version", "workflow_run_id"];
  if (keys.length !== expected.length
    || !keys.every((key, index) => key === expected[index])
    || record.schema_version !== 1
    || typeof record.workflow_run_id !== "string"
    || !/^\d+$/.test(record.workflow_run_id)
    || typeof record.run_attempt !== "number"
    || !Number.isSafeInteger(record.run_attempt)
    || record.run_attempt < 1
    || typeof record.commit_sha !== "string"
    || !/^[a-f0-9]{40}$/.test(record.commit_sha)) {
    throw new HttpError(422, "invalid_soak_request");
  }
  return record as unknown as SoakRequest;
}

function assertScheduledIdentity(request: SoakRequest, identity: AuthContext): void {
  if (identity.eventName !== "schedule") throw new HttpError(403, "schedule_identity_required");
  if (request.workflow_run_id !== identity.workflowRunId) {
    throw new HttpError(403, "workflow_run_mismatch");
  }
  if (request.run_attempt !== identity.runAttempt) {
    throw new HttpError(403, "run_attempt_mismatch");
  }
  if (request.commit_sha !== identity.commitSha) {
    throw new HttpError(403, "commit_sha_mismatch");
  }
}

async function findReceipt(
  db: D1Database,
  workflowRunId: string,
  runAttempt: number,
): Promise<ReceiptRow | null> {
  return await db.prepare(
    `SELECT commit_sha, evidence_json FROM soak_observations
     WHERE workflow_run_id = ? AND run_attempt = ?`,
  ).bind(workflowRunId, runAttempt).first<ReceiptRow>() ?? null;
}

function replayReceipt(existing: ReceiptRow, request: SoakRequest): SoakObservation {
  if (existing.commit_sha !== request.commit_sha) {
    throw new HttpError(409, "soak_identity_conflict");
  }
  try {
    const observation = JSON.parse(existing.evidence_json) as SoakObservation;
    if (observation.workflow_run_id !== request.workflow_run_id
      || observation.commit_sha !== request.commit_sha
      || observation.run_attempt !== request.run_attempt
      || observation.schema_version !== 1) {
      throw new Error("receipt identity mismatch");
    }
    return { ...observation, replayed: true };
  } catch {
    throw new HttpError(503, "soak_receipt_invalid");
  }
}

async function readAdmission(db: D1Database, workflowRunId: string): Promise<AdmissionRow> {
  const row = await db.prepare(
    `SELECT decision, reason, requested_at
     FROM run_admissions WHERE workflow_run_id = ?`,
  ).bind(workflowRunId).first<AdmissionRow>();
  if (row === null) throw new HttpError(409, "soak_admission_missing");
  return row;
}

async function readCounts(db: D1Database): Promise<CountRow> {
  const row = await db.prepare(
    `SELECT
      (SELECT COUNT(*) FROM runs) AS runs,
      (SELECT COUNT(*) FROM runs WHERE status = 'published') AS published_runs,
      (SELECT COUNT(*) FROM raw_items) AS raw_items,
      (SELECT COUNT(*) FROM topic_snapshots) AS topic_snapshots,
      (SELECT COUNT(*) FROM audit_events) AS audit_events,
      (SELECT COUNT(*) FROM run_admissions) AS run_admissions,
      (SELECT COUNT(*) FROM operational_alerts) AS operational_alerts,
      (SELECT COUNT(*) FROM operational_alerts WHERE state = 'open') AS open_alerts`,
  ).first<CountRow>();
  if (row === null) throw new HttpError(503, "soak_counts_unavailable");
  return {
    runs: Number(row.runs),
    published_runs: Number(row.published_runs),
    raw_items: Number(row.raw_items),
    topic_snapshots: Number(row.topic_snapshots),
    audit_events: Number(row.audit_events),
    run_admissions: Number(row.run_admissions),
    operational_alerts: Number(row.operational_alerts),
    open_alerts: Number(row.open_alerts),
  };
}

async function readScheduledRun(
  db: D1Database,
  workflowRunId: string,
  admission: AdmissionRow,
  status: StatusResponse,
): Promise<ScheduledRunEvidence> {
  const row = await db.prepare(
    `SELECT run_id, snapshot_id, status, item_count, published_at
     FROM runs WHERE workflow_run_id = ?
     ORDER BY collected_at DESC, run_id DESC
     LIMIT 1`,
  ).bind(workflowRunId).first<ScheduledRunRow>();
  if (row === null) {
    return {
      state: admission.decision === "denied" ? "not_admitted" : "not_started",
      run_id: null,
      snapshot_id: null,
      item_count: null,
      published_at: null,
      current_snapshot_matches: false,
    };
  }
  const currentSnapshotMatches = row.status === "published"
    && status.current_snapshot?.run_id === row.run_id
    && status.current_snapshot.snapshot_id === row.snapshot_id;
  return {
    state: currentSnapshotMatches ? "published" : "incomplete",
    run_id: row.run_id,
    snapshot_id: row.snapshot_id,
    item_count: Number(row.item_count),
    published_at: row.published_at,
    current_snapshot_matches: currentSnapshotMatches,
  };
}

async function readObjectSampleRows(db: D1Database): Promise<ObjectRow[]> {
  const topic = await db.prepare(
    `SELECT 'topic' AS kind, topic.object_key, topic.content_sha256
     FROM current_snapshot AS current
     JOIN topic_snapshots AS topic ON topic.snapshot_id = current.snapshot_id
     WHERE current.singleton_id = 1`,
  ).first<ObjectRow>();
  if (topic === null) throw new HttpError(409, "soak_snapshot_missing");
  const raw = await db.prepare(
    `SELECT 'raw' AS kind, item.object_key, item.content_sha256
     FROM current_snapshot AS current
     JOIN topic_snapshots AS topic ON topic.snapshot_id = current.snapshot_id
     JOIN run_items AS link ON link.run_id = topic.run_id
     JOIN raw_items AS item ON item.item_id = link.item_id
     WHERE current.singleton_id = 1
     ORDER BY item.item_id
     LIMIT ?`,
  ).bind(MAX_RAW_OBJECT_CHECKS).all<ObjectRow>();
  return [topic, ...raw.results];
}

async function verifyR2Integrity(
  bucket: R2Bucket,
  rows: ObjectRow[],
): Promise<IntegritySample[]> {
  if (rows.length < 1 || rows.length > MAX_R2_OBJECT_CHECKS) {
    throw new HttpError(500, "soak_sample_bound_violation");
  }
  const samples: IntegritySample[] = [];
  for (const row of rows) {
    let object: R2Object | null;
    try {
      object = await bucket.head(row.object_key);
    } catch {
      throw new HttpError(503, "soak_r2_unavailable");
    }
    if (object === null
      || object.size < 1
      || object.customMetadata?.content_sha256 !== row.content_sha256) {
      throw new HttpError(409, "soak_r2_integrity_failed", [row.object_key]);
    }
    samples.push({
      kind: row.kind,
      object_key: row.object_key,
      size: object.size,
      content_sha256: row.content_sha256,
    });
  }
  return samples;
}
