import {
  type ResearchJobRequest,
  type ResearchJobCompletionRequest,
  type ResearchJobFailureRequest,
  type ResearchPack,
  type ResearchReport,
  type ResearchEvidenceGraph,
  type ResearchEvidenceGraphClaim,
  type FinancialDepth,
  validateResearchJobRequest,
  validateResearchPack,
  validateResearchJobCompletion,
  validateResearchJobFailure,
  validateResearchJobStatus,
  validateResearchReportEnvelope,
  validateMarketAlignmentEnvelope,
  validateTopicSnapshot,
  PayloadValidationError,
} from "./contracts";
import type { AuthContext, McpAuthContext } from "./auth";
import {
  buildAuditStatement,
  HttpError,
  readPrivateJson,
  sha256Hex,
} from "./storage";
import { canonicalJson } from "./canonical-json";
import { generateResearchReports, type AiRunner } from "./research-agent";
import {
  buildPersistedResearchPlan,
  type ResearchRequirement,
  type SourceBundleManifest,
} from "./research-planner";
import { dispatchResearchWorkflow } from "./github-dispatch";
import { buildInvestmentHarnessArtifacts } from "./investment-harness";
import { matchesTargetEvidence } from "./target-evidence";


const PIPELINE_VERSION = "research-report-generator-v1";
const MAX_JOB_IDEMPOTENCY_KEY = 128;
const LATEST_PUBLISHED_DEFAULT_FRESHNESS_MS = 24 * 60 * 60 * 1000;
const LATEST_PUBLISHED_MARKET_FRESHNESS_MS = 6 * 60 * 60 * 1000;
// A status read is also a recovery boundary.  If a Worker invocation dies
// after claiming a job, MCP clients must not poll `running` forever; they can
// safely retry the failed job and the stale executor is prevented from
// publishing its artifacts by the running-state guard below.
const RUNNING_JOB_TIMEOUT_MS = 10 * 60 * 1000;

export interface ResearchJobStatus {
  schema_version: 1;
  request_id: string;
  job_id: string;
  status: "queued" | "running" | "blocked" | "completed" | "partial" | "failed" | "stale";
  target: ResearchJobRequest["target"];
  requirements: ResearchJobRequest["requirements"];
  run_id: string | null;
  pack_id: string | null;
  report_count: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  stage: ResearchJobStage;
  progress: number | null;
  retryable: boolean;
  next_action: ResearchJobNextAction | null;
  planner: {
    requirement: ResearchRequirement;
    source_bundle: SourceBundleManifest;
  } | null;
  replayed?: boolean;
}

export type ResearchJobStage =
  | "queued"
  | "dispatching"
  | "processing"
  | "published"
  | "blocked"
  | "failed"
  | "stale";

export type ResearchJobNextAction =
  | "poll_job_status"
  | "wait_for_actions"
  | "configure_actions_dispatch_and_retry"
  | "provide_document_engine"
  | "increase_source_budget"
  | "retry_research_job"
  | "request_refresh"
  | "review_error";

export interface ResearchJobExecutionDependencies {
  runAi: AiRunner;
}

export interface ResearchJobDispatchDependencies {
  dispatchFetch?: typeof fetch;
}

export interface ResearchJobExecutionOptions {
  runId?: string;
  planId?: string;
  alignmentId?: string;
}

export interface ResearchJobSubmitResult extends ResearchJobStatus {
  replayed: boolean;
}

export interface ResearchJobRetryResult extends ResearchJobStatus {
  execute_job_id?: string;
}

interface JobRow {
  job_id: string;
  request_id: string;
  idempotency_key: string;
  subject: string;
  target_json: string;
  requirements_json: string;
  request_sha256: string;
  status: ResearchJobStatus["status"];
  run_id: string | null;
  pack_id: string | null;
  report_count: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  requirement_json: string;
  source_bundle_json: string;
  planner_version: string;
  dispatch_id: string | null;
}

interface RunRow {
  run_id: string;
  workflow_run_id: string;
  commit_sha: string;
  snapshot_id: string;
  source_manifest_hash: string;
  status: string;
  collected_at: string;
  published_at: string | null;
}

interface PlanRow {
  plan_id: string;
  alignment_id: string;
  object_key: string;
}

interface AlignmentRow {
  alignment_id: string;
  topic_snapshot_id: string;
  market_snapshot_id: string;
  object_key: string;
}

interface MarketRow {
  snapshot_id: string;
  object_key: string;
}

interface SnapshotRow {
  object_key: string;
}

interface RawItemRow {
  item_id: string;
  source_id: string;
  canonical_url: string;
  title: string;
  summary: string;
  published_at: string | null;
  content_sha256: string;
  created_at: string;
}

/**
 * Keep the current run first, then fill empty incremental windows from the
 * recent last-good corpus.  The item_id de-duplication makes replayed runs
 * auditable without inflating source coverage.
 */
export function mergeResearchEvidenceRows(
  currentRows: RawItemRow[],
  lastGoodRows: RawItemRow[],
  limit: number,
): RawItemRow[] {
  const merged: RawItemRow[] = [];
  const seen = new Set<string>();
  for (const row of [...currentRows, ...lastGoodRows]) {
    if (seen.has(row.item_id)) continue;
    seen.add(row.item_id);
    merged.push(row);
    if (merged.length >= limit) break;
  }
  return merged;
}

