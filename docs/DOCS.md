# Testing Toolkit — Feature Reference

> Web v3.40.0 / Agent v3.40.0 — July 2026

This document is the complete feature reference for the Testing Toolkit
platform: what every feature does, how to use it, workspace layout, runtime
dependencies, and known constraints.

---

## Table of contents

1. [Quick start](#1-quick-start)
2. [UI overview](#2-ui-overview)
3. [Features](#3-features)
4. [First-run setup](#4-first-run-setup)
5. [Workspace layout](#5-workspace-layout)
6. [Settings reference](#6-settings-reference)
7. [AI stack](#7-ai-stack)
8. [E2E automation engine (Autonomous AI QA Agent)](#8-e2e-automation-engine-autonomous-ai-qa-agent--v3400)
9. [Knowledge base management](#9-knowledge-base-management)
10. [Hardware utilization](#10-hardware-utilization)
11. [Runtime dependencies](#11-runtime-dependencies)

---

## 1. Quick start

**Web app (browser — Vercel-hosted)**

Open the app in any modern browser. If no local agent is detected, the
onboarding screen offers a one-click installer per OS.

**Local agent (from source)**

```bash
cd agent-bundle/src
python -m agent.server        # starts on http://127.0.0.1:7842
```

**Web app (local development)**

```bash
npm install
npm run dev                   # http://localhost:3000
npm test                      # vitest unit tests (10 tests)
```

**Agent test suite**

```bash
cd agent-bundle
python -m pytest tests/ -q    # 177+ tests
```

---

## 2. UI overview

The interface follows a VS Code-style three-pane layout:

```
+----+------------------+--------------------------------------+----------+
| AB |   NAV PANEL      |    BOARD GRID                        |  DETAIL  |
|    |                  |    ActionBar (Generate/Publish/Tools) |  PANE    |
|    |  Projects        |    CoverageBar (metrics strip)       |          |
|    |  Boards          |    Swim-lane work-item grid          |          |
|    |                  |    LogPanel (collapsible)            |          |
+----+------------------+--------------------------------------+----------+
| STATUS BAR: agent health, ADO connection, KB state, last sync           |
+-------------------------------------------------------------------------+
```

**Activity Bar (AB)** — icon rail buttons from top:
- Refresh — check for agent updates
- Folder — open project/board navigator
- Grid — open board navigator
- Layers — open AI Stack explorer
- Settings — open settings dialog
- Brain — open Project KB dialog (badge dot shows KB state)
- Help — open help

**CoverageBar** — slim strip between ActionBar and BoardGrid showing:
- Total work items on the board
- Currently selected count
- Active board name

**BoardGrid** — swim-lane table with:
- Per-type left-border color accent (Story=blue, Bug=red, Task=amber,
  Epic=purple, Feature=green)
- Type and state badge pills
- Lane group headers with count badges
- Monospace ID column with type-colored left border

**DetailPane** — right panel when a work item is selected:
- Header with type/state/column badge pills
- 2-column property grid: Assigned, Area, Iteration, Column
- Tag chip bubbles (teal) with inline Add Tag for ADO items
- Description and Acceptance Criteria rendered as HTML
- Comments with name-hue colored avatars
- Attachments and links
- Outputs tab: generated test cases, download links

---

## 3. Features

### 3.1 Test case generation (Recursive Language Model)

Select one or more work items and a phase. The RLM pipeline:

1. **Gather context** — work-item text, attachments, and project KB
2. **Navigate / Map / Reduce** — if KB exceeds ~150k tokens, a fast model
   selects and summarizes relevant chunks
3. **Decompose** — fast model enumerates atomic testable requirements as a
   numbered checklist (field validations, boundaries, state transitions, errors)
4. **Generate** — primary model produces test cases covering the decomposed
   requirements
5. **Coverage verification + gap-fill** — a second pass identifies uncovered
   acceptance criteria and produces additional test cases for gaps

**Quality features:**
- Few-shot examples (gold-standard TCs in system prompt)
- Requirement decomposition (atomic coverage checklist)
- Coverage verification + gap-fill (always-on)
- Context prioritization (longer = more relevant, sorted first)
- Query decomposition (multi-sub-query retrieval for multi-criteria stories)
- Contextual retrieval (LLM-generated situating prefixes per chunk, 49-67%
  fewer retrieval misses)

**Regeneration with feedback** — after generation, write change instructions and
the entire test set is regenerated incorporating the feedback. Up to 10
iterations per session per set of work items.

**Output** — a reviewable Excel workbook written to `project/generated/`. The
GenerateDialog shows:
- Animated stage label with pulse dot
- TC count badge with pop animation on completion
- Quality/coverage pill badges (green = threshold met, amber = gaps)
- Level-colored log lines with left-border stripes

### 3.2 Per-client template support

Upload a client's Excel test-script template (one per phase: Implementation /
SIT / UAT) via the Project KB dialog > Templates tab.

On upload, the LLM analyzes the template's full structure — header row, column
purposes, row organization — even with non-English headers or unconventional
layouts. The result is cached as a deterministic spec; all subsequent renders
use it with zero LLM calls.

Generated test cases are then rendered into a **copy of the original template**,
preserving its headers, branding, column widths, and styles.

### 3.3 E2E automation

The E2E dialog generates and runs Playwright automation scripts directly from
test case steps.

See [Section 8](#8-e2e-automation-engine) for the full engine specification.

The E2EDialog shows:
- Test case list with last-run status badge per row (pass/fail/skip)
- Last-run summary bar: pass count, fail count, skip count, timestamp
- Level-colored progress log with left-border stripes
- Stop button (wired to server-side `stop_fn` propagated at TC and step level)

### 3.4 PDF packaging

**Per-WI PDFs** contain:
- Cover page (title, metadata, description with inline images, acceptance
  criteria, comments)
- All attachments converted to PDF (Office, images, existing PDFs)

**Combined PDF** (`All_WIs_Combined.pdf`) merges all selected WI PDFs.

**KB Bundle** (`Upload to KB/`) splits the combined PDF into AI knowledge
base-ready chunks: text chunks, image-lookup PDFs, `index.json` and `README.md`.

### 3.5 Upload to ADO

Push reviewed test cases to Azure DevOps:
- Creates Test Cases as children of parent stories
- ADO-compliant Steps XML
- Re-reads the reviewed Excel (respects `Skip=Yes` edits)

### 3.6 Bulk defect upload

1. **Select** — one or more documents (Word, Excel, PowerPoint, PDF)
2. **Parse** — adaptive heading detection extracts parent ID, title, description,
   repro steps, severity, expected/actual results, images; LLM fallback on parse
   failure
3. **Review** — generates an Excel with all defects + embedded images; mark
   `Skip=Yes` to exclude
4. **Upload** — creates Bug work items in ADO; Area Path, Iteration Path, and
   Board Column are derived from the parent work item

### 3.7 AI Stack explorer

Open via the Layers button in the Activity Bar. Shows all 7 layers of the
platform's AI architecture in an expandable accordion, with:

- Per-layer status chip (Connected / Partial / Not configured) driven by live
  settings state
- Active component with model ID, description, and a Configure shortcut
- Full ecosystem alternatives list from the Full AI Stack reference

Layers: LLMs, Vector DB, Text Embeddings, Data Extraction, Open LLM Access,
Framework, Evaluation.

### 3.8 Retrieval + RAG chat

The Retrieval dialog runs ad-hoc semantic search against the project KB —
useful for testing what context the generator will receive before running a
generation.

The Chat dialog sends messages to the LLM with KB context attached, for
interactive exploration of the project's requirement corpus.

### 3.9 Linked test-case discovery ("Generated Tests" column)

The board's **Generated Tests** column counts the test cases already linked to
each work item. Discovery is intentionally tracker-agnostic and tolerant of any
team's SDLC conventions — it does not depend on a single relation type.

**Azure DevOps** (`ado/boards.py`): a work item's linked test cases are counted
from its relations via EITHER:

- a **test relation** — reference name or friendly attribute name matching
  `Tested By`, `Tests`, or a renamed custom test link (matched
  case/punctuation-insensitively); OR
- a **parent/child/related/dependency link** whose target work item is itself a
  **test-type** item (its `System.WorkItemType` contains "test", e.g.
  `Test Case`).

Targets are de-duplicated by id so a test reachable two ways counts once.
Relations are fetched with `workitemsbatch` using the capitalized
`$expand: "Relations"` enum name; if a tenant ignores batch expand, a per-item
`GET ...?$expand=relations` fallback runs automatically.

**JIRA** (`jira/boards.py`): counted from EITHER:

- an **issue link** whose linked issue type or link-type name mentions "test"
  (covers `Tests` / `Tested By` link types and `Test` / `Test Execution` /
  `Test Set` issue types used by Xray/Zephyr); OR
- a **subtask** whose issue type mentions "test".

De-duplicated by issue key.

**Discovery diagnostic script.** To verify what the app sees for specific work
items (useful when a tenant uses unusual link/type names), run the shipped
script from the install's `src` directory:

```
# Azure DevOps
python -m scripts.discover_test_cases --tracker ado --project "MyProject" --ids 1536967 1536952

# JIRA
python -m scripts.discover_test_cases --tracker jira --keys PROJ-101 PROJ-102

# Add --json for machine-readable output, --verbose to list every relation/link
```

It reuses the app's stored credentials and prints, per work item, the matched
test cases and WHY each was matched (relation name vs. child type), so an
unmatched item's real link/type names are immediately visible.

**Running linked test cases in E2E.** The E2E Automation dialog lists BOTH
app-generated test cases and the real test cases linked to the selected work
items in the tracker (marked with a "linked" badge). Their actual steps are
pulled server-side — ADO from the Test Case work item's
`Microsoft.VSTS.TCM.Steps` XML, JIRA from Xray (`/rest/raven/.../test/.../step`)
or Zephyr / Zephyr Scale (`/testscript`, `/teststep`) with a description-text
fallback — and executed through the same self-healing Playwright runner as
generated test cases. The `/e2e/test-cases` and `/e2e/start` endpoints take a
`wi_ids` scope so the selectable list and the run share one identical index
space.

---

## 4. First launch and source setup

The app shell opens immediately after installation. The guided tour is the only
first-launch overlay; no work-item source is required to browse the application.
Configure Azure DevOps and/or JIRA later from Settings. AI endpoint, credential,
and model routing are centrally managed and are never exposed as user settings.

---

## 5. Workspace layout

Connection settings and credentials live in a stable config directory that is
**independent of the versioned install and the workspace**, so they survive
every agent update and reinstall (see 5.1).

```
~/.testing_toolkit/            stable config dir (NEVER touched by updates)
  settings.json                source connections, display prefix
  ado_pat.enc                  encrypted ADO PAT fallback (if no OS keyring)
  jira_pat.enc                 encrypted JIRA PAT fallback (if no OS keyring)

~/TestingToolkitWeb/
  ui_prefs.json               UI preferences (window, panel sizes)
  projects/<name>/
    system_prompt.txt         per-project RLM generation prompt
    kb/                        requirement documents (source files)
    kb_index.json             cached chunk index (BM25 + vector)
    templates/                 Excel templates + LLM-analyzed specs per phase
    generated/                 output payloads + review .xlsx per run
  runs/                       packager work dir
  outputs/
    <name>/
      packets/                 per-WI PDFs + combined PDF + KB bundle
      testcases/               test case review .xlsx
  logs/                       rotating debug log
```

### 5.1 Credential persistence and updates

- Personal Access Tokens are stored in the OS secret store first (Windows
  Credential Manager / macOS Keychain / Linux Secret Service). When no keyring
  is available the agent writes an **encrypted** fallback file into
  `~/.testing_toolkit/` (`ado_pat.enc`, `jira_pat.enc`).
- The ADO organization/prefix and JIRA URL/user live in
  `~/.testing_toolkit/settings.json`.
- Because none of these paths sit inside the versioned install directory, an
  agent update or reinstall cannot delete or overwrite them. On first launch of
  v2.19+, any pre-existing `~/TestingToolkitWeb/settings.json` and legacy PAT
  files are migrated once into the stable config dir automatically.
- Override the config dir with `TT_CONFIG_DIR` (absolute path) if required.

### 5.2 Update policy (web vs. agent)

- The web app deploys continuously to Vercel. **UI-only and label changes ship
  through the web deploy and never require an agent reinstall.**
- The installed agent only prompts to reinstall when the published update
  manifest advertises a version **strictly newer** than the running agent
  (semantic comparison, not string inequality). Re-publishing a manifest at an
  equal or lower version does not nag users.
- The agent update path is detection-only: the agent never downloads, patches,
  or restarts itself. A genuinely newer agent is installed through the normal
  installer, which preserves the workspace and the stable config dir.

---

## 6. Settings reference

User-editable Settings contain source connections and display preferences only.
GenAI endpoint, credential, provider format, and model routing are centrally
release-managed and are not returned by the local-agent API.

| Field | Description |
|---|---|
| `ado_org` | Azure DevOps organization URL; optional when using only JIRA |
| `ado_pat` | ADO Personal Access Token, stored through the OS secret-store path |
| `project_prefix` | Optional prefix stripped from displayed ADO project names |
| `jira_url` | JIRA site URL; optional when using only ADO |
| `jira_username` | JIRA username/email |
| `jira_pat` | JIRA API token, stored through the OS secret-store path |
| `jira_project_prefix` | Optional prefix stripped from displayed JIRA project names |
| `tls_mode` | System trust, trust-store, or explicitly disabled certificate verification |

Release owners source `LLM_UPSTREAM_BASE_URL`, `LLM_UPSTREAM_API_KEY`, and optional
`LLM_PROVIDER_FORMAT` from Vercel project variables. `seal_env.py` writes only an
authenticated AES-256-GCM `.env.enc` envelope. Installed agents rewrap valid
credentials into DPAPI, macOS Keychain, or Linux Secret Service when available.

---

## 7. AI stack

All AI operations route through the **GenAI LiteLLM proxy**. No local ONNX
models are required in the default configuration.

| Layer | Endpoint | Default model |
|---|---|---|
| LLM generation/verification | `/chat/completions` or `/v1/messages` | `bedrock.anthropic.claude-opus-4-8` |
| LLM navigation/extraction | `/chat/completions` or `/v1/messages` | `bedrock.anthropic.claude-sonnet-4-6` |
| LLM fallback | `/chat/completions` or `/v1/messages` | `bedrock.anthropic.claude-haiku-4-5` |
| Embeddings | `/embeddings` | `azure.text-embedding-3-large` (3072 dimensions) |
| Reranking | `/rerank` (Cohere API format) | `azure.cohere-rerank-v3-english` |
| OCR | `/chat/completions` (vision) | centrally routed vision-capable model |
| Audio | `/audio/transcriptions` | `azure.whisper` |

The **provider format** determines the wire protocol:
- `anthropic`: uses `/v1/messages` with content-block + tool-use format
- `openai`: uses `/chat/completions` with function-calling format

Both paths are supported by the LiteLLM proxy. The `core/openai_transport.py`
module handles all message/tool translation between the two formats.

### Credential protection and boundary

The browser never receives the GenAI endpoint/key. The release pipeline seals
Vercel-sourced values into a randomized, authenticated AES-256-GCM envelope using
Scrypt; plaintext `.env` fallback is prohibited. The installer restricts the
ciphertext to the current user, and first use rewraps it into Windows DPAPI,
macOS Keychain, or Linux Secret Service where available. Tampered envelopes fail
closed, safe rotation preserves the last valid OS-bound value, and both file and
streamed logs pass through centralized redaction.

This POC hardening prevents accidental disclosure, plaintext-at-rest exposure,
naive extraction, and undetected modification. Because the local process must
ultimately send a usable credential to the GenAI gateway, a determined device
administrator/debugger can still inspect runtime memory or traffic endpoints.
True non-extractability requires a server-side broker or hardware-backed remote
attestation.

### MCP servers (Model Context Protocol)

The agent ships with three MCP servers installed automatically by `install.py`
when Node.js 18+ is present:

| Server | npm package | Provides |
|---|---|---|
| ADO | `@azure-devops/mcp` | Work items, boards, PRs, pipelines, wikis |
| JIRA | `@atlassian/jira-mcp` | Issues, projects, sprints, boards, transitions |
| Playwright | `@playwright/mcp` | Live browser automation, screenshots, navigation |

Installed into `~/TestingToolkitWeb/mcp_servers/node_modules/`. The bridge
(`core/mcp_bridge.py`) resolves each entry point locally first, then falls
back to globally-installed npm packages.

**Startup**: servers are started in a background thread at agent boot. Each
server communicates over stdio (the MCP standard transport). The bridge
provides a thread-safe synchronous interface to the async MCP session.

**Degradation**: if Node.js is missing or an npm install fails, the bridge
returns an empty tool list for that server and the agent continues normally.
Configure credentials in **Settings** to enable ADO and JIRA MCP tools.

**Credentials**:

| Server | Required settings |
|---|---|
| ADO MCP | Organization URL + PAT (same as core ADO integration) |
| JIRA MCP | JIRA URL + Email + API Token (same as core JIRA integration) |
| Playwright MCP | None — starts headless automatically |

---

## 8. E2E automation engine (Autonomous AI QA Agent — v3.40.0)

The E2E runner is a fully autonomous AI QA agent that studies the project
knowledge base, discovers test cases via parent-child WI hierarchy, executes
up to 3 user stories in parallel with fully isolated browser contexts, observes
page state after every action, and produces per-WI artifacts (PDF + video +
Excel).

### Pipeline

1. **TC Discovery:** parent-child WI hierarchy traversal (or TestedBy/Tests links)
2. **KB Briefing:** builds TestBrief per-WI (screens, preconditions, business rules)
3. **Plan Compilation:** brief feeds into LLM plan compiler
4. **Parallel Execution:** up to 3 WIs in isolated BrowserContexts (maximized)
5. **Self-Healing:** 6-strategy locator waterfall on element not found
6. **Page Observation:** a11y tree capture + confidence scoring after every action
7. **Artifacts:** per-WI PDF report + video recording + Excel row append
8. **Human Review:** per-TC approve/reject, sign-off workflow

### Script generator

Translates test case steps into a valid Playwright async Python script:

- 20 action types: `click`, `fill`, `type`, `hover`, `double_click`,
  `press_key`, `scroll`, `wait_for_text`, `wait_for_url`, `assert_present`,
  `assert_not_present`, `assert_text`, `assert_url`, `assert_enabled`,
  `assert_disabled`, `assert_value`, `assert_checked`, `navigate`,
  `select`, `upload`
- Password fields inject `os.environ["E2E_PASSWORD"]` — no plaintext
- `assert_url` uses `await expect(page).to_have_url(re.compile(...))`
- Generated scripts are valid Python verified by `ast.parse`

### Self-healing runner

When a locator fails, the runner automatically tries 5 fallback strategies before
giving up:

```
1. role    -> get_by_role(role, name=label)
2. label   -> get_by_label(selector)
3. placeholder -> get_by_placeholder(selector)
4. text    -> get_by_text(selector)
5. test_id -> get_by_test_id(selector)
6. css     -> locator(selector)  [last resort]
```

Additional reliability features:
- Auto-retry: 3 attempts with exponential backoff (600ms base)
- iframe traversal: 1 level deep, tries each child frame
- Shadow DOM: CSS `>>` combinator as last resort
- Element stability: bounding-box convergence guard before interact
- Post-click wait: navigation clicks use `waitUntil: "commit"`, in-page
  clicks skip to avoid false network-idle waits
- `[HEAL]` log entries record which strategy succeeded for audit
- PageObserver integration: captures before/after a11y state per action

### Parallel execution

- ONE shared Chromium process, up to 3 independent BrowserContexts
- Each context: own cookies, own storage state, own video directory
- Per-WI cancellation: `POST /e2e/stop/{job_id}?wi_id=X`
- Sequential fallback when only 1 WI selected (zero regression risk)
- Browser launches maximized (`--start-maximized` + `no_viewport=True`)

### KB briefing

- Queries HybridRetriever + ProjectContext before plan compilation
- Produces TestBrief: screens, preconditions, business_rules, navigation_hints
- Brief is authoritative context for the plan compiler
- Falls back gracefully when KB has no indexed content

### Page observation

- Captures a11y tree, URL, error/success/loading signals after every action
- Confidence scoring (0.0-1.0) based on signal clarity
- ObservationDelta detects new errors between before/after states
- Feeds findings into PDF report as AI observations

### Artifacts per WI

- **PDF report** (reportlab): header, summary, step results table, AI
  observations, video reference
- **Video** (Playwright context-level): continuous recording, replaces screenshots
- **Excel append**: row per TC to project's `e2e_results.xlsx`

### Human review flow

- ReviewPanel in E2EDialog: per-TC approve/reject buttons
- Video link per TC for playback
- Sign-off button (enabled only when all TCs reviewed)
- Review is optional — does not block autonomous execution

---

## 9. Knowledge base management

Per-project KB supports the following document types:

| Category | Formats |
|---|---|
| Text | `.md`, `.txt`, `.html`, `.csv`, `.json` |
| Office | `.pdf`, `.docx`, `.xlsx`, `.pptx` |
| Scanned PDFs | OCR via vision API + PyMuPDF rasterizer |
| Images | OCR text extraction via vision API |
| Audio/Video | Transcription via Whisper API |
| Legacy | `.doc`, `.ppt`, `.odt`, `.eml`, `.epub` via olefile |

Preparation is project-scoped and starts when a project is selected or its KB
files change. Extraction, contextual enrichment, embedding, vector persistence,
and context summary work continue in the installed agent when the browser is
closed. Resumable jobs are checkpointed to disk and restarted automatically
after an unexpected agent restart.

A complete hybrid index is published as an immutable generation with one atomic
pointer swap. Generation and automation therefore keep using the last complete
index while a new generation is prepared; incomplete generations are never
served. The KB icon badge and status bar use the same lifecycle state (red = no
usable index, amber = preparing or recovering, green = ready).

**Hybrid retrieval:**
- BM25 lexical search (always active)
- Dense vector search (embedding API)
- Reciprocal Rank Fusion combining both (top 96 candidates)
- Cross-encoder reranking (rerank API) down to top 32
- Contextual retrieval (LLM-generated chunk prefixes at index time)
- Query decomposition (multi-sub-query for multi-criteria stories)

---

## 10. Hardware utilization

The agent auto-detects available hardware at startup via `core/hardware.py`:

| Resource | Detection | Usage |
|---|---|---|
| CPU cores | `os.sched_getaffinity` / `os.cpu_count()` | Thread pool sizing |
| GPU (CUDA) | onnxruntime providers | (reserved — future use) |
| GPU (DirectML) | onnxruntime DML | (reserved — future use) |
| GPU (Metal) | Apple MPS detection | (reserved — future use) |
| System RAM | `psutil` / `sysctl` / `/proc/meminfo` | Embedding batch sizing |

Since agent 2.8.0, ONNX models are no longer loaded by default (all inference
routes through the API). Hardware detection remains for system diagnostics and
is reported in the Settings dialog via the agent's `/health` response.

---

## 11. Runtime dependencies

### Web app

| Package | Version | Purpose |
|---|---|---|
| next | 16.2.9 | React framework + App Router |
| react | 19.2.4 | UI |
| tailwindcss | ^4 | Styling |
| lucide-react | ^1.21.0 | Icons |
| framer-motion | ^12.42.0 | Animations |
| @base-ui/react | ^1.6.0 | Accessible primitives |

### Local agent

| Package | Purpose |
|---|---|
| fastapi | HTTP server framework |
| uvicorn | ASGI server |
| httpx | HTTP client (ADO + LLM proxy calls) |
| lancedb | Embedded vector store |
| numpy | BM25 scoring |
| openpyxl | Excel read/write |
| pypdf | PDF text extraction |
| reportlab | PDF generation |
| Pillow | Image handling |
| python-docx / python-pptx | Office document handling |
| PyMuPDF | PDF rasterization (for OCR) |
| imageio-ffmpeg | Video audio-track extraction (local format conversion) |
| playwright | E2E browser automation (optional, auto-installed post-setup) |
| keyring | OS secret store |
| certifi / truststore | TLS certificates + OS trust store |
