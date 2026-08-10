const source = (url, requiredTerms, options = {}) =>
  Object.freeze({
    url,
    required_terms: Object.freeze(requiredTerms),
    min_content_chars: 300,
    eligible: options.eligible ?? true,
    exclusion_reason: options.exclusionReason || "",
  });

// Experimental invariant: this allowlist is the exact set of Browser URLs that
// failed the Crawl4AI baseline for anti-bot/CDN reasons. The four robots-denied
// sources are deliberately excluded and arbitrary target URLs are never accepted.
export const SOURCES = Object.freeze({
  bogleheads_personal_investments: source(
    "https://www.bogleheads.org/forum/viewforum.php?f=1",
    ["Bogleheads", "Personal Investments"],
  ),
  quant_stackexchange_hot: source(
    "https://quant.stackexchange.com/questions?tab=Hot",
    ["Quantitative Finance"],
    {
      eligible: false,
      exclusionReason: "robots_disallow_all_2026-08-09",
    },
  ),
  hotcopper_home: source("https://hotcopper.com.au/", ["HotCopper"]),
  advfn_uk_share_chat: source(
    "https://uk.advfn.com/stock-market/london/share-chat",
    ["ADVFN"],
  ),
  financial_wisdom_forum: source(
    "https://www.financialwisdomforum.org/forum/",
    ["Financial Wisdom Forum"],
  ),
  investing_analysis: source(
    "https://www.investing.com/analysis/",
    ["Investing.com", "Analysis"],
  ),
  mr_money_mustache_forum: source(
    "https://forum.mrmoneymustache.com/",
    ["Money Mustache"],
  ),
  white_coat_investor_forum: source(
    "https://forum.whitecoatinvestor.com/",
    ["White Coat Investor"],
  ),
});

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

export function evaluateContent(content, requiredTerms, minimum) {
  const normalized = typeof content === "string" ? content : "";
  const lowered = normalized.toLocaleLowerCase("en-US");
  const marker = BLOCK_MARKERS.find((candidate) => lowered.includes(candidate));
  if (marker) {
    return { ok: false, failure_category: "anti_bot_blocked", marker };
  }

  const missingTerms = requiredTerms.filter(
    (term) => !lowered.includes(term.toLocaleLowerCase("en-US")),
  );
  if (missingTerms.length > 0) {
    return {
      ok: false,
      failure_category: "content_validation_failed",
      missing_terms: missingTerms,
    };
  }
  if (normalized.length < minimum) {
    return {
      ok: false,
      failure_category: "content_too_short",
      content_length: normalized.length,
      minimum,
    };
  }
  return { ok: true };
}

const jsonResponse = (body, status = 200, extraHeaders = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...extraHeaders,
    },
  });

const errorMessage = (payload, fallback) => {
  if (typeof payload?.error === "string" && payload.error) return payload.error;
  if (typeof payload?.message === "string" && payload.message) return payload.message;
  return fallback;
};

export async function handleRequest(request, env) {
  if (request.method !== "GET") {
    return jsonResponse({ ok: false, error: "method not allowed" }, 405, { allow: "GET" });
  }

  const url = new URL(request.url);
  if (url.pathname === "/health") {
    return jsonResponse({
      ok: true,
      service: "cloudflare-browser-run-pilot",
      source_count: Object.keys(SOURCES).length,
    });
  }

  const match = url.pathname.match(/^\/probe\/([a-z0-9_]+)$/);
  const sourceId = match?.[1];
  const selected = sourceId ? SOURCES[sourceId] : undefined;
  if (!selected) {
    return jsonResponse({ ok: false, error: "unknown fixed source id" }, 404);
  }
  if (!selected.eligible) {
    return jsonResponse(
      {
        ok: false,
        source_id: sourceId,
        source_url: selected.url,
        failure_category: "robots_denied",
        error: "robots.txt currently disallows all crawling",
      },
      403,
    );
  }

  const waitParameter = url.searchParams.get("wait_ms");
  const waitAfterLoadMs = waitParameter === null ? 5000 : Number(waitParameter);
  if (![5000, 15000].includes(waitAfterLoadMs)) {
    return jsonResponse(
      { ok: false, error: "wait_ms must be the fixed 5000 or 15000 variant" },
      400,
    );
  }

  let browserResponse;
  try {
    browserResponse = await env.BROWSER.quickAction("markdown", {
      url: selected.url,
      gotoOptions: { timeout: 60000, waitUntil: "networkidle2" },
      waitForTimeout: waitAfterLoadMs,
    });
  } catch (error) {
    return jsonResponse(
      {
        ok: false,
        source_id: sourceId,
        source_url: selected.url,
        error: error instanceof Error ? error.message : String(error),
      },
      502,
    );
  }

  const browserMs = Number(browserResponse.headers.get("x-browser-ms-used") || 0);
  let payload;
  try {
    payload = await browserResponse.json();
  } catch {
    return jsonResponse(
      {
        ok: false,
        source_id: sourceId,
        source_url: selected.url,
        browser_ms: browserMs,
        error: `Browser Run returned non-JSON status ${browserResponse.status}`,
      },
      502,
    );
  }

  const content = typeof payload?.result === "string" ? payload.result : "";
  if (!browserResponse.ok || payload?.success === false || !content) {
    return jsonResponse(
      {
        ok: false,
        source_id: sourceId,
        source_url: selected.url,
        browser_ms: browserMs,
        error: errorMessage(payload, `Browser Run status ${browserResponse.status}`),
      },
      502,
    );
  }

  return jsonResponse({
    ok: true,
    source_id: sourceId,
    source_url: selected.url,
    route: "cloudflare_browser_run_markdown",
    browser_ms: browserMs,
    wait_after_load_ms: waitAfterLoadMs,
    content,
    evaluation: evaluateContent(
      content,
      selected.required_terms,
      selected.min_content_chars,
    ),
  });
}

export default { fetch: handleRequest };
