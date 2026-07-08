# Testing Toolkit

> **Web v3.0.0 — Agent v2.8.2**

A browser-based QA platform that turns **Azure DevOps** work items into
LLM-generated test cases, requirement PDF packets, E2E automation scripts, and
bulk defect uploads — without any secret or requirement data ever leaving the
user's machine.

The web app is deployed on **Vercel** and talks only to a small **local compute
agent** (`http://127.0.0.1:7842`) running on the user's machine. All credentials,
ADO traffic, and LLM calls happen inside that agent. The browser never touches a
secret.

---

## What it does

| Capability | Description |
|---|---|
| **Browse ADO boards** | Pick a project and board; view work items in a swim-lane grid with a full detail pane (description, acceptance criteria, comments, attachments, links) |
| **Generate test cases** | Select work items and a phase (Implementation / SIT / UAT); the RLM pipeline reads the work items + project KB and produces a reviewable Excel workbook |
| **E2E automation** | Self-healing Playwright script generation and execution with a 6-strategy locator waterfall, auto-retry, iframe/shadow-DOM traversal, and a full per-TC result history |
| **Review + regenerate** | Refine generated test cases and re-run with written feedback (up to 10 iterations per session) |
| **Upload to ADO** | Push approved test cases into Azure DevOps as properly structured Test Case work items |
| **Package PDFs** | Bundle requirement documents and attachments into per-work-item PDF packets plus a combined PDF and a KB-ready chunk folder |
| **Bulk defects** | Parse defect documents and upload Bug work items to ADO with inherited board position |
| **Per-project knowledge base** | Upload requirement documents; they are chunked and indexed locally (BM25 + dense vectors) for retrieval-augmented generation |
| **AI Stack explorer** | Live 7-layer visualization of the platform's AI architecture: LLMs, Vector DB, Embeddings, Data Extraction, Open LLM Access, Framework, Evaluation |

---

## Architecture overview

Everything that requires trust or local resources stays on the user's machine.
The browser only ever calls `http://127.0.0.1:7842`.

```
Browser (Vercel web app)  ──HTTP──>  Local agent (FastAPI @ 127.0.0.1:7842)
                                         |-> OS secret store (PAT, API key)
                                         |-> ~/TestingToolkit workspace
                                         |-> LanceDB embedded vector store
                                         |-> Azure DevOps REST API
                                         |-> LiteLLM proxy (GenAI gateway)
                                              |-> azure.gpt-4o (generation)
                                              |-> azure.text-embedding-3-small
                                              |-> azure.cohere-rerank-v3-english
```

The LLM layer is **fully API-first** as of agent 2.8.0: no local ONNX/ML models
ship in the default bundle. Embeddings, reranking, OCR (via vision), and audio
transcription all route through the GenAI LiteLLM proxy. The only local component
is the LanceDB embedded vector store.

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full picture.

---

## Repository layout

```
app/                        Next.js App Router (UI entry, installer API route)
components/
  layout/                   AppShell, NavPanel, StatusBar, ActivityBar
  board/                    BoardGrid, DetailPane, ActionBar, LogPanel
  dashboard/                CoverageBar
  dialogs/                  Settings, Generate, E2E, KB, Defects, Upload, Package,
                            Retrieval, AiStack, About, ViewLog
  onboarding/               OnboardingScreen, FirstRunGate
  ui/                       shared primitives (button, modal, dropdown)
lib/
  agent-client.ts           typed client for the local agent (127.0.0.1:7842)
  app-state.tsx             central UI state (project/board/selection/log/KB)
  agent-context.tsx         agent connection status
  installer-template.ts     per-OS installer script generation
agent-bundle/src/           the local compute agent + ported desktop backend
  agent/                    FastAPI server, routes, model loader, updater
  ado/ kb/ testgen/         Azure DevOps, knowledge base, generation pipeline
  defects/ tools/ core/     defect handling, PDF/Office tools, config + orchestrator
  automation/               E2E script generator + self-healing runner
  tests/                    pytest suite (177 tests)
docs/                       ARCHITECTURE.md, DOCS.md
e2e/                        Playwright E2E specs + mock-agent helpers (8 specs)
scripts/                    build-agent-update-manifest.mjs
```

---

## Web app — local development

Requirements: Node.js 20+.

```bash
npm install
npm run dev        # http://localhost:3000
npm run build      # production build
npm run lint       # eslint
npm test           # vitest unit tests (10 tests)
```

