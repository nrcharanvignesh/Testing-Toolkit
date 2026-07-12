import { test, expect } from "@playwright/test";
import { guardAdoWrites, mockAgent } from "./helpers";

test.describe("No-source startup", () => {
  test.beforeEach(async ({ page }) => {
    await mockAgent(page, { configured: false });
    guardAdoWrites(page);
  });

  test("opens the application shell without a source setup gate", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByText("Projects", { exact: true })).toBeVisible();
    await expect(page.getByText("Boards", { exact: true })).toBeVisible();
    await expect(
      page.getByText("No projects. Connect Azure DevOps or JIRA in Settings."),
    ).toBeVisible();
    await expect(page.getByText("Set up your connection")).toHaveCount(0);
    await expect(page.getByText(/manual mode/i)).toHaveCount(0);
  });

  test("keeps source configuration available in Settings", async ({ page }) => {
    await page.goto("/");
    await page.getByTitle("Settings").click();

    await expect(page.getByText("Azure DevOps (optional)")).toBeVisible();
    await expect(page.getByText("JIRA (optional)")).toBeVisible();
    await expect(page.getByLabel("API Key:")).toHaveCount(0);
    await expect(page.getByText(/manual mode/i)).toHaveCount(0);
  });
});