export async function submitResearchJob(
  env: Env,
  payload: unknown,
  auth: McpAuthContext,
  now: Date,
  requestId = crypto.randomUUID(),
): Promise<ResearchJobSubmitResult> {
  const request = validateRequest(payload);
  if (request.idempotency_key.length > MAX_JOB_IDEMPOTENCY_KEY) {
    throw new HttpError(422, "invalid_payload", ["$.idempotency_key: too long"]);
  }
  const requestHash = await sha256Hex(canonicalJson(request));
  const existing = await env.DB.prepare(
    `SELECT job_id, request_id, idempotency_key, subject, target_json,
            requirements_json, request_sha256, status, run_id, pack_id,
            report_count, error_code, created_at, updated_at, completed_at,
            requirement_json, source_bundle_json, planner_version, dispatch_id
     FROM research_jobs WHERE idempotency_key = ?`,
  ).bind(request.idempotency_key).first<JobRow>();
  if (existing !== null) {
    if (existing.request_sha256 !== requestHash) {
      throw new HttpError(409, "idempotency_conflict");
    }
    return { ...statusFromRow(existing), replayed: true };
  }

  const planner = await buildPersistedResearchPlan(env.DB, request, requestId, now);
  const plannerBlocked = planner.source_bundle.strategy === "blocked";
  const initialStatus: ResearchJobStatus["status"] = plannerBlocked ? "blocked" : "queued";
  const initialError = plannerBlocked
    ? planner.source_bundle.reason ?? planner.source_bundle.sufficiency.reasons[0] ?? "research_plan_blocked"
    : null;

  const createdAt = now.toISOString();
  const jobId = `research_${compactTimestamp(now)}_${crypto.randomUUID().slice(0, 8)}`;
  try {
    await env.DB.prepare(
      `INSERT INTO research_jobs (
        job_id, request_id, idempotency_key, subject, target_json,
        requirements_json, request_sha256, status, report_count,
        requirement_json, source_bundle_json, planner_version, dispatch_id,
        error_code, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, NULL, ?, ?, ?)`,
    ).bind(
      jobId,
      requestId,
      request.idempotency_key,
      auth.subject,
      JSON.stringify(request.target),
      JSON.stringify(request.requirements),
      requestHash,
      initialStatus,
      JSON.stringify(planner.requirement),
      JSON.stringify(planner.source_bundle),
      planner.requirement.schema_version === 1 ? "research-requirement-planner-v1" : "unknown",
      initialError,
      createdAt,
      createdAt,
    ).run();
  } catch (error) {
    console.error(JSON.stringify({
      event: "research_job_submit_failed",
      job_id: jobId,
      error: error instanceof Error ? error.message : String(error),
    }));
    throw new HttpError(503, "storage_write_failed");
  }
  return {
    ...statusFromRow({
      job_id: jobId,
      request_id: requestId,
      idempotency_key: request.idempotency_key,
      subject: auth.subject,
      target_json: JSON.stringify(request.target),
      requirements_json: JSON.stringify(request.requirements),
      request_sha256: requestHash,
      status: initialStatus,
      run_id: null,
      pack_id: null,
      report_count: 0,
      error_code: initialError,
      created_at: createdAt,
      updated_at: createdAt,
      completed_at: null,
      requirement_json: JSON.stringify(planner.requirement),
      source_bundle_json: JSON.stringify(planner.source_bundle),
      planner_version: "research-requirement-planner-v1",
      dispatch_id: null,
    }),
    replayed: false,
  };
}

export async function readResearchJob(env: Env, jobId: string, now?: Date): Promise<ResearchJobStatus> {
  const row = await recoverStuckJob(env.DB, await findJob(env.DB, jobId), now);
  if (row === null) throw new HttpError(404, "research_job_not_found");
  return statusFromRow(row);
}

export async function readResearchJobByRequestId(
  env: Env,
  requestId: string,
  now?: Date,
): Promise<ResearchJobStatus> {
  const row = await recoverStuckJob(env.DB, await findJobByRequestId(env.DB, requestId), now);
  if (row === null) throw new HttpError(404, "research_job_not_found");
  return statusFromRow(row);
}

export async function dispatchActionsResearchJob(
  env: Env,
  jobId: string,
  now: Date,
  dependencies: ResearchJobDispatchDependencies = {},
): Promise<ResearchJobStatus> {
  const row = await findJob(env.DB, jobId);
  if (row === null) throw new HttpError(404, "research_job_not_found");
  if (row.status === "completed" || row.status === "partial") return statusFromRow(row);
  if (row.status === "queued" && row.dispatch_id !== null) return statusFromRow(row);
  const request = requestFromRow(row);
  if (request.requirements.source_strategy !== "actions") {
    throw new HttpError(409, "research_job_strategy_conflict");
  }
  const planner = parseOptionalPlanner(row.requirement_json, row.source_bundle_json);
  if (planner === null || planner.source_bundle.strategy !== "refresh") {
    return await updateJob(env, row, "blocked", now, "research_plan_blocked");
  }
  try {
    const dispatched = await dispatchResearchWorkflow(
      env,
      {
        job_id: row.job_id,
        source_ids: planner.source_bundle.source_ids,
        target: planner.requirement.target as unknown as Record<string, unknown>,
        requirement: planner.requirement,
        source_bundle: planner.source_bundle,
      },
      dependencies.dispatchFetch,
    );
    await env.DB.prepare(
      "UPDATE research_jobs SET status = 'queued', dispatch_id = ?, updated_at = ?, error_code = NULL WHERE job_id = ?",
    ).bind(dispatched.dispatch_id, now.toISOString(), row.job_id).run();
    return await readResearchJob(env, row.job_id);
  } catch (error) {
    const code = error instanceof HttpError ? error.code : "actions_dispatch_failed";
    return await updateJob(env, row, "blocked", now, code);
  }
}

/**
 * Re-arm a failed/blocked job without making the MCP call wait for crawling or
 * model execution. Actions jobs are re-dispatched only when no dispatch was
 * recorded; latest-published jobs are returned to queued and the handler
 * schedules the existing background executor with waitUntil.
 */
export async function retryResearchJob(
  env: Env,
  jobId: string,
  now: Date,
  dependencies: ResearchJobDispatchDependencies = {},
): Promise<ResearchJobRetryResult> {
  const row = await findJob(env.DB, jobId);
  if (row === null) throw new HttpError(404, "research_job_not_found");
  if (row.status === "completed" || row.status === "partial") return statusFromRow(row);
  const request = requestFromRow(row);
  if (request.requirements.source_strategy === "actions") {
    return await dispatchActionsResearchJob(env, jobId, now, dependencies);
  }
  if (row.status === "running") return statusFromRow(row);
  await env.DB.prepare(
    `UPDATE research_jobs
     SET status = 'queued', error_code = NULL, completed_at = NULL, updated_at = ?
     WHERE job_id = ?`,
  ).bind(now.toISOString(), jobId).run();
  return { ...(await readResearchJob(env, jobId)), execute_job_id: jobId };
}

