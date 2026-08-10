import { createHash } from "node:crypto";

export const BLOCK_MARKERS = Object.freeze([
  "access denied",
  "captcha",
  "blocked by anti-bot protection",
  "attention required! | cloudflare",
  "cf-chl-",
  "datadome",
  "verify you are human",
  "just a moment",
  "403 forbidden",
  "bot detection",
]);

export const CRAWLER_POLICY = Object.freeze({
  maxConcurrency: 1,
  maxRequestRetries: 0,
  maxSessionRotations: 0,
  navigationTimeoutSecs: 40,
  requestHandlerTimeoutSecs: 60,
  respectRobotsTxtFile: Object.freeze({ userAgent: "*" }),
  retryOnBlocked: false,
});

export function selectBrowserSources(manifest) {
  const defaults = manifest?.defaults || {};
  return (manifest?.sources || [])
    .filter(
      (item) =>
        item.transport === "browser" &&
        (item.enabled ?? defaults.enabled ?? true),
    )
    .map((item) => ({
      ...item,
      min_content_chars:
        item.min_content_chars ?? defaults.min_content_chars ?? 300,
      timeout_seconds: item.timeout_seconds ?? defaults.timeout_seconds ?? 40,
    }));
}

export function catalogRobotsExclusions(sources) {
  return Object.fromEntries(
    sources
      .filter((source) => source.robots_denied)
      .map((source) => [
        source.id,
        `catalog robots.txt disallow verified ${source.robots_checked_at}: ${source.robots_evidence}`,
      ]),
  );
}

const isAuthRedirect = (sourceUrl, finalUrl) => {
  if (!finalUrl || finalUrl === sourceUrl) return false;
  const source = new URL(sourceUrl);
  const final = new URL(finalUrl);
  if (source.hostname !== final.hostname) return false;
  return ["/auth", "/login", "/sign-in", "/signin"].includes(
    final.pathname.replace(/\/$/, "").toLocaleLowerCase("en-US"),
  );
};

export function statusCodeFromError(error) {
  const match = String(error || "").match(/\breceived\s+(\d{3})\s+status code\b/i);
  if (!match) return null;
  const statusCode = Number(match[1]);
  return statusCode >= 400 && statusCode <= 599 ? statusCode : null;
}

export function evaluatePage(source, page) {
  if (page.skippedReason) {
    return { outcome: "robots_denied", error: page.skippedReason };
  }

  const content = typeof page.content === "string" ? page.content : "";
  const combined = `${page.error || ""}\n${page.title || ""}\n${content}`.toLocaleLowerCase(
    "en-US",
  );
  if (page.statusCode === 429) return { outcome: "rate_limited", error: "HTTP 429" };
  if (page.statusCode === 401) return { outcome: "auth_required", error: "HTTP 401" };
  if (page.statusCode === 403) return { outcome: "blocked", error: "HTTP 403" };
  if (isAuthRedirect(source.url, page.finalUrl)) {
    return { outcome: "auth_required", error: "authentication redirect detected" };
  }

  const marker = BLOCK_MARKERS.find((candidate) => combined.includes(candidate));
  if (marker) {
    return { outcome: "blocked", error: `anti-bot marker found: ${marker}` };
  }
  if (page.error) {
    const error = String(page.error);
    const lowered = error.toLocaleLowerCase("en-US");
    const outcome = lowered.includes("timeout") ? "timeout" : "error";
    return { outcome, error };
  }
  if (page.statusCode === null || page.statusCode === undefined) {
    return { outcome: "error", error: "missing navigation status" };
  }
  if (page.statusCode >= 400) {
    return { outcome: "http_error", error: `HTTP ${page.statusCode}` };
  }

  const loweredContent = `${page.title || ""}\n${content}`.toLocaleLowerCase("en-US");
  const missingTerms = (source.required_terms || []).filter(
    (term) => !loweredContent.includes(String(term).toLocaleLowerCase("en-US")),
  );
  if (missingTerms.length > 0) {
    return {
      outcome: "invalid_content",
      error: `required term missing: ${missingTerms.join(", ")}`,
    };
  }
  if (content.length < source.min_content_chars) {
    return {
      outcome: "invalid_content",
      error: `content shorter than minimum: ${content.length} < ${source.min_content_chars}`,
    };
  }
  return { outcome: "success", error: "" };
}

export function buildResult(source, page) {
  const content = typeof page.content === "string" ? page.content : "";
  const evaluation = evaluatePage(source, page);
  return {
    source_id: source.id,
    name: source.name,
    url: source.url,
    final_url: page.finalUrl || source.url,
    engine: "crawlee-playwright",
    outcome: evaluation.outcome,
    status_code: page.statusCode ?? null,
    elapsed_ms: Number(page.elapsedMs || 0),
    content_chars: content.length,
    content_sha256: content
      ? createHash("sha256").update(content, "utf8").digest("hex")
      : "",
    preview: content.replace(/\s+/g, " ").slice(0, 500),
    title: page.title || "",
    error: evaluation.error,
  };
}

export function buildSkippedResultForEvent(sourceByUrl, event) {
  const source = sourceByUrl.get(event?.url);
  if (!source) return null;
  const reason = String(event.reason || "unknown");
  const page = {
    statusCode: null,
    title: "",
    content: "",
    finalUrl: source.url,
    elapsedMs: 0,
  };
  if (reason === "robotsTxt") {
    page.skippedReason = `robots.txt skipped request: ${reason}`;
  } else {
    page.error = `Crawlee skipped request: ${reason}`;
  }
  return buildResult(source, page);
}

export function summarizeResults(results) {
  const eligible = results.filter((result) => result.outcome !== "robots_denied");
  const successes = eligible.filter((result) => result.outcome === "success").length;
  return {
    cohort_denominator: results.length,
    eligible_denominator: eligible.length,
    robots_denied: results.length - eligible.length,
    successes,
    failures: eligible.length - successes,
    success_rate: eligible.length
      ? Number(((successes / eligible.length) * 100).toFixed(2))
      : 0,
  };
}
