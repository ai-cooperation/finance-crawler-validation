import assert from "node:assert/strict";
import test from "node:test";

import { TARGETS, createHandler } from "../src/index.mjs";


test("health exposes only the fixed feed allowlist", async () => {
  const handler = createHandler(async () => {
    throw new Error("upstream fetch should not run");
  });

  const response = await handler(new Request("https://worker.example/health"));
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(body.routes, Object.keys(TARGETS).sort());
  assert.equal(
    TARGETS.financial_wisdom_forum_feed.url,
    "https://www.financialwisdomforum.org/forum/app.php/feed",
  );
});


test("unknown routes cannot turn the worker into an open proxy", async () => {
  const handler = createHandler(async () => {
    throw new Error("upstream fetch should not run");
  });

  const response = await handler(
    new Request("https://worker.example/v1/feed/https%3A%2F%2Fevil.example"),
  );

  assert.equal(response.status, 404);
});


test("feed response is streamed with bounded routing metadata", async () => {
  let upstreamUrl = "";
  let upstreamInit;
  const handler = createHandler(async (url, init) => {
    upstreamUrl = url;
    upstreamInit = init;
    return new Response("<rss><item>market</item></rss>", {
      status: 200,
      headers: { "Content-Type": "application/rss+xml", ETag: "feed-v1" },
    });
  });

  const response = await handler(
    new Request("https://worker.example/v1/feed/money_stackexchange_feed"),
  );

  assert.equal(upstreamUrl, TARGETS.money_stackexchange_feed.url);
  assert.match(upstreamInit.headers.Accept, /^application\/atom\+xml/);
  assert.equal(upstreamInit.redirect, "manual");
  assert.equal(upstreamInit.cf.cacheEverything, true);
  assert.deepEqual(upstreamInit.cf.cacheTtlByStatus, {
    "200-299": 300,
    "300-599": 0,
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("X-Crawler-Origin-Status"), "200");
  assert.equal(response.headers.get("ETag"), "feed-v1");
  assert.match(await response.text(), /market/);
});


test("redirects and oversized declared bodies fail closed", async () => {
  const redirectHandler = createHandler(async () =>
    new Response(null, { status: 302, headers: { Location: "https://evil.example" } }),
  );
  const oversizedHandler = createHandler(async () =>
    new Response("small", {
      status: 200,
      headers: { "Content-Length": "3000000", "Content-Type": "application/rss+xml" },
    }),
  );

  const redirect = await redirectHandler(
    new Request("https://worker.example/v1/feed/money_stackexchange_feed"),
  );
  const oversized = await oversizedHandler(
    new Request("https://worker.example/v1/feed/money_stackexchange_feed"),
  );

  assert.equal(redirect.status, 502);
  assert.equal(oversized.status, 502);
});


test("upstream failures are never cached as successful feed responses", async () => {
  const handler = createHandler(async () =>
    new Response("challenge", {
      status: 403,
      headers: { "Content-Type": "text/html" },
    }),
  );

  const response = await handler(
    new Request("https://worker.example/v1/feed/bogleheads_forum_feed"),
  );

  assert.equal(response.status, 403);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
});
