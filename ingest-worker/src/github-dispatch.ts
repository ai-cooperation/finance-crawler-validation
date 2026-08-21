import { HttpError } from "./storage";
import type { ResearchRequirement, SourceBundleManifest } from "./research-planner";


const GITHUB_REPOSITORY = "ai-cooperation/finance-crawler-validation";
const GITHUB_WORKFLOW = "topic-radar.yml";
const GITHUB_REF = "main";

export interface WorkflowDispatchJob {
  job_id: string;
  source_ids: string[];
  target: Record<string, unknown>;
  requirement: ResearchRequirement;
  source_bundle: SourceBundleManifest;
}

export interface WorkflowDispatchRequest {
  ref: string;
  inputs: {
    research_job_id: string;
    research_source_ids: string;
    research_requirement_id: string;
    research_target: string;
    research_question: string;
    research_include_market_data: string;
    research_collection_scope: string;
  };
}

export function buildWorkflowDispatchRequest(job: {
  job_id: string;
  source_ids: string[];
  target: Record<string, unknown>;
  question?: string;
  requirement_id: string;
  include_market_data?: boolean;
  collection_scope?: "full_catalog" | "legacy_smoke";
}): WorkflowDispatchRequest {
  const uniqueSourceIds = [...new Set(job.source_ids)];
  if (!/^research_[a-z0-9_]+$/.test(job.job_id)) {
    throw new HttpError(422, "invalid_research_job_id");
  }
  if (uniqueSourceIds.length < 1 || uniqueSourceIds.length > 256) {
    throw new HttpError(422, "invalid_research_source_ids");
  }
  if (!/^req_[a-z0-9_:-]+$/.test(job.requirement_id)) {
    throw new HttpError(422, "invalid_research_requirement_id");
  }
  return {
    ref: GITHUB_REF,
    inputs: {
      research_job_id: job.job_id,
      research_source_ids: JSON.stringify(uniqueSourceIds),
      research_requirement_id: job.requirement_id,
      research_target: JSON.stringify(job.target),
      research_question: job.question ?? "",
    research_include_market_data: String(job.include_market_data ?? true),
      research_collection_scope: job.collection_scope ?? "full_catalog",
    },
  };
}

export async function dispatchResearchWorkflow(
  env: Env,
  job: WorkflowDispatchJob,
  fetchImpl: typeof fetch = fetch,
): Promise<{ dispatch_id: string }> {
  const token = (env as Env & { GITHUB_DISPATCH_TOKEN?: string }).GITHUB_DISPATCH_TOKEN;
  if (!token) throw new HttpError(503, "actions_dispatch_not_configured");
  const request = buildWorkflowDispatchRequest({
    job_id: job.job_id,
    source_ids: job.source_ids,
    target: job.target,
    question: job.requirement.question,
    requirement_id: job.requirement.requirement_id,
    include_market_data: job.requirement.include_market_data,
    collection_scope: job.requirement.collection_scope,
  });
  let response: Response;
  try {
    response = await fetchImpl(
      `https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/workflows/${GITHUB_WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          "Content-Type": "application/json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "finance-research-report-generator",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(request),
      },
    );
  } catch (error) {
    console.error(JSON.stringify({
      event: "actions_dispatch_transport_failed",
      job_id: job.job_id,
      error: error instanceof Error ? error.message : String(error),
    }));
    throw new HttpError(503, "actions_dispatch_failed");
  }
  if (response.status !== 204) {
    console.error(JSON.stringify({
      event: "actions_dispatch_rejected",
      job_id: job.job_id,
      status: response.status,
    }));
    throw new HttpError(response.status === 401 || response.status === 403 ? 503 : 502, "actions_dispatch_failed");
  }
  return { dispatch_id: `workflow:${GITHUB_WORKFLOW}:${job.job_id}` };
}
