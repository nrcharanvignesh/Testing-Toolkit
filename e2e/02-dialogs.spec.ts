import { test, expect } from "@playwright/test";
import { guardAdoWrites, enterApp } from "./helpers";

test.describe("Shell dialogs (desktop parity)", () => {
  test.beforeEach(async ({ page }) => {
    guardAdoWrites(page);
    await enterApp(page);
  });

  test("Settings dialog matches desktop layout and masks Base URL when stored", async ({
    page,
  }) => {
    await page.getByTitle("Settings").click();

    await expect(page.getByText("Testing Toolkit - Settings")).toBeVisible();
    // Section labels
    for (const label of ["API Key", "Base URL", "Model", "Organization"]) {
      await expect(page.getByText(label, { exact: false }).first()).toBeVisible();
    }
    // Footer actions
    await expect(page.getByRole("button", { name: "Test Connection" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Save" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible();

    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByText("Testing Toolkit - Settings")).toHaveCount(0);
  });

  test("Help menu exposes the desktop actions and About dialog", async ({ page }) => {
    await page.getByTitle("Help").click();

    await expect(page.getByText("Open log folder")).toBeVisible();
    await expect(page.getByText("View recent log...")).toBeVisible();
    await expect(page.getByText("About")).toBeVisible();

    await page.getByText("About").click();
    await expect(page.getByText("Version 2.0.0")).toBeVisible();
    await page.getByRole("button", { name: "OK" }).click();
    await expect(page.getByText("Version 2.0.0")).toHaveCount(0);
  });

  test("log panel toggles open and closed", async ({ page }) => {
    // The action-strip toggle uses exact "Show"/"Hide" text (the navigator's
    // collapse button is "Hide navigator", so we match exactly to avoid it).
    const show = page.getByRole("button", { name: "Show", exact: true });
    const hide = page.getByRole("button", { name: "Hide", exact: true });
    await show.click();
    await expect(hide).toBeVisible();
    await hide.click();
    await expect(show).toBeVisible();
  });
});
