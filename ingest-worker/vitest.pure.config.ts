import { defineConfig } from "vitest/config";

/**
 * Pure unit-test lane for deterministic helpers that do not need workerd/D1.
 * The default vitest.config.ts intentionally exercises the Worker runtime;
 * this lane keeps identity/relevance tests runnable when Wrangler auth or
 * preview subdomain access is unavailable.
 */
export default defineConfig({
  test: {
    include: ["test/target-evidence.test.ts", "test/provider-registry.test.ts"],
  },
});
