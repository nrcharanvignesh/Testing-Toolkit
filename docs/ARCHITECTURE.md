# Testing Toolkit — Architecture

> Web v3.0.0 / Agent v2.8.2 — July 2026

This document describes the end-to-end architecture: what problem the platform
solves, how the pieces fit together, and how data and secrets flow through the
system.

---

## 1. The problem it solves

QA engineers working against **Azure DevOps (ADO)** repeatedly perform manual,
time-consuming tasks:

1. Open a board, read work items and their attachments.
2. Hand-write test cases for each work item across multiple phases
   (Implementation, SIT, UAT).
3. Bundle requirement documents and attachments into per-work-item PDF packets.
4. Triage and file bulk defects back into ADO.
5. Write and maintain E2E automation scripts.

The platform automates all five with an LLM-backed generation pipeline, a local
knowledge base, and a self-healing E2E runner — while keeping all secrets and
requirement content on the user's machine.

---

## 2. High-level shape

The system is split into two cooperating halves plus a distribution channel:

```
Browser (Vercel web app — web 3.0.0)
    |
    | HTTP (CORS, loopback only)
    v
Local compute agent (FastAPI @ 127.0.0.1:7842 — agent 2.8.2)
    |-- OS secret store  (PAT, LLM API key)
    |-- ~/TestingToolkit workspace  (projects, runs, outputs, logs)
    |-- LanceDB embedded vector store  (local, per-project)
    |-- Azure DevOps REST API  (PAT auth)
    |-- LiteLLM proxy (GenAI gateway)
         |-- /chat/completions      -> azure.gpt-4o, azure.gpt-4-turbo
         |-- /embeddings            -> azure.text-embedding-3-small
         |-- /rerank                -> azure.cohere-rerank-v3-english
         |-- /audio/transcriptions  -> azure.whisper
         |-- /v1/messages           -> Anthropic-format passthrough

GitHub repo (code + 'parts' branch: installer bundle + update manifest)
    |-- Browser downloads installer -> installs agent
    |-- Agent polls agent-update.json -> self-patches
```

Key property: **the browser never sees a secret and never talks to ADO or the
LLM directly.** The web app only ever calls `http://127.0.0.1:7842`.

---

## 3. Components

### 3.1 Web app (`app/`, `components/`, `lib/`)

- **Framework:** Next.js 16 (App Router, React 19), Tailwind CSS v4, deployed on
  Vercel.
- **Entry / gating (`app/page.tsx`):** polls `/health`. Three states:
  `connecting` → loading screen; `offline` → `OnboardingScreen`; `connected` →
  loads settings and renders `AppShell`.
- **Central UI state (`lib/app-state.tsx`):** React context mirroring the desktop
  `MainWindow` — current project/board, loaded board view, work-item selection
  set, log/progress feed, KB indexing status, dialog visibility.
- **Typed agent client (`lib/agent-client.ts`):** typed wrapper around `fetch` to
  `http://127.0.0.1:7842`. All request/response types mirror the Python data
  model.
- **Layout (`components/layout/`):**
  - `AppShell` — root three-pane layout wrapper
  - `NavPanel` — project/board navigator with avatar chips and WI count badges
  - `StatusBar` — agent health + connection metric chips with colored pill backgrounds
  - `ActivityBar` — icon rail: Layers (AI Stack), Settings, Brain (KB), Help;
    teal glow ring on hover, KB badge dot driven by `kbState`
- **Board workspace (`components/board/`):**
  - `ActionBar` — three action groups (Generate / Publish / Tools) with icons,
    separators, and selection count badge
  - `BoardGrid` — swim-lane grid with per-type left-border colors, type and state
    badge pills, lane count badges, mono ID column
  - `DetailPane` — 2-column property grid with icons, type/state badge pills, tag
    chip bubbles, name-hue comment avatars, inline Add Tag for ADO work items
  - `LogPanel` — HH:MM:SS timestamps, level left-border stripes (ERROR/WARN/SUCCESS),
    Clear and Copy-all toolbar
- **Dashboard (`components/dashboard/`):**
  - `CoverageBar` — board-level metric strip (total WIs, selected count, board
    name) mounted between ActionBar and BoardGrid
- **Dialogs (`components/dialogs/`):**
  - `SettingsDialog` — LLM config (base URL, API key, model selectors with live
    Fetch Models), ADO config, agent diagnostics
  - `GenerateDialog` — phase selector, TC count badge with badge-pop animation,
    quality/coverage pill badges, animated stage label with pulse dot, level-colored
    log lines
  - `E2EDialog` — TC list with last-run status badges, last-run summary bar
    (pass/fail/skip), level-colored progress log, Stop button
  - `AiStackDialog` — 7-layer AI stack visualizer (LLMs / Vector DB / Embeddings /
    Data Extraction / Open LLM Access / Framework / Evaluation), each layer with
    live status chip, active tool, and ecosystem alternatives
  - `ProjectKbDialog` — document upload, indexing progress, template management
    (upload/open/remove per phase)
  - `PackageDialog`, `DefectDialog`, `UploadDialog`, `RetrievalDialog`, `ChatDialog`

