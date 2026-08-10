const FEED_ACCEPT =
  "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1";
const MAX_DECLARED_BYTES = 2_000_000;

export const TARGETS = Object.freeze({
  aussie_stock_forums_rss: Object.freeze({
    url: "https://www.aussiestockforums.com/forums/-/index.rss",
  }),
  bitcoin_stackexchange_feed: Object.freeze({
    url: "https://bitcoin.stackexchange.com/feeds",
  }),
  bogleheads_forum_feed: Object.freeze({
    url: "https://www.bogleheads.org/forum/app.php/feed",
  }),
  financial_wisdom_forum_feed: Object.freeze({
    url: "https://www.financialwisdomforum.org/forum/app.php/feed",
  }),
  money_stackexchange_feed: Object.freeze({
    url: "https://money.stackexchange.com/feeds",
  }),
  mr_money_mustache_feed: Object.freeze({
    url: "https://forum.mrmoneymustache.com/index.php?action=.xml;type=rss",
  }),
  quant_stackexchange_feed: Object.freeze({
    url: "https://quant.stackexchange.com/feeds",
  }),
});

const PUBLIC_RESPONSE_HEADERS = Object.freeze([
  "content-type",
  "etag",
  "last-modified",
]);

function jsonResponse(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

export function createHandler(fetchUpstream) {
  return async function handle(request) {
    if (request.method !== "GET") {
      return jsonResponse({ error: "method_not_allowed" }, 405);
    }

    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return jsonResponse({ ok: true, routes: Object.keys(TARGETS).sort() }, 200);
    }

    const match = /^\/v1\/feed\/([a-z][a-z0-9_]{1,63})$/.exec(url.pathname);
    const routeId = match?.[1];
    const target = routeId ? TARGETS[routeId] : undefined;
    if (!target) {
      return jsonResponse({ error: "route_not_found" }, 404);
    }

    let upstream;
    try {
      upstream = await fetchUpstream(target.url, {
        headers: {
          Accept: FEED_ACCEPT,
          "User-Agent":
            "FinanceCrawlerCapabilityProbe/0.2 (+https://github.com/AlanChen75/finance-crawler-poc)",
        },
        redirect: "manual",
        cf: {
          cacheEverything: true,
          cacheTtlByStatus: { "200-299": 300, "300-599": 0 },
        },
      });
    } catch (error) {
      console.error(JSON.stringify({ event: "upstream_fetch_failed", routeId, error: String(error) }));
      return jsonResponse({ error: "upstream_fetch_failed", route: routeId }, 502);
    }

    if (upstream.status >= 300 && upstream.status < 400) {
      return jsonResponse(
        { error: "upstream_redirect_rejected", route: routeId, status: upstream.status },
        502,
      );
    }
    const declaredLength = Number(upstream.headers.get("content-length") || "0");
    if (declaredLength > MAX_DECLARED_BYTES) {
      return jsonResponse({ error: "upstream_too_large", route: routeId }, 502);
    }

    const headers = new Headers({
      "Cache-Control": upstream.ok ? "public, max-age=300" : "no-store",
      "X-Content-Type-Options": "nosniff",
      "X-Crawler-Origin-Status": String(upstream.status),
      "X-Crawler-Route": routeId,
    });
    for (const name of PUBLIC_RESPONSE_HEADERS) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  };
}

const handle = createHandler(fetch);

export default {
  async fetch(request) {
    return handle(request);
  },
};
