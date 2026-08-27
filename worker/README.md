# Fixed feed relay

This Worker is an egress fallback for a fixed allowlist of explicit RSS/Atom/JSON endpoints. It is intentionally not a general proxy. The five `*_alt` routes are publisher-scoped fallback IDs used only after the primary route fails; they remain bound to the same publisher/independence group in the crawler registry.

```bash
npm install
npm test
npm run types
npm run deploy:dry
npm run deploy:staging
```

After staging deploy, set the public Worker origin as the GitHub repository variable `CF_RELAY_BASE_URL`. The Python probe first tries the catalog URL directly and only calls the endpoint's declared `/v1/feed/<endpoint_id>` route after a 403, 429, network failure, or 5xx response. Both direct and relay delivery attempts are preserved inside the endpoint observation.

Safety properties:

- fixed route-to-origin allowlist;
- GET only;
- no caller-provided target URL;
- upstream redirects rejected;
- declared response bodies over 2 MB rejected;
- five-minute Cloudflare cache;
- response streamed without buffering the full feed in Worker memory.
