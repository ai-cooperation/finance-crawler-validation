# Crawlee Browser treatment

This treatment runs Crawlee `PlaywrightCrawler` against the exact 38 enabled
Browser sources in `foreign-community-sources.yaml`. It uses GitHub Actions
egress without a proxy, one request attempt per eligible URL, no session
rotation, concurrency one, and Crawlee's robots.txt enforcement.

Seven versioned robots exclusions are read from the shared manifest. They remain
in the 38-URL governance cohort but are not sent to Chromium, leaving 31
technically eligible URLs. The result uses the same status, anti-bot marker,
required-term and minimum-content contract as the Crawl4AI probe. Full page
content is discarded; only a bounded preview, length and SHA-256 hash are
retained.

```sh
npm ci
npm test
npx playwright install chromium
npm run probe -- --manifest ../../foreign-community-sources.yaml --output ../../artifacts/crawlee-browser.json
```
