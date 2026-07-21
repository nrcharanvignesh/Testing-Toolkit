# Testing Toolkit

> **Web v3.40.0 — Agent v3.40.0**

An autonomous AI QA platform that turns **Azure DevOps** and **JIRA** work items
into fully executed end-to-end tests, LLM-generated test cases, requirement PDF
packets, and bulk defect uploads — driven by a knowledge-base-aware AI agent
that studies, navigates, observes, reasons, and reports with zero human input
until final review sign-off.

The web app is deployed on **Vercel** and talks only to a small **local compute
agent** (`http://127.0.0.1:7842`) running on the user's machine. ADO and GenAI
credentials remain agent-side and are never returned by its API.

---

## What it does

| Capability | Description |
|---|---|
| **Autonomous E2E Testing** | AI QA agent studies the project KB, discovers test cases via parent-child WI hierarchy, executes up to 3 user stories in parallel with isolated browser contexts, observes page state after every action, and produces per-WI PDF reports + video recordings + Excel audit trail |
| **Browse ADO / JIRA boards** | Pick a project and board from Azure DevOps or JIRA; view work items in a swim-lane grid with clickable WI ID hyperlinks and a full detail pane (description, acceptance criteria, comments, attachments, links) |
| **Export to Excel** | Export the current board as a full audit workbook: board sheet (filtered state, KPIs, hyperlinked IDs), test coverage, traceability matrix (WI x TC x pass/fail), defect density by column/sprint, pivot-ready flat data, and E2E execution results. Conditional formatting (red/amber/green) on coverage and status cells. Export all boards as a multi-sheet workbook with a summary page |
| **WI-level PDF** | Download any work item as a standalone PDF from the detail pane (ADO only) |
| **Generate test cases** | Select work items and a phase (Implementation / SIT / UAT); the RLM pipeline reads the work items + project KB and produces a reviewable Excel workbook |
| **E2E automation** | Self-healing Playwright execution with a 6-strategy locator waterfall, auto-retry, iframe/shadow-DOM traversal, KB-driven step discovery, and full per-TC result history |
| **Review + regenerate** | Refine generated test cases and re-run with written feedback (up to 10 iterations per session) |
| **Upload to ADO / JIRA** | Push approved test cases into Azure DevOps or JIRA as properly structured Test Case work items |
| **Package PDFs** | Bundle requirement documents and attachments into per-work-item PDF packets plus a combined PDF and a KB-ready chunk folder |
| **Bulk defects** | Parse defect documents and upload Bug work items to ADO/JIRA with inherited board position |
| **Per-project knowledge base** | Upload requirement documents; they are chunked and indexed locally (BM25 + dense vectors) for retrieval-augmented generation |
| **AI Stack explorer** | Live 7-layer visualization of the platform's AI architecture: LLMs, Vector DB, Embeddings, Data Extraction, Open LLM Access, Framework, Evaluation |
| **Human review flow** | Post-execution per-TC approve/reject with video playback, AI observations panel, and sign-off workflow |

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
                                         |-> JIRA Server/Data Center REST API
                                         |-> LiteLLM proxy (GenAI gateway)
                                         |    |-> azure.gpt-4o (generation)
                                         |    |-> azure.text-embedding-3-small
                                         |    |-> azure.cohere-rerank-v3-english
                                         |-> Playwright (E2E browser automation)
                                              |-> 1 Browser, up to 3 BrowserContexts
                                              |-> Per-WI video recording
                                              |-> KB Briefing Engine (RAG)
                                              |-> Page Observer (a11y analysis)
```

### E2E Intelligence Layers (v3.40.0)

```
Layer 5: REASONING    -- Business rule validation against KB
Layer 4: OBSERVATION  -- Full page state analysis after every action
Layer 3: NAVIGATION   -- Autonomous pathfinding from KB app map
Layer 2: INTERACTION  -- Self-healing locator waterfall + LLM recompile
Layer 1: BROWSER      -- Playwright contexts, video, maximized, isolated
```

The LLM layer is **fully API-first**: no local ONNX/ML models ship in the
default bundle. Embeddings, reranking, OCR (via vision), and audio transcription
all route through the GenAI LiteLLM proxy. The only local component is the
LanceDB embedded vector store.

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
  onboarding/               OnboardingScreen, AgentUpdateRequired
  ui/                       shared primitives (button, modal, dropdown)
lib/
  agent-client.ts           typed client for the local agent (127.0.0.1:7842)
  app-state.tsx             central UI state (project/board/selection/log/KB)
  agent-context.tsx         agent connection status
  export-board.ts           Excel export (single board + all-boards workbook)
  installer-template.ts     per-OS installer script generation
agent-bundle/src/           the local compute agent + ported desktop backend
  agent/                    FastAPI server, routes, model loader, updater
  ado/ jira/ kb/ testgen/   Azure DevOps, JIRA, knowledge base, generation pipeline
  defects/ tools/ core/     defect handling, PDF/Office tools, config + orchestrator
  automation/               E2E autonomous AI QA agent:
    e2e_runner.py             self-healing runner + slot-based execution
    parallel_runner.py        1 Browser -> N isolated BrowserContexts (max 3)
    kb_briefing.py            KB-driven step discovery + test brief builder
    page_observer.py          post-action a11y tree analysis + confidence scoring
    report_pdf.py             per-WI PDF report generation (reportlab)
    playwright_bridge.py      browser launch, maximized, CDP management
    e2e_plan.py               LLM-based plan compilation
    script_generator.py       Playwright action generation
    healing_guardrails.py     self-healing locator waterfall (6 strategies)
  tests/                    pytest suite (48 test files)
docs/                       ARCHITECTURE.md, DOCS.md
e2e/                        Playwright E2E specs + mock-agent helpers (8 specs)
scripts/                    build-agent-update-manifest.mjs
```

