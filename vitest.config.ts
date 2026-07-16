import path from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    // Unit/logic tests only. Playwright E2E specs live in e2e/ and are run
    // via `npm run test:e2e`, not vitest.
    include: ["**/__tests__/**/*.test.{ts,tsx}", "**/*.unit.test.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**", ".next/**", "agent-bundle/**"],
    environment: "node",
  },
});
