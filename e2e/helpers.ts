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
    // fallback() (not continue()) so a layered mock handler still gets a chance;
    // with no other handler this continues to the network as before.
    return route.fallback();
  });
  return { blocked: () => blocked };
}

/**
 * Get past the first-run gate. If the agent is configured we land directly in
 * the shell; otherwise we click "Skip (manual mode)" to enter manual mode.
 */
export async function enterApp(page: Page): Promise<void> {
  await page.goto("/");
  // NOTE: never wait for "networkidle" here -- the app polls the agent health
  // endpoint on an interval, so the network is never idle. Wait for concrete
  // UI instead. The shell renders under the wizard, so the Projects rail is
  // visible even before we dismiss the wizard.
  await expect(page.getByText("Projects", { exact: true })).toBeVisible();
  const skip = page.getByRole("button", { name: "Skip (manual mode)" });
  if (await skip.isVisible().catch(() => false)) {
    await skip.click();
  }
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

// The web app hardcodes this origin (lib/agent-client.ts AGENT_URL); the mock
// MUST match it exactly or health calls miss the interceptor and the app hangs
// on "Connecting to agent...".
const AGENT_ORIGIN = "http://127.0.0.1:7842";

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
  // Unconfigured agents have no source connection, so they list no projects
  // (keeps the manual-mode "empty board" state truthful).
  const projects = opts.projects ?? (configured ? ["Demo Project"] : []);

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
  const workItemRow = {
    wi_id: 101,
    title: "As a user I can sign in",
    wi_type: "User Story",
    state: "Active",
    board_column: "Doing",
    board_lane: "",
    assigned_to: "Alice",
    tags: [] as string[],
    iteration_path: "Sprint 1",
  };
  const workItemDetail = {
    ...workItemRow,
    area_path: "Demo",
    description_html: "<p>Login story</p>",
    acceptance_html: "<p>Given valid creds, I can sign in.</p>",
    url: "https://demo-org/_workitems/edit/101",
  };

  await page.route(`${AGENT_ORIGIN}/**`, (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/health") return json(route, health);
    if (path === "/capabilities") return json(route, {});
    if (path === "/settings" && route.request().method() === "GET")
      return json(route, settings);
    if (path === "/settings") return json(route, settings); // POST save
    if (path === "/settings/tour") return json(route, { ok: true });
    // /sources/projects -> string[]
    if (path === "/sources/projects") return json(route, projects);
    // /sources/boards/{project} -> Board[]
    if (path.startsWith("/sources/boards"))
      return json(route, [
        {
          id: "b1",
          name: "Board 1",
          team_id: "t1",
          team_name: "Team 1",
          label: "Board 1",
        },
      ]);
    // /sources/workitems/stream -> Server-Sent Events (batch + done). The
    // board loader consumes this first and only falls back to the blocking
    // endpoint if the stream throws, so it must be real SSE.
    if (path === "/sources/workitems/stream") {
      const body =
        `data: ${JSON.stringify({
          type: "batch",
          items: [workItemRow],
          columns: ["Doing"],
        })}\n\n` +
        `data: ${JSON.stringify({
          type: "done",
          columns: ["Doing"],
          total: 1,
        })}\n\n`;
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body,
      });
    }
    // /sources/workitems (POST) blocking fallback
    if (path === "/sources/workitems")
      return json(route, {
        columns: ["Doing"],
        groups: [{ column: "Doing", items: [workItemRow] }],
        total: 1,
      });
    // /sources/workitem/{project}/{id} -> RawWorkItemDetail
    if (path.startsWith("/sources/workitem/"))
      return json(route, workItemDetail);

    // --- Generation flow (local, safe; never touches ADO) ---
    if (path === "/generate/start")
      return json(route, { job_id: "job-1" });
    if (path.startsWith("/jobs/"))
      return json(route, {
        id: "job-1",
        kind: "generate",
        state: "done",
        logs: ["[INFO] generating", "[SUCCESS] done"],
        log_count: 2,
        progress: { stage: "done", current: 1, total: 1 },
        error: "",
        result: {
          payload: {
            stories: [
              {
                parent_work_item_id: 101,
                title: "As a user I can sign in",
                test_cases: [
                  { action: "Open login", expected: "Login page shown" },
                ],
              },
            ],
          },
          n_test_cases: 1,
          n_stories: 1,
          xlsx_path: "/tmp/out/TC-101.xlsx",
          xlsx_name: "TC-101.xlsx",
          quality: { avg_score: 82, below_threshold: 0 },
          coverage: {
            total_work_items: 1,
            covered: 1,
            uncovered: 0,
            coverage_pct: 100,
          },
        },
      });

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
