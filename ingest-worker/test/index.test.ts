import { env } from "cloudflare:workers";
import { Validator, type Schema } from "@cfworker/json-schema";
import { beforeEach, describe, expect, it } from "vitest";

import {
  assertGithubClaims,
  authenticateGithubOidc,
  AuthenticationError,
} from "../src/auth";
import { canonicalJson } from "../src/canonical-json";
import { createHandler } from "../src/handler";
import { HttpError, ingestItems, publishSnapshot } from "../src/storage";
import {
  parseFreshnessPolicy,
  readStatus,
  StatusConfigurationError,
  StatusReadError,
} from "../src/status";
import statusResponseSchema from "../../schemas/status-response.schema.json";


const ITEM_ID = "a".repeat(64);
const CONTENT_HASH = "b".repeat(64);
const MANIFEST_HASH = "c".repeat(64);
const STATUS_VALIDATOR = new Validator(statusResponseSchema as Schema, "2020-12", false);

function rawItem() {
  return {
    schema_version: 1,
    item_id: ITEM_ID,
    source_id: "federal_reserve_press_rss",
    canonical_url: "https://www.federalreserve.gov/newsevents/example.htm",
    title: "Federal Reserve issues a policy statement",
    summary: "Synthetic for testing only",
    content: "Synthetic for testing only",
    published_at: "2026-08-10T02:00:00Z",
    collected_at: "2026-08-10T02:05:00Z",
    transport: "rss",
    kind: "official_news",
    layer: "official",
    content_sha256: CONTENT_HASH,
    rights: {
      redistribution: "metadata_only",
      retention_days: 30,
      public_excerpt_chars: 0,
    },
    engagement: { score: null, comments: null, shares: null, likes: null },
    evidence: {
      route: "direct",
      status_code: 200,
      final_url: "https://www.federalreserve.gov/newsevents/example.htm",
      extraction_method: "rss",
    },
  };
}

function envelope() {
  return {
    schema_version: 1,
    operation: "upsert_items",
    run_id: "run_20260810t020500z",
    workflow_run_id: "31309377786",
    commit_sha: "d".repeat(40),
    snapshot_id: "radar_20260810t020500z",
    source_manifest_hash: MANIFEST_HASH,
    collected_at: "2026-08-10T02:05:00Z",
    items: [rawItem()],
    checkpoints: [
      {
        source_id: "federal_reserve_press_rss",
        status: "success",
        last_successful_crawl: "2026-08-10T02:05:00Z",
        last_article_date: "2026-08-10T02:00:00Z",
        cursor: null,
      },
    ],
  };
}

function topicSnapshot() {
  return {
    schema_version: 1,
    snapshot_id: "radar_20260810t020500z",
    run_id: "run_20260810t020500z",
    as_of: "2026-08-10T02:05:00Z",
    partial: true,
    failed_sources: [],
    input_item_ids: [ITEM_ID],
    topics: [
      {
        topic_id: "monetary_policy",
        label: "Monetary policy",
        score: 1,
        item_count: 1,
        source_count: 1,
        news_count: 0,
        social_count: 0,
        evidence_ids: [ITEM_ID],
        divergence: { direction: "insufficient_data", magnitude: null },
      },
    ],
  };
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM current_snapshot"),
    env.DB.prepare("DELETE FROM topic_snapshots"),
    env.DB.prepare("DELETE FROM run_items"),
    env.DB.prepare("DELETE FROM raw_items"),
    env.DB.prepare("DELETE FROM source_state"),
    env.DB.prepare("DELETE FROM audit_events"),
    env.DB.prepare("DELETE FROM ingest_receipts"),
    env.DB.prepare("DELETE FROM runs"),
  ]);
  const objects = await env.RAW_OBJECTS.list();
  if (objects.objects.length > 0) {
    await env.RAW_OBJECTS.delete(objects.objects.map((object) => object.key));
  }
});

