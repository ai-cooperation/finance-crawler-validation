import { HttpError } from "./storage";


const MAX_SOURCES = 20;
const SOURCE_ID_PATTERN = /^[a-z0-9][a-z0-9_]{0,99}$/;

interface RunPlanRequest {
  schema_version: 1;
  workflow_run_id: string;
  commit_sha: string;
  source_ids: string[];
}

interface SourceStateRow {
  source_id: string;
  status: "success" | "partial" | "failed";
  last_successful_crawl: string | null;
  last_article_date: string | null;
  cursor: string | null;
}

interface AdmissionRow {
  workflow_run_id: string;
  commit_sha: string;
  decision: "admitted" | "denied";
  reason: "admitted" | "daily_run_limit" | "minimum_interval";
  requested_at: string;
}

interface UsageRow {
  admitted_runs_today: number;
  last_admitted_at: string | null;
}

export interface RunAdmissionPolicy {
  dailyRunLimit: number;
  minimumIntervalSeconds: number;
}

export class RunPlanConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RunPlanConfigurationError";
  }
}

export function parseRunAdmissionPolicy(env: Env): RunAdmissionPolicy {
  const dailyRunLimit = positiveInteger(env.RUN_DAILY_LIMIT);
  const minimumIntervalSeconds = positiveInteger(env.RUN_MIN_INTERVAL_SECONDS);
  if (dailyRunLimit === null || minimumIntervalSeconds === null) {
    throw new RunPlanConfigurationError("run admission policy must contain positive integers");
  }
  return { dailyRunLimit, minimumIntervalSeconds };
}

export async function buildRunPlan(
  db: D1Database,
  payload: unknown,
  identity: { workflowRunId: string; commitSha: string },
  now: Date,
  policy: RunAdmissionPolicy,
): Promise<object> {
  const request = parseRunPlanRequest(payload);
  if (request.workflow_run_id !== identity.workflowRunId) {
    throw new HttpError(403, "workflow_run_mismatch");
  }
  if (request.commit_sha !== identity.commitSha) {
    throw new HttpError(403, "commit_sha_mismatch");
  }

  const startOfDay = new Date(now);
  startOfDay.setUTCHours(0, 0, 0, 0);
  const endOfDay = new Date(startOfDay.getTime() + 86_400_000);
  const cutoff = new Date(now.getTime() - policy.minimumIntervalSeconds * 1000);
  let admission = await db.prepare(
    `SELECT workflow_run_id, commit_sha, decision, reason, requested_at
    FROM run_admissions WHERE workflow_run_id = ?`,
  ).bind(request.workflow_run_id).first<AdmissionRow>();
  if (admission !== null && admission.commit_sha !== request.commit_sha) {
    throw new HttpError(409, "run_admission_identity_conflict");
  }
  if (admission === null) {
    await db.prepare(
      `INSERT INTO run_admissions (
        workflow_run_id, commit_sha, decision, reason, requested_at
      )
      SELECT ?, ?, 'admitted', 'admitted', ?
      WHERE (
        SELECT COUNT(*) FROM run_admissions
        WHERE decision = 'admitted' AND requested_at >= ? AND requested_at < ?
      ) < ?
      AND NOT EXISTS (
        SELECT 1 FROM run_admissions
        WHERE decision = 'admitted' AND requested_at > ?
      )`,
    ).bind(
      request.workflow_run_id,
      request.commit_sha,
      now.toISOString(),
      startOfDay.toISOString(),
      endOfDay.toISOString(),
      policy.dailyRunLimit,
      cutoff.toISOString(),
    ).run();
    admission = await db.prepare(
      `SELECT workflow_run_id, commit_sha, decision, reason, requested_at
      FROM run_admissions WHERE workflow_run_id = ?`,
    ).bind(request.workflow_run_id).first<AdmissionRow>();
  }

  const usage = await db.prepare(
    `SELECT
      SUM(CASE WHEN decision = 'admitted' AND requested_at >= ? AND requested_at < ?
        THEN 1 ELSE 0 END) AS admitted_runs_today,
      MAX(CASE WHEN decision = 'admitted' THEN requested_at ELSE NULL END) AS last_admitted_at
    FROM run_admissions`,
  ).bind(startOfDay.toISOString(), endOfDay.toISOString()).first<UsageRow>();
  const admittedRunsToday = Number(usage?.admitted_runs_today ?? 0);
  const lastAdmittedAt = usage?.last_admitted_at ?? null;

  let admitted = admission?.decision === "admitted";
  let reason = admission?.reason ?? "admitted";
  let retryAfterSeconds = 0;
  if (admission === null) {
    admitted = false;
    if (admittedRunsToday >= policy.dailyRunLimit) {
      reason = "daily_run_limit";
    } else {
      reason = "minimum_interval";
    }
    await db.prepare(
      `INSERT OR IGNORE INTO run_admissions (
        workflow_run_id, commit_sha, decision, reason, requested_at
      ) VALUES (?, ?, 'denied', ?, ?)`,
    ).bind(
      request.workflow_run_id,
      request.commit_sha,
      reason,
      now.toISOString(),
    ).run();
  }
  if (!admitted && reason === "daily_run_limit") {
    retryAfterSeconds = Math.max(0, Math.ceil((endOfDay.getTime() - now.getTime()) / 1000));
  } else if (!admitted && lastAdmittedAt !== null) {
    const lastTime = Date.parse(lastAdmittedAt);
    if (!Number.isFinite(lastTime)) throw new HttpError(503, "run_plan_unavailable");
    const elapsedSeconds = Math.floor((now.getTime() - lastTime) / 1000);
    retryAfterSeconds = Math.max(
      0,
      policy.minimumIntervalSeconds - Math.max(0, elapsedSeconds),
    );
  }

  const checkpoints = await readCheckpoints(db, request.source_ids);
  return {
    schema_version: 1,
    as_of: now.toISOString(),
    admitted,
    reason,
    retry_after_seconds: retryAfterSeconds,
    policy: {
      daily_run_limit: policy.dailyRunLimit,
      minimum_interval_seconds: policy.minimumIntervalSeconds,
      admitted_runs_today: admittedRunsToday,
    },
    checkpoints,
  };
}

