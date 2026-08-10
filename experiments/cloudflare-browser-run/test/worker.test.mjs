import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BLOCK_MARKERS,
  SOURCES,
  evaluateContent,
  handleRequest,
} from "../src/index.mjs";

const request = (path, init = {}) =>
  new Request(`http://localhost${path}`, init);

test("the pilot freezes the same eight blocked Browser sources", () => {
  assert.equal(Object.keys(SOURCES).length, 8);
  assert.deepEqual(Object.keys(SOURCES).sort(), [
    "advfn_uk_share_chat",
    "bogleheads_personal_investments",
    "financial_wisdom_forum",
    "hotcopper_home",
    "investing_analysis",
    "mr_money_mustache_forum",
    "quant_stackexchange_hot",
    "white_coat_investor_forum",
  ]);
  assert.equal(Object.isFrozen(SOURCES), true);
  assert.equal(SOURCES.quant_stackexchange_hot.eligible, false);
  assert.equal(SOURCES.quant_stackexchange_hot.exclusion_reason, "robots_disallow_all_2026-08-09");
});

test("health check never opens a browser", async () => {
  let calls = 0;
  const env = {
    BROWSER: {
      quickAction: async () => {
        calls += 1;
      },
    },
  };

  const response = await handleRequest(request("/health"), env);

  assert.equal(response.status, 200);
  assert.equal(calls, 0);
  assert.deepEqual(await response.json(), {
    ok: true,
    service: "cloudflare-browser-run-pilot",
    source_count: 8,
  });
});

test("only GET and fixed source ids are accepted", async () => {
  const env = { BROWSER: { quickAction: async () => assert.fail() } };

  const methodResponse = await handleRequest(
    request("/probe/hotcopper_home", { method: "POST" }),
    env,
  );
  const unknownResponse = await handleRequest(
    request("/probe/https%3A%2F%2Fexample.com"),
    env,
  );

  assert.equal(methodResponse.status, 405);
  assert.equal(methodResponse.headers.get("allow"), "GET");
  assert.equal(unknownResponse.status, 404);
});

test("a current robots exclusion never opens Browser Run", async () => {
  let calls = 0;
  const env = {
    BROWSER: {
      quickAction: async () => {
        calls += 1;
      },
    },
  };

  const response = await handleRequest(request("/probe/quant_stackexchange_hot"), env);
  const body = await response.json();

  assert.equal(response.status, 403);
  assert.equal(body.ok, false);
  assert.equal(body.failure_category, "robots_denied");
  assert.equal(calls, 0);
});

test("a known source invokes markdown Quick Action and preserves usage metadata", async () => {
  const calls = [];
  const env = {
    BROWSER: {
      quickAction: async (action, options) => {
        calls.push({ action, options });
        return new Response(
          JSON.stringify({ success: true, result: "# HotCopper\n" + "x".repeat(400) }),
          {
            headers: {
              "content-type": "application/json",
              "x-browser-ms-used": "1234",
            },
          },
        );
      },
    },
  };

  const response = await handleRequest(request("/probe/hotcopper_home"), env);
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.source_id, "hotcopper_home");
  assert.equal(body.browser_ms, 1234);
  assert.match(body.content, /HotCopper/);
  assert.deepEqual(calls, [
    {
      action: "markdown",
      options: {
        url: "https://hotcopper.com.au/",
        gotoOptions: { timeout: 60000, waitUntil: "networkidle2" },
        waitForTimeout: 5000,
      },
    },
  ]);
});

test("the diagnostic wait override accepts only the fixed 15-second variant", async () => {
  const calls = [];
  const env = {
    BROWSER: {
      quickAction: async (_action, options) => {
        calls.push(options);
        return new Response(
          JSON.stringify({ success: true, result: "# HotCopper\n" + "x".repeat(400) }),
          { headers: { "content-type": "application/json" } },
        );
      },
    },
  };

  const response = await handleRequest(
    request("/probe/hotcopper_home?wait_ms=15000"),
    env,
  );
  const invalid = await handleRequest(
    request("/probe/hotcopper_home?wait_ms=60000"),
    env,
  );

  assert.equal(response.status, 200);
  assert.equal(calls[0].waitForTimeout, 15000);
  assert.equal(invalid.status, 400);
  assert.equal(calls.length, 1);
});

test("Quick Action failures are explicit and do not become false successes", async () => {
  const env = {
    BROWSER: {
      quickAction: async () =>
        new Response(JSON.stringify({ success: false, error: "navigation failed" }), {
          status: 502,
          headers: { "content-type": "application/json" },
        }),
    },
  };

  const response = await handleRequest(request("/probe/hotcopper_home"), env);
  const body = await response.json();

  assert.equal(response.status, 502);
  assert.equal(body.ok, false);
  assert.match(body.error, /navigation failed/);
});

test("content contract rejects challenges, missing terms, and short bodies", () => {
  assert.ok(BLOCK_MARKERS.includes("verify you are human"));
  assert.ok(BLOCK_MARKERS.includes("just a moment"));
  assert.ok(BLOCK_MARKERS.includes("403 forbidden"));
  assert.deepEqual(
    evaluateContent('---\ntitle: "Just a moment..."\n---', ["Bogleheads"], 300),
    { ok: false, failure_category: "anti_bot_blocked", marker: "just a moment" },
  );
  assert.deepEqual(
    evaluateContent(
      '---\ntitle: "403 Forbidden"\n---\nYou don\'t have permission to access this resource.',
      ["Financial Wisdom Forum"],
      300,
    ),
    { ok: false, failure_category: "anti_bot_blocked", marker: "403 forbidden" },
  );
  assert.deepEqual(
    evaluateContent("Verify you are human " + "x".repeat(400), ["HotCopper"], 300),
    { ok: false, failure_category: "anti_bot_blocked", marker: "verify you are human" },
  );
  assert.deepEqual(evaluateContent("x".repeat(400), ["HotCopper"], 300), {
    ok: false,
    failure_category: "content_validation_failed",
    missing_terms: ["HotCopper"],
  });
  assert.deepEqual(evaluateContent("HotCopper", ["HotCopper"], 300), {
    ok: false,
    failure_category: "content_too_short",
    content_length: 9,
    minimum: 300,
  });
  assert.deepEqual(
    evaluateContent("# HotCopper\n" + "x".repeat(400), ["HotCopper"], 300),
    { ok: true },
  );
});