export async function executeResearchJob(
  env: Env,
  jobId: string,
  dependencies: ResearchJobExecutionDependencies,
  now: Date,
  options: ResearchJobExecutionOptions = {},
): Promise<ResearchJobStatus> {
  const row = await findJob(env.DB, jobId);
  if (row === null) throw new HttpError(404, "research_job_not_found");
  if (row.status === "completed" || row.status === "partial") return statusFromRow(row);
  const request = requestFromRow(row);
  if (request.requirements.source_strategy === "actions" && !options.runId) {
    return await updateJob(env, row, "blocked", now, "actions_dispatch_not_configured");
  }

  await setRunning(env.DB, row.job_id, now);
  console.log(JSON.stringify({ event: "research_job_stage", job_id: row.job_id, stage: "running" }));
  try {
    console.log(JSON.stringify({ event: "research_job_stage", job_id: row.job_id, stage: "load_run" }));
    const run = options.runId
      ? await runById(env.DB, options.runId)
      : await latestPublishedRun(env.DB);
    if (run === null) throw new HttpError(409, "research_inputs_unavailable");
    // `latest_published` is a read-only freshness contract.  It may reuse a
    // last-good run only while that run is inside the requirement SLA; it must
    // never silently turn stale data into a completed/partial report.  An
    // explicit Actions request (`source_strategy=actions`) is the only path
    // allowed to execute against a refresh run.
    if (
      request.requirements.source_strategy === "latest_published"
      && !options.runId
      && isRunStaleForLatestPublished(request, run, now)
    ) {
      return await updateJob(env, row, "blocked", now, "research_snapshot_refresh_required");
    }
    console.log(JSON.stringify({ event: "research_job_stage", job_id: row.job_id, stage: "load_plan" }));
    const plan = await planForRun(env.DB, run, options.planId);
    if (plan === null) throw new HttpError(409, "research_plan_unavailable");
    console.log(JSON.stringify({ event: "research_job_stage", job_id: row.job_id, stage: "load_alignment" }));
    const alignment = await alignmentForRun(env.DB, run, plan, options.alignmentId);
    if (alignment === null) throw new HttpError(409, "research_alignment_unavailable");

    console.log(JSON.stringify({ event: "research_job_stage", job_id: row.job_id, stage: "generate_reports" }));
    const persistedPlanner = parseOptionalPlanner(row.requirement_json, row.source_bundle_json);
    // Only an explicit production request opts into the bounded deterministic
    // first opinion.  Legacy callers that omit collection_scope retain the
    // model-backed contract used by the report-generation regression suite.
    const fullCatalog = request.requirements.collection_scope === "full_catalog"
      && persistedPlanner?.source_bundle.collection_scope === "full_catalog";
    const generation = await generateResearchReports(
      env,
      {
        schema_version: 1,
        operation: "generate_research_reports",
        run_id: run.run_id,
        workflow_run_id: run.workflow_run_id,
        commit_sha: run.commit_sha,
        plan_id: plan.plan_id,
        alignment_id: plan.alignment_id,
        target: request.target,
        research_question: request.requirements.question,
        authorize_model_execution: true,
        report_profile: request.requirements.report_profile,
        requested_outputs: request.requirements.requested_outputs,
        max_reports: 3,
        ...(fullCatalog ? { generation_mode: "ai_enrichment" as const } : {}),
        report_instance_id: jobId,
      },
      { workflowRunId: run.workflow_run_id, commitSha: run.commit_sha },
      now,
      dependencies.runAi,
    );
    console.log(JSON.stringify({ event: "research_job_stage", job_id: row.job_id, stage: "build_pack", report_count: generation.reports.length }));
    await assertJobRunning(env.DB, row.job_id);
    const pack = await buildResearchPack(
      env,
      row,
      request,
      run,
      plan,
      alignment,
      generation.reports.map((report) => report.report_id),
      now,
    );
    const packId = `pack_${jobId.slice("research_".length)}`;
    const packWithId: ResearchPack = { ...pack, pack_id: packId };
    const validated = validateResearchPack(packWithId);
    const serialized = canonicalJson(validated);
    const contentHash = await sha256Hex(serialized);
    const objectKey = `research-packs/${packId}.json`;
    await env.RAW_OBJECTS.put(objectKey, serialized, {
      httpMetadata: { contentType: "application/json" },
      customMetadata: { content_sha256: contentHash, job_id: jobId, run_id: run.run_id },
    });
    const happenedAt = now.toISOString();
    await assertJobRunning(env.DB, row.job_id);
    const audit = await buildAuditStatement(env.DB, jobId, "research_pack_completed", contentHash, happenedAt);
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO research_packs (
          pack_id, job_id, run_id, object_key, content_sha256, as_of,
          partial, stale, evidence_count, report_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        packId,
        jobId,
        run.run_id,
        objectKey,
        contentHash,
        validated.as_of,
        validated.quality.partial ? 1 : 0,
        validated.quality.stale ? 1 : 0,
        validated.evidence.length,
        validated.reports.length,
        happenedAt,
      ),
      env.DB.prepare(
        `UPDATE research_jobs SET status = ?, run_id = ?, pack_id = ?, report_count = ?,
          error_code = NULL, updated_at = ?, completed_at = ? WHERE job_id = ?`,
      ).bind(
        validated.quality.partial ? "partial" : "completed",
        run.run_id,
        packId,
        validated.reports.length,
        happenedAt,
        happenedAt,
        jobId,
      ),
      audit,
    ]);
    console.log(JSON.stringify({ event: "research_job_stage", job_id: row.job_id, stage: "published", report_count: validated.reports.length }));
    return await readResearchJob(env, jobId);
  } catch (error) {
    const status = error instanceof HttpError && error.code === "research_inputs_unavailable"
      ? "blocked"
      : "failed";
    const code = error instanceof HttpError ? error.code : "research_job_failed";
    console.error(JSON.stringify({
      event: "research_job_failed",
      job_id: row.job_id,
      error_code: code,
      error_message: error instanceof Error ? error.message : String(error),
      error_details: error instanceof PayloadValidationError ? error.details : undefined,
      error_stack: error instanceof Error ? error.stack : undefined,
    }));
    await updateJob(env, row, status, now, code);
    return await readResearchJob(env, jobId);
  }
}

