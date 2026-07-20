# Testing Toolkit -- Complete Codebase Documentation

**Web App Version:** 3.9.0  
**Agent Version:** 2.30.0  
**Generated:** 2026-07-19  
**Commit:** dae6cd1

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Technology Stack](#2-technology-stack)
3. [Frontend (Next.js Web App)](#3-frontend-nextjs-web-app)
4. [Backend (Python Agent)](#4-backend-python-agent)
5. [Data Flows](#5-data-flows)
6. [Security Model](#6-security-model)
7. [Test Suite](#7-test-suite)
8. [Infrastructure and Deployment](#8-infrastructure-and-deployment)
9. [Configuration Reference](#9-configuration-reference)

---

## 1. Architecture Overview

```
+---------------------------+       +----------------------------+
|  Vercel (Production)      |       |  Local Machine             |
|                           |       |                            |
|  Next.js 16 App           |       |  Python FastAPI Agent      |
|  - app/page.tsx           | <---> |  - localhost:7842           |
|  - /api/installer         |       |  - ADO/JIRA integration    |
|  - /api/build-id          |       |  - LLM proxy client        |
|  - /api/llm/[...path]    |       |  - KB indexing pipeline     |
|                           |       |  - E2E browser automation  |
+---------------------------+       +----------------------------+
         |                                     |
         |  HTTPS (LLM proxy)                  |  HTTPS (PAT auth)
         v                                     v
+---------------------------+       +----------------------------+
|  GenAI Gateway            |       |  Azure DevOps / JIRA       |
|  (Anthropic / OpenAI)     |       |  REST API                  |
+---------------------------+       +----------------------------+
         |                                     |
         v                                     v
+---------------------------+       +----------------------------+
|  GitHub API               |       |  MCP Servers (Node.js)     |
|  parts branch manifest    |       |  ADO, JIRA, Playwright     |
+---------------------------+       +----------------------------+
```

**Topology:**
- The **web app** (Next.js 16, React 19) is deployed to Vercel via git-push integration on `main`.
- The **agent** (FastAPI/uvicorn, Python 3.9+) runs locally on port 7842.
- The web app communicates with the agent over HTTP on localhost (CORS-allowed).
- The agent connects outbound to ADO/JIRA (PAT), the GenAI gateway (API key from encrypted envelope), and GitHub (update manifest).
- The `/api/llm/[...path]` route on Vercel proxies LLM calls, injecting server-side credentials.

---

## 2. Technology Stack

### Frontend
| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | Next.js (App Router) | 16.2.9 |
| UI Library | React | 19.2.4 |
| Language | TypeScript | ^5 |
| Styling | Tailwind CSS v4 | ^4 |
| Package Manager | pnpm | 9.15.9 |
| State | React Context + useSyncExternalStore | - |
| Data Fetching | SWR | ^2.4.2 |
| Animation | Framer Motion | ^12.42.0 |
| Excel Generation | ExcelJS | ^4.4.0 |
| Markdown | react-markdown + remark-gfm | ^10.1.0 |
| Icons | lucide-react | ^1.21.0 |
| HTML Sanitization | sanitize-html | ^2.17.6 |
| Testing | Vitest (unit), Playwright (E2E) | ^4.1.10, ^1.61.1 |

### Backend
| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | >=0.111 |
| Server | uvicorn | >=0.29 |
| HTTP Client | httpx | >=0.27 |
| Validation | Pydantic | >=2.7 |
| Vector DB | LanceDB | >=0.6 |
| Embeddings | API-based (azure.text-embedding-3-large) | - |
| Reranking | API-based (azure.cohere-rerank-v3-english) | - |
| Document Extraction | pypdf, python-docx, python-pptx, openpyxl, PyMuPDF | various |
| Encryption | AES-256-GCM (cryptography or pure-Python fallback) | >=42.0 |
| Credential Store | keyring + DPAPI (Windows) | >=24.0 |
| Browser Automation | Playwright | >=1.44 |
| PDF Generation | ReportLab + Pillow | >=4.1, >=10.0 |
| MCP | mcp SDK + Node.js child processes | >=1.0 |

---

## 3. Frontend (Next.js Web App)

### 3.1 App Router Structure

```
app/
  layout.tsx          -- Root layout (fonts, ThemeProvider, AgentProvider)
  page.tsx            -- Main entry (routing: offline/connecting/connected/reinstalling)
  error.tsx           -- Route-level error boundary
  global-error.tsx    -- Layout-level error boundary
  api/
    build-id/route.ts   -- Deployment freshness endpoint
    installer/route.ts  -- Platform-specific installer download
    llm/[...path]/route.ts  -- Secure LLM proxy (injects server credentials)
```

### 3.2 Component Hierarchy

```
RootLayout
  ThemeProvider
    AgentProvider
      Home (page.tsx)
        OnboardingScreen             [when offline or reinstalling]
        LoadingScreen                [when connecting or loading settings]
        ConnectedApp
          AppStateProvider
            AppShell
              NavPanel | ActivityBar   [mutually exclusive]
              main:
                BoardGrid
                  KpiTiles
                  FilterSelects
                  LaneGroup > rows
                  DetailPane           [toggled]
                ActionBar
                LogPanel               [toggled]
              StatusBar
              DialogHost
                SettingsDialog | GenerateDialog | ProjectKbDialog |
                UploadDialog | PackageDialog | DefectDialog |
                RetrievalDialog | ChatDialog | CredentialsDialog |
                E2EDialog | AboutDialog | ViewLogDialog | AiStackDialog
              AgentUpdateRequired      [overlay when blocked]
```

### 3.3 State Management

| Layer | Mechanism | Scope |
|-------|-----------|-------|
| Agent connectivity | `AgentContext` (agent-context.tsx) | Global |
| Theme | `ThemeContext` (theme.tsx) | Global |
| App state | `AppStateContext` (app-state.tsx) | Connected app |
| Preferences | External store + `useSyncExternalStore` (preferences.ts) | Global, localStorage |
| Board columns | External store + `useSyncExternalStore` (board-columns.ts) | Global, localStorage |
| Board lanes | External store + `useSyncExternalStore` (board-lanes.ts) | Global, localStorage |
| Event bus | Fire-and-forget buffer (event-bus.ts) | Global |
| Data fetching | SWR (BoardGrid test cases, last run) | Component-scoped |

No Redux/Zustand/Jotai -- minimal context + localStorage external stores.

### 3.4 Lib Modules

| Module | Purpose |
|--------|---------|
| `agent-client.ts` | Typed HTTP client for agent at localhost:7842. All API methods, types, retry logic. |
| `agent-context.tsx` | React context managing agent connection with health polling (3s offline, 30s connected). |
| `app-state.tsx` | Central UI state: project/board selection, KB orchestration, dialogs, logs. |
| `agent-version.ts` | `REQUIRED_AGENT_VERSION = "2.29.0"`, `isAgentOutdated()`, `compareVersions()`. |
| `board-columns.ts` | Per-column width/collapsed state for work-item grid. |
| `board-lanes.ts` | Collapsed row-group (swim lane) state. |
| `board-utils.ts` | Row grouping, filtering, KPI classification, source detection. |
| `event-bus.ts` | Batched telemetry (flushes to `/events/batch` every 3s or 50 events). |
| `export-board.ts` | ExcelJS workbook generation (single/all boards/all projects). Project-prefix sheet disambiguation, row validation, auto-filter on Summary. |
| `installer-template.ts` | Platform-specific installer script generation (Windows cmd/PS, Unix bash/Python). |
| `preferences.ts` | localStorage-backed preferences with `useSyncExternalStore`. |
| `reinstall.ts` | Reinstall flow state (reason, flag, reload trigger). |
| `theme.tsx` | Light/dark theme with pre-hydration script (no FOUC). |
| `toast.ts` | Imperative toast notifications (top-center, auto-dismiss). |
| `use-app-update.ts` | Detection-only update hook with patch auto-apply. |
| `use-metrics.ts` | Polls `/metrics` for CPU/RAM/GPU stats (5s interval, graceful 3-strike disable). |
| `use-web-freshness.ts` | Auto-reload on new Vercel deployment (polls `/api/build-id` every 5 min). |
| `utils.ts` | `cn()` -- Tailwind class merging (clsx + tailwind-merge). |

### 3.5 Agent Client API Surface

Every method on the `agent` object in `lib/agent-client.ts`:

| Method | HTTP | Purpose |
|--------|------|---------|
| `health()` | GET /health | Version + capabilities + hardware |
| `metrics()` | GET /metrics | Live CPU/RAM/GPU usage |
| `capabilities()` | GET /capabilities | Feature map |
| `doctor()` | GET /doctor | Self-diagnostics |
| `checkConnection()` | GET /health | Returns AgentStatus string |
| `getSettings()` | GET /settings | ADO/JIRA config |
| `saveSettings(payload)` | POST /settings | Save connection settings |
| `getSystemPrompt(project, scope)` | GET /settings/system-prompt | Per-project system prompt |
| `saveSystemPrompt(...)` | POST /settings/system-prompt | Save system prompt |
| `resetSystemPrompt(...)` | POST /settings/system-prompt/reset | Reset to default |
| `verifyPat()` | GET /sources/verify | Verify all sources |
| `verifyAdo()` | GET /ado/verify | Verify ADO PAT |
| `verifyJira()` | GET /jira/verify | Verify JIRA creds |
| `verifyLlm()` | POST /llm/complete | AI API probe |
| `listProjects()` | GET /sources/projects | Merged project list |
| `listBoards(project)` | GET /sources/boards/{project} | Boards for project |
| `boardView(project, board)` | POST /sources/workitems | Blocking board load |
| `boardViewStream(...)` | POST /sources/workitems/stream | SSE progressive load |
| `workItemDetail(project, wiId)` | GET /sources/workitem/{project}/{wiId} | Full WI detail |
| `tagWorkItem(...)` | POST /sources/tag | Add tag to WI |
| `kbStatus(project)` | GET /kb/status/{project} | KB status |
| `kbRetrieve(project, query, topK)` | POST /kb/retrieve | RAG retrieval |
| `kbIndex(project, handlers, force)` | POST /kb/index + poll | Index KB |
| `kbUpload(project, file)` | POST /kb/upload/{project} | Upload KB doc |
| `kbUploadProgress(...)` | XHR POST /kb/upload/{project} | Upload with progress |
| `deleteKbDocument(project, name)` | DELETE /kb/document/{project} | Remove KB doc |
| `clearKb(project, keep)` | POST /kb/clear/{project} | Wipe KB |
| `templateStatus(project, phase)` | GET /kb/template/{project}/{phase} | Template status |
| `uploadTemplate(...)` | POST /kb/template/{project}/{phase} | Upload template |
| `deleteTemplate(...)` | DELETE /kb/template/{project}/{phase} | Delete template |
| `activeContextJob(project)` | GET /kb/context/active/{project} | Active context job |
| `projectContext(project)` | GET /kb/context/{project} | Context summary |
| `setContextEnabled(project, enabled)` | PUT /kb/context/{project}/setting | Toggle context |
| `regenerateContext(project)` | POST /kb/context/{project}/regenerate | Force regenerate |
| `editContext(project, summary)` | PUT /kb/context/{project} | Manual edit |
| `clearContext(project)` | DELETE /kb/context/{project} | Clear context |
| `listArtifacts(project)` | GET /artifacts/{project} | List outputs |
| `deleteArtifact(path)` | DELETE /artifacts/delete | Delete artifact |
| `uploadArtifact(blob, filename, project)` | POST /artifacts/upload | Upload artifact |
| `generate(payload, handlers)` | POST /generate/start + poll | Start generation |
| `listCredentials(project)` | GET /credentials/{project} | List masked creds |
| `upsertCredential(project, cred)` | POST /credentials/{project} | Add/update credential |
| `deleteCredential(project, env)` | DELETE /credentials/{project}/{env} | Delete credential |
| `e2eTestCases(project, wiIds)` | GET /e2e/test-cases/{project} | E2E test cases |
| `e2eEnvironments(project)` | GET /e2e/environments/{project} | Runnable envs |
| `e2eLastRun(project)` | GET /e2e/last-run/{project} | Last run summary |
| `runE2E(payload, handlers)` | POST /e2e/start + poll | Run E2E tests |
| `pushPayload(payload, handlers)` | POST /generate/push + poll | Push TCs to ADO |
| `pushReviewedXlsx(...)` | POST /generate/push-xlsx + poll | Push from Excel |
| `loadArtifact(xlsxPath)` | POST /generate/load-xlsx | Load for regen |
| `parseDefects(files, useLlm)` | POST /defects/parse | Parse defect docs |
| `downloadDefectsExcel(defects)` | POST /defects/excel | Export defects Excel |
| `uploadDefects(project, defects, handlers)` | POST /defects/upload + poll | Create Bugs in ADO |
| `packagePdfs(payload, handlers)` | POST /tools/package + poll | Bundle PDFs |
| `stopJob(jobId)` | POST /jobs/{jobId}/stop | Stop job |
| `recentLog(maxBytes)` | GET /tools/log | Agent log text |
| `openLogFolder()` | POST /open-log-folder | Open OS log folder |
| `updateStatus()` | GET /update/status | Check for updates |
| `applyPatch()` | POST /update/apply | Apply patch update |
| `extractAttachments(files)` | POST /generate/extract | Extract text |
| `chatStream(req, handlers, signal)` | POST /chat/stream (SSE) | Agentic chat |

### 3.6 Components

#### Layout Components
| Component | File | Purpose |
|-----------|------|---------|
| `AppShell` | components/layout/AppShell.tsx | Main shell: nav, grid, log, status, dialogs, update gate |
| `NavPanel` | components/layout/NavPanel.tsx | Expanded nav: projects, boards, exports, toolbar |
| `ActivityBar` | components/layout/ActivityBar.tsx | Collapsed nav rail (icon-only) |
| `StatusBar` | components/layout/StatusBar.tsx | Bottom bar: activity, KB progress, metrics, connectivity |

#### Board Components
| Component | File | Purpose |
|-----------|------|---------|
| `BoardGrid` | components/board/BoardGrid.tsx | Work-item grid: filters, KPIs, columns, lanes, selection |
| `DetailPane` | components/board/DetailPane.tsx | Right-side WI detail (description, attachments, artifacts) |
| `ActionBar` | components/board/ActionBar.tsx | Generation/publish/tools toolbar |
| `LogPanel` | components/board/LogPanel.tsx | Resizable activity log |

#### Dialog Components
| Component | File | Purpose |
|-----------|------|---------|
| `DialogHost` | dialogs/DialogHost.tsx | Dialog router with ErrorBoundary |
| `SettingsDialog` | dialogs/SettingsDialog.tsx | ADO/JIRA connection, verify, save |
| `GenerateDialog` | dialogs/GenerateDialog.tsx | Test case generation flow |
| `ProjectKbDialog` | dialogs/ProjectKbDialog.tsx | KB docs, index, context, templates |
| `UploadDialog` | dialogs/UploadDialog.tsx | Push TCs to ADO |
| `PackageDialog` | dialogs/PackageDialog.tsx | PDF packaging |
| `DefectDialog` | dialogs/DefectDialog.tsx | Defect parse/export/upload |
| `RetrievalDialog` | dialogs/RetrievalDialog.tsx | RAG query test |
| `ChatDialog` | dialogs/ChatDialog.tsx | Agentic chat with streaming |
| `CredentialsDialog` | dialogs/CredentialsDialog.tsx | Encrypted test credentials |
| `E2EDialog` | dialogs/E2EDialog.tsx | Playwright E2E execution |
| `AboutDialog` | dialogs/AboutDialog.tsx | Version, diagnostics, AI stack |
| `ViewLogDialog` | dialogs/ViewLogDialog.tsx | Full agent log viewer |
| `AiStackDialog` | dialogs/AiStackDialog.tsx | AI infrastructure details |

#### Onboarding Components
| Component | File | Purpose |
|-----------|------|---------|
| `OnboardingScreen` | onboarding/OnboardingScreen.tsx | Installer download + agent wait |
| `AgentUpdateRequired` | onboarding/AgentUpdateRequired.tsx | Non-dismissable update gate |

#### UI Primitives
| Component | File | Purpose |
|-----------|------|---------|
| `Modal` | ui/modal.tsx | Portal dialog with focus trap |
| `ErrorBoundary` | ui/error-boundary.tsx | Generic error boundary |
| `Dropdown` | ui/dropdown.tsx | Click-triggered menu |
| `ResizeHandle` | ui/resizer.tsx | Drag handle for panel resize |
| `Markdown` | ui/markdown.tsx | GFM markdown renderer |
| `SourceLogo` | ui/source-logo.tsx | ADO/JIRA brand badges |
| `DownloadLinks` | ui/download-links.tsx | File download cards |

---

## 4. Backend (Python Agent)

### 4.1 Server Architecture

**Entry:** `agent-bundle/src/agent/server.py`  
**Port:** 7842 (`agent/version.py`)  
**Workspace:** `~/TestingToolkitWeb/`

#### Middleware Stack
1. `BodySizeLimitMiddleware` -- rejects > 50 MB
2. `TraceRequestMiddleware` -- correlation IDs, duration logging
3. `PrivateNetworkAccessMiddleware` -- PNA preflight for HTTPS->localhost
4. CORS -- allows `*.vercel.app`, `*.testing-toolkit.dev`, `localhost:*`

#### Lifespan
- Ensures workspace directories
- Initializes logging
- Preloads embedding models (background thread)
- Recovers interrupted KB jobs
- Self-heals Windows autostart

### 4.2 Route Modules

| Prefix | Module | Purpose |
|--------|--------|---------|
| `/` | health | Version, hardware, capabilities, doctor |
| `/settings` | settings | ADO/JIRA config CRUD |
| `/ado` | ado | ADO PAT verify, projects |
| `/jira` | jira | JIRA verify |
| `/sources` | sources | Unified project/board facade |
| `/kb` | kb | Knowledge base (upload, index, retrieve, context) |
| `/llm` | llm | Internal completion probe |
| `/chat` | chat | Agentic SSE streaming with tool use |
| `/generate` | generate | RLM test case generation |
| `/defects` | defects | Defect parse/upload |
| `/credentials` | credentials | Encrypted test credentials |
| `/e2e` | e2e | Playwright browser automation |
| `/jobs` | jobs | Job poll/stop |
| `/tools` | tools | PDF packaging, log access |
| `/artifacts` | artifacts | Generated file management |
| `/update` | update | Version detection + patch apply |
| `/events` | events | Frontend telemetry batch |

### 4.3 Core Modules

| Module | Purpose |
|--------|---------|
| `core/app_config.py` | Constants, paths, model defaults, credential loading |
| `core/anthropic_client.py` | Async LLM client (Anthropic + OpenAI formats), retry, streaming |
| `core/model_router.py` | Task-based model routing (Frontier/Medium/Small tiers) |
| `core/settings_store.py` | ADO/JIRA settings persistence, PAT encryption (keyring + DPAPI) |
| `core/project_store.py` | Per-project workspace (KB, prompts, templates, context) |
| `core/credential_envelope.py` | AES-256-GCM seal/unseal for GenAI credentials |
| `core/credential_store.py` | OS-bound rewrap + rotation for release envelope |
| `core/app_logging.py` | Logging setup with secret redaction |
| `core/network_status.py` | Network state machine (idle/online/offline) |

### 4.4 KB Pipeline

| Module | Purpose |
|--------|---------|
| `kb/indexer.py` | Parallel file extraction, chunking, checkpointed indexing |
| `kb/embeddings.py` | API-based text embeddings (azure.text-embedding-3-large, dim=3072) |
| `kb/contextual.py` | LLM-generated situating prefix per chunk (Contextual Retrieval) |
| `kb/context_summary.py` | Incremental 16-category domain knowledge extraction (dynamic timeouts, frontier model escalation for docs >100K chars) |
| `kb/retrieval.py` | Hybrid BM25 + dense + RRF + reranker retrieval |

**KB Index On-Disk Format:**
- `chunks.jsonl` -- one JSON object per chunk
- `bm25.json` -- serialized BM25 index
- `vectors.npy` -- dense float32 matrix
- `vector_ids.json` -- row-ID alignment
- `manifest.json` -- capabilities, model, dim, counts
- `current.json` -- atomic generation pointer

### 4.5 Test Generation (RLM)

| Module | Purpose |
|--------|---------|
| `testgen/rlm.py` | Recursive Language Model -- core generation engine |
| `testgen/quality_scorer.py` | Quality scoring across dimensions |
| `testgen/traceability.py` | Coverage traceability matrix |
| `testgen/testcase_excel.py` | Payload <-> reviewer Excel conversion |
| `testgen/test_data.py` | Pattern-based test data suggestions |
| `testgen/gen_cache.py` | SHA-keyed generation cache |
| `testgen/template_analyzer.py` | LLM analysis of uploaded test templates |
| `testgen/tc_types.py` | Type definitions (Implementation/SIT/UAT) |
| `testgen/diff_engine.py` | Payload diff for regeneration |

**RLM Pipeline:**
1. Decompose requirements (fast model)
2. Build KB context (navigate chunks -> map-extract)
3. Generate test cases per WI (frontier model)
4. Verify coverage + fill gaps (frontier model)
5. Parse JSON payload, validate schema

### 4.6 ADO/JIRA Integration

| Module | Purpose |
|--------|---------|
| `ado/api.py` | Auth header, PAT verify, project list |
| `ado/boards.py` | Teams, boards, WIQL queries, streaming WI load |
| `ado/testcase_creator.py` | Create test case work items in ADO |
| `ado/extract.py` | Download/extract WI attachments |
| `ado/test_steps.py` | ADO Test Case XML step format |

### 4.7 Jobs System

**File:** `agent/jobs.py`

- Durable job registry with persistence to `~/TestingToolkitWeb/jobs/registry.json`
- States: `pending -> running -> done/error/recovering`
- Checkpointing: per-job `checkpoint(**values)` for crash resume
- Recovery: on startup, "running" jobs become "recovering" (if resumable) or "error"
- Expiry: 45-min stale sweep, 24h TTL, 200K log lines max
- Cooperative cancellation via `stop_event`

### 4.8 Installer

**File:** `agent-bundle/install.py`

**Flow:**
1. Parse args, detect platform (windows/macos/linux + arch)
2. Stop running agent, clean previous install
3. Find Python (prefer wheelhouse-compatible version)
4. Create venv + install deps (offline from wheelhouse, fallback online)
5. Install cryptography (AES-256-GCM), Playwright (Chromium), MCP SDK
6. Extract MCP Node.js servers
7. Copy source to `~/TestingToolkitWeb/agent/src`
8. Set permissions on `.env.enc` (0o600)
9. Write `update.json` (manifest URL + token)
10. Register OS autostart (Windows Task Scheduler / macOS LaunchAgent / Linux .desktop)
11. Start agent + verify health

---

## 5. Data Flows

### 5.1 App Startup

```
Browser loads page.tsx
  -> AgentProvider polls GET /health every 3s
  -> status: "connecting" -> "connected" | "offline"
  -> If connected: fetch /settings -> mount AppStateProvider -> AppShell
  -> AppShell: reloadProjects() -> selectProject(lastProject) -> loadBoard
  -> If offline: show OnboardingScreen (installer download)
```

### 5.2 Agent Update Flow

```
1. AppShell calls checkForAgentUpdate() on launch + every 10 min
2. Hook calls GET /update/status -> agent fetches manifest from GitHub
3. Manifest at: github.com/nrcharanvignesh/Testing-Toolkit/agent-update.json?ref=parts
4. If patch_only: auto-apply POST /update/apply (SHA-256 verified, atomic rollback)
5. If major update: dispatch "tt:agent-update-required" event
6. AppShell renders AgentUpdateRequired overlay (non-dismissable)
7. User clicks "Update application" -> requestReinstall("update") -> reload
8. page.tsx shows OnboardingScreen with installer download
9. Installer runs -> stops old agent -> installs new -> starts
10. OnboardingScreen detects offline->connected transition -> auto-complete
```

**Guard rails:**
- `REQUIRED_AGENT_VERSION = "2.29.0"` -- hard block if agent < this (no GitHub needed)
- `reason === "update"` prevents auto-dismiss of installer screen
- 60s cache TTL on manifest checks
- `_is_newer()` uses strict semantic comparison (never prompts for equal/older)

### 5.3 KB Flow

```
Upload: POST /kb/upload/{project} (250MB max, streamed)
  -> Atomic file write to project kb/ dir
  -> 5s debounced selective context trigger

Index: POST /kb/index (deduplicated -- returns existing job if running)
  -> Extract text from documents (PDF, DOCX, PPTX, XLSX, etc.)
  -> Chunk by heading/paragraph
  -> Optional: LLM-generated situating prefix per chunk (Contextual Retrieval)
  -> BM25 index from tokenized chunks
  -> Dense embeddings via gateway (azure.text-embedding-3-large, 3072-dim)
  -> Atomic generation pointer write

Context: Parallel job started alongside index
  -> Per-document windowed LLM extraction (16 categories)
  -> Incremental: caches per-doc by content SHA
  -> Concurrent: max 3 docs, max 4 windows per doc
  -> 120s timeout, 3 retries per document
  -> Merge into unified ProjectContext

Retrieval: POST /kb/retrieve
  -> BM25 lexical search (always)
  -> Dense cosine similarity (if embeddings exist)
  -> Reciprocal Rank Fusion (k=60)
  -> Cross-encoder rerank (optional)
  -> Return top-k RetrievedChunk results

Frontend polling (app-state.tsx):
  -> Polls GET /kb/context/active/{project} every 2s
  -> Max 450 polls (15 min cap)
  -> Auto-retry partial failures: 3 attempts, exponential backoff
  -> Circuit breaker: stops if no progress between retries
```

### 5.4 Test Generation Flow

```
1. User selects WIs in BoardGrid, clicks Generate (Implementation/SIT/UAT)
2. GenerateDialog opens, configures options
3. POST /generate/start -> creates Job, spawns RLM pipeline
4. RLM: decompose -> KB context -> generate per-WI -> verify -> parse
5. Frontend polls job progress via GET /jobs/{id}
6. On complete: displays results, reviewer Excel download available
7. User reviews, optionally regenerates with feedback
8. Push to ADO: POST /generate/push (or push-xlsx for Excel-reviewed)
```

### 5.5 Excel Export Flow

```
Single Board:
  1. Fetch relationships in batches of 5 (parallel)
  2. Build ExcelJS workbook with buildBoardSheet()
  3. Sort: _BOTTOM_TYPES (bug, defect, issue, impediment, risk) go last
  4. Add hyperlinks, conditional formatting (red/amber/green)
  5. Write buffer -> browser download + upload to agent artifacts

All Boards/Projects:
  - One sheet per board + Summary sheet with hyperlinks
  - 3s rest between project requests
```

### 5.6 Credential Flow

```
Sealing (CI/deploy time):
  seal_env.py --from-vercel-env
  -> Reads LLM_UPSTREAM_BASE_URL, API_KEY, PROVIDER_FORMAT from env
  -> seal_credentials() -> AES-256-GCM envelope
  -> write_envelope_atomic() -> agent-bundle/src/.env.enc (0o600)

Loading (agent startup):
  app_config._load_env()
  -> load_release_credentials(envelope_path)
  -> Try OS-bound cache first (DPAPI on Windows)
  -> Compare release_id (SHA-256 of envelope bytes) with cached
  -> If match: return cached (no decryption needed)
  -> If different: authenticate+decrypt new envelope -> re-wrap OS-bound
  -> Fail-closed: no credentials = all AI features disabled
```

---

## 6. Security Model

### 6.1 Credential Envelope (AES-256-GCM)

**File:** `core/credential_envelope.py`

| Property | Value |
|----------|-------|
| Format | `tt-credential-envelope` |
| Version | 3 |
| Cipher | AES-256-GCM (12-byte nonce, 16-byte auth tag) |
| KDF (v3) | PBKDF2-SHA256, 600,000 iterations |
| KDF (v2, legacy read) | scrypt N=32768, r=8, p=1 |
| AAD | `b"TestingToolkit/GenAI/release-envelope/v3"` |
| Max size | 16,384 bytes |
| Permissions | 0o600 (owner-only) |

**Schema validated on open:**
- Exactly 3 keys: `BASE_URL`, `API_KEY`, `LLM_PROVIDER_FORMAT`
- `BASE_URL`: HTTPS, no credentials in URL, no fragments, max 2048 chars
- `API_KEY`: no control chars, max 8192 chars
- `LLM_PROVIDER_FORMAT`: must be `"anthropic"` or `"openai"`

**Fail-closed:** Any validation/decryption failure raises `CredentialEnvelopeError`. No credentials = all AI features disabled.

### 6.2 OS-Bound Rewrap

**File:** `core/credential_store.py`

- **Windows:** DPAPI `CryptProtectData` (CurrentUser scope, `CRYPTPROTECT_UI_FORBIDDEN`)
- **macOS/Linux:** System keyring (only if secure priority backend)
- **Rotation:** SHA-256 fingerprint of envelope detects changes; new envelope decrypted before replacing cache
- **Graceful degradation:** If OS-bound save fails, still returns credentials with state `"release-envelope"`

### 6.3 PAT Storage

**File:** `core/settings_store.py`

- Primary: OS keyring (Credential Manager / Keychain)
- Fallback: DPAPI on Windows, machine-derived key (PBKDF2 of hostname+user) with XOR + HMAC on others
- Dual write: both paths updated together for self-healing

### 6.4 Path Traversal Protection

- Artifact download/delete: `Path(path).resolve()` must be within workspace root
- Upload: filename stripped of directory components + separator replacement
- KB upload: resolved against project kb/ dir
- ZIP extraction: skips members with `../` (zip-slip guard)

### 6.5 Request Security

- Body size limit: 50 MB (middleware)
- KB upload limit: 250 MB
- Template/attachment limits: 25 MB
- CORS: restricted to known origins
- PNA preflight: allows HTTPS frontend -> localhost agent
- LLM proxy: constant-time token comparison, strips sensitive headers

### 6.6 Log Redaction

**File:** `core/app_logging.py`

- Regex catches `sk-*`, `key-*`, `token-*`, `bearer-*` patterns (12+ chars)
- Reads known credential env vars and replaces all occurrences with `[REDACTED]`

### 6.7 Security Posture Statement

From `AGENTS.md`: "An autonomous local client cannot guarantee non-extractability against its administrator; do not claim otherwise. True non-extractability requires a remote broker or hardware-backed attestation."

---

## 7. Test Suite

### 7.1 Python Tests (`agent-bundle/tests/`)

**45 test files** covering:

| Category | Files | Coverage |
|----------|-------|----------|
| Credential security | test_credentials.py, test_core_logic.py | AES-GCM, tamper detection, rotation, leakage scanning |
| Routes/API | test_routes.py, test_events_route.py | ASGI integration, no-500 sweep, contract validation |
| Installer | test_installer.py, test_install.py | Cross-platform, wheel tags, offline deps |
| KB/Retrieval | test_kb_logic.py, test_retrieval_quality_eval.py, test_context_summary.py | BM25, RRF, zip-slip, incremental merge |
| LLM/Chat | test_chat_streaming.py, test_chat_tools.py, test_openai_transport.py | SSE streaming, tool dispatch, protocol translation |
| E2E/Automation | test_automation.py, test_e2e_runner.py, test_bug_tracker.py | Script gen, session reuse, leakage scanning |
| ADO/JIRA | test_boards_logic.py, test_extract.py, test_linked_test_steps.py, test_jira_*.py | WIQL, relations, step parsing |
| Config | test_app_config.py, test_runtime_config.py, test_settings_store.py | Env parsing, encryption, validation |
| Core | test_guardrails.py, test_model_router.py, test_updater.py, test_job_durability.py | Input filtering, routing, versioning, persistence |

**Key security test patterns:**
- Fake sentinel values: `"sentinel-secret-key-1234567890"`, `"FAKE-PAT-000000..."`, `"hunter2"`
- Leakage scanning: assert secret bytes not in ciphertext, temp files, or logs
- Tamper detection: modify envelope bytes -> must raise CredentialEnvelopeError

### 7.2 TypeScript Tests (`__tests__/`)

| File | Coverage |
|------|----------|
| `app-state.unit.test.tsx` | Type contracts for KbState, DialogId, LogLine |
| `ProjectKbDialog.unit.test.tsx` | Dialog behavior, section tabs, TC_TYPES |
| `StatusBar.unit.test.tsx` | fmtMem, version display, network indicators |

### 7.3 Known Coverage Gaps

1. No dedicated path-traversal unit test (only indirect via route sweep)
2. No DOM rendering tests for React components (no DOM environment)
3. No integration tests for real ADO/JIRA HTTP calls
4. No rate limiting/abuse protection tests
5. No CORS/CSP header verification tests
6. DPAPI ctypes calls never exercised in CI (Linux-hosted)

---

## 8. Infrastructure and Deployment

### 8.1 Deployment Model

| Component | Platform | Trigger |
|-----------|----------|---------|
| Web App | Vercel | git push to `main` (auto-deploy) |
| Agent | User machine | Installer (Windows/macOS/Linux) |
| Agent manifest | GitHub `parts` branch | Manual push of `agent-update.json` |

No GitHub Actions CI -- Vercel's built-in git integration handles builds.

### 8.2 Vercel Configuration

**vercel.json:**
```json
{
  "framework": "nextjs",
  "headers": [
    { "source": "/agent/(.*)", "headers": [
      { "key": "Cache-Control", "value": "no-cache, no-store, must-revalidate" }
    ]}
  ]
}
```

**next.config.ts security headers** (all paths):
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `X-DNS-Prefetch-Control: on`
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), browsing-topics=()`
- `Strict-Transport-Security: max-age=63072000; includeSubDomains`
- `/` route: `Cache-Control: no-cache, no-store, must-revalidate`
- No frame-blocking headers (app may be embedded in iframes)

**Build ID:** `VERCEL_GIT_COMMIT_SHA` > `VERCEL_DEPLOYMENT_ID` > `dev-<timestamp>`

**Environment variables:**

| Variable | Context | Purpose |
|----------|---------|---------|
| `VERCEL_GIT_COMMIT_SHA` | Build-time | Git commit for build ID |
| `VERCEL_DEPLOYMENT_ID` | Build-time | Deployment ID (fallback) |
| `NEXT_PUBLIC_BUILD_ID` | Client-side | Baked-in build ID for freshness |
| `BUNDLE_READ_TOKEN` | Server-only | GitHub token (contents:read) for installer |
| `GITHUB_TOKEN` | Server-only | Fallback GitHub token |
| `LLM_UPSTREAM_BASE_URL` | Server-only | Real GenAI gateway URL |
| `LLM_UPSTREAM_API_KEY` | Server-only | Real GenAI API key |
| `LLM_PROVIDER_FORMAT` | Server-only | "anthropic" or "openai" |
| `LLM_PROXY_TOKEN` | Server-only | Shared secret for proxy auth (optional) |

### 8.3 Build Configuration

**tsconfig.json:**
- Target: ES2017, Module: esnext, JSX: react-jsx
- Strict mode enabled
- Path alias: `@/*` -> `./*`
- Excludes: `node_modules`, `src`, `agent-bundle`

**ESLint (eslint.config.mjs):**
- Flat config (v9+)
- Extends: `next/core-web-vitals`, `next/typescript`
- Disabled: `react-hooks/set-state-in-effect` (false positives)
- Ignores: `.next/`, `out/`, `build/`

**PostCSS:** `@tailwindcss/postcss` only (Tailwind v4 CSS-first config)

**No tailwind.config.ts** -- Tailwind v4 uses `@theme inline` in `globals.css`

### 8.4 Styling System (globals.css, 924 lines)

Material Design 3 / Apple HIG dual-theme:
- `@theme inline` maps CSS custom properties to Tailwind utilities
- Dark theme (default): 5-level surface hierarchy (`#0d1017` through `#2f333d`)
- Light theme (`.light` class): full light-mode palette
- shadcn token mapping onto `--tt-*` palette
- Component layer: `.tt-card`, `.tt-btn-*`, `.tt-input`, `.tt-rail`, `.tt-wi-card`, etc.
- `@media (prefers-reduced-motion: reduce)` disables animations
- macOS-style thin scrollbars

### 8.5 Test Configuration

**Vitest (vitest.config.ts):**
- Pattern: `**/__tests__/**/*.test.{ts,tsx}`, `**/*.unit.test.{ts,tsx}`
- Environment: `node` (no jsdom)
- Excludes: `e2e/`, `node_modules/`, `.next/`, `agent-bundle/`
- Path alias: `@/` -> project root

**Playwright (playwright.config.ts):**
- Test dir: `./e2e`
- Browser: Chromium (1440x900)
- Timeouts: 120s test, 15s expect, 20s action
- Base URL: `PLAYWRIGHT_BASE_URL` or `http://localhost:3000`
- Uses mocked local agent (no real agent/PAT/LLM needed)
- AI agent integration via `.claude/agents/playwright-test-*.md`

**pytest (agent-bundle/pytest.ini):**
- `asyncio_mode = auto`
- Test paths: `tests/`
- Conftest redirects all state to temp dir, sets dead-port BASE_URL

### 8.6 Agent Installation Paths

| OS | Install Dir | Autostart |
|----|-------------|-----------|
| Windows | `~/TestingToolkitWeb/` | Task Scheduler + Startup folder VBS |
| macOS | `~/TestingToolkitWeb/` | LaunchAgent plist |
| Linux | `~/TestingToolkitWeb/` | XDG autostart .desktop file |

### 8.7 MCP Servers (Bundled Node.js)

| Server | Package | Purpose |
|--------|---------|---------|
| Azure DevOps | `@azure-devops/mcp` ^2.7.0 | ADO work items, boards, repos |
| Atlassian/JIRA | `mcp-atlassian` ^2.1.0 | JIRA issues, projects |
| Playwright | `@playwright/mcp` ^0.0.77 | Browser automation |

- Node.js 20.18.0 bundled (split parts on `parts` branch)
- `node_modules` pre-built tarball (no npm at install time)
- Platforms: win32-x64, linux-x64, darwin-arm64

### 8.8 Branch Structure

| Branch | Purpose |
|--------|---------|
| `main` | Web app source (Vercel auto-deploys) |
| `parts` | Agent bundle manifest + binary parts + Node.js |

**`parts` branch contents:**
- `agent-update.json` -- overlay manifest (123 files, SHA-256 hashes)
- `manifest.json` -- full bundle manifest (626 MB, 13 split parts)
- Node.js binary split parts (per-platform)
- `node_modules_bundle/` -- pre-built MCP dependencies

### 8.9 Update Manifest (`parts` branch `agent-update.json`)

Required 7 keys:
```json
{
  "version": "2.30.0",
  "ref": "<commit SHA>",
  "generatedAt": "<ISO timestamp>",
  "files": [{"path": "...", "url": "...", "hash": "<SHA-256>"}],
  "installer": {"url": "...", "hash": "..."},
  "requirements": {"url": "...", "hash": "..."},
  "extraWheels": [],
  "mcpFiles": []
}
```

### 8.10 Key Ports and URLs

| Service | Address |
|---------|---------|
| Agent | `http://127.0.0.1:7842` |
| Vercel prod | `https://*.vercel.app` or custom domain |
| LLM proxy | `/api/llm/[...path]` (on Vercel) |
| GenAI gateway | Configured via `LLM_UPSTREAM_BASE_URL` |
| GitHub manifest | `api.github.com/repos/nrcharanvignesh/Testing-Toolkit/contents/agent-update.json?ref=parts` |
| ADO | `dev.azure.com/{org}/...` |
| JIRA | Configured base URL |

### 8.11 Installer Generation Flow

1. User clicks "Install" in web app (SSO-authenticated)
2. Browser GETs `/api/installer?os=windows&fresh=1`
3. Server generates cmd/PowerShell polyglot with embedded GitHub token
4. Script downloads bundle parts from `parts` branch (parallel, SHA-256 verified)
5. Reassembles into `bundle.zip`, extracts, launches `install.py`
6. Token never in source -- only in the downloaded file, which requires SSO access

---

## 9. Configuration Reference

### 9.1 Agent Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `TT_INSTALL_DIR` | Agent install directory | `~/TestingToolkitWeb` |
| `AGENT_MANIFEST_URL` | Override manifest URL | (from update.json) |
| `TT_UPDATE_TOKEN` | GitHub token for manifest | (from update.json) |
| `TT_UPDATE_REPO` | Override update repo | `nrcharanvignesh/Testing-Toolkit` |
| `TT_UPDATE_REF` | Override manifest branch | `parts` |
| `TT_KB_CONTEXT_CONCURRENCY` | Context extraction concurrency | 8 |

### 9.2 Agent Constants

| Constant | Value | Location |
|----------|-------|----------|
| `AGENT_PORT` | 7842 | version.py |
| `AGENT_VERSION` | "2.30.0" | version.py |
| `_CACHE_TTL_SEC` | 60 | updater.py |
| `_MAX_JOBS` | 100 | jobs.py |
| `_JOB_EXPIRY_SECONDS` | 2700 (45 min) | jobs.py |
| `DEFAULT_MODEL` | "bedrock.anthropic.claude-opus-4-8" | app_config.py |
| `DEFAULT_FAST_MODEL` | "bedrock.anthropic.claude-sonnet-4-6" | app_config.py |
| `DEFAULT_FALLBACK_MODEL` | "bedrock.anthropic.claude-haiku-4-5" | app_config.py |
| `EMBED_MODEL` | "azure.text-embedding-3-large" | app_config.py |
| `EMBED_DIM` | 3072 | app_config.py |
| `RERANK_MODEL` | "azure.cohere-rerank-v3-english" | app_config.py |
| `RLM_DIRECT_CONTEXT_TOKENS` | 150,000 | app_config.py |
| `_MAX_CONCURRENCY` | 4 (document-level) | context_summary.py |
| `_MAX_WINDOW_CONCURRENCY` | 5 (window-level) | context_summary.py |
| `_DOC_TIMEOUT_SEC` | 120s base (scales by window count, max 600s) | context_summary.py |
| `_LARGE_DOC_CHARS` | 100,000 (frontier model escalation threshold) | context_summary.py |
| `_DIR_TTL` | 60s (app data size cache) | routes/health.py |

### 9.3 Web App Constants

| Constant | Value | Location |
|----------|-------|----------|
| `REQUIRED_AGENT_VERSION` | "2.29.0" | lib/agent-version.ts |
| Health poll (connected) | 30s | lib/agent-context.tsx |
| Health poll (offline) | 3s | lib/agent-context.tsx |
| Offline threshold | 3 consecutive failures | lib/agent-context.tsx |
| Web freshness poll | 5 min | lib/use-web-freshness.ts |
| Update check interval | 10 min | AppShell.tsx |
| Metrics poll interval | 5s | lib/use-metrics.ts |
| Context poll interval | 2s | lib/app-state.tsx |
| Max context polls | 450 (15 min) | lib/app-state.tsx |
| Context retry max | 3 | lib/app-state.tsx |
| Event bus flush | 3s or 50 events | lib/event-bus.ts |
| SSE stall watchdog | 75s | lib/agent-client.ts |
| Agent fetch timeout | 60s | lib/agent-client.ts |
| Agent fetch retry codes | 408, 429, 502, 503, 504 | lib/agent-client.ts |

### 9.4 NavPanel Bottom Toolbar Order

```
Update | Help (Dropdown) | Settings | Project KB | Credentials | Theme | Collapse Navigation Bar
```

### 9.5 Export Sort Order

`_BOTTOM_TYPES` (sorted to end of worksheet): Bug, Defect, Issue, Impediment, Risk

### 9.6 Output Directory

Generated artifacts: `~/Downloads/Testing_Toolkit/`

---

## End of Documentation

**Residual assumptions:**

- JIRA route details inferred from the unified sources facade pattern (symmetric with ADO).
- MCP server runtime behavior (stdio transport, tool dispatch) inferred from package structure; internal message handling not traced line-by-line.
