import { test, expect } from "@playwright/test";
import { guardAdoWrites, agentConfigured, enterApp, selectFirstProject } from "./helpers";

/**
 * READ-ONLY ADO flows: list projects, list boards, load work items, open a work
 * item's detail. These require a configured agent (real PAT). They auto-skip if
 * the agent is not configured. No ADO writes occur and the route guard blocks
 * any write attempt regardless.
 */
test.describe("ADO read flows (no writes)", () => {
  test.beforeEach(async ({ page }) => {
    guardAdoWrites(page);
    test.skip(!(await agentConfigured(page)), "agent not configured (no PAT) — skipping live reads");
    await enterApp(page);
  });

  test("lists projects, loads boards, and renders the work item grid", async ({ page }) => {
    const haveProject = await selectFirstProject(page);
    test.skip(!haveProject, "no projects returned by ADO for this PAT");

    // Boards rail should populate (or explicitly say none found).
    await expect(
      page.locator('[data-selected]').nth(1).or(page.getByText("No boards found.")),
    ).toBeVisible({ timeout: 30_000 });

    // Pick the first board if present.
    const boards = page.locator('[data-selected]');
    if ((await boards.count()) > 1) {
      await boards.nth(1).click();
      // Work items grid loads (header + filter input) without error.
      await expect(page.getByPlaceholder("Filter by id or title...")).toBeVisible();
      await expect(page.getByText(/Loading work items\.\.\./)).toHaveCount(0, {
        timeout: 60_000,
      });
    }
  });

  test("selecting a work item populates the detail pane", async ({ page }) => {
    const haveProject = await selectFirstProject(page);
    test.skip(!haveProject, "no projects returned by ADO for this PAT");

    const boards = page.locator('[data-selected]');
    test.skip((await boards.count()) < 2, "no board to load");
    await boards.nth(1).click();
    await expect(page.getByText(/Loading work items\.\.\./)).toHaveCount(0, { timeout: 60_000 });

    // Click the first work item row (rows carry a title attribute).
    const firstRow = page.locator("[title]").filter({ hasText: /\S/ }).first();
    test.skip(!(await firstRow.isVisible().catch(() => false)), "no work items in board");
    await firstRow.click();

    // Detail pane shows the Detail/Outputs tabs.
    await expect(page.getByRole("button", { name: "Outputs" })).toBeVisible();
  });
});
