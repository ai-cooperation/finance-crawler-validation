# Cloudflare Browser Run fixed-eight pilot

This experiment preserves the eight Browser URLs that the GitHub Actions +
Crawl4AI baseline classified as anti-bot/CDN failures. A fresh preflight on
2026-08-09 found that Quant StackExchange now returns `Disallow: /`, so that
source remains in the cohort for auditability but is excluded before Browser
Run. The Worker does not accept arbitrary target URLs.

The Worker uses Cloudflare Browser Run's `markdown` Quick Action through a
remote binding. It waits for `networkidle2` and then another five seconds so a
JavaScript-rendered page or challenge has time to settle. A result passes only
when it contains every required source term, is at least 300 characters, and
contains none of the baseline anti-bot markers. Full page content is discarded;
the result artifact stores only its length and SHA-256 hash.

Run unit tests and a configuration check:

```sh
npm install
npm test
npm run check
```

Start the temporary remote-binding development Worker in one terminal, then
run the fixed pilot in another:

```sh
npm run dev:remote
npm run pilot -- --output results/latest.json
```

The default 11-second interval stays within the Workers Free Quick Action
limit of one request per 10 seconds. Stop the development Worker after the
pilot; this experiment does not require a permanent public deployment.