describe("ingest items", () => {
  it("stores validated raw objects and checkpoint metadata", async () => {
    const result = await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));

    expect(result.received_items).toBe(1);
    const item = await env.DB.prepare(
      "SELECT item_id, object_key FROM raw_items WHERE item_id = ?",
    ).bind(ITEM_ID).first<{ item_id: string; object_key: string }>();
    expect(item?.item_id).toBe(ITEM_ID);
    const stored = await env.RAW_OBJECTS.get(item!.object_key);
    expect(await stored?.json()).toMatchObject({ item_id: ITEM_ID, schema_version: 1 });
    const state = await env.DB.prepare(
      "SELECT status, last_successful_crawl FROM source_state WHERE source_id = ?",
    ).bind("federal_reserve_press_rss").first<{
      status: string;
      last_successful_crawl: string;
    }>();
    expect(state).toEqual({
      status: "success",
      last_successful_crawl: "2026-08-10T02:05:00Z",
    });
  });

  it("is idempotent when the same envelope is replayed", async () => {
    const first = await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    const replay = await ingestItems(env, envelope(), new Date("2026-08-10T02:06:00Z"));

    expect(first.replayed).toBe(false);
    expect(replay).toMatchObject({ replayed: true, status: "staging" });

    const items = await env.DB.prepare("SELECT COUNT(*) AS count FROM raw_items").first<{
      count: number;
    }>();
    const links = await env.DB.prepare("SELECT COUNT(*) AS count FROM run_items").first<{
      count: number;
    }>();
    expect(items?.count).toBe(1);
    expect(links?.count).toBe(1);
    const audits = await env.DB.prepare(
      "SELECT COUNT(*) AS count FROM audit_events WHERE run_id = ? AND stage = 'raw_collected'",
    ).bind(envelope().run_id).first<{ count: number }>();
    expect(audits?.count).toBe(1);
  });

  it("rejects a changed payload that reuses an existing run id", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    const changed = envelope();
    changed.items[0].title = "Changed title with a reused identity";

    await expect(
      ingestItems(env, changed, new Date("2026-08-10T02:06:00Z")),
    ).rejects.toMatchObject({ status: 409, code: "run_payload_conflict" });
    const stored = await env.RAW_OBJECTS.get(
      `raw/federal_reserve_press_rss/${ITEM_ID}.json`,
    );
    expect(await stored?.json()).toMatchObject({
      title: "Federal Reserve issues a policy statement",
    });
  });

  it("rejects changed immutable identity fields on an existing run", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    const changed = envelope();
    changed.snapshot_id = "radar_20260810t030000z";

    await expect(
      ingestItems(env, changed, new Date("2026-08-10T02:06:00Z")),
    ).rejects.toMatchObject({ status: 409, code: "run_identity_conflict" });
  });

  it("treats JSON object key order as the same ingest payload", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    const reordered = Object.fromEntries(Object.entries(envelope()).reverse());

    const replay = await ingestItems(env, reordered, new Date("2026-08-10T02:06:00Z"));

    expect(replay.replayed).toBe(true);
  });

  it("does not guess a payload receipt for a legacy run", async () => {
    const payload = envelope();
    await env.DB.prepare(
      `INSERT INTO runs (
        run_id, workflow_run_id, commit_sha, snapshot_id, source_manifest_hash,
        status, collected_at, item_count
      ) VALUES (?, ?, ?, ?, ?, 'staging', ?, 0)`,
    ).bind(
      payload.run_id,
      payload.workflow_run_id,
      payload.commit_sha,
      payload.snapshot_id,
      payload.source_manifest_hash,
      payload.collected_at,
    ).run();

    await expect(
      ingestItems(env, payload, new Date("2026-08-10T02:05:30Z")),
    ).rejects.toMatchObject({ status: 409, code: "run_receipt_missing" });
  });

  it("rejects invalid items before either binding is written", async () => {
    const invalid = envelope();
    delete (invalid.items[0] as Partial<ReturnType<typeof rawItem>>).title;

    await expect(
      ingestItems(env, invalid, new Date("2026-08-10T02:05:30Z")),
    ).rejects.toMatchObject({ status: 422 });
    const items = await env.DB.prepare("SELECT COUNT(*) AS count FROM raw_items").first<{
      count: number;
    }>();
    expect(items?.count).toBe(0);
    expect(await env.RAW_OBJECTS.get(`raw/federal_reserve_press_rss/${ITEM_ID}.json`)).toBeNull();
  });

  it("rejects cross-field envelope invariant violations", async () => {
    const duplicateCheckpoint = envelope();
    duplicateCheckpoint.checkpoints.push(duplicateCheckpoint.checkpoints[0]);
    const missingSuccessTime = envelope();
    missingSuccessTime.checkpoints[0].last_successful_crawl = null;
    const missingCheckpoint = envelope();
    missingCheckpoint.items[0].source_id = "unregistered_source";

    await expect(ingestItems(env, duplicateCheckpoint, new Date())).rejects.toMatchObject({
      status: 422,
    });
    await expect(ingestItems(env, missingSuccessTime, new Date())).rejects.toMatchObject({
      status: 422,
    });
    await expect(ingestItems(env, missingCheckpoint, new Date())).rejects.toMatchObject({
      status: 422,
    });
  });
});

