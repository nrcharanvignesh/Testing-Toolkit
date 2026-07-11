# Testing Toolkit — Feature Reference

> Web v3.0.0 / Agent v2.9.0 — July 2026

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
8. [E2E automation engine](#8-e2e-automation-engine)
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

### 3.9 Manual mode

When no API key is configured:
- Copy the system prompt + work-item dump from the Generate dialog
- Paste into any LLM session
- Paste the JSON response back into the app
- Continue the normal review > upload flow

---

## 4. First-run setup

On first launch after agent install, a setup wizard collects:

1. **LLM API** — base URL (GenAI proxy), API key, primary model, fast model
2. **Azure DevOps** — Personal Access Token (PAT), organization URL
3. **Display prefix** — optional short label shown in the status bar

All settings are editable at any time from the Settings dialog (gear icon).

---

## 5. Workspace layout

```
~/TestingToolkit/
  settings.json              base_url, model, org, prefix, provider_format
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

---

## 6. Settings reference

| Field | Description |
|---|---|
| `base_url` | LiteLLM proxy URL (e.g. `https://genai.example.com`) |
| `api_key` | Bearer token for the LiteLLM proxy (stored in OS keyring) |
| `model_large` | Primary model ID (generation quality) — default `azure.gpt-4o` |
| `model_medium` | Fast model ID (decompose, navigate) — default `azure.gpt-4-turbo` |
| `model_small` | Fallback model ID — default `azure.gpt-4` |
| `embed_model` | Embedding model ID — default `azure.text-embedding-3-small` |
| `rerank_model` | Rerank model ID — default `azure.cohere-rerank-v3-english` |
| `ado_org` | Azure DevOps organization URL |
| `ado_pat` | ADO Personal Access Token (stored in OS keyring) |
| `provider_format` | `anthropic` (default) or `openai` — wire format to the proxy |

---

## 7. AI stack

All AI operations route through the **GenAI LiteLLM proxy**. No local ONNX
models are required in the default configuration.

| Layer | Endpoint | Default model |
|---|---|---|
| LLM generation | `/chat/completions` or `/v1/messages` | `azure.gpt-4o` |
| LLM fast | `/chat/completions` or `/v1/messages` | `azure.gpt-4-turbo` |
| LLM fallback | `/chat/completions` or `/v1/messages` | `azure.gpt-4` |
| Embeddings | `/embeddings` | `azure.text-embedding-3-small` |
| Reranking | `/rerank` (Cohere API format) | `azure.cohere-rerank-v3-english` |
| OCR | `/chat/completions` (vision) | `azure.gpt-4o` |
| Audio | `/audio/transcriptions` | `azure.whisper` |

The **provider format** determines the wire protocol:
- `anthropic`: uses `/v1/messages` with content-block + tool-use format
- `openai`: uses `/chat/completions` with function-calling format

Both paths are supported by the LiteLLM proxy. The `core/openai_transport.py`
module handles all message/tool translation between the two formats.

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

## 8. E2E automation engine

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
