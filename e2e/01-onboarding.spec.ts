import { test, expect } from "@playwright/test";
import { guardAdoWrites, agentConfigured } from "./helpers";

test.describe("Onboarding / first-run gate (desktop parity)", () => {
  test.beforeEach(async ({ page }) => {
    guardAdoWrites(page);
  });

  test("first-run form shows an editable Base URL placeholder (not masked dots)", async ({
    page,
  }) => {
    test.skip(await agentConfigured(page), "agent already configured; no first-run form");
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Base URL must render as an editable text field with its placeholder,
    // mirroring the desktop first-run form (no value seeded, no mask).
    const baseUrl = page.getByPlaceholder("https://your-llm-api-endpoint.com");
    await expect(baseUrl).toBeVisible();
    await expect(baseUrl).toBeEditable();
    await expect(baseUrl).toHaveValue("");
  });

  test("Skip (manual mode) enters the app shell with an empty board", async ({ page }) => {
    test.skip(await agentConfigured(page), "agent configured; gate auto-dismisses");
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: "Skip (manual mode)" }).click();

    // Desktop parity: the main window is always shown; Skip lands us in the
    // shell, not back on the setup form.
    await expect(page.getByText("Projects", { exact: true })).toBeVisible();
    await expect(page.getByText("Boards", { exact: true })).toBeVisible();
    await expect(
      page.getByText("No projects. Configure ADO in Settings, then Refresh."),
    ).toBeVisible();
    // We are no longer on the setup form.
    await expect(
      page.getByRole("button", { name: "Skip (manual mode)" }),
    ).toHaveCount(0);
  });
});
