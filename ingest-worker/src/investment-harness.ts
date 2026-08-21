import { canonicalJson } from "./canonical-json";

export interface InvestmentSignal {
  signal_id: string;
  signal_type: "novelty" | "divergence" | "risk";
  status: "active" | "insufficient_data";
  score: number;
  topic_id: string;
  observed_at: string;
  expires_at: string;
  evidence_ids: string[];
  ruleset: string;
}

export interface InvestmentSignalSnapshot {
  schema_version: 1;
  snapshot_id: string;
  target: Record<string, unknown>;
  generated_at: string;
  partial: boolean;
  signals: InvestmentSignal[];
  input_item_count: number;
  input_source_count: number;
}

export interface InvestmentActionTask {
  schema_version: 1;
  action_id: string;
  action_type: "build_research_pack" | "open_review";
  status: "queued" | "completed" | "blocked";
  trigger_signal_ids: string[];
  policy_version: "investment-research-action@1";
  idempotency_key: string;
  side_effect_level: "internal_write";
  tool_allowlist: string[];
}

export interface InvestmentActionReceipt {
  schema_version: 1;
  action_id: string;
  status: "completed" | "blocked";
  started_at: string;
  finished_at: string;
  input_sha256: string;
  output_sha256: string;
  tool: string;
  side_effect_level: "internal_write";
  error_class: string | null;
  audit_ref: string;
}

export interface InvestmentHarnessArtifacts {
  harness: {
    pack_id: "investment-research@1";
    signal_pack_id: "investment-signal@1";
    action_pack_id: "investment-research-action@1";
    collection_scope: "full_catalog" | "legacy_smoke";
  };
  signals: InvestmentSignalSnapshot;
  action_tasks: InvestmentActionTask[];
  action_receipts: InvestmentActionReceipt[];
}

export async function buildInvestmentHarnessArtifacts(input: {
  target: Record<string, unknown>;
  topics: Array<{
    topic_id: string;
    score: number;
    item_count: number;
    source_count: number;
    evidence_ids: string[];
    divergence?: { direction?: string; magnitude?: number | null };
  }>;
  input_item_count: number;
  input_source_count: number;
  snapshot_id: string;
  generated_at: string;
  collection_scope: "full_catalog" | "legacy_smoke";
}): Promise<InvestmentHarnessArtifacts> {
  const expiresAt = new Date(Date.parse(input.generated_at) + 24 * 60 * 60 * 1000).toISOString();
  const signals: InvestmentSignal[] = [];
  for (const topic of input.topics) {
    const evidence = [...new Set(topic.evidence_ids)];
    signals.push({
      signal_id: `sig_${input.snapshot_id}_${topic.topic_id}_novelty`,
      signal_type: "novelty",
      status: topic.item_count >= 2 && topic.source_count >= 2 ? "active" : "insufficient_data",
      score: round(Math.min(1, topic.item_count / 10 + topic.source_count / 20)),
      topic_id: topic.topic_id,
      observed_at: input.generated_at,
      expires_at: expiresAt,
      evidence_ids: evidence,
      ruleset: "investment-signal@1:novelty-v1",
    });
    const divergence = topic.divergence?.direction ?? "insufficient_data";
    signals.push({
      signal_id: `sig_${input.snapshot_id}_${topic.topic_id}_divergence`,
      signal_type: "divergence",
      status: divergence === "insufficient_data" ? "insufficient_data" : "active",
      score: round(topic.divergence?.magnitude ?? 0),
      topic_id: topic.topic_id,
      observed_at: input.generated_at,
      expires_at: expiresAt,
      evidence_ids: evidence,
      ruleset: `investment-signal@1:divergence-v1:${divergence}`,
    });
    if (topic.topic_id === "market_risk" || topic.topic_id === "monetary_policy") {
      signals.push({
        signal_id: `sig_${input.snapshot_id}_${topic.topic_id}_risk`,
        signal_type: "risk",
        status: evidence.length >= 2 ? "active" : "insufficient_data",
        score: round(Math.min(1, topic.score / 10)),
        topic_id: topic.topic_id,
        observed_at: input.generated_at,
        expires_at: expiresAt,
        evidence_ids: evidence,
        ruleset: "investment-signal@1:risk-v1",
      });
    }
  }
  const active = signals.filter((signal) => signal.status === "active");
  const snapshot: InvestmentSignalSnapshot = {
    schema_version: 1,
    snapshot_id: `signals_${input.snapshot_id}`,
    target: input.target,
    generated_at: input.generated_at,
    partial: active.length === 0 || signals.some((signal) => signal.status === "insufficient_data"),
    signals,
    input_item_count: input.input_item_count,
    input_source_count: input.input_source_count,
  };
  const triggerIds = active.map((signal) => signal.signal_id);
  const idempotency = `investment-research:${input.snapshot_id}`;
  const tasks: InvestmentActionTask[] = [
    {
      schema_version: 1,
      action_id: `act_${input.snapshot_id}_build_pack`,
      action_type: "build_research_pack",
      status: active.length > 0 ? "completed" : "blocked",
      trigger_signal_ids: triggerIds,
      policy_version: "investment-research-action@1",
      idempotency_key: idempotency,
      side_effect_level: "internal_write",
      tool_allowlist: ["read_d1", "read_r2", "write_private_r2", "write_d1"],
    },
    {
      schema_version: 1,
      action_id: `act_${input.snapshot_id}_open_review`,
      action_type: "open_review",
      status: snapshot.partial ? "queued" : "completed",
      trigger_signal_ids: triggerIds,
      policy_version: "investment-research-action@1",
      idempotency_key: `${idempotency}:review`,
      side_effect_level: "internal_write",
      tool_allowlist: ["write_d1"],
    },
  ];
  const action_receipts = await Promise.all(tasks.map(async (task) => ({
    schema_version: 1 as const,
    action_id: task.action_id,
    status: task.status === "blocked" ? "blocked" as const : "completed" as const,
    started_at: input.generated_at,
    finished_at: input.generated_at,
    input_sha256: await sha256(canonicalJson({ snapshot, task })),
    output_sha256: await sha256(canonicalJson({ task, signal_ids: triggerIds })),
    tool: task.action_type === "build_research_pack" ? "research_pack_builder" : "review_task_writer",
    side_effect_level: "internal_write" as const,
    error_class: task.status === "blocked" ? "insufficient_signal" : null,
    audit_ref: `audit:${task.action_id}`,
  })));
  return {
    harness: {
      pack_id: "investment-research@1",
      signal_pack_id: "investment-signal@1",
      action_pack_id: "investment-research-action@1",
      collection_scope: input.collection_scope,
    },
    signals: snapshot,
    action_tasks: tasks,
    action_receipts,
  };
}

function round(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

async function sha256(value: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
