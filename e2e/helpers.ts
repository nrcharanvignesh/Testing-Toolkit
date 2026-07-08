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

// ---------------------------------------------------------------------------
// In-sandbox agent mock
// ---------------------------------------------------------------------------
/**
 * Options that shape the mocked agent's reported state.
 * - configured=false  -> first-run SetupWizard renders (Skip / Save & Connect)
 * - configured=true   -> app boots straight into the shell with data
 */
export interface MockAgentOptions {
  configured?: boolean;
  tourCompleted?: boolean;
  projects?: string[];
}

const AGENT_ORIGIN = (process.env.AGENT_URL ?? "http://127.0.0.1:7842").replace(
  /\/$/,
  ""
);

/**
 * Fulfill the local-agent HTTP contract at the network layer so the entire web
 * app can be driven in a CI sandbox with NO real agent running. This makes the
 * onboarding/dialog specs deterministic and lets the read/generate specs run
 * against stubbed data instead of auto-skipping. ADO-write endpoints are NOT
 * mocked here; guardAdoWrites still blocks them as a hard safety net.
 *
 * Install BEFORE navigation and before guardAdoWrites so the write-guard's
 * abort takes precedence for mutating calls.
 */
export async function mockAgent(
  page: Page,
  opts: MockAgentOptions = {}
): Promise<void> {
  const configured = opts.configured ?? true;
  const tourCompleted = opts.tourCompleted ?? true;
  const projects = opts.projects ?? ["Demo Project"];

  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

  const health = {
    status: "ok",
    version: "2.7.1",
    user: "e2e",
    machine: "sandbox",
    models_loaded: true,
    tls_mode: "system",
  };
  const settings = {
    configured,
    has_api_key: configured,
    has_pat: configured,
    organization: configured ? "demo-org" : "",
    model: "claude-sonnet-4",
    fast_model: "claude-haiku-4",
    fallback_model: "claude-sonnet-4",
    base_url: configured ? "https://api.anthropic.com" : "",
    project_prefix: "",
    tour_completed: tourCompleted,
    jira_configured: false,
  };
  const workItems = [
    {
      wi_id: 101,
      wi_type: "User Story",
      title: "As a user I can sign in",
      state: "Active",
      assigned_to: "Alice",
      board_column: "Doing",
      tags: [],
      description: "Login story",
      acceptance_criteria: "Given valid creds, I can sign in.",
    },
  ];

  await page.route(`${AGENT_ORIGIN}/**`, (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/health") return json(route, health);
    if (path === "/capabilities") return json(route, {});
    if (path === "/settings" && route.request().method() === "GET")
      return json(route, settings);
    if (path === "/settings") return json(route, settings); // POST save
    if (path === "/settings/tour") return json(route, { ok: true });
    if (path === "/sources/projects")
      return json(route, { projects: projects.map((name) => ({ name })) });
    if (path === "/sources/boards")
      return json(route, {
        boards: [
          { id: "b1", name: "Board 1", team_id: "t1", team_name: "Team 1" },
        ],
      });
    if (path === "/sources/workitems" || path === "/sources/workitems/stream")
      return json(route, {
        columns: [{ id: "Doing", name: "Doing", column_type: "" }],
        rows: workItems,
      });
    if (path.startsWith("/sources/workitem/"))
      return json(route, workItems[0]);
    if (path === "/metrics") return json(route, { cpu_percent: 5 });
    if (path === "/doctor") return json(route, { status: "pass", checks: [] });
    if (path.startsWith("/update/"))
      return json(route, { status: "up_to_date" });

    // Default: empty OK so nothing hangs on an unmocked read.
    return json(route, {});
  });
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
