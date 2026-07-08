import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E config for the Testing Toolkit web app.
 *
 * The specs drive the app against a MOCKED local agent (see mockAgent in
 * e2e/helpers.ts), which fulfills the agent HTTP contract at the network layer.
 * This means the full suite runs deterministically in CI/sandbox with NO real
 * agent, PAT, or LLM key. ADO-write endpoints are additionally hard-blocked by
 * guardAdoWrites so a test can never mutate real ADO data.
 *
 * To instead run against a real local agent, simply do not install mockAgent in
 * a spec; the app will talk to http://127.0.0.1:7842 directly.
 *
 * Target either your local dev server or the deployed app:
 *   PLAYWRIGHT_BASE_URL=http://localhost:3000            (local dev)
 *   PLAYWRIGHT_BASE_URL=https://testing-toolkit.vercel.app (production)
 *
 * Run:
 *   npm run test:e2e            # headless
 *   npm run test:e2e:headed     # watch it drive the browser
 *   npm run test:e2e:ui         # Playwright UI mode
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  timeout: 120_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 20_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
});
