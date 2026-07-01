# Testing Toolkit

A browser-based QA toolkit that turns **Azure DevOps** work items into
LLM-generated test cases, requirement PDF packets, and bulk defect uploads —
without any secret or requirement data ever leaving the user's machine.

It is the web re-platforming of the original PySide6 "Testing Toolkit" desktop
app: same capabilities and data model, delivered as a Vercel-hosted web UI backed
by a small **local compute agent** that runs on the user's own machine.

---

## What it does

- **Browse ADO boards** — pick a project and board, view work items in a
  swim-lane grid with a full detail pane (description, acceptance criteria,
  comments, attachments, links).
- **Generate test cases** — select work items and a phase
  (**Implementation / SIT / UAT**); a Recursive Language Model pipeline reads the
  work items plus the project knowledge base and produces a reviewable Excel
  workbook.
- **Review, edit, regenerate** — refine generated cases and re-run with feedback.
- **Upload back to ADO** — push approved test cases into Azure DevOps.
- **Package PDFs** — bundle requirement docs and attachments into per-work-item
  PDF packets.
- **Bulk defects** — parse defects from files and upload them to ADO.
- **Per-project knowledge base** — upload requirement documents; they are chunked
  and indexed locally (BM25 + dense vectors + reranking) for retrieval-augmented
  generation.

## Why the split architecture

Everything that requires trust or local resources — the Azure DevOps PAT, the LLM
API key, the requirement documents, and the local ML models — stays on the user's
machine. **The browser only ever talks to `http://127.0.0.1:7842`** (the local
agent). No credentials and no ADO/LLM traffic pass through Vercel.

```
Browser (Vercel web app)  ──HTTP──▶  Local agent (FastAPI @ 127.0.0.1:7842)
                                         ├─▶ OS secret store (PAT, API key)
                                         ├─▶ ~/TestingToolkit workspace
                                         ├─▶ Local ONNX models (embed/rerank)
                                         ├─▶ Azure DevOps REST API
                                         └─▶ LLM gateway (Anthropic-compatible)
```

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full picture and
**[docs/openapi.yaml](docs/openapi.yaml)** for the agent's API (Swagger/OpenAPI).

---

## Repository layout

```
app/                      Next.js App Router (UI entry, installer API route)
components/
  layout/                 AppShell, NavPanel, StatusBar, ActivityBar
  board/                  BoardGrid, DetailPane, ActionBar, LogPanel
  dialogs/                Settings, Generate, KB, Defects, Upload, Package, Retrieval
  onboarding/             OnboardingScreen, FirstRunGate
  ui/                     shared primitives (button, modal, dropdown)
lib/
  agent-client.ts         typed client for the local agent (127.0.0.1:7842)
  app-state.tsx           central UI state (project/board/selection/log/KB)
  agent-context.tsx       agent connection status
  installer-template.ts   per-OS installer script generation
agent-bundle/src/         the local compute agent + ported desktop backend
  agent/                  FastAPI server, routes, model loader, updater
  ado/ kb/ testgen/       Azure DevOps, knowledge base, generation pipeline
  defects/ tools/ core/   defect handling, PDF/Office tools, config + orchestrator
docs/                     ARCHITECTURE.md, openapi.yaml
scripts/                  build-agent-update-manifest.mjs
```

---

## Web app — local development

Requirements: Node.js 20+.

```bash
npm install
npm run dev        # http://localhost:3000
```

Other scripts:

```bash
npm run build      # production build
npm run start      # serve the production build
npm run lint       # eslint
```

Deployment is on **Vercel** (`vercel.json`, `framework: nextjs`). Pushing the
connected branch triggers a deploy.

### Environment variables (web app)

| Variable            | Required | Purpose |
|---------------------|----------|---------|
| `BUNDLE_READ_TOKEN` | for installer downloads | Fine-grained, `contents:read` GitHub token for this repo, embedded into the generated installer at download time. |
| `GITHUB_TOKEN`      | fallback | Used if `BUNDLE_READ_TOKEN` is unset (broadly privileged — prefer a dedicated read-only token in production). |

Without one of these, `GET /api/installer` returns `503` and the onboarding
download is disabled.

---

## The local agent

Users don't run the agent by hand — they download a one-click installer from the
app's onboarding screen, which fetches the bundle from GitHub and installs the
agent with autostart on login. The agent then serves the API at
`http://127.0.0.1:7842`.

For development, the agent can be run from source:

```bash
cd agent-bundle/src
python -m agent.server        # binds 127.0.0.1:7842
```

Configuration/identity lives near the top of `agent-bundle/src/core/app_config.py`
(no `.env`, no argparse — edit constants and restart). The workspace is created at
`~/TestingToolkit` on first launch.

### Agent API

The agent exposes 16 endpoints across five groups — see the OpenAPI spec in
[`docs/openapi.yaml`](docs/openapi.yaml):

| Group    | Endpoints |
|----------|-----------|
| Health   | `GET /health`, `GET /version` |
| Settings | `GET /settings`, `POST /settings` |
| ADO      | `GET /ado/verify`, `GET /ado/projects`, `GET /ado/boards/{project}`, `POST /ado/workitems` |
| KB       | `POST /kb/retrieve`, `POST /kb/embed`, `POST /kb/rerank`, `POST /kb/index`, `POST /kb/upload/{project}`, `GET /kb/status/{project}` |
| LLM      | `POST /llm/complete`, `GET /llm/models` |

You can view the spec interactively by pasting `docs/openapi.yaml` into
[editor.swagger.io](https://editor.swagger.io/).

---

## Distribution & auto-update

The agent bundle (Python + ONNX models) is large and is **not** stored on the app
branch. It lives split into parts on a dedicated `parts` branch alongside an
`agent-update.json` manifest:

- `app/api/installer/route.ts` generates the per-OS installer with a short-lived
  read token injected at download time.
- `agent/updater.py` polls the manifest (~every 60s) and overlays updated source,
  so most updates ship as a lightweight patch rather than a full reinstall.
- `scripts/build-agent-update-manifest.mjs` builds the manifest, which is
  published to the `parts` branch.

**Typical change flow**

- _Web-only change_ → push code branch, redeploy on Vercel.
- _Agent change_ → bump `AGENT_VERSION` (`agent-bundle/src/agent/version.py`),
  push source, regenerate `agent-update.json`, publish it to `parts`.

---

## Security model

- The agent binds to **loopback only** (`127.0.0.1`); trust comes from same-machine
  reachability, not a bearer token.
- Secrets (ADO PAT, LLM API key) are stored in the **OS secret store** and are
  never returned by the API (`GET /settings` exposes only `has_*` booleans).
- ADO and LLM calls are made **from the agent**, so credentials and requirement
  content never reach the browser or Vercel.

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — end-to-end architecture, data &
  secret flow, the RLM generation pipeline, and distribution/auto-update.
- [docs/openapi.yaml](docs/openapi.yaml) — OpenAPI 3.1 / Swagger spec for the
  local agent API.
