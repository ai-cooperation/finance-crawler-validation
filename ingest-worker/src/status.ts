export type OperationalState = "empty" | "healthy" | "warning" | "stale";
export type StatusReason =
  | "no_snapshot"
  | "freshness_warning"
  | "freshness_stale"
  | "partial_snapshot"
  | "source_failures"
  | "clock_skew";

export interface FreshnessPolicy {
  warningAfterSeconds: number;
  staleAfterSeconds: number;
}

interface SnapshotRow {
  snapshot_id: string;
  run_id: string;
  as_of: string;
  partial: number;
  failed_sources_json: string;
  topic_count: number;
  content_sha256: string;
}

interface SourceCountRow {
  total: number;
  success: number;
  partial: number;
  failed: number;
}

export interface StatusResponse {
  schema_version: 1;
  service: "finance-crawler-ingest";
  as_of: string;
  state: OperationalState;
  reasons: StatusReason[];
  freshness: {
    state: OperationalState;
    age_seconds: number | null;
    warning_after_seconds: number;
    stale_after_seconds: number;
  };
  current_snapshot: null | {
    snapshot_id: string;
    run_id: string;
    as_of: string;
    partial: boolean;
    failed_source_count: number;
    topic_count: number;
    content_sha256: string;
  };
  source_counts: SourceCountRow;
}

export class StatusReadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StatusReadError";
  }
}

export class StatusConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StatusConfigurationError";
  }
}

export function parseFreshnessPolicy(env: Env): FreshnessPolicy {
  const warningAfterSeconds = parsePositiveInteger(env.FRESHNESS_WARNING_SECONDS);
  const staleAfterSeconds = parsePositiveInteger(env.FRESHNESS_STALE_SECONDS);
  if (warningAfterSeconds === null || staleAfterSeconds === null) {
    throw new StatusConfigurationError("freshness thresholds must be positive integers");
  }
  if (staleAfterSeconds <= warningAfterSeconds) {
    throw new StatusConfigurationError("stale threshold must exceed warning threshold");
  }
  return { warningAfterSeconds, staleAfterSeconds };
}

export async function readStatus(
  db: D1Database,
  now: Date,
  policy: FreshnessPolicy,
): Promise<StatusResponse> {
  let snapshot: SnapshotRow | null;
  let sourceCounts: SourceCountRow | null;
  try {
    [snapshot, sourceCounts] = await Promise.all([
      db.prepare(
        `SELECT
          ts.snapshot_id, ts.run_id, ts.as_of, ts.partial,
          ts.failed_sources_json, ts.topic_count, ts.content_sha256
        FROM current_snapshot AS current
        JOIN topic_snapshots AS ts ON ts.snapshot_id = current.snapshot_id
        WHERE current.singleton_id = 1`,
      ).first<SnapshotRow>(),
      db.prepare(
        `SELECT
          COUNT(*) AS total,
          COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0) AS success,
          COALESCE(SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END), 0) AS partial,
          COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed
        FROM source_state`,
      ).first<SourceCountRow>(),
    ]);
  } catch (error) {
    throw new StatusReadError(error instanceof Error ? error.message : String(error));
  }

  const normalizedCounts = normalizeSourceCounts(sourceCounts);
  if (snapshot === null) return emptyStatus(now, policy, normalizedCounts);
  return snapshotStatus(snapshot, normalizedCounts, now, policy);
}

function emptyStatus(
  now: Date,
  policy: FreshnessPolicy,
  sourceCounts: SourceCountRow,
): StatusResponse {
  return {
    schema_version: 1,
    service: "finance-crawler-ingest",
    as_of: now.toISOString(),
    state: "empty",
    reasons: ["no_snapshot"],
    freshness: {
      state: "empty",
      age_seconds: null,
      warning_after_seconds: policy.warningAfterSeconds,
      stale_after_seconds: policy.staleAfterSeconds,
    },
    current_snapshot: null,
    source_counts: sourceCounts,
  };
}

function snapshotStatus(
  snapshot: SnapshotRow,
  sourceCounts: SourceCountRow,
  now: Date,
  policy: FreshnessPolicy,
): StatusResponse {
  const snapshotTime = Date.parse(snapshot.as_of);
  if (!Number.isFinite(snapshotTime)) throw new StatusReadError("invalid snapshot as_of in D1");
  const rawAgeSeconds = Math.floor((now.getTime() - snapshotTime) / 1000);
  const clockSkew = rawAgeSeconds < -300;
  const ageSeconds = Math.max(0, rawAgeSeconds);
  const freshnessState = freshnessStateFor(ageSeconds, clockSkew, policy);
  const reasons = statusReasons(snapshot, sourceCounts, freshnessState, clockSkew);
  const failedSources = parseFailedSources(snapshot.failed_sources_json);

  return {
    schema_version: 1,
    service: "finance-crawler-ingest",
    as_of: now.toISOString(),
    state: overallState(freshnessState, reasons),
    reasons,
    freshness: {
      state: freshnessState,
      age_seconds: ageSeconds,
      warning_after_seconds: policy.warningAfterSeconds,
      stale_after_seconds: policy.staleAfterSeconds,
    },
    current_snapshot: {
      snapshot_id: snapshot.snapshot_id,
      run_id: snapshot.run_id,
      as_of: snapshot.as_of,
      partial: snapshot.partial === 1,
      failed_source_count: failedSources.length,
      topic_count: Number(snapshot.topic_count),
      content_sha256: snapshot.content_sha256,
    },
    source_counts: sourceCounts,
  };
}

function freshnessStateFor(
  ageSeconds: number,
  clockSkew: boolean,
  policy: FreshnessPolicy,
): OperationalState {
  if (clockSkew) return "warning";
  if (ageSeconds >= policy.staleAfterSeconds) return "stale";
  if (ageSeconds >= policy.warningAfterSeconds) return "warning";
  return "healthy";
}

function statusReasons(
  snapshot: SnapshotRow,
  sourceCounts: SourceCountRow,
  freshnessState: OperationalState,
  clockSkew: boolean,
): StatusReason[] {
  const reasons: StatusReason[] = [];
  if (freshnessState === "warning" && !clockSkew) reasons.push("freshness_warning");
  if (freshnessState === "stale") reasons.push("freshness_stale");
  if (snapshot.partial === 1) reasons.push("partial_snapshot");
  if (sourceCounts.partial > 0 || sourceCounts.failed > 0) reasons.push("source_failures");
  if (clockSkew) reasons.push("clock_skew");
  return reasons;
}

function overallState(
  freshnessState: OperationalState,
  reasons: StatusReason[],
): OperationalState {
  if (freshnessState === "stale") return "stale";
  if (reasons.length > 0) return "warning";
  return "healthy";
}

function normalizeSourceCounts(row: SourceCountRow | null): SourceCountRow {
  return {
    total: Number(row?.total ?? 0),
    success: Number(row?.success ?? 0),
    partial: Number(row?.partial ?? 0),
    failed: Number(row?.failed ?? 0),
  };
}

function parseFailedSources(serialized: string): string[] {
  try {
    const value = JSON.parse(serialized) as unknown;
    if (Array.isArray(value) && value.every((item) => typeof item === "string")) return value;
  } catch {
    // The normalized error below keeps private D1 content out of the response.
  }
  throw new StatusReadError("invalid failed_sources_json in D1");
}

function parsePositiveInteger(value: string): number | null {
  if (!/^[1-9][0-9]*$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}
