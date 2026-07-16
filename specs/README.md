# Test Plans (Playwright Test Agents)

This directory contains structured test plans produced by the **Playwright Test Planner Agent**.

## Workflow

1. **Plan** — The planner agent explores the app and writes scenario specs here
2. **Generate** — The generator agent reads specs from here and produces tests in `e2e/`
3. **Heal** — The healer agent auto-repairs failing tests

## Usage (via Claude Code)

```bash
# Initialize agents (already done)
npx playwright init-agents --loop=claude

# Invoke agents from Claude Code:
# - @playwright-test-planner to create test plans
# - @playwright-test-generator to generate tests from plans
# - @playwright-test-healer to fix broken tests
```

## Structure

Each spec file is a Markdown document with:

- Application overview
- Test scenarios with steps and expected results
- Seed file reference for environment setup