### 3.2 Local compute agent (`agent-bundle/src/agent/`)

FastAPI app bound to `127.0.0.1:7842`. Responsibilities:

- Hold secrets in the OS secret store
- Call Azure DevOps and the LiteLLM proxy with those secrets
- Run the RLM test-case generation pipeline
- Run the self-healing E2E automation engine
- Manage the local LanceDB vector store
- Read/write the local workspace (`~/TestingToolkit`)
- Detect updates from the GitHub-hosted manifest; installation remains installer-only

### 3.3 Backend domain modules (`agent-bundle/src/`)

| Module | Responsibility |
|---|---|
| `ado/` | ADO client, board/work-item fetch, attachment extraction, TC upload |
| `kb/` | Document chunking, BM25, dense embeddings (API), LanceDB vector store, hybrid retrieval, OCR (API vision), audio (API whisper) |
| `testgen/` | Phase/type definitions, RLM pipeline, template analysis, Excel writers |
| `defects/` | Bulk defect parsing, review workbook, ADO upload |
| `tools/` | PDF packaging, Office/Visio conversion |
| `automation/` | E2E script generator, self-healing Playwright runner |
| `core/` | Config (`app_config.py`), settings store, secret store, LLM client (Anthropic + OpenAI transport), OpenAI transport layer, orchestrator, logging, hardware detection |

---

## 4. AI stack — API-first (agent 2.8.0+)

As of agent 2.8.0, all AI/ML operations route through the **GenAI LiteLLM proxy**
(an OpenAI-format AND Anthropic-format gateway). There are **no local ONNX or ML
models** in the default distribution.

```
Layer               Tool / Endpoint                    Default model ID
---------------------------------------------------------------------------
LLM (primary)       /chat/completions  OR  /v1/messages  azure.gpt-4o
LLM (fast)          /chat/completions  OR  /v1/messages  azure.gpt-4-turbo
LLM (fallback)      /chat/completions  OR  /v1/messages  azure.gpt-4
Embeddings          /embeddings                          azure.text-embedding-3-small
Reranking           /rerank                              azure.cohere-rerank-v3-english
OCR                 /chat/completions (vision)           azure.gpt-4o
Audio               /audio/transcriptions                azure.whisper
Vector store        LanceDB (embedded, local)            —
```

Transport is configurable via `LLM_PROVIDER_FORMAT` env var:
- `anthropic` (default): uses `/v1/messages` with Anthropic content-block format
- `openai`: uses `/chat/completions` with OpenAI message/tool format

The `core/anthropic_client.py` + `core/openai_transport.py` handle all
translation between the two wire formats transparently.

Reranking uses native `/rerank` (Cohere API format) with LLM-as-judge fallback
on failure.

---

## 5. The generation pipeline (RLM)

Test-case generation uses a **Recursive Language Model (RLM)** pipeline:

```
Selected work items + phase
    |
    v
Gather context: work-item text + attachments + project KB
    |
    +-- KB <= 150k tokens? --> Direct: full context, one shot
    |
    +-- KB > 150k tokens?  --> Recursive navigate / map / reduce
                                (fast model over KB chunks)
    |
    v
Decompose into atomic testable requirements (fast model)
    |
    v
Generate test cases (primary model)
    |
    v
Coverage verification + gap-fill pass
    |
    v
Write review .xlsx to project/generated/
    |
    v
User reviews / edits / regenerates (up to 10 iterations)
    |
    v
Upload approved test cases to ADO
```

---

## 6. E2E automation engine (agent 2.8.2)

The `automation/` module generates Playwright Python scripts from test case steps
and executes them via a self-healing runner:

**Script generator (`script_generator.py`):**
- Translates 20 action types to Playwright async Python
- Uses `await expect(page).to_have_url(re.compile(...))` for URL assertions
- Injects `E2E_PASSWORD` env var for sensitive fields — no plaintext passwords

