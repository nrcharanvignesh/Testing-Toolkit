# End-to-end tests (Playwright)

These tests drive the **whole web app in a real browser** and exercise read +
generate flows. They are written to run **on your machine**, because the web app
talks directly to the local compute agent at `http://127.0.0.1:7842` from the
browser — that agent (with your ADO PAT + LLM key) cannot be reached from a CI
sandbox or any other machine.

## Safety: nothing is ever pushed to ADO

Every test installs a network guard (`guardAdoWrites`) that **aborts** any request
to an ADO-mutating endpoint:

- `/generate/push`
- `/generate/push-xlsx`
- `/defects/upload`
- `/ado/create`, `/ado/update`

The generate test runs `AI Generate` (local only) and then asserts that **zero**
ADO-write requests were made. The "Push to ADO" button is never clicked.

## Prerequisites

1. The local agent is running and configured (Settings → API Key + Base URL + PAT
   + Organization). Reads/generation auto-skip if the agent reports
   `configured: false`.
2. Install browsers once:

   ```bash
   npx playwright install chromium
   ```

## Running

```bash
# Against local dev (default http://localhost:3000)
npm run dev            # in one terminal
npm run test:e2e       # in another

# Watch it drive the browser
npm run test:e2e:headed

# Interactive UI mode
npm run test:e2e:ui

# Against the deployed app (agent must still run locally)
PLAYWRIGHT_BASE_URL=https://testing-toolkit.vercel.app npm run test:e2e

# Point at a non-default agent
AGENT_URL=http://127.0.0.1:7842 npm run test:e2e
```

## Coverage

| Spec | Flow | Needs agent |
| --- | --- | --- |
| `01-onboarding.spec.ts` | First-run Base URL placeholder; Skip → manual mode shell | no |
| `02-dialogs.spec.ts` | Settings (masked Base URL), Help menu, About, log panel | no |
| `03-read-flows.spec.ts` | List projects → boards → work items → detail pane | yes (read) |
| `04-generate.spec.ts` | Select item → AI Generate → assert no ADO push | yes (LLM) |

Specs that need a configured agent **auto-skip** when one is not present, so the
onboarding + dialog specs always run anywhere.