describe("snapshot publication", () => {
  it("switches current only after a valid topic snapshot is persisted", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    const result = await publishSnapshot(
      env,
      topicSnapshot(),
      new Date("2026-08-10T02:07:00Z"),
    );

    expect(result.status).toBe("published");
    const current = await env.DB.prepare(
      "SELECT snapshot_id FROM current_snapshot WHERE singleton_id = 1",
    ).first<{ snapshot_id: string }>();
    expect(current?.snapshot_id).toBe("radar_20260810t020500z");
    expect(await env.RAW_OBJECTS.get("topics/radar_20260810t020500z.json")).not.toBeNull();
  });

  it("replays an identical publication without moving the current pointer", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    const first = await publishSnapshot(
      env,
      topicSnapshot(),
      new Date("2026-08-10T02:07:00Z"),
    );
    const before = await env.DB.prepare(
      "SELECT updated_at FROM current_snapshot WHERE singleton_id = 1",
    ).first<{ updated_at: string }>();
    const replay = await publishSnapshot(
      env,
      topicSnapshot(),
      new Date("2026-08-10T03:07:00Z"),
    );
    const after = await env.DB.prepare(
      "SELECT updated_at FROM current_snapshot WHERE singleton_id = 1",
    ).first<{ updated_at: string }>();

    expect(first.replayed).toBe(false);
    expect(replay).toMatchObject({ replayed: true, status: "published" });
    expect(after?.updated_at).toBe(before?.updated_at);
  });

  it("reports the persisted published state when ingest is replayed after publication", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-10T02:07:00Z"));

    const replay = await ingestItems(env, envelope(), new Date("2026-08-10T03:07:00Z"));

    expect(replay).toMatchObject({ replayed: true, status: "published" });
  });

  it("rejects changed content that reuses a published snapshot id", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-10T02:07:00Z"));
    const changed = topicSnapshot();
    changed.failed_sources = ["changed_source"];

    await expect(
      publishSnapshot(env, changed, new Date("2026-08-10T03:07:00Z")),
    ).rejects.toMatchObject({ status: 409, code: "snapshot_payload_conflict" });
    const stored = await env.RAW_OBJECTS.get("topics/radar_20260810t020500z.json");
    expect(await stored?.json()).toMatchObject({ failed_sources: [] });
  });

  it("keeps the last-good snapshot when the next publication is invalid", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-10T02:07:00Z"));
    const invalid = topicSnapshot();
    invalid.snapshot_id = "radar_20260810t030000z";
    invalid.run_id = "run_20260810t030000z";
    invalid.topics = Array.from({ length: 4 }, () => topicSnapshot().topics[0]);

    await expect(
      publishSnapshot(env, invalid, new Date("2026-08-10T03:00:00Z")),
    ).rejects.toBeInstanceOf(HttpError);
    const current = await env.DB.prepare(
      "SELECT snapshot_id FROM current_snapshot WHERE singleton_id = 1",
    ).first<{ snapshot_id: string }>();
    expect(current?.snapshot_id).toBe("radar_20260810t020500z");
  });
});

