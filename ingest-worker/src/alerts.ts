import { HttpError } from "./storage";


const GITHUB_RUN_URL_PATTERN = /^https:\/\/github\.com\/ai-cooperation\/finance-crawler-validation\/actions\/runs\/(\d+)$/;
const ALERT_DELIVERY_TIMEOUT_MS = 10_000;

interface ActionFailurePayload {
  schema_version: 1;
  workflow_run_id: string;
  commit_sha: string;
  conclusion: "failure";
  run_url: string;
}

interface ActionRecoveryPayload {
  schema_version: 1;
  workflow_run_id: string;
  commit_sha: string;
  conclusion: "success";
  run_url: string;
}

interface AlertRow {
  alert_key: string;
  fingerprint: string;
}

type AlertFetch = typeof fetch;

export type AlertWebhookFormat = "auto" | "generic_json" | "ntfy" | "slack" | "telegram";

interface AlertDelivery {
  url: string;
  body: object;
  provider: Exclude<AlertWebhookFormat, "auto">;
}

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
  assertActionIdentity(failure, identity);
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
  await deliverConfiguredAlert(env, alertKey, notification, alertFetch);
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

export async function resolveActionFailures(
  env: Env,
  payload: unknown,
  identity: { workflowRunId: string; commitSha: string },
  now: Date,
  alertFetch: AlertFetch,
): Promise<{
  delivered: boolean;
  transition: "resolved" | "healthy";
  resolved_alerts: number;
}> {
  const recovery = parseActionRecovery(payload);
  assertActionIdentity(recovery, identity);
  const open = await env.DB.prepare(
    `SELECT alert_key, fingerprint FROM operational_alerts
     WHERE state = 'open' AND alert_key LIKE 'github_action_failure:%'
     ORDER BY first_detected_at, alert_key`,
  ).all<AlertRow>();
  if (open.results.length === 0) {
    return { delivered: false, transition: "healthy", resolved_alerts: 0 };
  }

  const timestamp = now.toISOString();
  const recoveryKey = `github_action_recovery:${recovery.workflow_run_id}`;
  await deliverConfiguredAlert(env, recoveryKey, {
    schema_version: 1,
    alert_key: recoveryKey,
    state: "resolved",
    severity: "info",
    detected_at: timestamp,
    service: "finance-crawler-validation",
    summary: `Finance topic radar recovered in GitHub Actions run ${recovery.workflow_run_id}`,
    details: {
      workflow_run_id: recovery.workflow_run_id,
      commit_sha: recovery.commit_sha,
      run_url: recovery.run_url,
      resolved_alert_count: open.results.length,
    },
  }, alertFetch);

  const statements = open.results.map((row) => env.DB.prepare(
    `UPDATE operational_alerts
     SET state = 'resolved', last_detected_at = ?, last_notified_at = ?, resolved_at = ?
     WHERE alert_key = ? AND state = 'open' AND fingerprint = ?`,
  ).bind(timestamp, timestamp, timestamp, row.alert_key, row.fingerprint));
  const results = await env.DB.batch(statements);
  if (results.some((result) => !result.success) || results.reduce(
    (total, result) => total + Number(result.meta.changes), 0,
  ) !== open.results.length) {
    throw new HttpError(503, "alert_recovery_write_failed");
  }
  return {
    delivered: true,
    transition: "resolved",
    resolved_alerts: open.results.length,
  };
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

function parseActionRecovery(payload: unknown): ActionRecoveryPayload {
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
    || record.conclusion !== "success"
    || typeof record.run_url !== "string") {
    throw new HttpError(422, "invalid_alert_request");
  }
  return record as unknown as ActionRecoveryPayload;
}