---

## Web app — local development

Requirements: Node.js 20+.

```bash
pnpm install
pnpm dev           # http://localhost:3000
pnpm build         # production build
pnpm lint          # eslint
pnpm test          # vitest unit tests
```

Deployment is on **Vercel** (`vercel.json`, framework: nextjs). Pushing `main`
triggers a production deployment automatically.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `BUNDLE_READ_TOKEN` | for installer downloads | Fine-grained `contents:read` GitHub token embedded in generated installers at download time |
| `GITHUB_TOKEN` | fallback | Used when `BUNDLE_READ_TOKEN` is unset |
| `LLM_UPSTREAM_BASE_URL` | agent release | Centrally managed HTTPS GenAI gateway URL; never exposed to the browser |
| `LLM_UPSTREAM_API_KEY` | agent release | Centrally managed GenAI bearer credential; never exposed to the browser |
| `LLM_PROVIDER_FORMAT` | optional | `anthropic` (default) or `openai` wire format |

Agent release credentials are converted by `agent-bundle/tools/seal_env.py` into
an authenticated, randomized AES-256-GCM envelope. The script accepts only named
process variables, never a plaintext file, and never prints values. The resulting
`.env.enc` is ciphertext, but it is still sensitive release material and must not
be attached to tickets, logs, or public artifacts.

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
| Sources | `GET /sources/projects`, `GET /sources/boards/{project}`, `POST /sources/workitems`, `POST /sources/workitems/stream`, `POST /sources/tag` (ADO + JIRA) |
| Generate | `POST /generate/start`, `GET /jobs/{id}` |
| E2E | `POST /e2e/start`, `GET /e2e/jobs/{id}`, `POST /e2e/stop/{job_id}`, `POST /e2e/stop/{job_id}?wi_id=X`, `GET /e2e/test-cases/{project}` |
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

Web v3.40.0 ships a complete CXO-level design system:

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

## Distribution and updates

The agent bundle (Python + models) is large and lives on a dedicated `parts`
branch. The web app's onboarding screen offers a one-click installer per OS;
the installer pulls bundle parts from GitHub at install time. The running agent
checks `agent-update.json` for newer versions but never patches itself. Updates
are installed through the normal installer while project data is preserved.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full distribution flow.

---

## Security

- Agent binds to **loopback only** (`127.0.0.1`); trust comes from same-machine
  reachability, not a bearer token.
- The centrally managed GenAI credential is shipped only as a strict,
  authenticated AES-256-GCM envelope derived with Scrypt. Plaintext `.env`
  fallback is intentionally unsupported.
- On first successful load, the release credential is rewrapped into the strongest
  available per-user OS facility: Windows DPAPI, macOS Keychain, or Linux Secret
  Service. If no secure backend exists, the encrypted release envelope remains
  the fallback; plaintext is never written.
- Envelope corruption or tampering fails closed. A corrupt release update cannot
  overwrite a previously valid OS-bound credential.
- ADO/JIRA/user E2E credentials use the existing OS secret-store paths. API
  responses expose only presence/status booleans, never values.
- Central log redaction removes configured gateway values and credential-like
  bearer/key/token strings from rotating files and streamed Activity Logs.
- All ADO and GenAI calls originate **from the agent**. Credentials never reach
  the browser or Vercel; requirement context is sent to the approved GenAI
  gateway when an AI operation requires it.
- E2E scripts use environment-variable injection for sensitive fields; plaintext
  passwords are never embedded in generated scripts.

**Threat-model boundary:** this prevents accidental disclosure, plaintext-at-rest
leaks, naive repository extraction, and undetected ciphertext modification. It
does not make a credential mathematically unrecoverable from an autonomous client
controlled by a determined local administrator or debugger. True non-extractability
requires a server-side token broker or hardware-backed remote attestation.

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — end-to-end architecture, data
  and secret flow, RLM generation pipeline, distribution and auto-update
- [docs/DOCS.md](docs/DOCS.md) — feature reference, workspace layout, hardware
  support, runtime dependencies
