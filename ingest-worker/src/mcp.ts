import type { McpAuthContext } from "./auth";
import {
  executeResearchJob,
  dispatchActionsResearchJob,
  readEvidenceAppendix,
  readResearchPack,
  readResearchReport,
  readResearchJob,
  readResearchJobByRequestId,
  retryResearchJob,
  submitResearchJob,
  type ResearchJobExecutionDependencies,
  type ResearchJobDispatchDependencies,
} from "./research-jobs";
import { buildPersistedResearchPlan } from "./research-planner";
import { HttpError } from "./storage";
import { PayloadValidationError } from "./contracts";


export interface McpRequestResult {
  response: Record<string, unknown>;
  execute_job_id?: string;
}

export interface McpRequestDependencies extends ResearchJobExecutionDependencies, ResearchJobDispatchDependencies {}

const TOOLS = [
  {
    name: "resolve_target",
    description: "Validate and normalize a research target before creating a job. Use kind, symbol/name/market, or url; do not use asset or asset_class.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["kind"],
      properties: {
        kind: { enum: ["equity", "etf", "crypto", "company", "industry", "topic", "url"] },
        symbol: { type: "string" },
        name: { type: "string" },
        market: { type: "string" },
        url: { type: "string", format: "uri" },
      },
    },
  },
  {
    name: "plan_research_sources",
    description: "Check existing snapshots and return the approved source bundle and refresh decision. Production investment research uses collection_scope=full_catalog (120 brands + 166 endpoints); max_sources only controls legacy display context.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["target", "requirements"],
      properties: {
        target: {
          type: "object",
          additionalProperties: false,
          required: ["kind"],
          properties: {
            kind: { enum: ["equity", "etf", "crypto", "company", "industry", "topic", "url"] },
            symbol: { type: "string" },
            name: { type: "string" },
            market: { type: "string" },
            url: { type: "string", format: "uri" },
          },
        },
        requirements: {
          type: "object",
          additionalProperties: false,
          required: [
            "question",
            "source_strategy",
            "include_market_data",
            "include_topic_radar",
            "report_profile",
            "max_sources",
          ],
          properties: {
            question: { type: "string", minLength: 10 },
            source_strategy: { enum: ["latest_published", "actions"] },
            include_market_data: { type: "boolean" },
            include_topic_radar: { type: "boolean" },
            report_profile: { enum: ["detailed_traceable", "compact_traceable"] },
            max_sources: { type: "integer", minimum: 1, maximum: 5000 },
            collection_scope: { enum: ["full_catalog", "legacy_smoke"] },
            max_context_items: { type: "integer", minimum: 1, maximum: 5000 },
            max_evidence_items: { type: "integer", minimum: 1, maximum: 10000 },
            objective: { enum: ["screen", "research", "monitor", "compare", "due_diligence", "meeting_battle_card", "decision_support"] },
            as_of: { type: "string" },
            horizon: { enum: ["intraday", "days", "months", "years", "transaction"] },
            constraints: { type: "object", additionalProperties: true },
            requested_outputs: { type: "array", items: { enum: ["quick_card", "detailed_report", "evidence_appendix"] } },
          },
        },
      },
    },
  },
  {
    name: "submit_research_job",
    description: "Create an asynchronous research report job. The request must use target.kind and requirements fields; it never makes a personal buy/sell decision.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["schema_version", "operation", "idempotency_key", "target", "requirements"],
      properties: {
        schema_version: { const: 1 },
        operation: { const: "submit_research_job" },
        idempotency_key: { type: "string", minLength: 8 },
        target: {
          type: "object",
          additionalProperties: false,
          required: ["kind"],
          properties: {
            kind: { enum: ["equity", "etf", "crypto", "company", "industry", "topic", "url"] },
            symbol: { type: "string" },
            name: { type: "string" },
            market: { type: "string" },
            url: { type: "string", format: "uri" },
          },
        },
        requirements: {
          type: "object",
          additionalProperties: false,
          required: [
            "question",
            "source_strategy",
            "include_market_data",
            "include_topic_radar",
            "report_profile",
            "max_sources",
          ],
          properties: {
            question: { type: "string", minLength: 10 },
            source_strategy: { enum: ["latest_published", "actions"] },
            include_market_data: { type: "boolean" },
            include_topic_radar: { type: "boolean" },
            report_profile: { enum: ["detailed_traceable", "compact_traceable"] },
            max_sources: { type: "integer", minimum: 1, maximum: 5000 },
            collection_scope: { enum: ["full_catalog", "legacy_smoke"] },
            max_context_items: { type: "integer", minimum: 1, maximum: 5000 },
            max_evidence_items: { type: "integer", minimum: 1, maximum: 10000 },
            objective: { enum: ["screen", "research", "monitor", "compare", "due_diligence", "meeting_battle_card", "decision_support"] },
            as_of: { type: "string" },
            horizon: { enum: ["intraday", "days", "months", "years", "transaction"] },
            constraints: { type: "object", additionalProperties: true },
            requested_outputs: { type: "array", items: { enum: ["quick_card", "detailed_report", "evidence_appendix"] } },
          },
        },
      },
    },
  },
  {
    name: "get_job_status",
    description: "Read research job status and last-good artifact metadata by job_id or request_id. request_id is stable across client restarts.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      anyOf: [{ required: ["job_id"] }, { required: ["request_id"] }],
      properties: { job_id: { type: "string" }, request_id: { type: "string" } },
    },
  },
  {
    name: "retry_research_job",
    description: "Retry a blocked or failed research job without waiting for collection; Actions jobs are re-dispatched and latest-published jobs are queued for background execution.",
    inputSchema: { type: "object", additionalProperties: false, required: ["job_id"], properties: { job_id: { type: "string" } } },
  },
  {
    name: "get_research_pack",
    description: "Read a completed private Research Pack with citations and quality state.",
    inputSchema: { type: "object", additionalProperties: false, required: ["job_id"], properties: { job_id: { type: "string" } } },
  },
  {
    name: "get_research_report",
    description: "Read detailed evidence-grounded second-opinion reports for a completed job.",
    inputSchema: { type: "object", additionalProperties: false, required: ["job_id"], properties: { job_id: { type: "string" } } },
  },
  {
    name: "get_evidence_appendix",
    description: "Read the evidence appendix without exposing private raw content.",
    inputSchema: { type: "object", additionalProperties: false, required: ["job_id"], properties: { job_id: { type: "string" } } },
  },
] as const;