export async function completeResearchJob(
  env: Env,
  payload: unknown,
  auth: AuthContext,
  now: Date,
  dependencies: ResearchJobExecutionDependencies,
): Promise<ResearchJobStatus> {
  const completion = validateCompletion(payload);
  if (completion.workflow_run_id !== auth.workflowRunId) {
    throw new HttpError(403, "workflow_run_mismatch");
  }
  if (completion.commit_sha !== auth.commitSha) {
    throw new HttpError(403, "commit_sha_mismatch");
  }
  const row = await findJob(env.DB, completion.job_id);
  if (row === null) throw new HttpError(404, "research_job_not_found");
  const request = requestFromRow(row);
  if (request.requirements.source_strategy !== "actions") {
    throw new HttpError(409, "research_job_strategy_conflict");
  }
  if (canonicalJson(completion.research_target) !== canonicalJson(request.target)) {
    throw new HttpError(409, "research_target_mismatch");
  }
  if (row.dispatch_id === null) throw new HttpError(409, "research_dispatch_not_recorded");
  const planner = parseOptionalPlanner(row.requirement_json, row.source_bundle_json);
  if (planner === null || completion.research_requirement_id !== planner.requirement.requirement_id) {
    throw new HttpError(409, "research_requirement_mismatch");
  }
  if (!sameStringSet(completion.research_source_ids, planner.source_bundle.source_ids)) {
    throw new HttpError(409, "research_source_bundle_mismatch");
  }
  const run = await runById(env.DB, completion.run_id);
  if (run === null || run.status !== "published") {
    throw new HttpError(409, "research_run_not_published");
  }
  if (run.workflow_run_id !== completion.workflow_run_id || run.commit_sha !== completion.commit_sha) {
    throw new HttpError(409, "research_run_identity_conflict");
  }
  const observedSources = await env.DB.prepare(
    `SELECT DISTINCT raw_items.source_id
     FROM run_items JOIN raw_items ON raw_items.item_id = run_items.item_id
     WHERE run_items.run_id = ?`,
  ).bind(completion.run_id).all<{ source_id: string }>();
  const approvedSources = new Set(completion.research_source_ids);
  if (observedSources.results.some((item) => !approvedSources.has(item.source_id))) {
    throw new HttpError(409, "research_run_source_unapproved");
  }
  const plan = await planForRun(env.DB, run, completion.plan_id);
  if (plan === null || plan.alignment_id !== completion.alignment_id) {
    throw new HttpError(409, "research_plan_identity_conflict");
  }
  const alignment = await alignmentForRun(env.DB, run, plan, completion.alignment_id);
  if (alignment === null) throw new HttpError(409, "research_alignment_identity_conflict");
  return await executeResearchJob(env, completion.job_id, dependencies, now, {
    runId: completion.run_id,
    planId: completion.plan_id,
    alignmentId: completion.alignment_id,
  });
}

/**
 * Close the failure path for Actions-backed jobs. Without this callback an
 * admission denial or workflow error leaves a dispatched job in queued state
 * forever, so MCP clients cannot distinguish "still running" from "dead".
 * The callback is authenticated by the same GitHub OIDC run/commit pair and
 * must carry the frozen target and planner requirement from submission.
 */
export async function failResearchJob(
  env: Env,
  payload: unknown,
  auth: AuthContext,
  now: Date,
): Promise<ResearchJobStatus> {
  const failure = validateFailure(payload);
  if (failure.workflow_run_id !== auth.workflowRunId) {
    throw new HttpError(403, "workflow_run_mismatch");
  }
  if (failure.commit_sha !== auth.commitSha) {
    throw new HttpError(403, "commit_sha_mismatch");
  }
  const row = await findJob(env.DB, failure.job_id);
  if (row === null) throw new HttpError(404, "research_job_not_found");
  const request = requestFromRow(row);
  if (request.requirements.source_strategy !== "actions") {
    throw new HttpError(409, "research_job_strategy_conflict");
  }
  if (canonicalJson(failure.research_target) !== canonicalJson(request.target)) {
    throw new HttpError(409, "research_target_mismatch");
  }
  if (row.dispatch_id === null) throw new HttpError(409, "research_dispatch_not_recorded");
  const planner = parseOptionalPlanner(row.requirement_json, row.source_bundle_json);
  if (planner === null || failure.research_requirement_id !== planner.requirement.requirement_id) {
    throw new HttpError(409, "research_requirement_mismatch");
  }
  if (row.status === "completed" || row.status === "partial") return statusFromRow(row);
  if ((row.status === "failed" || row.status === "blocked") && row.error_code === failure.error_code) {
    return statusFromRow(row);
  }

  const status: ResearchJobStatus["status"] = failure.error_code === "actions_admission_denied"
    ? "blocked"
    : "failed";
  const happenedAt = now.toISOString();
  const payloadHash = await sha256Hex(canonicalJson(failure));
  const audit = await buildAuditStatement(
    env.DB,
    failure.job_id,
    status === "blocked" ? "research_job_blocked" : "research_job_failed",
    payloadHash,
    happenedAt,
  );
  await env.DB.batch([
    env.DB.prepare(
      `UPDATE research_jobs
       SET status = ?, error_code = ?, updated_at = ?, completed_at = NULL
       WHERE job_id = ?`,
    ).bind(status, failure.error_code, happenedAt, failure.job_id),
    audit,
  ]);
  return await readResearchJob(env, failure.job_id);
}

export async function readResearchPack(env: Env, jobId: string): Promise<ResearchPack> {
  const row = await env.DB.prepare(
    "SELECT object_key FROM research_packs WHERE job_id = ?",
  ).bind(jobId).first<{ object_key: string }>();
  if (row === null) throw new HttpError(409, "research_pack_not_ready");
  const pack = await readPrivateJson(env.RAW_OBJECTS, row.object_key, "research_pack");
  return validateResearchPack(pack);
}

export async function readResearchReport(env: Env, jobId: string): Promise<ResearchReport[]> {
  const pack = await readResearchPack(env, jobId);
  return pack.reports;
}

export async function readEvidenceAppendix(
  env: Env,
  jobId: string,
): Promise<{ schema_version: 1; job_id: string; as_of: string; evidence: ResearchPack["evidence"] }> {
  const pack = await readResearchPack(env, jobId);
  return {
    schema_version: 1,
    job_id: jobId,
    as_of: pack.as_of,
    evidence: pack.evidence,
  };
}

