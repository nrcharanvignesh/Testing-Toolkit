import type { Page, Route } from "@playwright/test";
import { expect } from "@playwright/test";

/**
 * Endpoints on the local agent that MUTATE Azure DevOps. The test suite must
 * NEVER hit these. We block them at the network layer as a hard safety net so
 * that even an accidental click can never create/modify ADO work items.
 */
export const ADO_WRITE_PATHS = [
  "/generate/push",
  "/generate/push-xlsx",
  "/defects/upload",
  "/ado/create",
  "/ado/update",
];

/**
 * Install a route guard on the page that aborts any request to an ADO-write
 * endpoint and records the attempt. Call this immediately after creating the
 * page / before navigation. Returns a getter for any blocked attempts so a
 * test can assert that nothing was pushed.
 */
export function guardAdoWrites(page: Page): { blocked: () => string[] } {
  const blocked: string[] = [];
  page.route("**/*", (route: Route) => {
    const url = route.request().url();
    const method = route.request().method();
    const isWrite =
      (method === "POST" || method === "PUT" || method === "PATCH" || method === "DELETE") &&
      ADO_WRITE_PATHS.some((p) => url.includes(p));
    if (isWrite) {
      blocked.push(`${method} ${url}`);
      // eslint-disable-next-line no-console
      console.warn(`[e2e] BLOCKED ADO-write attempt: ${method} ${url}`);
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  return { blocked: () => blocked };
}

/** True when the local agent reports it is configured (has PAT + LLM key). */
export async function agentConfigured(page: Page): Promise<boolean> {
  try {
    const base = (process.env.AGENT_URL ?? "http://127.0.0.1:7842").replace(/\/$/, "");
    const res = await page.request.get(`${base}/settings`);
    if (!res.ok()) return false;
    const json = await res.json();
    return Boolean(json?.configured);
  } catch {
    return false;
  }
}

/**
 * Get past the first-run gate. If the agent is configured we land directly in
 * the shell; otherwise we click "Skip (manual mode)" to enter manual mode.
 */
export async function enterApp(page: Page): Promise<void> {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  const skip = page.getByRole("button", { name: "Skip (manual mode)" });
  if (await skip.isVisible().catch(() => false)) {
    await skip.click();
  }
  // The Projects rail is the canonical "we are inside the app" marker.
  await expect(page.getByText("Projects", { exact: true })).toBeVisible();
}

/** Select the first project in the navigator and wait for boards to populate. */
export async function selectFirstProject(page: Page): Promise<boolean> {
  const refresh = page
    .locator("div", { hasText: /^Projects$/ })
    .getByRole("button", { name: "Refresh" })
    .first();
  await refresh.click().catch(() => {});
  const firstProject = page.locator('[data-selected]').first();
  if (!(await firstProject.isVisible().catch(() => false))) return false;
  await firstProject.click();
  return true;
}
