# Fixed feed relay

This Worker is an egress fallback for ten explicit RSS/Atom endpoints. It is intentionally not a general proxy. Three finance-news routes are included only because the same official feeds returned `403` from GitHub Actions; they remain fixed route-to-origin mappings.

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