describe("D1-backed status", () => {
  it("returns an empty versioned response before the first publication", async () => {
    const handler = createHandler({
      authenticate: async () => {
        throw new Error("status must not invoke write authentication");
      },
      now: () => new Date("2026-08-10T02:05:30Z"),
    });
    const response = await handler.fetch(new Request("https://ingest.example/v1/status"), env);

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(STATUS_VALIDATOR.validate(payload).valid).toBe(true);
    expect(payload).toMatchObject({
      schema_version: 1,
      state: "empty",
      reasons: ["no_snapshot"],
      current_snapshot: null,
      source_counts: { total: 0, success: 0, partial: 0, failed: 0 },
    });
  });

  it("reports a partial last-good snapshot as warning", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-10T02:07:00Z"));
    const handler = createHandler({
      authenticate: async () => ({
        workflowRunId: "31309377786",
        commitSha: "d".repeat(40),
      }),
      now: () => new Date("2026-08-10T02:10:00Z"),
    });
    const response = await handler.fetch(new Request("https://ingest.example/v1/status"), env);
    const payload = await response.json();

    expect(response.status).toBe(200);
    expect(payload).toMatchObject({
      state: "warning",
      reasons: ["partial_snapshot"],
      freshness: { state: "healthy", age_seconds: 300 },
      current_snapshot: {
        snapshot_id: "radar_20260810t020500z",
        run_id: "run_20260810t020500z",
        failed_source_count: 0,
        topic_count: 1,
      },
      source_counts: { total: 1, success: 1, partial: 0, failed: 0 },
    });
  });

  it("reports a stale snapshot without replacing last-good", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-10T02:07:00Z"));
    const invalid = topicSnapshot();
    invalid.topics = Array.from({ length: 4 }, () => topicSnapshot().topics[0]);
    await expect(
      publishSnapshot(env, invalid, new Date("2026-08-12T02:05:00Z")),
    ).rejects.toMatchObject({ status: 422 });
    const handler = createHandler({
      authenticate: async () => ({
        workflowRunId: "31309377786",
        commitSha: "d".repeat(40),
      }),
      now: () => new Date("2026-08-12T02:05:00Z"),
    });
    const response = await handler.fetch(new Request("https://ingest.example/v1/status"), env);

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      state: "stale",
      reasons: expect.arrayContaining(["freshness_stale"]),
      freshness: { state: "stale", age_seconds: 172800 },
      current_snapshot: { snapshot_id: "radar_20260810t020500z" },
    });
  });

  it("warns when producer time is more than five minutes ahead", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-10T02:07:00Z"));
    const handler = createHandler({
      authenticate: async () => ({
        workflowRunId: "31309377786",
        commitSha: "d".repeat(40),
      }),
      now: () => new Date("2026-08-10T01:59:59Z"),
    });
    const response = await handler.fetch(new Request("https://ingest.example/v1/status"), env);

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      state: "warning",
      reasons: expect.arrayContaining(["clock_skew"]),
      freshness: { state: "warning", age_seconds: 0 },
    });
  });

  it("warns at the exact freshness boundary and exposes source failures", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-10T02:07:00Z"));
    await env.DB.prepare(
      "UPDATE source_state SET status = 'partial' WHERE source_id = ?",
    ).bind("federal_reserve_press_rss").run();
    const handler = createHandler({
      authenticate: async () => ({
        workflowRunId: "31309377786",
        commitSha: "d".repeat(40),
      }),
      now: () => new Date("2026-08-10T08:05:00Z"),
    });
    const response = await handler.fetch(new Request("https://ingest.example/v1/status"), env);

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      state: "warning",
      reasons: expect.arrayContaining(["freshness_warning", "source_failures"]),
      freshness: { state: "warning", age_seconds: 21600 },
      source_counts: { partial: 1 },
    });
  });

  it("rejects invalid threshold configuration", () => {
    const base = {
      FRESHNESS_WARNING_SECONDS: "21600",
      FRESHNESS_STALE_SECONDS: "86400",
    };

    expect(() => parseFreshnessPolicy({
      ...base,
      FRESHNESS_WARNING_SECONDS: "0",
    } as Env)).toThrow(StatusConfigurationError);
    expect(() => parseFreshnessPolicy({
      ...base,
      FRESHNESS_STALE_SECONDS: "21600",
    } as Env)).toThrow(/stale threshold/);
    expect(() => parseFreshnessPolicy({
      ...base,
      FRESHNESS_WARNING_SECONDS: "999999999999999999999999999999",
    } as Env)).toThrow(StatusConfigurationError);
  });

  it("normalizes D1 read failures without exposing storage details", async () => {
    const failingDb = {
      prepare: () => {
        throw new Error("private storage detail");
      },
    } as unknown as D1Database;

    await expect(readStatus(failingDb, new Date(), {
      warningAfterSeconds: 21600,
      staleAfterSeconds: 86400,
    })).rejects.toBeInstanceOf(StatusReadError);
  });

  it("returns status_unavailable for corrupt operational metadata", async () => {
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    await publishSnapshot(env, topicSnapshot(), new Date("2026-08-10T02:07:00Z"));
    await env.DB.prepare(
      "UPDATE topic_snapshots SET failed_sources_json = 'not-json' WHERE snapshot_id = ?",
    ).bind(topicSnapshot().snapshot_id).run();
    const handler = createHandler({
      authenticate: async () => ({
        workflowRunId: "31309377786",
        commitSha: "d".repeat(40),
      }),
      now: () => new Date("2026-08-10T02:10:00Z"),
    });

    const response = await handler.fetch(new Request("https://ingest.example/v1/status"), env);

    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ error: "status_unavailable" });
  });
});