function assertActionIdentity(
  payload: ActionFailurePayload | ActionRecoveryPayload,
  identity: { workflowRunId: string; commitSha: string },
): void {
  if (payload.workflow_run_id !== identity.workflowRunId) {
    throw new HttpError(403, "workflow_run_mismatch");
  }
  if (payload.commit_sha !== identity.commitSha) {
    throw new HttpError(403, "commit_sha_mismatch");
  }
  const runUrlMatch = GITHUB_RUN_URL_PATTERN.exec(payload.run_url);
  if (!runUrlMatch || runUrlMatch[1] !== payload.workflow_run_id) {
    throw new HttpError(422, "invalid_alert_request");
  }
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

export async function deliverConfiguredAlert(
  env: Env,
  alertKey: string,
  payload: object,
  alertFetch: AlertFetch,
): Promise<void> {
  const primaryUrl = requireAlertWebhookUrl(env);
  const format = readAlertWebhookFormat(env);
  try {
    await deliverAlertWebhook(primaryUrl, alertKey, payload, alertFetch, format);
    return;
  } catch (error) {
    const fallbackUrl = readFallbackWebhookUrl(env);
    if (fallbackUrl === null) throw error;
    if (fallbackUrl === primaryUrl) {
      throw new AlertDeliveryError("alert_fallback_webhook_invalid");
    }
    console.warn(JSON.stringify({
      event: "alert_primary_delivery_failed",
      error_code: error instanceof AlertDeliveryError ? error.code : "unknown",
    }));
    await deliverAlertWebhook(fallbackUrl, alertKey, payload, alertFetch, format);
  }
}

export async function reportScheduledWatchdogFailure(
  env: Env,
  controller: Pick<ScheduledController, "scheduledTime" | "cron">,
  now: Date,
  alertFetch: AlertFetch,
): Promise<void> {
  const scheduledAt = new Date(controller.scheduledTime).toISOString();
  const alertKey = `cloudflare_watchdog_failure:${scheduledAt}`;
  await deliverConfiguredAlert(env, alertKey, {
    schema_version: 1,
    alert_key: alertKey,
    state: "open",
    severity: "critical",
    detected_at: now.toISOString(),
    service: "finance-crawler-validation",
    summary: "Finance topic radar Cloudflare freshness watchdog failed",
    details: {
      scheduled_at: scheduledAt,
      cron: controller.cron,
      error_code: "watchdog_execution_failed",
    },
  }, alertFetch);
}

export function readAlertWebhookFormat(env: Env): AlertWebhookFormat {
  const raw: string | undefined = (env as unknown as {
    ALERT_WEBHOOK_FORMAT?: string;
  }).ALERT_WEBHOOK_FORMAT;
  if (["auto", "generic_json", "ntfy", "slack", "telegram"].includes(raw ?? "")) {
    return raw as AlertWebhookFormat;
  }
  throw new AlertDeliveryError("alert_webhook_format_invalid");
}

export async function deliverAlertWebhook(
  webhookUrl: string,
  alertKey: string,
  payload: object,
  alertFetch: AlertFetch,
  format: AlertWebhookFormat = "generic_json",
): Promise<void> {
  const delivery = buildDelivery(webhookUrl, payload, format);
  let response: Response;
  try {
    response = await alertFetch(delivery.url, {
      method: "POST",
      // Cloudflare Workers supports only follow/manual. Manual preserves the
      // fail-closed invariant because every 3xx remains a non-ok response.
      redirect: "manual",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": alertKey,
      },
      body: JSON.stringify(delivery.body),
      signal: AbortSignal.timeout(ALERT_DELIVERY_TIMEOUT_MS),
    });
  } catch (error) {
    console.error(JSON.stringify({
      event: "alert_delivery_network_error",
      // Fetch errors may echo the request URL, which is itself a bearer secret
      // for Slack/Telegram. Log only the stable error class, never the message.
      error_type: error instanceof Error ? error.name : "NonError",
    }));
    throw new AlertDeliveryError("alert_delivery_failed");
  }
  if (!response.ok || !await providerAccepted(delivery.provider, response)) {
    throw new AlertDeliveryError("alert_delivery_rejected");
  }
}

function buildDelivery(
  webhookUrl: string,
  payload: object,
  format: AlertWebhookFormat,
): AlertDelivery {
  const resolved = format === "auto" ? detectWebhookFormat(webhookUrl) : format;
  if (resolved === "ntfy") return ntfyDelivery(webhookUrl, payload);
  if (resolved === "slack") return slackDelivery(webhookUrl, payload);
  if (resolved === "telegram") return telegramDelivery(webhookUrl, payload);
  return { url: webhookUrl, body: payload, provider: "generic_json" };
}

function detectWebhookFormat(webhookUrl: string): Exclude<AlertWebhookFormat, "auto"> {
  const parsed = new URL(webhookUrl);
  if (parsed.hostname === "ntfy.sh") return "ntfy";
  if (parsed.hostname === "hooks.slack.com") return "slack";
  if (parsed.hostname === "api.telegram.org") return "telegram";
  if (parsed.hostname.includes("slack.com") || parsed.hostname.includes("telegram.org")
    || parsed.hostname.includes("ntfy.sh")) {
    throw new AlertDeliveryError("alert_webhook_invalid");
  }
  return "generic_json";
}

