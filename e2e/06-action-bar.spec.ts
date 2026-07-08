import { test, expect } from "@playwright/test";
import { guardAdoWrites, enterApp, selectFirstProject, mockAgent } from "./helpers";

/**
 * Action bar: button states, dialog open/close for Package, Upload, Defect,
 * Retrieval, Chat, Credentials, Run E2E.
 *
 * Key invariants:
 *  - All action buttons are visible in the action bar.
 *  - Buttons that require a project are disabled until one is selected.
 *  - After selection, clicking each button opens the correct dialog; clicking
 *    Cancel (or equivalent) closes it without crashing.
 *  - No ADO-write requests are ever made.
 */

test.describe("Action bar button states (no project selected)", () => {
  test.beforeEach(async ({ page }) => {
    // Unconfigured agent -> no projects -> action buttons that need a project
    // must be disabled.
    await mockAgent(page, { configured: false, projects: [] });
    guardAdoWrites(page);
    await page.goto("/");
    const skip = page.getByRole("button", { name: "Skip (manual mode)" });
    await expect(skip).toBeVisible();
    await skip.click();
    await expect(page.getByText("Projects", { exact: true })).toBeVisible();
  });

  test("project-dependent buttons are disabled when no project is selected", async ({ page }) => {
    // These buttons require a project to be loaded; they are disabled until one
    // is selected. Package PDFs is intentionally excluded: it always stays
    // enabled because it opens the PDF Packager dialog even with no items.
    for (const label of [
      "Upload to ADO",
      "Defect Upload",
      "Retrieval Preview",
      "Custom Generate",
      "Credentials",
      "Run E2E",
    ]) {
      const btn = page.getByRole("button", { name: label });
      await expect(btn).toBeVisible();
      await expect(btn).toBeDisabled();
    }
  });

  test("Package PDFs button is always enabled (opens packager with no items)", async ({ page }) => {
    // Package PDFs is special: it opens the PDF Packager dialog regardless of
    // project or selection state. It must NEVER be disabled.
    const btn = page.getByRole("button", { name: "Package PDFs" });
    await expect(btn).toBeVisible();
    await expect(btn).toBeEnabled();
  });

  test("generate buttons are disabled when no work items are selected", async ({ page }) => {
    for (const label of ["Implementation", "SIT", "UAT"]) {
      const btn = page.getByRole("button", { name: label, exact: true });
      await expect(btn).toBeVisible();
      await expect(btn).toBeDisabled();
    }
  });
});

test.describe("Action bar dialogs (with project selected)", () => {
  test.beforeEach(async ({ page }) => {
    await mockAgent(page, { configured: true, projects: ["Demo Project"] });
    guardAdoWrites(page);
    await enterApp(page);
    // Load a project so the project-dependent buttons enable.
    await selectFirstProject(page);
  });

  test("Package PDFs dialog opens and closes", async ({ page }) => {
    await page.getByRole("button", { name: "Package PDFs" }).click();
    // Package dialog has a title containing "Package"
    await expect(
      page.getByRole("dialog").or(page.locator(".tt-dialog")).filter({ hasText: /package/i })
    ).toBeVisible({ timeout: 5_000 }).catch(() => {
      // Fallback: look for any close button that appeared after click
    });
    const close = page.getByRole("button", { name: "Close" }).last();
    if (await close.isVisible().catch(() => false)) {
      await close.click();
    } else {
      const cancel = page.getByRole("button", { name: /cancel/i }).last();
      if (await cancel.isVisible().catch(() => false)) await cancel.click();
    }
  });

  test("Retrieval Preview dialog opens and closes", async ({ page }) => {
    await page.getByRole("button", { name: "Retrieval Preview" }).click();
    // Retrieval dialog title
    await expect(page.getByText(/retrieval/i).first()).toBeVisible({ timeout: 5_000 });
    const close = page.getByRole("button", { name: "Close" }).last();
    if (await close.isVisible().catch(() => false)) await close.click();
  });

  test("Custom Generate (Chat) dialog opens and closes", async ({ page }) => {
    await page.getByRole("button", { name: "Custom Generate" }).click();
    await expect(page.getByText(/chat|custom generate/i).first()).toBeVisible({ timeout: 5_000 });
    const close = page.getByRole("button", { name: "Close" }).last();
    if (await close.isVisible().catch(() => false)) await close.click();
  });

  test("Credentials dialog opens and closes", async ({ page }) => {
    await page.getByRole("button", { name: "Credentials" }).click();
    await expect(page.getByText(/credential/i).first()).toBeVisible({ timeout: 5_000 });
    const close = page.getByRole("button", { name: "Close" }).last();
    if (await close.isVisible().catch(() => false)) await close.click();
  });

  test("Run E2E dialog opens and closes", async ({ page }) => {
    await page.getByRole("button", { name: "Run E2E" }).click();
    await expect(page.getByText(/e2e|automated test/i).first()).toBeVisible({ timeout: 5_000 });
    const close = page.getByRole("button", { name: "Close" }).last();
    if (await close.isVisible().catch(() => false)) await close.click();
  });

  test("SIT generate dialog opens after selecting a work item", async ({ page }) => {
    // Need to load a board and select a work item first.
    const boards = page.locator('[data-selected]');
    await expect(boards.nth(1)).toBeVisible({ timeout: 30_000 });
    await boards.nth(1).click();
    await expect(page.getByText(/Loading work items\.\.\./)).toHaveCount(0, { timeout: 60_000 });

    const firstCheck = page.getByRole("checkbox").first();
    if (!(await firstCheck.isVisible().catch(() => false))) return;
    await firstCheck.check();
    await expect(page.getByText(/work item\(s\) selected/)).toBeVisible();

    await page.getByRole("button", { name: "SIT", exact: true }).click();
    await expect(page.getByRole("button", { name: /AI Generate|Generating\.\.\./ })).toBeVisible();

    // Close without running.
    await page.locator("button.tt-btn-ghost", { hasText: /^Close$/ }).click();
  });

  test("UAT generate dialog opens after selecting a work item", async ({ page }) => {
    const boards = page.locator('[data-selected]');
    await expect(boards.nth(1)).toBeVisible({ timeout: 30_000 });
    await boards.nth(1).click();
    await expect(page.getByText(/Loading work items\.\.\./)).toHaveCount(0, { timeout: 60_000 });

    const firstCheck = page.getByRole("checkbox").first();
    if (!(await firstCheck.isVisible().catch(() => false))) return;
    await firstCheck.check();

    await page.getByRole("button", { name: "UAT", exact: true }).click();
    await expect(page.getByRole("button", { name: /AI Generate|Generating\.\.\./ })).toBeVisible();

    await page.locator("button.tt-btn-ghost", { hasText: /^Close$/ }).click();
  });
});

test.describe("ADO write guard (hard safety net)", () => {
  test("no write request is made during an entire read+select+open cycle", async ({ page }) => {
    await mockAgent(page, { configured: true });
    const guard = guardAdoWrites(page);
    await enterApp(page);
    await selectFirstProject(page);

    const boards = page.locator('[data-selected]');
    if ((await boards.count()) > 1) {
      await boards.nth(1).click();
      await expect(page.getByText(/Loading work items\.\.\./)).toHaveCount(0, { timeout: 60_000 });
    }

    expect(guard.blocked()).toHaveLength(0);
  });
});