describe("HTTP routing and error contracts", () => {
  it("separates health, unknown routes, and method errors without authentication", async () => {
    const handler = createHandler({
      authenticate: async () => {
        throw new Error("read-only routes must not authenticate");
      },
      now: () => new Date("2026-08-10T02:05:30Z"),
    });

    expect((await handler.fetch(new Request("https://ingest.example/health"), env)).status).toBe(200);
    expect((await handler.fetch(new Request("https://ingest.example/unknown"), env)).status).toBe(404);
    expect((await handler.fetch(new Request("https://ingest.example/v1/ingest/items"), env)).status)
      .toBe(405);
    expect((await handler.fetch(new Request("https://ingest.example/v1/status", {
      method: "POST",
    }), env)).status).toBe(405);
  });

  it("executes authenticated ingest, publish, and replay through HTTP", async () => {
    const handler = createHandler({
      authenticate: async () => ({
        workflowRunId: "31309377786",
        commitSha: "d".repeat(40),
      }),
      now: () => new Date("2026-08-10T02:07:00Z"),
    });
    const post = (path: string, body: object) => handler.fetch(
      new Request(`https://ingest.example${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
      env,
    );

    const ingested = await post("/v1/ingest/items", envelope());
    const published = await post("/v1/ingest/publish", topicSnapshot());
    const replayed = await post("/v1/ingest/items", envelope());

    expect(ingested.status).toBe(202);
    expect(await ingested.json()).toMatchObject({ replayed: false, status: "staging" });
    expect(published.status).toBe(200);
    expect(await published.json()).toMatchObject({ replayed: false, status: "published" });
    expect(await replayed.json()).toMatchObject({ replayed: true, status: "published" });
  });

  it("normalizes media type, empty body, invalid JSON, and declared size errors", async () => {
    const handler = createHandler({
      authenticate: async () => ({
        workflowRunId: "31309377786",
        commitSha: "d".repeat(40),
      }),
      now: () => new Date("2026-08-10T02:05:30Z"),
    });
    const send = (headers: HeadersInit, body?: string) => handler.fetch(
      new Request("https://ingest.example/v1/ingest/items", {
        method: "POST",
        headers,
        body,
      }),
      env,
    );

    const mediaType = await send({}, "{}");
    const empty = await send({ "Content-Type": "application/json" });
    const invalidJson = await send({ "Content-Type": "application/json" }, "{");
    const oversized = await send({
      "Content-Type": "application/json",
      "Content-Length": "2000001",
    }, "{}");

    expect(await mediaType.json()).toMatchObject({ error: "unsupported_media_type" });
    expect(await empty.json()).toMatchObject({ error: "empty_body" });
    expect(await invalidJson.json()).toMatchObject({ error: "invalid_json" });
    expect(await oversized.json()).toMatchObject({ error: "payload_too_large" });
  });

  it("normalizes authentication and unexpected errors", async () => {
    const authenticationFailure = createHandler({
      authenticate: async () => {
        throw new AuthenticationError(401, "invalid_oidc_token");
      },
      now: () => new Date(),
    });
    const unexpectedFailure = createHandler({
      authenticate: async () => {
        throw new Error("private error detail");
      },
      now: () => new Date(),
    });
    const request = () => new Request("https://ingest.example/v1/ingest/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });

    const unauthorized = await authenticationFailure.fetch(request(), env);
    const internal = await unexpectedFailure.fetch(request(), env);

    expect(unauthorized.status).toBe(401);
    expect(await unauthorized.json()).toMatchObject({ error: "invalid_oidc_token" });
    expect(internal.status).toBe(500);
    expect(await internal.json()).toMatchObject({ error: "internal_error" });
  });
});

describe("canonical payload hashing", () => {
  it("rejects values that JSON cannot represent deterministically", () => {
    expect(() => canonicalJson(Number.NaN)).toThrow(/finite numbers/);
    expect(() => canonicalJson(undefined)).toThrow(/unsupported canonical JSON value/);
  });
});

describe("authentication boundary", () => {
  it("accepts the complete immutable GitHub claim set", () => {
    expect(() => assertGithubClaims(
      {
        repository_id: "99",
        repository_owner_id: "42",
        workflow_ref: "owner/repo/.github/workflows/crawl.yml@refs/heads/main",
        ref: "refs/heads/main",
        event_name: "workflow_dispatch",
      },
      {
        repositoryId: "99",
        ownerId: "42",
        workflowRef: "owner/repo/.github/workflows/crawl.yml@refs/heads/main",
        ref: "refs/heads/main",
        eventName: "workflow_dispatch",
      },
    )).not.toThrow();
  });

  it("fails closed before JWT verification when OIDC is unconfigured or absent", async () => {
    const configured = {
      GITHUB_REPOSITORY_ID: "99",
      GITHUB_OWNER_ID: "42",
      GITHUB_OIDC_AUDIENCE: "audience",
      GITHUB_WORKFLOW_REF: "owner/repo/.github/workflows/crawl.yml@refs/heads/main",
      GITHUB_REF: "refs/heads/main",
      GITHUB_EVENT_NAME: "workflow_dispatch",
    } as Env;
    const placeholder = { ...configured, GITHUB_REPOSITORY_ID: "TBD" } as Env;

    await expect(authenticateGithubOidc(
      new Request("https://ingest.example/v1/ingest/items"),
      placeholder,
    )).rejects.toMatchObject({ status: 503, code: "oidc_not_configured" });
    await expect(authenticateGithubOidc(
      new Request("https://ingest.example/v1/ingest/items"),
      configured,
    )).rejects.toMatchObject({ status: 401, code: "missing_bearer_token" });
  });

  it("rejects mutable names when immutable GitHub identity claims do not match", () => {
    expect(() =>
      assertGithubClaims(
        {
          repository_id: "wrong",
          repository_owner_id: "42",
          workflow_ref: "owner/repo/.github/workflows/crawl.yml@refs/heads/main",
          ref: "refs/heads/main",
          event_name: "workflow_dispatch",
          run_id: "31309377786",
        },
        {
          repositoryId: "99",
          ownerId: "42",
          workflowRef: "owner/repo/.github/workflows/crawl.yml@refs/heads/main",
          ref: "refs/heads/main",
          eventName: "workflow_dispatch",
        },
      ),
    ).toThrow(/repository_id/);
  });

  it("binds an authenticated workflow run to the envelope run id", async () => {
    const handler = createHandler({
      authenticate: async () => ({ workflowRunId: "999", commitSha: "d".repeat(40) }),
      now: () => new Date("2026-08-10T02:05:30Z"),
    });
    const response = await handler.fetch(
      new Request("https://ingest.example/v1/ingest/items", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(envelope()),
      }),
      env,
    );

    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ error: "workflow_run_mismatch" });
  });

  it("binds the ingest envelope to the authenticated commit SHA", async () => {
    const handler = createHandler({
      authenticate: async () => ({
        workflowRunId: "31309377786",
        commitSha: "e".repeat(40),
      }),
      now: () => new Date("2026-08-10T02:05:30Z"),
    });
    const response = await handler.fetch(
      new Request("https://ingest.example/v1/ingest/items", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(envelope()),
      }),
      env,
    );

    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ error: "commit_sha_mismatch" });
  });
});