function parseRunPlanRequest(payload: unknown): RunPlanRequest {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new HttpError(422, "invalid_run_plan_request");
  }
  const record = payload as Record<string, unknown>;
  if (!sameKeys(record, ["schema_version", "workflow_run_id", "commit_sha", "source_ids"])) {
    throw new HttpError(422, "invalid_run_plan_request");
  }
  if (
    record.schema_version !== 1
    || typeof record.workflow_run_id !== "string"
    || !/^\d+$/.test(record.workflow_run_id)
    || typeof record.commit_sha !== "string"
    || !/^[a-f0-9]{40}$/.test(record.commit_sha)
    || !Array.isArray(record.source_ids)
    || record.source_ids.length < 1
    || record.source_ids.length > MAX_SOURCES
    || !record.source_ids.every(
      (sourceId): sourceId is string => typeof sourceId === "string" && SOURCE_ID_PATTERN.test(sourceId),
    )
    || new Set(record.source_ids).size !== record.source_ids.length
  ) {
    throw new HttpError(422, "invalid_run_plan_request");
  }
  return record as unknown as RunPlanRequest;
}

async function readCheckpoints(
  db: D1Database,
  sourceIds: string[],
): Promise<Array<SourceStateRow | {
  source_id: string;
  status: null;
  last_successful_crawl: null;
  last_article_date: null;
  cursor: null;
}>> {
  const placeholders = sourceIds.map(() => "?").join(", ");
  const rows = await db.prepare(
    `SELECT source_id, status, last_successful_crawl, last_article_date, cursor
    FROM source_state WHERE source_id IN (${placeholders})`,
  ).bind(...sourceIds).all<SourceStateRow>();
  const bySource = new Map(rows.results.map((row) => [row.source_id, row]));
  return sourceIds.map((sourceId) => bySource.get(sourceId) ?? {
    source_id: sourceId,
    status: null,
    last_successful_crawl: null,
    last_article_date: null,
    cursor: null,
  });
}

function sameKeys(record: Record<string, unknown>, expected: string[]): boolean {
  const keys = Object.keys(record).sort();
  const normalizedExpected = [...expected].sort();
  return keys.length === normalizedExpected.length
    && keys.every((key, index) => key === normalizedExpected[index]);
}

function positiveInteger(value: string): number | null {
  if (!/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}
