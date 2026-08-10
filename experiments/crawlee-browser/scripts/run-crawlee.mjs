import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import { PlaywrightCrawler } from "@crawlee/playwright";
import { chromium } from "playwright";
import YAML from "yaml";

import {
  CRAWLER_POLICY,
  buildResult,
  buildSkippedResultForEvent,
  catalogRobotsExclusions,
  selectBrowserSources,
  statusCodeFromError,
  summarizeResults,
} from "../src/contract.mjs";

const argument = (name) => {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) throw new Error(`Missing ${name}`);
  return process.argv[index + 1];
};

const manifestPath = resolve(argument("--manifest"));
const outputPath = resolve(argument("--output"));
const manifest = YAML.parse(await readFile(manifestPath, "utf8"));
const sources = selectBrowserSources(manifest);
if (sources.length !== 38) {
  throw new Error(`Fixed Browser cohort changed: expected 38, received ${sources.length}`);
}

const sourceById = new Map(sources.map((source) => [source.id, source]));
const sourceByUrl = new Map(sources.map((source) => [source.url, source]));
const preflightExclusions = catalogRobotsExclusions(sources);
const results = sources
  .filter((source) => preflightExclusions[source.id])
  .map((source) =>
    buildResult(source, {
      skippedReason: preflightExclusions[source.id],
      statusCode: null,
      title: "",
      content: "",
      finalUrl: source.url,
      elapsedMs: 0,
    }),
  );

const crawler = new PlaywrightCrawler({
  ...CRAWLER_POLICY,
  launchContext: {
    launcher: chromium,
    launchOptions: { headless: true },
  },
  preNavigationHooks: [
    async ({ request }, gotoOptions) => {
      request.userData.startedAt = Date.now();
      gotoOptions.waitUntil = "domcontentloaded";
      gotoOptions.timeout = CRAWLER_POLICY.navigationTimeoutSecs * 1000;
    },
  ],
  postNavigationHooks: [
    async ({ page }) => {
      await page.waitForTimeout(1000);
    },
  ],
  async requestHandler({ page, request, response }) {
    const source = sourceById.get(request.userData.sourceId);
    if (!source) throw new Error(`Unknown source id: ${request.userData.sourceId}`);
    const title = await page.title().catch(() => "");
    const content = await page
      .locator("body")
      .innerText({ timeout: 5000 })
      .catch(() => "");
    const result = buildResult(source, {
      statusCode: response?.status() ?? null,
      title,
      content,
      finalUrl: request.loadedUrl || page.url() || source.url,
      elapsedMs: Date.now() - Number(request.userData.startedAt || Date.now()),
    });
    results.push(result);
    process.stdout.write(
      `${JSON.stringify({ event: "crawlee_probe_finished", source_id: source.id, outcome: result.outcome, status_code: result.status_code, elapsed_ms: result.elapsed_ms })}\n`,
    );
  },
  async failedRequestHandler({ request }) {
    const source = sourceById.get(request.userData.sourceId);
    if (!source) return;
    const error = request.errorMessages.at(-1) || "Crawlee request failed";
    results.push(
      buildResult(source, {
        statusCode: statusCodeFromError(error),
        title: "",
        content: "",
        finalUrl: request.loadedUrl || source.url,
        elapsedMs: Date.now() - Number(request.userData.startedAt || Date.now()),
        error,
      }),
    );
  },
  async onSkippedRequest(event) {
    const result = buildSkippedResultForEvent(sourceByUrl, event);
    if (result) results.push(result);
  },
});

const requests = sources
  .filter((source) => !preflightExclusions[source.id])
  .map((source) => ({
    url: source.url,
    uniqueKey: source.id,
    userData: { sourceId: source.id },
  }));
await crawler.run(requests);

const firstResultById = new Map();
for (const result of results) {
  if (!firstResultById.has(result.source_id)) firstResultById.set(result.source_id, result);
}
for (const source of sources) {
  if (firstResultById.has(source.id)) continue;
  firstResultById.set(
    source.id,
    buildResult(source, {
      statusCode: null,
      title: "",
      content: "",
      finalUrl: source.url,
      elapsedMs: 0,
      error: "Crawlee produced no terminal result",
    }),
  );
}

const orderedResults = sources.map((source) => firstResultById.get(source.id));
const report = {
  schema_version: 1,
  generated_at: new Date().toISOString(),
  experiment: "crawlee-playwright-github-actions-fixed-browser-cohort",
  engine: {
    name: "Crawlee PlaywrightCrawler",
    crawlee_version: "3.18.0",
    playwright_version: "1.62.1",
    proxy: "none",
    policy: CRAWLER_POLICY,
  },
  preflight_exclusions: preflightExclusions,
  summary: summarizeResults(orderedResults),
  results: orderedResults,
};

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");

const summary = report.summary;
const markdown = [
  "## Crawlee Browser treatment",
  "",
  `- Cohort: ${summary.cohort_denominator}`,
  `- Robots excluded: ${summary.robots_denied}`,
  `- Eligible: ${summary.eligible_denominator}`,
  `- Success: ${summary.successes}/${summary.eligible_denominator} (${summary.success_rate}%)`,
  "- Proxy: none",
  "- Retries/session rotations: 0/0",
  "",
].join("\n");
await writeFile(resolve(dirname(outputPath), "crawlee-report.md"), markdown, "utf8");
process.stdout.write(`${JSON.stringify(summary)}\n`);
