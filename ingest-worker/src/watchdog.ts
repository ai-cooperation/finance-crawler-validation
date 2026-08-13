import {
  deliverConfiguredAlert,
  requireAlertWebhookUrl,
} from "./alerts";
import { parseFreshnessPolicy, readStatus } from "./status";
import { HttpError } from "./storage";


const ALERT_KEY = "topic_radar_freshness";

interface AlertRow {
  state: "open" | "resolved";
  fingerprint: string;
  first_detected_at: string;
}

export interface WatchdogResult {
  delivered: boolean;
  transition: "opened" | "deduplicated" | "resolved" | "healthy";
}

interface FreshnessCheckPayload {
  schema_version: 1;
  workflow_run_id: string;
  commit_sha: string;
}

export async function runAuthenticatedFreshnessWatchdog(
  env: Env,
  payload: unknown,
  identity: { workflowRunId: string; commitSha: string },
  now: Date,
  alertFetch: typeof fetch = fetch,
): Promise<WatchdogResult> {
  const request = parseFreshnessCheck(payload);
  if (request.workflow_run_id !== identity.workflowRunId) {
    throw new HttpError(403, "workflow_run_mismatch");
  }
  if (request.commit_sha !== identity.commitSha) {
    throw new HttpError(403, "commit_sha_mismatch");
  }
  return runFreshnessWatchdog(env, now, alertFetch);
}

export async function runFreshnessWatchdog(
  env: Env,
  now: Date,
  alertFetch: typeof fetch = fetch,
): Promise<WatchdogResult> {
  // Validate primary configuration even when the current state is healthy;
  // scheduled monitoring without any external destination is not operational.
  requireAlertWebhookUrl(env);
  const status = await readStatus(env.DB, now, parseFreshnessPolicy(env));
  const existing = await env.DB.prepare(
    "SELECT state, fingerprint, first_detected_at FROM operational_alerts WHERE alert_key = ?",
  ).bind(ALERT_KEY).first<AlertRow>();
  const unhealthy = status.state === "empty" || status.state === "stale";

  if (!unhealthy) {
    if (existing?.state !== "open") return { delivered: false, transition: "healthy" };
    const notification = {
      schema_version: 1,
      alert_key: ALERT_KEY,
      state: "resolved",
      severity: "info",
      detected_at: now.toISOString(),
      service: "finance-crawler-validation",
      summary: "Finance topic radar freshness recovered",
      details: {
        operational_state: status.state,
        reasons: status.reasons,
        age_seconds: status.freshness.age_seconds,
      },
    };
    await deliverConfiguredAlert(
      env,
      `${ALERT_KEY}:resolved:${existing.first_detected_at}`,
      notification,
      alertFetch,
    );
    await env.DB.prepare(
      `UPDATE operational_alerts
      SET state = 'resolved', last_detected_at = ?, last_notified_at = ?, resolved_at = ?
      WHERE alert_key = ? AND state = 'open'`,
    ).bind(now.toISOString(), now.toISOString(), now.toISOString(), ALERT_KEY).run();
    return { delivered: true, transition: "resolved" };
  }

  const fingerprint = await sha256(JSON.stringify({
    state: status.state,
    reasons: status.reasons,
  }));
  if (existing?.state === "open") {
    await env.DB.prepare(
      "UPDATE operational_alerts SET last_detected_at = ? WHERE alert_key = ?",
    ).bind(now.toISOString(), ALERT_KEY).run();
    return { delivered: false, transition: "deduplicated" };
  }
  const summary = status.state === "empty"
    ? "Finance topic radar has no published snapshot"
    : `Finance topic radar snapshot is stale (${status.freshness.age_seconds}s)`;
  const notification = {
    schema_version: 1,
    alert_key: ALERT_KEY,
    state: "open",
    severity: "critical",
    detected_at: now.toISOString(),
    service: "finance-crawler-validation",
    summary,
    details: {
      operational_state: status.state,
      reasons: status.reasons,
      age_seconds: status.freshness.age_seconds,
      snapshot_id: status.current_snapshot?.snapshot_id ?? null,
    },
  };
  await deliverConfiguredAlert(
    env,
    `${ALERT_KEY}:open:${now.toISOString()}`,
    notification,
    alertFetch,
  );
  const timestamp = now.toISOString();
  await env.DB.prepare(
    `INSERT INTO operational_alerts (
      alert_key, state, fingerprint, summary, first_detected_at,
      last_detected_at, last_notified_at, resolved_at
    ) VALUES (?, 'open', ?, ?, ?, ?, ?, NULL)
    ON CONFLICT(alert_key) DO UPDATE SET
      state = 'open', fingerprint = excluded.fingerprint,
      summary = excluded.summary, first_detected_at = excluded.first_detected_at,
      last_detected_at = excluded.last_detected_at,
      last_notified_at = excluded.last_notified_at, resolved_at = NULL`,
  ).bind(ALERT_KEY, fingerprint, summary, timestamp, timestamp, timestamp).run();
  return { delivered: true, transition: "opened" };
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function parseFreshnessCheck(payload: unknown): FreshnessCheckPayload {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new HttpError(422, "invalid_alert_request");
  }
  const record = payload as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  const expected = ["commit_sha", "schema_version", "workflow_run_id"];
  if (keys.length !== expected.length
    || !keys.every((key, index) => key === expected[index])
    || record.schema_version !== 1
    || typeof record.workflow_run_id !== "string"
    || !/^\d+$/.test(record.workflow_run_id)
    || typeof record.commit_sha !== "string"
    || !/^[a-f0-9]{40}$/.test(record.commit_sha)) {
    throw new HttpError(422, "invalid_alert_request");
  }
  return record as unknown as FreshnessCheckPayload;
}
