import { HttpError } from "./storage";


const GITHUB_RUN_URL_PATTERN = /^https:\/\/github\.com\/ai-cooperation\/finance-crawler-validation\/actions\/runs\/(\d+)$/;

interface ActionFailurePayload {
  schema_version: 1;
  workflow_run_id: string;
  commit_sha: string;
  conclusion: "failure";
  run_url: string;
}

interface AlertRow {
  alert_key: string;
  fingerprint: string;
}

type AlertFetch = typeof fetch;

export class AlertDeliveryError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "AlertDeliveryError";
    this.code = code;
  }
}

export async function reportActionFailure(
  env: Env,
  payload: unknown,
  identity: { workflowRunId: string; commitSha: string },
  now: Date,
  alertFetch: AlertFetch,
): Promise<{ delivered: boolean; transition: "opened" | "deduplicated" }> {
  const failure = parseActionFailure(payload);
  if (failure.workflow_run_id !== identity.workflowRunId) {
    throw new HttpError(403, "workflow_run_mismatch");
  }
  if (failure.commit_sha !== identity.commitSha) {
    throw new HttpError(403, "commit_sha_mismatch");
  }
  const runUrlMatch = GITHUB_RUN_URL_PATTERN.exec(failure.run_url);
  if (!runUrlMatch || runUrlMatch[1] !== failure.workflow_run_id) {
    throw new HttpError(422, "invalid_alert_request");
  }
  const webhookUrl = requireAlertWebhookUrl(env);
  const alertKey = `github_action_failure:${failure.workflow_run_id}`;
  const summary = `Finance topic radar GitHub Actions run ${failure.workflow_run_id} failed`;
  const fingerprint = await sha256(`${alertKey}\n${failure.commit_sha}\n${failure.conclusion}`);
  const existing = await env.DB.prepare(
    "SELECT alert_key, fingerprint FROM operational_alerts WHERE alert_key = ?",
  ).bind(alertKey).first<AlertRow>();
  if (existing !== null) {
    if (existing.fingerprint !== fingerprint) throw new HttpError(409, "alert_identity_conflict");
    return { delivered: false, transition: "deduplicated" };
  }

  const notification = {
    schema_version: 1,
    alert_key: alertKey,
    state: "open",
    severity: "critical",
    detected_at: now.toISOString(),
    service: "finance-crawler-validation",
    summary,
    details: {
      workflow_run_id: failure.workflow_run_id,
      commit_sha: failure.commit_sha,
      run_url: failure.run_url,
    },
  };
  await deliverAlertWebhook(webhookUrl, alertKey, notification, alertFetch);
  const timestamp = now.toISOString();
  try {
    await env.DB.prepare(
      `INSERT INTO operational_alerts (
        alert_key, state, fingerprint, summary, first_detected_at,
        last_detected_at, last_notified_at, resolved_at
      ) VALUES (?, 'open', ?, ?, ?, ?, ?, NULL)`,
    ).bind(alertKey, fingerprint, summary, timestamp, timestamp, timestamp).run();
  } catch (error) {
    const race = await env.DB.prepare(
      "SELECT alert_key, fingerprint FROM operational_alerts WHERE alert_key = ?",
    ).bind(alertKey).first<AlertRow>();
    if (race?.fingerprint === fingerprint) return { delivered: false, transition: "deduplicated" };
    throw error;
  }
  return { delivered: true, transition: "opened" };
}

function parseActionFailure(payload: unknown): ActionFailurePayload {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new HttpError(422, "invalid_alert_request");
  }
  const record = payload as Record<string, unknown>;
  if (!sameKeys(record, [
    "schema_version",
    "workflow_run_id",
    "commit_sha",
    "conclusion",
    "run_url",
  ]) || record.schema_version !== 1
    || typeof record.workflow_run_id !== "string"
    || !/^\d+$/.test(record.workflow_run_id)
    || typeof record.commit_sha !== "string"
    || !/^[a-f0-9]{40}$/.test(record.commit_sha)
    || record.conclusion !== "failure"
    || typeof record.run_url !== "string") {
    throw new HttpError(422, "invalid_alert_request");
  }
  return record as unknown as ActionFailurePayload;
}

export function requireAlertWebhookUrl(env: Env): string {
  const raw = (env as Env & { ALERT_WEBHOOK_URL?: string }).ALERT_WEBHOOK_URL;
  if (typeof raw !== "string" || raw.length === 0) {
    throw new AlertDeliveryError("alert_webhook_not_configured");
  }
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new AlertDeliveryError("alert_webhook_invalid");
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new AlertDeliveryError("alert_webhook_invalid");
  }
  return parsed.toString();
}

export async function deliverAlertWebhook(
  webhookUrl: string,
  alertKey: string,
  payload: object,
  alertFetch: AlertFetch,
): Promise<void> {
  let response: Response;
  try {
    response = await alertFetch(webhookUrl, {
      method: "POST",
      redirect: "error",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": alertKey,
      },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new AlertDeliveryError("alert_delivery_failed");
  }
  if (!response.ok) throw new AlertDeliveryError("alert_delivery_rejected");
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function sameKeys(record: Record<string, unknown>, expected: string[]): boolean {
  const keys = Object.keys(record).sort();
  const normalizedExpected = [...expected].sort();
  return keys.length === normalizedExpected.length
    && keys.every((key, index) => key === normalizedExpected[index]);
}