async function buildResearchPack(
  env: Env,
  row: JobRow,
  request: ResearchJobRequest,
  run: RunRow,
  plan: PlanRow,
  alignment: AlignmentRow,
  reportIds: string[],
  now: Date,
): Promise<Omit<ResearchPack, "pack_id">> {
  const snapshotRow = await env.DB.prepare(
    "SELECT object_key FROM topic_snapshots WHERE snapshot_id = ? AND run_id = ?",
  ).bind(run.snapshot_id, run.run_id).first<SnapshotRow>();
  if (snapshotRow === null) throw new HttpError(503, "topic_snapshot_missing");
  const topicSnapshot = validateTopicSnapshot(
    await readPrivateJson(env.RAW_OBJECTS, snapshotRow.object_key, "topic_snapshot"),
  );
  const alignmentPayload = asRecord(await readPrivateJson(env.RAW_OBJECTS, alignment.object_key, "market_alignment"));
  const marketRow = await env.DB.prepare(
    "SELECT snapshot_id, object_key FROM market_snapshots WHERE snapshot_id = ? AND run_id = ?",
  ).bind(alignment.market_snapshot_id, run.run_id).first<MarketRow>();
  if (marketRow === null) throw new HttpError(503, "market_snapshot_missing");
  const marketPayload = asRecord(await readPrivateJson(env.RAW_OBJECTS, marketRow.object_key, "market_snapshot"));
  const validatedAlignment = validateMarketAlignmentEnvelope({
    schema_version: 1,
    operation: "upsert_market_alignment",
    run_id: run.run_id,
    workflow_run_id: run.workflow_run_id,
    commit_sha: run.commit_sha,
    market_snapshot: marketPayload,
    alignment: alignmentPayload,
  });
  const currentRawItems = await env.DB.prepare(
    `SELECT raw_items.item_id, raw_items.source_id, raw_items.canonical_url,
            raw_items.title, raw_items.summary, raw_items.published_at,
            raw_items.content_sha256, raw_items.created_at
     FROM raw_items JOIN run_items ON run_items.item_id = raw_items.item_id
     WHERE run_items.run_id = ? ORDER BY raw_items.published_at DESC`,
  ).bind(run.run_id).all<RawItemRow>();
  const planner = parseOptionalPlanner(row.requirement_json, row.source_bundle_json);
  const approvedSourceIds = planner === null ? null : new Set(planner.source_bundle.source_ids);
  const lookback = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString();
  const recentRawItems = await env.DB.prepare(
    `SELECT item_id, source_id, canonical_url, title, summary, published_at,
            content_sha256, created_at
     FROM raw_items WHERE created_at >= ? ORDER BY published_at DESC LIMIT 120`,
  ).bind(lookback).all<RawItemRow>();
  const filteredRecent = recentRawItems.results.filter((item) =>
    approvedSourceIds === null || approvedSourceIds.has(item.source_id),
  );
  const rawItems = mergeResearchEvidenceRows(
    currentRawItems.results,
    filteredRecent,
    // Never drop current-run rows: a generated report may cite an item that
    // sorts below the initial display window and must remain auditable.
    Math.max(request.requirements.max_sources, currentRawItems.results.length, 1),
  );
  const collectionScope = planner?.source_bundle.collection_scope ?? request.requirements.collection_scope ?? "legacy_smoke";
  const maxContextItems = request.requirements.max_context_items ?? (collectionScope === "full_catalog" ? 120 : request.requirements.max_sources);
  const snapshotScope = (topicSnapshot as unknown as Record<string, any>).target_scope;
  const snapshotScopedIds = Array.isArray(snapshotScope?.input_item_ids)
    ? (snapshotScope.input_item_ids as unknown[]).filter((id): id is string => typeof id === "string")
    : [];
  const fallbackScopedItems = rawItems.filter((item) => targetMatchesEvidence(item, request.target));
  const scopedIds = new Set<string>(snapshotScopedIds.length > 0
    ? snapshotScopedIds
    : fallbackScopedItems.map((item) => item.item_id));
  const targetRelevantItems = rawItems.filter((item) => scopedIds.has(item.item_id));
  const initialItems = targetRelevantItems.slice(0, maxContextItems);
  const reports: ResearchReport[] = [];
  for (const reportId of reportIds) {
    const reportRow = await env.DB.prepare(
      "SELECT object_key FROM research_reports WHERE report_id = ? AND run_id = ?",
    ).bind(reportId, run.run_id).first<{ object_key: string }>();
    if (reportRow === null) throw new HttpError(503, "research_report_missing", [reportId]);
    const report = validateResearchReportEnvelope({
      schema_version: 1,
      operation: "upsert_research_report",
      run_id: run.run_id,
      workflow_run_id: run.workflow_run_id,
      commit_sha: run.commit_sha,
      report: await readPrivateJson(env.RAW_OBJECTS, reportRow.object_key, "research_report"),
    }).report;
    reports.push(report);
  }
  // A report may cite an item that falls outside the first max_sources rows
  // after sorting. Preserve every cited item in the Pack; otherwise the
  // evidence graph would contain dangling edges and downstream readers could
  // not audit a claim back to its source.
  const includedItemIds = new Set(
    collectionScope === "full_catalog"
      ? targetRelevantItems.map((item) => item.item_id)
      : initialItems.map((item) => item.item_id),
  );
  for (const report of reports) {
    report.evidence_ids.forEach((itemId) => includedItemIds.add(itemId));
    for (const claim of [...report.bull_case, ...report.bear_case, ...report.risk_view,
      ...(report.catalysts ?? []), ...(report.failure_conditions ?? [])]) {
      claim.evidence_ids.forEach((itemId) => includedItemIds.add(itemId));
    }
    for (const note of report.data_gaps ?? []) {
      note.evidence_ids.forEach((itemId) => includedItemIds.add(itemId));
    }
  }
  const limitedItems = rawItems.filter((item) => includedItemIds.has(item.item_id));
  const sourceIds = new Set(limitedItems.map((item) => item.source_id));
  const targetSourceIds = new Set(targetRelevantItems.map((item) => item.source_id));
  const failedSources = Array.isArray(topicSnapshot.failed_sources)
    ? topicSnapshot.failed_sources.filter((source): source is string => typeof source === "string")
    : [];
  const snapshotTime = Date.parse(topicSnapshot.as_of);
  const stale = Number.isFinite(snapshotTime) && now.getTime() - snapshotTime > 86_400_000;
  const expectedSourceGroups = planner?.source_bundle.expected_source_group_count ?? request.requirements.max_sources;
  // Coverage measures source collection health, not how many sources happened
  // to publish a target headline.  A BTC pack with no BTC headline must not
  // be misreported as a failed crawl.
  const coverageRatio = expectedSourceGroups === 0
    ? 0
    : Math.min(1, Math.max(0, (expectedSourceGroups - failedSources.length) / expectedSourceGroups));
  const harness = await buildInvestmentHarnessArtifacts({
    target: request.target as unknown as Record<string, unknown>,
    topics: topicSnapshot.topics,
    input_item_count: targetRelevantItems.length,
    input_source_count: targetSourceIds.size,
    snapshot_id: run.snapshot_id,
    generated_at: topicSnapshot.as_of,
    collection_scope: collectionScope,
  });
  const evidenceGraph = buildEvidenceGraph(reports);
  const financialDepth = enrichFinancialDepth(
    validatedAlignment.market_snapshot.financial_depth ?? null,
    request.target,
    limitedItems,
  );
  const targetTopicId = request.target.kind === "crypto"
    ? "digital_assets"
    : request.target.kind === "equity"
      ? "equities_earnings"
      : request.target.kind === "etf"
        ? "personal_finance"
        : undefined;
  const scopedTopics = targetTopicId === undefined
    ? topicSnapshot.topics
    : topicSnapshot.topics
      .filter((topic) => topic.topic_id === targetTopicId)
      .map((topic) => {
        const scopedEvidenceIds = topic.evidence_ids.filter((itemId) => scopedIds.has(itemId));
        const scopedSourceCount = new Set(
          targetRelevantItems.filter((item) => scopedEvidenceIds.includes(item.item_id)).map((item) => item.source_id),
        ).size;
        return {
          ...topic,
          evidence_ids: scopedEvidenceIds.length > 0 ? scopedEvidenceIds : topic.evidence_ids.slice(0, 1),
          item_count: scopedEvidenceIds.length > 0 ? scopedEvidenceIds.length : topic.item_count,
          source_count: scopedSourceCount > 0 ? scopedSourceCount : topic.source_count,
        };
      });
  return {
    schema_version: 1,
    job_id: row.job_id,
    target: request.target,
    question: request.requirements.question,
    as_of: topicSnapshot.as_of,
    source_bundle: {
      run_id: run.run_id,
      snapshot_id: run.snapshot_id,
      source_count: targetSourceIds.size,
      item_ids: limitedItems.map((item) => item.item_id),
      source_manifest_hash: run.source_manifest_hash,
      collection_scope: collectionScope,
      collection_source_group_count: expectedSourceGroups,
      endpoint_attempt_count: planner?.source_bundle.expected_endpoint_count ?? sourceIds.size,
      normalized_item_count: rawItems.length,
      target_relevant_item_count: targetRelevantItems.length,
      model_context_item_count: initialItems.length,
      evidence_appendix_item_count: limitedItems.length,
      target_relevant_source_group_count: targetSourceIds.size,
    },
    target_scope: topicSnapshot.target_scope ?? {
      policy: "worker_target_identity_or_asset_family_v1",
      target: request.target,
      input_item_count: rawItems.length,
      relevant_item_count: targetRelevantItems.length,
      relevant_source_group_count: targetSourceIds.size,
      input_item_ids: [...scopedIds],
    },
    ...(planner === null ? {} : {
      requirement: planner.requirement,
      source_bundle_plan: planner.source_bundle,
    }),
    topics: scopedTopics,
    market: request.requirements.include_market_data
      ? { ...validatedAlignment.market_snapshot, financial_depth: financialDepth ?? undefined }
      : null,
    financial_depth: request.requirements.include_market_data ? financialDepth : null,
    reports,
    evidence_graph: evidenceGraph,
    ...harness,
    evidence: limitedItems.map((item) => ({
      evidence_id: item.item_id,
      source_id: item.source_id,
      canonical_url: item.canonical_url,
      content_sha256: item.content_sha256,
      title: item.title,
      summary: item.summary,
      published_at: item.published_at,
    })),
    quality: {
      partial: topicSnapshot.partial || failedSources.length > 0,
      stale,
      failed_sources: failedSources,
      coverage_ratio: coverageRatio,
      collection_source_group_count: expectedSourceGroups,
      endpoint_attempt_count: planner?.source_bundle.expected_endpoint_count ?? sourceIds.size,
      normalized_item_count: rawItems.length,
      target_relevant_item_count: targetRelevantItems.length,
      model_context_item_count: initialItems.length,
      evidence_appendix_item_count: limitedItems.length,
      target_relevant_source_group_count: targetSourceIds.size,
    },
    producer: {
      pipeline_version: PIPELINE_VERSION,
      model: reports.map((report) => report.model).find((model): model is string => typeof model === "string") ?? "unknown",
      audit_event_ids: [],
    },
  };
}

