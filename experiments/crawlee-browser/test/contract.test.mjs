import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BLOCK_MARKERS,
  CRAWLER_POLICY,
  catalogRobotsExclusions,
  buildSkippedResultForEvent,
  buildResult,
  evaluatePage,
  selectBrowserSources,
  statusCodeFromError,
} from "../src/contract.mjs";

const source = {
  id: "hotcopper_home",
  name: "HotCopper",
  url: "https://hotcopper.com.au/",
  transport: "browser",
  required_terms: ["HotCopper"],
  min_content_chars: 300,
  enabled: true,
};

test("Crawlee uses a controlled one-attempt proxyless policy", () => {
  assert.deepEqual(CRAWLER_POLICY, {
    maxConcurrency: 1,
    maxRequestRetries: 0,
    maxSessionRotations: 0,
    navigationTimeoutSecs: 40,
    requestHandlerTimeoutSecs: 60,
    respectRobotsTxtFile: { userAgent: "*" },
    retryOnBlocked: false,
  });
});

test("only enabled Browser sources enter the Crawlee treatment", () => {
  const selected = selectBrowserSources({
    defaults: { min_content_chars: 200, enabled: true },
    sources: [
      source,
      { ...source, id: "rss", transport: "rss" },
      { ...source, id: "disabled", enabled: false },
    ],
  });

  assert.deepEqual(selected, [
    { ...source, min_content_chars: 300, timeout_seconds: 40 },
  ]);
});

test("versioned catalog robots denials become preflight exclusions", () => {
  const denied = {
    ...source,
    robots_denied: true,
    robots_evidence: "https://hotcopper.com.au/robots.txt",
    robots_checked_at: "2026-08-09",
  };

  assert.deepEqual(catalogRobotsExclusions([source, denied]), {
    hotcopper_home:
      "catalog robots.txt disallow verified 2026-08-09: https://hotcopper.com.au/robots.txt",
  });
});

test("the page contract accepts valid content and rejects WAF pages", () => {
  assert.ok(BLOCK_MARKERS.includes("just a moment"));
  assert.deepEqual(
    evaluatePage(source, {
      statusCode: 200,
      title: "HotCopper",
      content: "x".repeat(400),
      finalUrl: source.url,
    }),
    { outcome: "success", error: "" },
  );
  assert.deepEqual(
    evaluatePage(source, {
      statusCode: 200,
      title: "Just a moment...",
      content: "Checking your browser",
      finalUrl: source.url,
    }),
    { outcome: "blocked", error: "anti-bot marker found: just a moment" },
  );
  assert.deepEqual(
    evaluatePage(source, {
      statusCode: 403,
      title: "Forbidden",
      content: "HotCopper " + "x".repeat(400),
      finalUrl: source.url,
    }),
    { outcome: "blocked", error: "HTTP 403" },
  );
});

test("Crawlee blocked errors do not become timeouts from a package path", () => {
  const error = [
    "Error: Request blocked - received 403 status code.",
    "at @crawlee/basic",
    "at @apify/timeout/cjs/index.cjs",
  ].join("\n");

  assert.equal(statusCodeFromError(error), 403);
  assert.deepEqual(
    evaluatePage(source, {
      statusCode: statusCodeFromError(error),
      title: "",
      content: "",
      finalUrl: source.url,
      error,
    }),
    { outcome: "blocked", error: "HTTP 403" },
  );
});

test("the contract rejects missing required terms and short content", () => {
  assert.deepEqual(
    evaluatePage(source, {
      statusCode: 200,
      title: "Home",
      content: "x".repeat(400),
      finalUrl: source.url,
    }),
    { outcome: "invalid_content", error: "required term missing: HotCopper" },
  );
  assert.deepEqual(
    evaluatePage(source, {
      statusCode: 200,
      title: "HotCopper",
      content: "HotCopper",
      finalUrl: source.url,
    }),
    { outcome: "invalid_content", error: "content shorter than minimum: 9 < 300" },
  );
});

test("robots skips remain excluded instead of crawler failures", () => {
  assert.deepEqual(
    evaluatePage(source, {
      skippedReason: "robots.txt disallowed this URL",
      statusCode: null,
      title: "",
      content: "",
      finalUrl: source.url,
    }),
    { outcome: "robots_denied", error: "robots.txt disallowed this URL" },
  );
});

test("Crawlee 3.18 skipped events resolve by URL without request userData", () => {
  const result = buildSkippedResultForEvent(
    new Map([[source.url, source]]),
    { url: source.url, reason: "robotsTxt" },
  );

  assert.equal(result.source_id, source.id);
  assert.equal(result.outcome, "robots_denied");
  assert.equal(result.error, "robots.txt skipped request: robotsTxt");
});

test("saved results retain evidence hashes but discard full page content", () => {
  const content = "HotCopper " + "x".repeat(400);
  const result = buildResult(source, {
    statusCode: 200,
    title: "HotCopper",
    content,
    finalUrl: source.url,
    elapsedMs: 123,
  });

  assert.equal(result.outcome, "success");
  assert.equal(result.content_chars, content.length);
  assert.equal(result.content_sha256.length, 64);
  assert.equal(result.elapsed_ms, 123);
  assert.equal(Object.hasOwn(result, "content"), false);
});
