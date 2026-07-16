// Seed test for Playwright Test Agents (planner/generator/healer).
// Sets up the Testing Toolkit app with a mocked agent so the agents
// can explore and generate tests against a deterministic state.
import { test, expect } from '@playwright/test';
import { mockAgent, guardAdoWrites, enterApp } from './helpers';

test.describe('Testing Toolkit seed', () => {
  test('seed', async ({ page }) => {
    guardAdoWrites(page);
    await mockAgent(page, { configured: true });
    await enterApp(page);
    await expect(page.getByText('Projects', { exact: true })).toBeVisible();
  });
});
