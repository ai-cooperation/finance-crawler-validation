import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { assertGithubClaims } from "../src/auth";
import { createHandler } from "../src/handler";
import { HttpError, ingestItems, publishSnapshot } from "../src/storage";


const ITEM_ID = "a".repeat(64);
const CONTENT_HASH = "b".repeat(64);
const MANIFEST_HASH = "c".repeat(64);

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
    await ingestItems(env, envelope(), new Date("2026-08-10T02:05:30Z"));
    await ingestItems(env, envelope(), new Date("2026-08-10T02:06:00Z"));

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

describe("authentication boundary", () => {
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
