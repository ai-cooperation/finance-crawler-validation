import {
  type AuthContext,
  AuthenticationError,
  authenticateGithubOidc,
  authenticateMcp,
  type McpAuthContext,
} from "./auth";
import {
  HttpError,
  ingestItems,
  ingestMarketAlignment,
  ingestResearchReport,
  ingestTradingAgentsPlan,
  publishSnapshot,
} from "./storage";
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
import {
  AlertDeliveryError,
  reportActionFailure,
  reportScheduledWatchdogFailure,
  resolveActionFailures,
} from "./alerts";
import {
  runAuthenticatedFreshnessWatchdog,
  runFreshnessWatchdog,
} from "./watchdog";
import { observeScheduledSoak } from "./soak";
import { generateResearchReports, type AiRunner } from "./research-agent";
import {
  executeSubmittedJob,
  handleMcpRequest,
} from "./mcp";
import { completeResearchJob, failResearchJob } from "./research-jobs";


const MAX_JSON_BYTES = 2_000_000;

type Authenticator = (request: Request, env: Env) => Promise<AuthContext>;
type McpAuthenticator = (request: Request, env: Env) => Promise<McpAuthContext>;

export interface HandlerDependencies {
  authenticate: Authenticator;
  authenticateMcp: McpAuthenticator;
  now: () => Date;
  alertFetch: typeof fetch;
  dispatchFetch: typeof fetch;
  runAi: AiRunner;
}

const defaultDependencies: HandlerDependencies = {
  authenticate: authenticateGithubOidc,
  authenticateMcp,
  now: () => new Date(),
  alertFetch: fetch,
  dispatchFetch: fetch,
  runAi: async (env, model, input) => env.AI.run(model, input),
};

