import assert from "node:assert/strict";
import { test } from "node:test";

import { buildResult, summarizeResults } from "../src/pilot.mjs";

test("pilot results discard body content but retain reproducibility metadata", () => {
  const result = buildResult(
    "hotcopper_home",
    {
      source_id: "hotcopper_home",
      source_url: "https://hotcopper.com.au/",
      browser_ms: 812,
      content: "# HotCopper\n" + "x".repeat(400),
    },
    200,
  );

  assert.equal(result.success, true);
  assert.equal(result.content_length, 412);
  assert.equal(result.browser_ms, 812);
  assert.equal(result.content_sha256.length, 64);
  assert.equal(Object.hasOwn(result, "content"), false);
});

test("pilot summary separates the original cohort from the eligible denominator", () => {
  const results = [
    { success: true, browser_ms: 100 },
    { success: true, browser_ms: 200 },
    ...Array.from({ length: 5 }, () => ({ success: false, browser_ms: 0 })),
    { success: false, excluded: true, browser_ms: 0 },
  ];

  assert.deepEqual(summarizeResults(results), {
    cohort_denominator: 8,
    eligible_denominator: 7,
    excluded: 1,
    recovered: 2,
    failed: 5,
    recovery_rate: 28.57,
    total_browser_ms: 300,
  });
});

test("runner records the robots exclusion without fetching content", () => {
  const result = buildResult(
    "quant_stackexchange_hot",
    {
      ok: false,
      failure_category: "robots_denied",
      error: "robots.txt currently disallows all crawling",
    },
    403,
  );

  assert.equal(result.excluded, true);
  assert.equal(result.success, false);
  assert.equal(result.failure_category, "robots_denied");
});

test("runner classifies an upstream error explicitly", () => {
  const result = buildResult(
    "hotcopper_home",
    { source_id: "hotcopper_home", error: "navigation failed" },
    502,
  );

  assert.equal(result.success, false);
  assert.equal(result.failure_category, "browser_run_error");
  assert.equal(result.error, "navigation failed");
});
