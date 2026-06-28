import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E config for the Testing Toolkit web app.
 *
 * IMPORTANT: these tests must run on a machine where the local compute agent
 * (http://127.0.0.1:7842) is running and configured with a real ADO PAT + LLM
 * key. The web app talks to that agent directly from the browser, so the agent
 * cannot be reached from a CI sandbox or any other machine.
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