export function buildEvidenceGraph(reports: ResearchReport[]): ResearchEvidenceGraph {
  const claims: ResearchEvidenceGraphClaim[] = [];
  const append = (
    report: ResearchReport,
    category: ResearchEvidenceGraphClaim["category"],
    entries: Array<{ text: string; confidence?: number; evidence_ids: string[] }>,
  ): void => {
    entries.forEach((entry, index) => {
      claims.push({
        claim_id: `${report.report_id}:${category}:${index}`,
        report_id: report.report_id,
        topic_id: report.topic_id,
        category,
        text: entry.text,
        ...(entry.confidence === undefined ? {} : { confidence: entry.confidence }),
        evidence_ids: [...new Set(entry.evidence_ids)],
      });
    });
  };
  for (const report of reports) {
    append(report, "bull_case", report.bull_case);
    append(report, "bear_case", report.bear_case);
    append(report, "risk_view", report.risk_view);
    append(report, "catalyst", report.catalysts ?? []);
    append(report, "failure_condition", report.failure_conditions ?? []);
    append(report, "data_gap", report.data_gaps ?? []);
  }
  return { schema_version: 1, claims };
}

export function targetMatchesEvidence(item: RawItemRow, target: ResearchJobRequest["target"]): boolean {
  return matchesTargetEvidence(item, target);
}