**Self-healing runner (`e2e_runner.py`):**
- 6-strategy locator waterfall: role -> label -> placeholder -> text -> test_id -> CSS
- Auto-retry with exponential backoff (3 attempts, 600ms base)
- iframe traversal 1 level deep
- Shadow DOM pierce via CSS `>>` combinator
- Element stability guard (bounding-box convergence before interact)
- Smart post-click wait: navigation clicks wait `commit`, in-page clicks skip
- Configurable soft assertions (`assert_*` don't abort the flow)
- `stop_fn` propagation checked at TC boundary and inside each step
- `locator_strategy` field on `StepResult` records which strategy won (`[HEAL]` log entries)

---

## 7. Data and secret flow

```
Release owner / Vercel                 Installed local agent
LLM_UPSTREAM_* variables                         |
   | seal_env.py (no value output)               |
   v                                              |
AES-256-GCM .env.enc -- installer/update ------->| authenticate + decrypt
                                                  | rewrap per user
                                                  v
                                      DPAPI / Keychain / Secret Service
                                                  |
Browser -- loopback request -------------------->| approved API operation
                                                  | Authorization header
                                                  v
                                           GenAI gateway
```

The browser never receives the GenAI base URL or API key. The release envelope
uses randomized AES-256-GCM with Scrypt-derived material and authenticated
metadata. It is installed with owner-restricted permissions; first use migrates
it to OS-bound storage where available. Plaintext files and plaintext fallback
are prohibited. If secure OS storage is unavailable, runtime continues from the
authenticated ciphertext envelope and never writes a plaintext cache.

Rotation is update-driven: the new release envelope has a different ciphertext
fingerprint, is authenticated, and only then replaces the OS-bound value. If the
new envelope is corrupt, the last valid OS-bound value is retained. Diagnostics
return only `ai_service_configured` and `ai_credential_protection` state labels.

ADO/JIRA credentials still enter through Settings and are stored through their
OS keyring paths. `GET /settings` and runtime diagnostics return presence/status
metadata only, never secret values. Requirement context is sent from the agent
to the approved GenAI gateway for AI operations; it does not transit Vercel or
the browser.

All central file and streamed Activity Logs pass through secret redaction. This
layer removes exact configured gateway values and credential-like key/token
strings; callers must still avoid logging headers, request objects, encrypted
payloads, or user secrets.

### Security boundary

The autonomous client must ultimately present a usable credential to the remote
gateway. These controls materially reduce accidental disclosure, plaintext-at-
rest exposure, naive extraction, and undetected tampering, but cannot guarantee
non-extractability against a determined administrator who controls the process,
memory, debugger, or operating system. A server-side broker or hardware-backed
attestation is required to move that trust boundary off the client.

---

## 8. Local workspace (`~/TestingToolkit`)

```
~/TestingToolkit/
  settings.json             non-secret source settings and UI preferences
  projects/<name>/
    system_prompt.txt       per-project RLM prompt
    kb/                     dropped requirement documents
    kb_index.json           cached chunk index
    templates/              client Excel templates + LLM-analyzed specs
    generated/              output payloads + review .xlsx per run
  runs/                     packager work dir
  outputs/                  finished PDF packets
  logs/                     rotating debug log
```

---

## 9. Distribution and auto-update

```
User clicks Download on onboarding screen
    |
    v
GET /api/installer?os=...
    |  (generates installer with embedded read token, valid for one download)
    v
Installer script (.cmd / .command / .sh)
    |  (fetches bundle parts from GitHub 'parts' branch in parallel)
    v
Agent installed + autostart registered
    |  (every ~60s polls agent-update.json on 'parts' branch)
    v
Version changed? -> overlay new src/ files (lightweight patch)
```

- `app/api/installer/route.ts` embeds a short-lived `BUNDLE_READ_TOKEN` at
  download time so users pull the bundle without needing repo access
- `agent/updater.py` overlays source files for lightweight updates — a full
  bundle rebuild is only needed to change the from-scratch install baseline
- `scripts/build-agent-update-manifest.mjs` produces `agent-update.json` for the
  `parts` branch
- The `parts` branch has a `vercel.json` with `deploymentEnabled: false` so Vercel
  never tries to build it as a Next.js app

---

## 10. Test coverage

| Suite | Tool | Count | Status |
|---|---|---|---|
| Frontend unit tests | vitest | 10 | All pass |
| Backend unit + integration | pytest | 177+ | All pass |
| E2E contract tests | Playwright + mockAgent | 8 specs | All pass (mock agent) |

The E2E specs use `mockAgent(page, {...})` in `e2e/helpers.ts` to fulfil the
entire agent HTTP contract at the network layer — no live agent required to run
them. Live ADO flows require a real agent and real credentials.

---

## 11. Technology summary

| Layer | Technology |
|---|---|
| Web UI | Next.js 16 (App Router), React 19, Tailwind CSS v4, lucide-react |
| Hosting | Vercel |
| Local agent | Python 3.10+, FastAPI, Uvicorn (`127.0.0.1:7842`) |
| AI gateway | LiteLLM proxy (OpenAI + Anthropic format) |
| LLM models | Azure OpenAI via GenAI proxy (`azure.gpt-4o`, `azure.gpt-4-turbo`) |
| Embeddings | Azure OpenAI via GenAI proxy (`azure.text-embedding-3-small`) |
| Reranking | Cohere via GenAI proxy (`/rerank` endpoint) |
| Vector store | LanceDB (embedded, local, per project) |
| Lexical search | BM25 (numpy, in-process) |
| E2E automation | Playwright (async Python), self-healing runner |
| Source of truth | Azure DevOps REST API |
| Secrets | OS-native secret store (per user, on device) |
| Distribution | GitHub `parts` branch (split bundle + update manifest) |