Deployment is on **Vercel** (`vercel.json`, framework: nextjs). Pushing `main`
triggers a production deployment automatically.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `BUNDLE_READ_TOKEN` | for installer downloads | Fine-grained `contents:read` GitHub token embedded in generated installers at download time |
| `GITHUB_TOKEN` | fallback | Used when `BUNDLE_READ_TOKEN` is unset |

---

## Local agent — development

```bash
cd agent-bundle/src
python -m agent.server        # binds 127.0.0.1:7842
```

Run the test suite:

```bash
cd agent-bundle
python -m pytest tests/ -q    # 177 tests
```

### Agent API surface (70+ endpoints)

| Group | Key endpoints |
|---|---|
| Health | `GET /health`, `GET /version` |
| Settings | `GET /settings`, `POST /settings` |
| Sources | `GET /sources/projects`, `GET /sources/boards/{project}`, `POST /sources/workitems`, `POST /sources/workitems/stream`, `POST /sources/tag` |
| Generate | `POST /generate/start`, `GET /jobs/{id}` |
| E2E | `POST /e2e/start`, `GET /e2e/jobs/{id}`, `GET /e2e/test-cases/{project}` |
| KB | `POST /kb/index`, `POST /kb/retrieve`, `GET /kb/status/{project}`, `POST /kb/upload/{project}` |
| Chat | `POST /chat/stream` |

---

## AI stack

The platform uses the **GenAI LiteLLM proxy** (OpenAI-format + Anthropic-format
endpoints) as the sole external AI surface. All model IDs follow the
`<provider>.<model>` convention:

| Layer | Component | Default model / tool |
|---|---|---|
| LLM generation | Azure OpenAI via LiteLLM | `azure.gpt-4o` |
| LLM fast | Azure OpenAI via LiteLLM | `azure.gpt-4-turbo` |
| Text embeddings | Azure OpenAI via LiteLLM | `azure.text-embedding-3-small` |
| Reranking | Cohere via LiteLLM `/rerank` | `azure.cohere-rerank-v3-english` |
| Vector store | LanceDB (embedded, local) | — |
| OCR | Vision endpoint (`/chat/completions`) | `azure.gpt-4o` |
| Audio | Whisper endpoint (`/audio/transcriptions`) | `azure.whisper` |
| Framework | FastAPI + httpx | — |

Transport is configurable: set `LLM_PROVIDER_FORMAT=openai` to use the OpenAI
path; default is `anthropic` (also supported by the LiteLLM proxy).

---

## Design system

Web v3.0.0 ships a complete CXO-level design system:

- **Token set**: work-item type colors (story/bug/task/epic/feature), state
  badge colors, elevation shadows (sm/md/lg), teal brand accent
- **Components**: `tt-badge` (9 variants), `tt-metric-chip`, `tt-section-header`,
  `tt-action-group`, `CoverageBar`
- **Animations**: `tt-fade-up`, `tt-badge-pop`, `tt-pulse-dot`
- **Board**: per-type left-border color, badge pills for type + state, lane
  count badges, mono ID column
- **DetailPane**: 2-column property grid, tag chips, name-hue comment avatars
- **Dialogs**: animated stage labels, level-colored log lines with left-border
  stripes, pass/fail/skip summary bars

---

## Distribution and auto-update

The agent bundle (Python + models) is large and lives on a dedicated `parts`
branch. The web app's onboarding screen offers a one-click installer per OS;
the installer pulls bundle parts from GitHub at install time. The running agent
self-updates from an `agent-update.json` manifest on the `parts` branch.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full distribution flow.

---

## Security

- Agent binds to **loopback only** (`127.0.0.1`); trust comes from same-machine
  reachability, not a bearer token
- Secrets (ADO PAT, LLM API key) stored in **OS secret store**, never returned
  by the API (`GET /settings` returns only `has_*` booleans)
- All ADO and LLM calls originate **from the agent** — credentials and requirement
  content never reach the browser or Vercel
- E2E scripts use environment-variable injection for sensitive fields — plaintext
  passwords are never embedded in generated scripts

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — end-to-end architecture, data
  and secret flow, RLM generation pipeline, distribution and auto-update
- [docs/DOCS.md](docs/DOCS.md) — feature reference, workspace layout, hardware
  support, runtime dependencies
