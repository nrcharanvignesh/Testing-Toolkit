import { test, expect } from "@playwright/test";
import { guardAdoWrites, enterApp, selectFirstProject, mockAgent } from "./helpers";

/**
 * GENERATE flow WITHOUT pushing to ADO.
 *
 * Generation (`/generate/start` + `/generate/dump`) produces test cases locally
 * and is safe. The "Push to ADO" button (`/generate/push`) is NEVER clicked and
 * is additionally blocked by the network guard. The test asserts that zero
 * ADO-write requests were made.
 */
test.describe("Generate test cases (no ADO push)", () => {
  test("opens generate dialog, runs AI Generate, and never pushes to ADO", async ({ page }) => {
    await mockAgent(page, { configured: true });
    const guard = guardAdoWrites(page);
    await enterApp(page);

    const haveProject = await selectFirstProject(page);
    test.skip(!haveProject, "no projects for this PAT");

    // Boards load asynchronously after the project is selected; wait for the
    // board row (2nd [data-selected]) rather than counting immediately.
    const boards = page.locator('[data-selected]');
    await expect(boards.nth(1)).toBeVisible({ timeout: 30_000 });
    await boards.nth(1).click();
    await expect(page.getByText(/Loading work items\.\.\./)).toHaveCount(0, { timeout: 60_000 });

    // Tick the first work item checkbox.
    const firstCheck = page.getByRole("checkbox").first();
    test.skip(!(await firstCheck.isVisible().catch(() => false)), "no work items to select");
    await firstCheck.check();
    await expect(page.getByText(/work item\(s\) selected/)).toBeVisible();

    // Open the Implementation generate dialog from the action strip.
    await page.getByRole("button", { name: "Implementation", exact: true }).click();
    await expect(page.getByRole("button", { name: /AI Generate|Generating\.\.\./ })).toBeVisible();

    // Run generation (local only). Allow generous time for the LLM.
    await page.getByRole("button", { name: "AI Generate" }).click();
    await expect(page.getByRole("button", { name: "AI Generate" })).toBeEnabled({
      timeout: 180_000,
    });

    // "Push to ADO" exists but we never click it.
    await expect(page.getByRole("button", { name: "Push to ADO" })).toBeVisible();

    // Close the dialog instead of pushing. There is an icon "Close" (aria-label)
    // and the footer text button (both accessible name "Close"); target the
    // footer text button by its visible text node.
    await page.locator("button.tt-btn-ghost", { hasText: /^Close$/ }).click();

    // HARD ASSERTION: nothing was ever pushed to ADO.
    expect(guard.blocked(), `blocked ADO-write attempts: ${guard.blocked().join(", ")}`).toHaveLength(0);
  });
});