function ntfyDelivery(webhookUrl: string, payload: object): AlertDelivery {
  const parsed = new URL(webhookUrl);
  if (parsed.hostname !== "ntfy.sh" || parsed.search || parsed.hash) {
    throw new AlertDeliveryError("alert_webhook_invalid");
  }
  const topic = parsed.pathname.split("/").filter(Boolean);
  if (topic.length !== 1 || !/^[A-Za-z0-9_-]{16,128}$/.test(topic[0])) {
    throw new AlertDeliveryError("alert_webhook_invalid");
  }
  const record = payload as Record<string, unknown>;
  const details = typeof record.details === "object" && record.details !== null
    ? record.details as Record<string, unknown>
    : {};
  const click = typeof details.run_url === "string" ? details.run_url : undefined;
  const body: Record<string, unknown> = {
    topic: topic[0],
    title: "Finance crawler alert",
    message: `${String(record.summary ?? "Finance crawler state changed")}\n${String(record.alert_key ?? "unknown")}`,
    priority: record.severity === "critical" ? 5 : 2,
    tags: record.state === "resolved" ? ["white_check_mark"] : ["warning"],
  };
  if (click !== undefined) body.click = click;
  return { url: "https://ntfy.sh/", body, provider: "ntfy" };
}

function slackDelivery(webhookUrl: string, payload: object): AlertDelivery {
  const parsed = new URL(webhookUrl);
  const segments = parsed.pathname.split("/").filter(Boolean);
  if (parsed.hostname !== "hooks.slack.com" || parsed.search || parsed.hash
    || segments.length !== 4 || segments[0] !== "services"
    || segments.slice(1).some((segment) => !/^[A-Za-z0-9_-]+$/.test(segment))) {
    throw new AlertDeliveryError("alert_webhook_invalid");
  }
  return {
    url: parsed.toString(),
    body: { text: publicAlertText(payload) },
    provider: "slack",
  };
}

function telegramDelivery(webhookUrl: string, payload: object): AlertDelivery {
  const parsed = new URL(webhookUrl);
  const pathMatch = /^\/bot([0-9]+:[A-Za-z0-9_-]+)\/sendMessage$/.exec(parsed.pathname);
  const chatId = parsed.searchParams.get("chat_id");
  if (parsed.hostname !== "api.telegram.org" || !pathMatch || parsed.hash
    || parsed.searchParams.size !== 1 || chatId === null
    || !/^(?:-?[0-9]+|@[A-Za-z][A-Za-z0-9_]{4,31})$/.test(chatId)) {
    throw new AlertDeliveryError("alert_webhook_invalid");
  }
  return {
    url: `https://api.telegram.org/bot${pathMatch[1]}/sendMessage`,
    body: {
      chat_id: chatId,
      text: publicAlertText(payload),
      link_preview_options: { is_disabled: true },
    },
    provider: "telegram",
  };
}

function publicAlertText(payload: object): string {
  const record = payload as Record<string, unknown>;
  const details = typeof record.details === "object" && record.details !== null
    ? record.details as Record<string, unknown>
    : {};
  const icon = record.state === "resolved" ? "✅" : "🚨";
  const lines = [
    `${icon} ${String(record.summary ?? "Finance crawler state changed")}`,
    String(record.alert_key ?? "unknown"),
  ];
  if (typeof details.run_url === "string") lines.push(details.run_url);
  return lines.join("\n");
}

async function providerAccepted(
  provider: Exclude<AlertWebhookFormat, "auto">,
  response: Response,
): Promise<boolean> {
  if (provider === "slack") return (await response.text()).trim() === "ok";
  if (provider === "telegram") {
    try {
      const body = await response.json() as { ok?: unknown };
      return body.ok === true;
    } catch {
      return false;
    }
  }
  return true;
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

function readFallbackWebhookUrl(env: Env): string | null {
  const raw = (env as Env & { ALERT_FALLBACK_WEBHOOK_URL?: string })
    .ALERT_FALLBACK_WEBHOOK_URL;
  if (raw === undefined || raw === "") return null;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new AlertDeliveryError("alert_fallback_webhook_invalid");
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new AlertDeliveryError("alert_fallback_webhook_invalid");
  }
  return parsed.toString();
}
