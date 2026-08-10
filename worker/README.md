# Fixed feed relay

This Worker is an egress fallback for seven explicit RSS/Atom endpoints. It is intentionally not a general proxy.

```bash
npm install
npm test
npm run types
npm run deploy:dry
npm run deploy:staging
```

After staging deploy, set the public Worker origin as the GitHub repository variable `CF_RELAY_BASE_URL`. The Python probe first tries the manifest URL directly and only calls the matching `/v1/feed/<source_id>` route after a 403, 429, network failure, or 5xx response. Report schema v4 preserves both delivery attempts.

Safety properties:

- fixed route-to-origin allowlist;
- GET only;
- no caller-provided target URL;
- upstream redirects rejected;
- declared response bodies over 2 MB rejected;
- five-minute Cloudflare cache;
- response streamed without buffering the full feed in Worker memory.
