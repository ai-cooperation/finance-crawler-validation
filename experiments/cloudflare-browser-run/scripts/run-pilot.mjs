import { runPilot } from "../src/pilot.mjs";

const option = (name, fallback) => {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
};

const baseUrl = option("--base-url", "http://127.0.0.1:8790");
const outputPath = option("--output", "results/latest.json");
const intervalMs = Number(option("--interval-ms", "11000"));

if (!Number.isFinite(intervalMs) || intervalMs < 0) {
  throw new Error("--interval-ms must be a non-negative number");
}

const report = await runPilot({
  baseUrl,
  outputPath,
  intervalMs,
  onProgress(event) {
    if (event.phase === "start") {
      process.stdout.write(`[${event.index}/${event.total}] ${event.sourceId} ... `);
      return;
    }
    process.stdout.write(
      `${event.result.success ? "PASS" : "FAIL"} (${event.result.failure_category || "valid_content"})\n`,
    );
  },
});

process.stdout.write(`${JSON.stringify(report.summary)}\n`);