export async function handleMcpRequest(
  env: Env,
  payload: unknown,
  auth: McpAuthContext,
  now: Date,
  dependencies: McpRequestDependencies,
): Promise<McpRequestResult> {
  const request = asMcpRequest(payload);
  if (request.method === "notifications/initialized") {
    return { response: { jsonrpc: "2.0" } };
  }
  if (request.method === "ping") {
    return { response: success(request.id, {}) };
  }
  if (request.method === "initialize") {
    return {
      response: success(request.id, {
        protocolVersion: "2025-06-18",
        capabilities: { tools: {} },
        serverInfo: { name: "finance-research-report-generator", version: "1.0.0" },
      }),
    };
  }
  if (request.method === "tools/list") {
    requireScope(auth, "research:read");
    return { response: success(request.id, { tools: TOOLS }) };
  }
  if (request.method !== "tools/call") {
    return { response: failure(request.id, -32601, "method_not_found") };
  }
  const params = asRecord(request.params);
  const name = stringField(params, "name");
  const args = params.arguments ?? {};
  if (!name) return { response: failure(request.id, -32602, "tool_name_required") };
  try {
    switch (name) {
      case "resolve_target":
        requireScope(auth, "research:read");
        return toolSuccess(request.id, resolveTarget(args));
      case "plan_research_sources":
        requireScope(auth, "research:read");
        return toolSuccess(request.id, await planSources(env, args, now));
      case "submit_research_job": {
        requireScope(auth, "research:submit");
        const submitted = await submitResearchJob(env, normalizeSubmitArguments(args), auth, now);
        const result = submitted.requirements.source_strategy === "actions" && !submitted.replayed
          ? await dispatchActionsResearchJob(env, submitted.job_id, now, dependencies)
          : submitted;
        return {
          response: toolResult(result, request.id),
          execute_job_id: result.replayed || result.requirements.source_strategy === "actions"
            ? undefined
            : result.job_id,
        };
      }
      case "get_job_status":
        requireScope(auth, "research:read");
        return toolSuccess(request.id, await readJobStatus(env, args));
      case "retry_research_job": {
        requireScope(auth, "research:submit");
        const retried = await retryResearchJob(
          env,
          requiredJobId(args),
          now,
          dependencies,
        );
        const { execute_job_id: executeJobId, ...status } = retried;
        return {
          response: toolResult(status, request.id),
          execute_job_id: executeJobId,
        };
      }
      case "get_research_pack":
        requireScope(auth, "research:read");
        return toolSuccess(request.id, await readResearchPack(env, requiredJobId(args)));
      case "get_research_report":
        requireScope(auth, "research:read");
        return toolSuccess(request.id, {
          schema_version: 1,
          job_id: requiredJobId(args),
          reports: await readResearchReport(env, requiredJobId(args)),
        });
      case "get_evidence_appendix":
        requireScope(auth, "research:read");
        return toolSuccess(request.id, await readEvidenceAppendix(env, requiredJobId(args)));
      default:
        return { response: failure(request.id, -32602, "tool_not_found") };
    }
  } catch (error) {
    const normalized = normalizeToolError(error);
    return toolError(request.id, normalized.code, normalized.details);
  }
}

export async function executeSubmittedJob(
  env: Env,
  jobId: string,
  dependencies: ResearchJobExecutionDependencies,
  now: Date,
): Promise<void> {
  await executeResearchJob(env, jobId, dependencies, now);
}

