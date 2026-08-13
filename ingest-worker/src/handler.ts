import {
  type AuthContext,
  AuthenticationError,
  authenticateGithubOidc,
} from "./auth";
import { HttpError, ingestItems, publishSnapshot } from "./storage";
import {
  parseFreshnessPolicy,
  readStatus,
  StatusConfigurationError,
  StatusReadError,
} from "./status";
import {
  buildRunPlan,
  parseRunAdmissionPolicy,
  RunPlanConfigurationError,
} from "./run-plan";
import { AlertDeliveryError, reportActionFailure } from "./alerts";
import {
  runAuthenticatedFreshnessWatchdog,
  runFreshnessWatchdog,
} from "./watchdog";


const MAX_JSON_BYTES = 2_000_000;

type Authenticator = (request: Request, env: Env) => Promise<AuthContext>;

export interface HandlerDependencies {
  authenticate: Authenticator;
  now: () => Date;
  alertFetch: typeof fetch;
}

const defaultDependencies: HandlerDependencies = {
  authenticate: authenticateGithubOidc,
  now: () => new Date(),
  alertFetch: fetch,
};

export function createHandler(
  overrides: Partial<HandlerDependencies> = {},
): Pick<ExportedHandler<Env>, "fetch" | "scheduled"> {
  const dependencies: HandlerDependencies = { ...defaultDependencies, ...overrides };
  return {
    async scheduled(_controller, env): Promise<void> {
      await runFreshnessWatchdog(env, dependencies.now(), dependencies.alertFetch);
    },
    async fetch(request: Request, env: Env): Promise<Response> {
      const requestId = crypto.randomUUID();
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname === "/health") {
        return jsonResponse({ ok: true, service: "finance-crawler-ingest" }, 200);
      }
      if (url.pathname === "/v1/status") {
        if (request.method !== "GET") {
          return jsonResponse({ error: "method_not_allowed", request_id: requestId }, 405);
        }
        try {
          const status = await readStatus(
            env.DB,
            dependencies.now(),
            parseFreshnessPolicy(env),
          );
          return jsonResponse(status, 200);
        } catch (error) {
          const normalized = normalizeError(error);
          return jsonResponse(
            { error: normalized.code, request_id: requestId },
            normalized.status,
          );
        }
      }
      if (![
        "/v1/ingest/items",
        "/v1/ingest/publish",
        "/v1/run/plan",
        "/v1/alerts/action-failure",
        "/v1/alerts/freshness-check",
      ].includes(url.pathname)) {
        return jsonResponse({ error: "route_not_found", request_id: requestId }, 404);
      }
      if (request.method !== "POST") {
        return jsonResponse({ error: "method_not_allowed", request_id: requestId }, 405);
      }

      try {
        const auth = await dependencies.authenticate(request, env);
        const payload = await readBoundedJson(request, MAX_JSON_BYTES);
        if (url.pathname === "/v1/run/plan") {
          const plan = await buildRunPlan(
            env.DB,
            payload,
            auth,
            dependencies.now(),
            parseRunAdmissionPolicy(env),
          );
          return jsonResponse({ ...plan, request_id: requestId }, 200);
        }
        if (url.pathname === "/v1/alerts/action-failure") {
          const result = await reportActionFailure(
            env,
            payload,
            auth,
            dependencies.now(),
            dependencies.alertFetch,
          );
          return jsonResponse(
            { ...result, request_id: requestId },
            result.delivered ? 202 : 200,
          );
        }
        if (url.pathname === "/v1/alerts/freshness-check") {
          const result = await runAuthenticatedFreshnessWatchdog(
            env,
            payload,
            auth,
            dependencies.now(),
            dependencies.alertFetch,
          );
          return jsonResponse(
            { ...result, request_id: requestId },
            result.delivered ? 202 : 200,
          );
        }
        if (url.pathname === "/v1/ingest/items") {
          if (stringField(payload, "workflow_run_id") !== auth.workflowRunId) {
            throw new HttpError(403, "workflow_run_mismatch");
          }
          if (stringField(payload, "commit_sha") !== auth.commitSha) {
            throw new HttpError(403, "commit_sha_mismatch");
          }
          const result = await ingestItems(env, payload, dependencies.now());
          return jsonResponse({ ...result, request_id: requestId }, 202);
        }

        const runId = stringField(payload, "run_id");
        if (runId === null) throw new HttpError(422, "invalid_payload");
        const run = await env.DB.prepare(
          "SELECT workflow_run_id, commit_sha FROM runs WHERE run_id = ?",
        ).bind(runId).first<{ workflow_run_id: string; commit_sha: string }>();
        if (!run || run.workflow_run_id !== auth.workflowRunId) {
          throw new HttpError(403, "workflow_run_mismatch");
        }
        if (run.commit_sha !== auth.commitSha) {
          throw new HttpError(403, "commit_sha_mismatch");
        }
        const result = await publishSnapshot(env, payload, dependencies.now());
        return jsonResponse({ ...result, request_id: requestId }, 200);
      } catch (error) {
        const normalized = normalizeError(error);
        console.error(JSON.stringify({
          event: "ingest_request_failed",
          request_id: requestId,
          code: normalized.code,
          status: normalized.status,
        }));
        return jsonResponse(
          {
            error: normalized.code,
            details: normalized.details,
            request_id: requestId,
          },
          normalized.status,
        );
      }
    },
  };
}

async function readBoundedJson(request: Request, maxBytes: number): Promise<unknown> {
  const contentType = request.headers.get("Content-Type")?.split(";", 1)[0].trim();
  if (contentType !== "application/json") throw new HttpError(415, "unsupported_media_type");
  const declared = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(declared) && declared > maxBytes) {
    throw new HttpError(413, "payload_too_large");
  }
  if (!request.body) throw new HttpError(400, "empty_body");

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) {
      await reader.cancel("payload_too_large");
      throw new HttpError(413, "payload_too_large");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(bytes)) as unknown;
  } catch {
    throw new HttpError(400, "invalid_json");
  }
}

function stringField(value: unknown, field: string): string | null {
  if (typeof value !== "object" || value === null || !(field in value)) return null;
  const fieldValue = Reflect.get(value, field);
  return typeof fieldValue === "string" && fieldValue.length > 0 ? fieldValue : null;
}

function normalizeError(error: unknown): HttpError {
  if (error instanceof HttpError) return error;
  if (error instanceof AuthenticationError) return new HttpError(error.status, error.code);
  if (error instanceof StatusReadError) return new HttpError(503, "status_unavailable");
  if (error instanceof StatusConfigurationError) {
    return new HttpError(500, "status_configuration_invalid");
  }
  if (error instanceof RunPlanConfigurationError) {
    return new HttpError(500, "run_plan_configuration_invalid");
  }
  if (error instanceof AlertDeliveryError) {
    return new HttpError(
      error.code === "alert_webhook_not_configured" ? 503 : 502,
      error.code,
    );
  }
  console.error(JSON.stringify({
    event: "unhandled_ingest_error",
    error: error instanceof Error ? error.message : String(error),
  }));
  return new HttpError(500, "internal_error");
}

function jsonResponse(payload: object, status: number): Response {
  return Response.json(payload, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
