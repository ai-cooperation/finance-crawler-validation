import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";

import { SOURCES, evaluateContent } from "./index.mjs";

const hashContent = (content) =>
  content ? createHash("sha256").update(content, "utf8").digest("hex") : "";

export function buildResult(sourceId, payload, statusCode) {
  const selected = SOURCES[sourceId];
  if (!selected) throw new Error(`Unknown fixed source id: ${sourceId}`);

  const content = typeof payload?.content === "string" ? payload.content : "";
  const base = {
    source_id: sourceId,
    source_url: selected.url,
    route: "cloudflare_browser_run_markdown",
    worker_status_code: statusCode,
    browser_ms: Number(payload?.browser_ms || 0),
    content_length: content.length,
    content_sha256: hashContent(content),
  };

  if (!selected.eligible || payload?.failure_category === "robots_denied") {
    return {
      ...base,
      success: false,
      excluded: true,
      failure_category: "robots_denied",
      error: payload?.error || selected.exclusion_reason,
    };
  }

  if (statusCode < 200 || statusCode >= 300 || payload?.ok === false || !content) {
    return {
      ...base,
      success: false,
      failure_category: "browser_run_error",
      error: payload?.error || `Worker HTTP ${statusCode}`,
    };
  }

  const evaluation = evaluateContent(
    content,
    selected.required_terms,
    selected.min_content_chars,
  );
  return {
    ...base,
    success: evaluation.ok,
    ...(evaluation.ok ? {} : evaluation),
  };
}

export function summarizeResults(results) {
  const eligibleResults = results.filter((result) => !result.excluded);
  const recovered = eligibleResults.filter((result) => result.success).length;
  return {
    cohort_denominator: results.length,
    eligible_denominator: eligibleResults.length,
    excluded: results.length - eligibleResults.length,
    recovered,
    failed: eligibleResults.length - recovered,
    recovery_rate: eligibleResults.length
      ? Number(((recovered / eligibleResults.length) * 100).toFixed(2))
      : 0,
    total_browser_ms: results.reduce(
      (total, result) => total + Number(result.browser_ms || 0),
      0,
    ),
  };
}

const defaultSleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

export async function runPilot({
  baseUrl,
  intervalMs = 11000,
  outputPath,
  fetchFn = fetch,
  sleep = defaultSleep,
  onProgress = () => {},
}) {
  const startedAt = new Date().toISOString();
  const sourceIds = Object.keys(SOURCES);
  const results = [];
  let hasRequested = false;

  for (const [index, sourceId] of sourceIds.entries()) {
    onProgress({ phase: "start", index: index + 1, total: sourceIds.length, sourceId });
    const selected = SOURCES[sourceId];
    if (!selected.eligible) {
      const result = buildResult(
        sourceId,
        {
          ok: false,
          failure_category: "robots_denied",
          error: selected.exclusion_reason,
        },
        403,
      );
      results.push(result);
      onProgress({ phase: "complete", index: index + 1, total: sourceIds.length, result });
      continue;
    }

    if (hasRequested && intervalMs > 0) await sleep(intervalMs);
    let response;
    let payload;
    try {
      response = await fetchFn(`${baseUrl.replace(/\/$/, "")}/probe/${sourceId}`);
      payload = await response.json();
    } catch (error) {
      response = { status: 0 };
      payload = { ok: false, error: error instanceof Error ? error.message : String(error) };
    }
    const result = buildResult(sourceId, payload, response.status);
    results.push(result);
    hasRequested = true;
    onProgress({ phase: "complete", index: index + 1, total: sourceIds.length, result });
  }

  const report = {
    schema_version: 1,
    experiment: "cloudflare-browser-run-fixed-eight-pilot",
    started_at: startedAt,
    completed_at: new Date().toISOString(),
    baseline: {
      engine: "Crawl4AI on GitHub Actions",
      denominator: 8,
      successes: 0,
      failure_class: "anti_bot_or_cdn_blocked",
    },
    treatment: {
      engine: "Cloudflare Browser Run Quick Action markdown networkidle2 wait5s",
      interval_ms: intervalMs,
      source_ids: sourceIds,
      eligible_source_ids: sourceIds.filter((sourceId) => SOURCES[sourceId].eligible),
    },
    results,
    summary: summarizeResults(results),
  };

  if (outputPath) await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return report;
}