function resolveTarget(value: unknown): Record<string, unknown> {
  const target = asRecord(value);
  const kind = stringField(target, "kind");
  if (!kind) throw new HttpError(422, "target_kind_required");
  if (!["equity", "etf", "crypto", "company", "industry", "topic", "url"].includes(kind)) {
    throw new HttpError(422, "target_kind_invalid");
  }
  const symbol = stringField(target, "symbol");
  const name = stringField(target, "name");
  if (!symbol && !name && kind !== "url") throw new HttpError(422, "target_identifier_required");
  const url = stringField(target, "url");
  if (kind === "url" && !url) throw new HttpError(422, "target_url_required");
  if (url) {
    try {
      const parsed = new URL(url);
      if (!parsed.protocol || !parsed.hostname) throw new Error("invalid URL");
    } catch {
      throw new HttpError(422, "target_url_invalid");
    }
  }
  return {
    schema_version: 1,
    resolved: true,
    target: {
      kind,
      ...(symbol ? { symbol: symbol.toUpperCase() } : {}),
      ...(name ? { name } : {}),
      ...(stringField(target, "market") ? { market: stringField(target, "market") } : {}),
      ...(url ? { url } : {}),
    },
  };
}

async function planSources(
  env: Env,
  value: unknown,
  now: Date,
): Promise<Record<string, unknown>> {
  const input = asRecord(value);
  const target = asRecord(input.target);
  const requirements = asRecord(input.requirements);
  const request = {
    schema_version: 1,
    operation: "submit_research_job",
    idempotency_key: `planner_preview_${now.getTime()}`,
    target,
    requirements,
  };
  const plan = await buildPersistedResearchPlan(env.DB, request, `preview_${now.getTime()}`, now);
  return {
    schema_version: 1,
    requirement: plan.requirement,
    source_bundle: plan.source_bundle,
    snapshot: plan.snapshot,
    async: true,
  };
}

function requiredJobId(value: unknown): string {
  const jobId = stringField(asRecord(value), "job_id");
  if (!jobId) throw new HttpError(422, "job_id_required");
  return jobId;
}

async function readJobStatus(env: Env, value: unknown): Promise<unknown> {
  const input = asRecord(value);
  const jobId = stringField(input, "job_id");
  const requestId = stringField(input, "request_id");
  if (jobId && requestId) throw new HttpError(422, "job_id_or_request_id_exclusive");
  if (jobId) return await readResearchJob(env, jobId);
  if (requestId) return await readResearchJobByRequestId(env, requestId);
  throw new HttpError(422, "job_id_required");
}

function normalizeSubmitArguments(value: unknown): unknown {
  const input = asRecord(value);
  return input.schema_version === "1"
    ? { ...input, schema_version: 1 }
    : input;
}

function toolSuccess(id: string | number | null, structuredContent: unknown): McpRequestResult {
  return { response: toolResult(structuredContent, id) };
}

function toolError(id: string | number | null, code: string, details: string[]): McpRequestResult {
  const structuredContent = { error: code, details };
  return {
    response: {
      jsonrpc: "2.0",
      id,
      result: {
        isError: true,
        structuredContent,
        content: [{ type: "text", text: JSON.stringify(structuredContent) }],
      },
    },
  };
}

function toolResult(structuredContent: unknown, id: string | number | null = null): Record<string, unknown> {
  return {
    jsonrpc: "2.0",
    id,
    result: {
      structuredContent,
      content: [{ type: "text", text: JSON.stringify(structuredContent) }],
    },
  };
}

function success(id: string | number | null, result: unknown): Record<string, unknown> {
  return { jsonrpc: "2.0", id, result };
}

function failure(id: string | number | null, code: number, message: string): Record<string, unknown> {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function asMcpRequest(value: unknown): {
  id: string | number | null;
  method: string;
  params?: unknown;
} {
  const input = asRecord(value);
  const method = stringField(input, "method");
  if (!method) throw new HttpError(400, "invalid_jsonrpc_request");
  const idValue = input.id;
  const id = typeof idValue === "string" || typeof idValue === "number" ? idValue : null;
  return { id, method, params: input.params };
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new HttpError(400, "invalid_jsonrpc_request");
  }
  return value as Record<string, unknown>;
}

function stringField(value: Record<string, unknown>, field: string): string | null {
  const candidate = value[field];
  return typeof candidate === "string" && candidate.length > 0 ? candidate : null;
}

function requireScope(auth: McpAuthContext, scope: string): void {
  if (!auth.scopes.includes(scope)) throw new HttpError(403, "mcp_scope_denied", [scope]);
}

function normalizeToolError(error: unknown): { code: string; details: string[] } {
  if (error instanceof HttpError) return { code: error.code, details: error.details };
  if (error instanceof PayloadValidationError) {
    return { code: "invalid_payload", details: error.details };
  }
  return { code: "internal_error", details: [] };
}