export function createHandler(
  overrides: Partial<HandlerDependencies> = {},
): Pick<ExportedHandler<Env>, "fetch" | "scheduled"> {
  const dependencies: HandlerDependencies = { ...defaultDependencies, ...overrides };
  return {
    async scheduled(controller, env): Promise<void> {
      try {
        await runFreshnessWatchdog(env, dependencies.now(), dependencies.alertFetch);
      } catch (error) {
        if (!(error instanceof AlertDeliveryError)) {
          try {
            await reportScheduledWatchdogFailure(
              env,
              controller,
              dependencies.now(),
              dependencies.alertFetch,
            );
          } catch (alertError) {
            console.error(JSON.stringify({
              event: "scheduled_watchdog_failure_alert_failed",
              error_code: alertError instanceof AlertDeliveryError
                ? alertError.code
                : "unknown",
            }));
          }
        }
        throw error;
      }
    },
    async fetch(request: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
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
      if (url.pathname === "/mcp") {
        if (request.method === "GET") {
          try {
            await dependencies.authenticateMcp(request, env);
            const sessionId = crypto.randomUUID();
            return mcpSseEndpointResponse(url.pathname, sessionId);
          } catch (error) {
            const normalized = normalizeError(error);
            return jsonResponse(
              { error: normalized.code, details: normalized.details, request_id: requestId },
              normalized.status,
            );
          }
        }
        if (request.method !== "POST") {
          return jsonResponse({ error: "method_not_allowed", request_id: requestId }, 405);
        }
        try {
          const auth = await dependencies.authenticateMcp(request, env);
          const payload = await readBoundedJson(request, MAX_JSON_BYTES);
          const mcp = await handleMcpRequest(
            env,
            payload,
            auth,
            dependencies.now(),
            { runAi: dependencies.runAi, dispatchFetch: dependencies.dispatchFetch },
          );
          if (mcp.execute_job_id && ctx) {
            ctx.waitUntil(
              executeSubmittedJob(
                env,
                mcp.execute_job_id,
                { runAi: dependencies.runAi },
                dependencies.now(),
              ).catch((error) => {
                console.error(JSON.stringify({
                  event: "research_job_background_failed",
                  job_id: mcp.execute_job_id,
                  error: error instanceof Error ? error.message : String(error),
                }));
              }),
            );
          }
          return jsonResponse(mcp.response, 200);
        } catch (error) {
          const normalized = normalizeError(error);
          return jsonResponse(
            { error: normalized.code, details: normalized.details, request_id: requestId },
            normalized.status,
          );
        }
      }
      if (![
        "/v1/ingest/items",
        "/v1/ingest/publish",
        "/v1/ingest/market-alignment",
        "/v1/ingest/tradingagents-plan",
        "/v1/ingest/research-report",
        "/v1/agent/research-reports",
        "/v1/research/jobs/complete",
        "/v1/research/jobs/fail",
        "/v1/run/plan",
        "/v1/alerts/action-failure",
        "/v1/alerts/action-recovery",
        "/v1/alerts/freshness-check",
        "/v1/soak/observe",
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
        if (url.pathname === "/v1/alerts/action-recovery") {
          const result = await resolveActionFailures(
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
        if (url.pathname === "/v1/soak/observe") {
          const result = await observeScheduledSoak(
            env,
            payload,
            auth,
            dependencies.now(),
          );
          return jsonResponse(
            { ...result, request_id: requestId },
            result.replayed ? 200 : 201,
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

        if (url.pathname === "/v1/ingest/market-alignment") {
          if (stringField(payload, "workflow_run_id") !== auth.workflowRunId) {
            throw new HttpError(403, "workflow_run_mismatch");
          }
          if (stringField(payload, "commit_sha") !== auth.commitSha) {
            throw new HttpError(403, "commit_sha_mismatch");
          }
          const result = await ingestMarketAlignment(env, payload, dependencies.now());
          return jsonResponse(
            { ...result, request_id: requestId },
            result.replayed ? 200 : 201,
          );
        }

        if (url.pathname === "/v1/ingest/tradingagents-plan") {
          if (stringField(payload, "workflow_run_id") !== auth.workflowRunId) {
            throw new HttpError(403, "workflow_run_mismatch");
          }
          if (stringField(payload, "commit_sha") !== auth.commitSha) {
            throw new HttpError(403, "commit_sha_mismatch");
          }
          const result = await ingestTradingAgentsPlan(env, payload, dependencies.now());
          return jsonResponse(
            { ...result, request_id: requestId },
            result.replayed ? 200 : 201,
          );
        }

        if (url.pathname === "/v1/ingest/research-report") {
          if (stringField(payload, "workflow_run_id") !== auth.workflowRunId) {
            throw new HttpError(403, "workflow_run_mismatch");
          }
          if (stringField(payload, "commit_sha") !== auth.commitSha) {
            throw new HttpError(403, "commit_sha_mismatch");
          }
          const result = await ingestResearchReport(env, payload, dependencies.now());
          return jsonResponse(
            { ...result, request_id: requestId },
            result.replayed ? 200 : 201,
          );
        }

        if (url.pathname === "/v1/agent/research-reports") {
          const result = await generateResearchReports(
            env,
            payload,
            auth,
            dependencies.now(),
            dependencies.runAi,
          );
          return jsonResponse({ ...result, request_id: requestId }, 200);
        }
        if (url.pathname === "/v1/research/jobs/complete") {
          const result = await completeResearchJob(
            env,
            payload,
            auth,
            dependencies.now(),
            { runAi: dependencies.runAi },
          );
          return jsonResponse({ ...result, request_id: requestId }, 200);
        }
        if (url.pathname === "/v1/research/jobs/fail") {
          const result = await failResearchJob(
            env,
            payload,
            auth,
            dependencies.now(),
          );
          return jsonResponse({ ...result, request_id: requestId }, 200);
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

function mcpSseEndpointResponse(pathname: string, sessionId: string): Response {
  const body = `event: endpoint\ndata: ${pathname}?sessionId=${encodeURIComponent(sessionId)}\n\n`;
  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Mcp-Session-Id": sessionId,
      "Access-Control-Allow-Origin": "*",
    },
  });
}