function enrichFinancialDepth(
  depth: FinancialDepth | null | undefined,
  target: ResearchJobRequest["target"],
  evidence: RawItemRow[],
): FinancialDepth | null {
  if (depth === null || depth === undefined) return null;
  if (depth.market_drivers !== undefined) return depth;
  const timeSeries = isRecord(depth.time_series) ? depth.time_series : {};
  const returns = isRecord(timeSeries.returns) ? timeSeries.returns : {};
  const candidates: Array<Record<string, unknown>> = [];
  const seen = new Set<string>();
  const terms: Array<[string, string]> = [
    ["etf", "ETF flows"], ["inflow", "Fund inflows"], ["outflow", "Fund outflows"],
    ["regulation", "Regulation"], ["rate", "Rates and liquidity"],
    ["liquidation", "Liquidations"], ["leverage", "Leverage"],
    ["approval", "Approval/catalyst"], ["hack", "Security event"],
  ];
  for (const item of evidence) {
    const text = `${item.title} ${item.summary}`.toLowerCase();
    const matched = terms.find(([term]) => text.includes(term));
    const title = item.title.trim();
    if (!matched || !title || /\bcapture\b|_html\s+capture/i.test(title)) continue;
    const key = title.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().slice(0, 120);
    if (seen.has(key)) continue;
    seen.add(key);
    candidates.push({
      event_id: key,
      label: matched[1],
      title: title.slice(0, 300),
      evidence_ids: [item.item_id],
      source_count: 1,
      causal_status: "unresolved",
    });
  }
  const normalizedReturns = {
    ...returns,
    ...deriveObservedReturns(timeSeries.points),
  };
  return {
    ...depth,
    market_drivers: {
      schema_version: 1,
      status: "unresolved",
      target,
      price_and_returns: { returns: normalizedReturns },
      provider_status: {
        volume: { status: "unavailable", reason: "provider_not_configured" },
        etf_flows: { status: "unavailable", reason: "provider_not_configured" },
        derivatives: { status: "unavailable", reason: "provider_not_configured" },
        on_chain: { status: "unavailable", reason: "provider_not_configured" },
      },
      news_driver_candidates: candidates.slice(0, 12),
      limitations: [
        "headline matches are candidate drivers, not causal attribution",
        "provider gaps prevent volume, flows, derivatives, and on-chain confirmation",
      ],
    },
  } as FinancialDepth;
}

function deriveObservedReturns(points: unknown): Record<string, number> {
  if (!Array.isArray(points)) return {};
  const values = points
    .map((point) => isRecord(point) && typeof point.value === "number" ? point.value : null)
    .filter((value): value is number => value !== null && Number.isFinite(value) && value > 0);
  const result: Record<string, number> = {};
  for (const days of [1, 3, 7, 30, 90, 365]) {
    const index = values.length - 1 - days;
    if (index >= 0 && values[index] > 0 && values.length > 1) {
      result[`${days}d_observed_pct`] = Number((((values[values.length - 1] / values[index]) - 1) * 100).toFixed(6));
    }
  }
  return result;
}

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function latestPublishedRun(db: D1Database): Promise<RunRow | null> {
  return await db.prepare(
    `SELECT run_id, workflow_run_id, commit_sha, snapshot_id, source_manifest_hash,
            status, collected_at, published_at
     FROM runs WHERE status = 'published'
     ORDER BY published_at DESC, collected_at DESC LIMIT 1`,
  ).first<RunRow>();
}

async function runById(db: D1Database, runId: string): Promise<RunRow | null> {
  return await db.prepare(
    `SELECT run_id, workflow_run_id, commit_sha, snapshot_id, source_manifest_hash,
            status, collected_at, published_at
     FROM runs WHERE run_id = ?`,
  ).bind(runId).first<RunRow>();
}

async function planForRun(
  db: D1Database,
  run: RunRow,
  planId?: string,
): Promise<PlanRow | null> {
  if (planId) {
    return await db.prepare(
      `SELECT plan_id, alignment_id, object_key
       FROM tradingagents_plans WHERE run_id = ? AND plan_id = ?`,
    ).bind(run.run_id, planId).first<PlanRow>();
  }
  return await db.prepare(
    `SELECT plan_id, alignment_id, object_key
     FROM tradingagents_plans WHERE run_id = ? ORDER BY created_at DESC LIMIT 1`,
  ).bind(run.run_id).first<PlanRow>();
}

async function alignmentForRun(
  db: D1Database,
  run: RunRow,
  plan: PlanRow,
  alignmentId?: string,
): Promise<AlignmentRow | null> {
  return await db.prepare(
    `SELECT alignment_id, topic_snapshot_id, market_snapshot_id, object_key
     FROM topic_market_alignments
     WHERE alignment_id = ? AND run_id = ? AND topic_snapshot_id = ?`,
  ).bind(alignmentId ?? plan.alignment_id, run.run_id, run.snapshot_id).first<AlignmentRow>();
}

async function findJob(db: D1Database, jobId: string): Promise<JobRow | null> {
  return await db.prepare(
    `SELECT job_id, request_id, idempotency_key, subject, target_json,
            requirements_json, request_sha256, status, run_id, pack_id,
            report_count, error_code, created_at, updated_at, completed_at,
            requirement_json, source_bundle_json, planner_version, dispatch_id
     FROM research_jobs WHERE job_id = ?`,
  ).bind(jobId).first<JobRow>();
}

async function findJobByRequestId(db: D1Database, requestId: string): Promise<JobRow | null> {
  return await db.prepare(
    `SELECT job_id, request_id, idempotency_key, subject, target_json,
            requirements_json, request_sha256, status, run_id, pack_id,
            report_count, error_code, created_at, updated_at, completed_at,
            requirement_json, source_bundle_json, planner_version, dispatch_id
     FROM research_jobs WHERE request_id = ?`,
  ).bind(requestId).first<JobRow>();
}

async function recoverStuckJob(db: D1Database, row: JobRow | null, now?: Date): Promise<JobRow | null> {
  if (row === null || row.status !== "running") return row;
  const updatedAt = Date.parse(row.updated_at);
  if (!now || !Number.isFinite(updatedAt) || now.getTime() - updatedAt <= RUNNING_JOB_TIMEOUT_MS) return row;
  const recoveredAt = now.toISOString();
  await db.prepare(
    `UPDATE research_jobs
     SET status = 'failed', error_code = 'research_execution_timeout', updated_at = ?, completed_at = NULL
     WHERE job_id = ? AND status = 'running' AND updated_at = ?`,
  ).bind(recoveredAt, row.job_id, row.updated_at).run();
  return await findJob(db, row.job_id);
}

async function assertJobRunning(db: D1Database, jobId: string): Promise<void> {
  const row = await findJob(db, jobId);
  if (row === null) throw new HttpError(404, "research_job_not_found");
  if (row.status !== "running") throw new HttpError(409, "research_job_not_running");
}

async function setRunning(db: D1Database, jobId: string, now: Date): Promise<void> {
  await db.prepare(
    "UPDATE research_jobs SET status = 'running', updated_at = ?, error_code = NULL WHERE job_id = ? AND status = 'queued'",
  ).bind(now.toISOString(), jobId).run();
}

async function updateJob(
  env: Env,
  row: JobRow,
  status: ResearchJobStatus["status"],
  now: Date,
  errorCode: string,
): Promise<ResearchJobStatus> {
  const updatedAt = now.toISOString();
  await env.DB.prepare(
    "UPDATE research_jobs SET status = ?, error_code = ?, updated_at = ? WHERE job_id = ?",
  ).bind(status, errorCode, updatedAt, row.job_id).run();
  return await readResearchJob(env, row.job_id);
}

function statusFromRow(row: JobRow): ResearchJobStatus {
  const metadata = statusMetadata(row);
  const status: ResearchJobStatus = {
    schema_version: 1,
    request_id: row.request_id,
    job_id: row.job_id,
    status: row.status,
    target: parseJson<ResearchJobRequest["target"]>(row.target_json, "target"),
    requirements: parseJson<ResearchJobRequest["requirements"]>(row.requirements_json, "requirements"),
    run_id: row.run_id,
    pack_id: row.pack_id,
    report_count: Number(row.report_count),
    error_code: row.error_code,
    created_at: row.created_at,
    updated_at: row.updated_at,
    completed_at: row.completed_at,
    ...metadata,
    planner: parseOptionalPlanner(row.requirement_json, row.source_bundle_json),
  };
  validateResearchJobStatus(status);
  return status;
}

function statusMetadata(row: JobRow): Pick<ResearchJobStatus, "stage" | "progress" | "retryable" | "next_action"> {
  if (row.status === "queued") {
    return row.dispatch_id === null
      ? { stage: "queued", progress: 0, retryable: true, next_action: "poll_job_status" }
      : { stage: "dispatching", progress: 0, retryable: true, next_action: "wait_for_actions" };
  }
  if (row.status === "running") {
    return { stage: "processing", progress: 0.5, retryable: true, next_action: "poll_job_status" };
  }
  if (row.status === "completed" || row.status === "partial") {
    return { stage: "published", progress: 1, retryable: false, next_action: null };
  }
  if (row.status === "stale") {
    return { stage: "stale", progress: null, retryable: true, next_action: "request_refresh" };
  }
  if (row.status === "blocked") {
    return {
      stage: "blocked",
      progress: null,
      retryable: !["document_engine_required", "source_budget_too_low", "market_target_not_supported"].includes(row.error_code ?? ""),
      next_action: blockedNextAction(row.error_code),
    };
  }
  return {
    stage: "failed",
    progress: null,
    retryable: true,
    next_action: "retry_research_job",
  };
}

function blockedNextAction(errorCode: string | null): ResearchJobNextAction {
  if (errorCode === "document_engine_required") return "provide_document_engine";
  if (errorCode === "source_budget_too_low") return "increase_source_budget";
  if (errorCode === "actions_dispatch_not_configured" || errorCode === "actions_dispatch_failed") {
    return "configure_actions_dispatch_and_retry";
  }
  if (errorCode === "actions_admission_denied") return "retry_research_job";
  if (errorCode === "research_inputs_unavailable" || errorCode === "research_plan_unavailable" || errorCode === "research_alignment_unavailable") {
    return "retry_research_job";
  }
  if (errorCode === "research_snapshot_refresh_required") return "request_refresh";
  return "review_error";
}

function isRunStaleForLatestPublished(
  request: ResearchJobRequest,
  run: RunRow,
  now: Date,
): boolean {
  const collectedAt = Date.parse(run.collected_at);
  if (!Number.isFinite(collectedAt)) return true;
  const freshnessMs = request.requirements.include_market_data
    ? LATEST_PUBLISHED_MARKET_FRESHNESS_MS
    : LATEST_PUBLISHED_DEFAULT_FRESHNESS_MS;
  return now.getTime() - collectedAt > freshnessMs;
}

function requestFromRow(row: JobRow): ResearchJobRequest {
  return {
    schema_version: 1,
    operation: "submit_research_job",
    idempotency_key: row.idempotency_key,
    target: parseJson(row.target_json, "target"),
    requirements: parseJson(row.requirements_json, "requirements"),
  };
}

function validateRequest(payload: unknown): ResearchJobRequest {
  try {
    const request = validateResearchJobRequest(payload);
    return {
      ...request,
      target: {
        ...request.target,
        ...(request.target.symbol ? { symbol: request.target.symbol.toUpperCase() } : {}),
      },
    };
  } catch (error) {
    const details = error instanceof Error ? [error.message] : [];
    throw new HttpError(422, "invalid_payload", details);
  }
}

function validateCompletion(payload: unknown): ResearchJobCompletionRequest {
  try {
    return validateResearchJobCompletion(payload);
  } catch (error) {
    const details = error instanceof Error ? [error.message] : [];
    throw new HttpError(422, "invalid_payload", details);
  }
}

function validateFailure(payload: unknown): ResearchJobFailureRequest {
  try {
    return validateResearchJobFailure(payload);
  } catch (error) {
    const details = error instanceof Error ? [error.message] : [];
    throw new HttpError(422, "invalid_payload", details);
  }
}

function parseJson<T>(serialized: string, field: string): T {
  try {
    return JSON.parse(serialized) as T;
  } catch {
    throw new HttpError(503, "research_job_corrupt", [field]);
  }
}

function sameStringSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const rightSet = new Set(right);
  return new Set(left).size === rightSet.size && left.every((value) => rightSet.has(value));
}

function parseOptionalPlanner(
  requirementJson: string,
  sourceBundleJson: string,
): ResearchJobStatus["planner"] {
  if (requirementJson === "{}" || sourceBundleJson === "{}") return null;
  return {
    requirement: parseJson<ResearchRequirement>(requirementJson, "requirement"),
    source_bundle: parseJson<SourceBundleManifest>(sourceBundleJson, "source_bundle"),
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new HttpError(503, "research_artifact_invalid");
  }
  return value as Record<string, unknown>;
}

function compactTimestamp(now: Date): string {
  return now.toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
}
