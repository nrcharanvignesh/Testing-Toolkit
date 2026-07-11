> **Historical desktop parity snapshot:** retained for comparison only. Current
> credential, model-routing, and installation guidance lives in `../DOCS.md` and
> `../../agent-bundle/INSTALL.md`.

# Testing Toolkit v3.0 - Technical Documentation

A unified Azure DevOps + Jira desktop application (PySide6, dark glass UI)
that consolidates test case generation, AI chatbot, work-item PDF packaging,
E2E test automation, and quality analytics into a single board-driven
experience.

---

## Quick Start

### From Source (connected machine with Python 3.10+)

```bash
cd src
python build.py     # one command: cleans, installs, checks, builds
```

`build.py` handles everything automatically in one command:

1. Cleans old build/dist artifacts
2. Installs all required packages (no manual pip)
3. Runs environment preflight checks + auto-resolves issues
4. Prepares MCP servers (bundles node.exe for ADO MCP)
5. Encrypts `.env` with DPAPI -> `.env.enc`
6. PyInstaller build (produces one-folder in `src/dist/TestingToolkit/`)

To run from source instead: `cd src && python main.py`

To run the test suite: `cd src && python tests/test_full_e2e.py`

### Portable Zero-Install (air-gapped / restricted machines)

```
Windows:    double-click install.cmd
macOS:      ./install.sh
Linux:      ./install.sh
```

Each launcher does a full clean-install-build-launch cycle on every run:

1. Cleans old `build/` and `dist/` directories + all `__pycache__`
2. Installs all packages from offline wheelhouse (pip + app deps + PyInstaller)
3. Builds the `.exe` via PyInstaller (bundles models, assets, all backends)
4. Launches the built `TestingToolkit.exe`

`install.cmd` is native cmd.exe (NO Git Bash dependency). `install.sh` is
for macOS/Linux. No Python, pip, or network access required on the target
machine - everything is bundled.

---

## What It Does

1. **Generate Test Cases** - Recursive Language Model with extended thinking,
   requirement decomposition, and coverage verification produces ADO Test
   Cases with per-client template support. Iterative regeneration with user
   feedback (up to 10 iterations per session).
2. **Package PDFs** - bundles work items into per-WI PDFs, a combined PDF,
   and a KB-ready chunk folder.
3. **Custom Generate Chatbot** - Streaming AI chatbot for ad-hoc generation
   with multi-conversation support, file/image attachments, and artifact saving.

---

## Architecture

### VS Code-Style Navigation

The app uses a dual-state navigation pattern:

- **Expanded**: Full nav panel with Projects list, Boards list, and action
  buttons (Settings, KB, Collapse)
- **Collapsed**: Thin activity bar (44px) with center-aligned SVG icon
  buttons (folder, board, gear, expand chevron)

Toggle between states via the chevron button or keyboard shortcut. All buttons
auto-size to content - no hardcoded dimensions anywhere in the UI.

**Panel Visibility Preferences (persisted across restarts):**

- Navigation bar: hidden by default on first launch, preference key `nav_visible`
- Detail/Outputs pane: hidden by default, toggled via "Show Details" / "Hide
  Details" button in the action bar, preference key `detail_visible`
- Log panel: hidden by default, toggled via "Show Logs" / "Hide Logs" button,
  preference key `log_visible`

All three preferences persist to `ui_prefs.json` and restore on next launch.
Panel transitions are instant (no animations). Progress bar pulse is the only
animated element in the app.

### Three-Pane Layout

```
+---+----------+----------------------------------------------+-----------+
| A | PROJECTS |  Kanban Board (columns x iterations)         |  Detail   |
| c |  (list)  |  [cards with id, title, state, assignee]     |  (HTML)   |
| t +----------+                                              |           |
| i | BOARDS   |  [Generate TC] [Package PDFs]                |           |
| v |  (list)  |  [Upload to ADO]                            |           |
| i | [Gear]   |  log/progress panel (collapsible)            |           |
| t | [Chevron]|                                              |           |
| y |          |                                              |           |
+---+----------+----------------------------------------------+-----------+
| status bar: NW/AI/ADO dots, CPU %, RAM MB, GPU (if detected), Disk MB    |
+--------------------------------------------------------------------------+
```

### Package Structure

```
Testing Toolkit/
    install.cmd             # Windows native launcher (cmd.exe, no Git Bash)
    install.sh              # macOS/Linux launcher (clean + build + launch)
    python-embed/           # Portable Python (~27MB, created by make_portable.py)
    docs/
        DOCS.md             # This file (single source of truth)
    src/
        main.py             # Entry point (hardware init + bootstrap)
        build.py            # One-command OS-agnostic build pipeline
        doctor.py           # Environment preflight checks
        make_portable.py    # Downloads portable Python for offline deployment
        make_wheelhouse.py  # Wheel bundle builder for air-gapped installs
        requirements.txt    # Pinned dependencies
        clean_old_installs.py   # Remove old build artifacts safely
        fetch_models.py     # Offline model downloader
        .env                # LLM config (MODEL_SMALL, MODEL_MEDIUM, MODEL_LARGE, BASE_URL, API_KEY)
        .env.enc            # DPAPI-encrypted .env (shipped in frozen builds)
        assets/             # SVG icons (folder, board, gear, chevrons, ado)
        core/               # Configuration, logging, storage, TLS, API client, hardware, metrics
        ui/                 # PySide6 GUI: windows, dialogs, theming, animations, chatbot
        ado/                # Azure DevOps API: boards, extraction, upload
        kb/                 # Knowledge base: storage, indexing, retrieval, OCR (parallelized)
        testgen/            # Test case generation: RLM, templates, Excel I/O
        defects/            # Bulk defect parsing (internal, not yet exposed in UI)
        tools/              # PDF packaging, office conversion, Visio conversion
        wheelhouse/         # Pre-downloaded .whl files (created by make_wheelhouse.py)
        tests/              # E2E test suite + fixture generator
        build/              # PyInstaller build artifacts
        dist/               # Built distribution output
```

### Key Design Principles

- **Service-oriented architecture** - domain services (AdoService, KbService,
  LlmService) with RAM-first caching, event bus, and clean async contracts
- **Modular** - clean package separation with explicit boundaries between concerns
- **Dark-only** Material Design 3 + Apple HIG theme with purposeful animations
  (150ms fade transitions, skeleton loaders during async fetch)
- **Clean display** - underscores replaced with spaces in project/board names,
  leading/trailing whitespace trimmed, no trailing ellipsis on button labels
- **Responsive** - auto-collapses nav at < 800px, adjusts splitter at < 1200px,
  adapts layout proportions based on window size
- **First-launch UX** - all panels (nav, detail, log) hidden by default for a
  clean initial experience; user reveals panels as needed, preferences persist
- **Offline-first** - works behind air-gapped networks with bundled models
- **Zero-install portable** - bundled Python + pip + wheels; runs on bare machines
- **Blazing fast startup** - one-folder build, lazy imports, no splash screen
- **Hardware-aware** - auto-detects CPU cores, GPU (CUDA/DML/Metal), RAM; scales
  thread pools, batch sizes, and execution providers accordingly
- **Cross-platform** - Windows (x64/ARM64), macOS (Intel/Apple Silicon), Linux (x64)
- **LLM-provider neutral** - works with any API-compatible LLM provider
- **OS keyring** for credential storage (no plaintext secrets)
- **Combined TLS bundle** for corporate proxy compatibility (Zscaler)
- **Deterministic** - same inputs produce same outputs
- **Memory-agnostic** - streaming and chunked processing; works on 4GB RAM
- **Parallelized** - OCR, multimedia indexing, and document extraction use
  ThreadPoolExecutor with optimal_cpu_workers() for maximum throughput

### Dependency Flow (No Circular Imports)

```
core  <-  ado  <-  kb  <-  testgen  <-  tools
  ^         ^       ^         ^           ^
  |    +----+-------+---------+-----------+
  |    |         services/
  |    |    (AdoService, KbService, LlmService,
  |    |     CacheService, EventBus)
  |    +--------------------------------------------+
  +---------------- ui ----------------------------+
```

- core imports NOTHING from other internal packages
- services imports from core + ado + kb (facade layer with caching + events)
- ado imports from core only (uses `@azure-devops/mcp` via bundled node.exe)
- kb imports from core + tools (for office_convert)
- testgen imports from core + kb + ado
- tools imports from core only
- ui imports from services + core (prefers services for cached/event-driven access)

### MCP Integration

ADO tool calls use the MCP (Model Context Protocol) bridge:

- `@azure-devops/mcp@2.7.0` globally installed npm package
- App bundles `node.exe` (prepared during build step 4)
- ADO operations (boards, work items, upload) route through MCP protocol
- No direct REST calls to ADO - all interactions via MCP server

---

## Features

### 1. Test Case Generation (Recursive Language Model)

The RLM pipeline maximizes test case quality through multiple stages:

1. **Navigate** - fast model examines a chunk map and selects relevant
   chunks for the work items.
2. **Map** - each selected chunk is distilled to relevant facts.
3. **Decompose** - fast model enumerates atomic testable requirements as
   a numbered checklist (field validations, boundaries, state transitions,
   error conditions).
4. **Generate** - primary model with **Extended Thinking** (10k token
   reasoning budget) plans test strategy before producing JSON. The
   decomposed requirements are included as an explicit coverage target.
5. **Verify + Gap-Fill** - a second pass maps requirements to generated
   test cases, identifies uncovered acceptance criteria, and produces
   additional test cases for gaps. Always-on by default.

KBs up to ~375 pages (150k tokens) skip navigate/map and are passed
whole for 100% information coverage. Projects with no KB generate from
work items alone.

**Quality Features:**
- Extended Thinking (temperature=1.0, cached for determinism)
- Few-Shot Examples (3 gold-standard TCs in system prompt)
- Requirement Decomposition (atomic coverage checklist)
- Coverage Verification + Gap-Fill (always-on)
- Context Prioritization (longer=more relevant, sorted first)
- Query Decomposition (multi-sub-query retrieval for multi-criteria stories)
- Contextual Retrieval (LLM-generated situating prefixes per chunk, 49-67%
  fewer retrieval misses)

**Regeneration with Feedback:**
After generation, users can provide change instructions (paragraph input)
and the entire test set is regenerated incorporating the feedback. Up to
10 regeneration iterations per session per set of work items.

**Models used:**
- Primary: configurable (generation quality + extended thinking)
- Fast: configurable (retrieval navigation, decomposition, contextual retrieval)

### 2. Per-Client Template Support

Upload a client's Excel test-script template (one per testing phase:
Implementation / SIT / UAT). Templates are typically workbooks with
existing test data for another scenario - the app analyzes the
**structure, formatting, styling, sheet organization, merged cells,
column widths, and data patterns** to understand the layout.

**LLM-Assisted Analysis (automatic on upload):**

- The template's full structure is sent to the LLM for semantic analysis
- The LLM identifies the header row, column purposes, and row organization
- The result is saved as a deterministic spec - all subsequent renders use
  the cached spec with zero LLM calls
- Falls back to heuristic detection if no API key is configured

**Deterministic Rendering:**
Once analyzed, the spec drives a deterministic renderer that fills a *copy
of the original template* with generated test cases. Same inputs = same
outputs, always.

### 3. PDF Packaging

**Per-WI PDFs** contain:
- Cover page (title, metadata, description with inline images,
  acceptance criteria, comments)
- Attachments separator
- All attachments converted to PDF (Office, images, existing PDFs)

**Combined PDF** (`All_WIs_Combined.pdf`) merges all selected WI PDFs.

**KB Bundle** (`Upload to KB/` folder) splits the combined PDF into
AI knowledge base-ready chunks:
- Text chunks (each < 700 KB / ~175k tokens)
- Image-lookup PDFs (one image per page, labeled)
- index.json + README.md

### 4. Hybrid Retrieval

For KBs exceeding the 150k-token direct threshold, hybrid retrieval
selects the top 32 most relevant chunks (from a pool of 96 candidates):

- **BM25** lexical search (always active)
- **Dense vectors** (API: azure.text-embedding-3-small 512-dim)
- **Reciprocal Rank Fusion** combining both
- **LLM reranking** (GPT-4o-mini via API; replaces local ONNX cross-encoder)
- **Contextual Retrieval** (LLM-generated situating prefixes per chunk)
- **Query Decomposition** (heuristic sub-query extraction from acceptance
  criteria; multi-query retrieval unions results for better recall)

Falls back to BM25-only when no API key is configured.

### 5. Knowledge Base Management

Per-project KB supports 80+ file formats (all processing via API):

- **Text/Code** (40+ languages): .py .js .ts .jsx .tsx .java .cs .cpp .c
  .h .hpp .go .rb .php .swift .kt .scala .rs .lua .pl .r .m .f90 .vb
  .dart .zig .nim .ex .erl .hs .clj .lisp .ml .fs .groovy .coffee .v .sv
- **Config**: .yaml .yml .toml .ini .cfg .conf .properties .env
- **Docs**: .md .txt .rst .adoc .tex .bib .wiki .org .rtf
- **Data**: .json .jsonl .csv .tsv .xml .html .htm .geojson .graphql
  .proto .thrift .avsc
- **Web/Build/Shell**: .css .scss .sass .less .svg .sh .bat .ps1 .cmd
  .makefile .cmake .gradle
- **Office**: .docx .xlsx .pptx .pdf
- **Legacy**: .doc .ppt .xls .msg .odt .eml .epub (via olefile/antiword)
- **Scanned PDFs**: automatic OCR via GPT-4o vision API + PyMuPDF rasterizer
- **Images**: text extraction via GPT-4o vision API
- **Audio/Video**: transcription via Whisper API (`/audio/transcriptions`)
- **Archives**: .zip files extracted (up to 500 files indexed recursively)

**Hybrid Local + Programmatic + API Architecture (extreme inputs):**

The KB pipeline uses a three-tier approach to handle ANY file of ANY size:

1. **Local (ffmpeg subprocess)** - Audio/video demuxing, format conversion,
   time-based segmentation. Runs headless (no terminal windows; pythonw-safe).
   Timeouts scale with file size (300s base + 1s/MB).
2. **Programmatic (Python)** - Text extraction (pypdf, openpyxl read_only,
   python-docx, OLE2 parsing), chunking, BM25 indexing, archive extraction
   with security guards (zip-slip, decompression bomb, recursion cap).
3. **API (GenAI proxy)** - OCR (GPT-4o vision), transcription (Whisper),
   embeddings, reranking, contextual retrieval. Auto-converts unsupported
   audio formats to mp3 before API submission.

All tiers enforce:

- 50 MB text extraction cap per file (early break + gc.collect)
- 24 MB Whisper API limit (auto-segments into 10-min chunks)
- Path traversal protection on archives
- No terminal/console windows (headless subprocess kwargs on Windows)
- Temp file cleanup in finally blocks (no disk leaks)
- Graceful degradation: if API unavailable, extraction still yields
  local text; if ffmpeg missing, video/audio skipped with warning

Index rebuilds automatically when documents change. Resumable
background indexing with progress reporting.

### 6. Upload to ADO

Push reviewed test cases to Azure DevOps:

- Creates Test Cases as children of parent stories
- ADO-compliant Steps XML
- Re-reads the reviewed Excel (honors Skip=Yes edits)

### 7. Custom Generate Chatbot

A streaming AI chatbot for ad-hoc generation tasks:

- **Streaming responses**: SSE-based text streaming via `stream_message()` on
  a QThread (`StreamWorker`), non-blocking UI updates as tokens arrive
- **Multi-conversation**: Create, rename, delete conversations; sidebar list
- **File/image attachments**: Drag-drop files, paste images from clipboard
- **Artifact saving**: Code blocks saved to `outputs/artifacts/`
- **Auto-titling**: First message auto-titles the conversation (first 40 chars)
- **Stop generation**: Cancel in-flight streaming at any point

### 8. E2E Test Automation

Browser-based test execution via Playwright CDP attach to the user's real
browser (supports SSO):

- **Run Test button** enables when selecting work items (checkboxes or
  double-click) that have generated test cases in the sidecar JSON
- **Window**: Opens maximized with full window controls (maximize + close).
  Title shows `"Run Test - <WI ID> - <Title>"` for single WI, or
  `"Run Test - N Work Items"` for bulk selection
- **Bulk mode** (multiple WIs): Left panel lists work items with aggregate
  pass/fail status. Clicking a WI filters the TC list. "All Work Items"
  entry shows everything
- **Per-TC status**: Color + badge prefix (`[PASS]`, `[FAIL]`, `[SKIP]`)
  updated in real-time via `on_tc_done` callback from the runner
- **Per-WI aggregate**: After each TC finishes, the parent WI's label in
  the left panel updates with `(X/Y pass, Z fail)` and changes color
- **Execution history**: Results persisted to execution store. Last run
  summary shown at top of dialog (passed/failed/skipped counts + timestamp)
- **Re-run Failed**: Selects only previously-failed TCs and triggers a run
- **Credential vault**: Per-project/per-environment encrypted credentials
  with AI instructions for login steps
- **Artifacts**: Screenshots (annotated), video (stream-copied during run),
  and rerunnable Playwright scripts saved per TC

### 9. Per-Process Resource Metrics

Real-time metrics for this application process only (not system-wide):

- **CPU**: Process CPU usage percentage normalized by logical core count to match
  Task Manager display (Windows: `GetProcessTimes` via ctypes; Unix: `os.times()`)
- **RAM**: RSS in MB (Windows: `K32GetProcessMemoryInfo`; macOS/Linux: `resource`)
- **GPU**: Detected GPU device name if GPU acceleration is active
- **Disk**: Total size of `outputs/` AND `projects/` directories. Rescanned every 30s.
- **Status bar**: Updated every 2s, format:
  `CPU: X% | RAM: Y MB | GPU: Z (if detected) | Data: W MB`
- **Connection indicators**: NW (green/yellow/red), AI (green/white), ADO (green/white)

### 10. LLM Guardrails

Two-layer defense against off-topic use:

- **Layer 1**: Input pre-filter with frozenset keyword matching + regex patterns
  (blocks before API call, saves tokens)
- **Layer 2**: System prompt suffix appended to ALL LLM calls
- Refusal message: single source of truth in `guardrails.REFUSAL_MESSAGE`

---

## Embeddings, Indexing and Search

### The Stack

| Component | Model / Tech | Format | Library |
| --------- | ------------ | ------ | ------- |
| Embeddings | azure.text-embedding-3-small | 512-dim float32 | httpx (GenAI proxy) |
| Reranker | azure.gpt-4o-mini (LLM rerank) | JSON prompt | httpx (GenAI proxy) |
| OCR | azure.gpt-4o (vision API) | PNG -> text | httpx (GenAI proxy) |
| Transcription | Whisper API | audio -> text | httpx (GenAI proxy) |
| Lexical Search | Okapi BM25 (k1=1.5, b=0.75) | NumPy arrays | Pure NumPy |
| Vector Store | NumPy (vectors.npy) | Memory-mapped | NumPy |

All processing via API. Local storage only (vectors.npy, bm25.json, manifest).

### Embedding Model

- **API**: `azure.text-embedding-3-small` - 512-dimensional via GenAI proxy
  `/embeddings` endpoint. L2-normalized float32. Batched in groups of 64,
  retries on 429/5xx.
- Input truncated to 30,000 characters (safety margin for 8191 token limit)
- No local fallback needed; BM25-only mode if no API key configured.

### Reranker (LLM-based)

- **azure.gpt-4o-mini** via completions API (structured JSON prompt)
- Candidates presented as numbered passages; LLM returns ordered indices
- Applied after RRF fusion to pick the final 32 best chunks
- Temperature 0.0 for deterministic ranking

### BM25 (Lexical Search)

- Classic Okapi BM25 scoring
- Tokenization: lowercase alphanumeric, 44 English stop words
- Persisted as `bm25.json` - instant reload, no rebuild needed
- Excellent for exact identifiers, field names, error codes

### Indexing Pipeline

```
Documents (80+ formats, archives, multimedia)
  -> Extract text (format-specific extractors)
     - Scanned PDFs: rasterize (PyMuPDF) -> GPT-4o vision API
     - Audio/Video: Whisper API transcription
     - Images: GPT-4o vision API text extraction
     - Archives (.zip): extract up to 500 files, index each recursively
     - Text files >50MB: read first 50MB only
  -> Coarse chunks (~6,000 tokens, split on headings/paragraphs)
  -> Dense chunks (~550 tokens, no overlap)
  -> Contextualize: GPT-4o-mini generates situating prefix per chunk
  -> BM25 index (always built)
  -> API embeddings (512-dim float32, input capped at 30K chars)
  -> Persist to disk (kb_index.json + bm25.json + vectors.npy)
```

Stable deterministic IDs: `d003c0007#2` = document 3, coarse chunk 7, dense sub-chunk 2.
Index rebuilds only when files change (mtime/size check).

### Search Pipeline (at query time)

```
Query (work item dump text)
  |
  +- BM25 -> top 96 lexical candidates (ranked by term overlap)
  |
  +- Dense search -> top 96 semantic candidates (cosine similarity)
  |
  +- RRF Fusion: score = sum(1/(60 + rank)), merge both lists
  |
  +- LLM rerank (GPT-4o-mini): rescore top 96 -> pick best 32
  |
  +- Deduplicate (signature on first 240 chars) -> final 32 chunks
```

**Graceful degradation:**

- No API key -> BM25-only (still effective for exact terms)
- No reranker response -> RRF-fused order (still combines lexical + semantic)
- No vector store -> full fallback to RLM recursive navigation (LLM-guided)

### Performance

| Operation | Time |
|-----------|------|
| Embed 1 query (API, 512-dim) | ~200ms (network) |
| BM25 search (1000 chunks) | ~2ms |
| Dense vector search (1000 chunks) | ~5ms |
| LLM rerank 96 candidates | ~1-2s (network) |
| **Total retrieval** | **~2-3s** |

---

## Installation and Deployment

### Option 1: Portable Zero-Install (Recommended for restricted machines)

For machines that cannot install Python, cannot reach PyPI, or are behind
Zscaler/air-gap. Two machines involved:

- **Machine A - connected** (your work box; reaches PyPI through Zscaler)
- **Machine B - restricted** (the target laptop; NO Python, NO pip, NO network)

#### What is bundled

| Item | How it travels | Where |
|------|----------------|-------|
| Python interpreter | Portable distribution (no installer) | `python-embed/` (~27 MB) |
| pip + setuptools | Offline wheels + get-pip.py | `python-embed/` + `src/wheelhouse/` |
| All Python dependencies | `.whl` files you build once | `src/wheelhouse/` |

#### Step 1: Prepare portable Python (Machine A)

```bash
cd src
python make_portable.py                         # auto-detect this platform
python make_portable.py --platform win_amd64    # cross-build for Windows x64
python make_portable.py --platform win_arm64    # cross-build for Windows ARM
```

#### Step 2: Build the wheelhouse (Machine A)

```bash
cd src
python make_wheelhouse.py
python make_wheelhouse.py --plat win_amd64 --pyver 3.12  # cross-build
```

#### Step 3: Transfer and run (Machine B)

```bash
# Zip entire project folder, transfer via USB/share/network
# On target machine:
# Windows: double-click install.cmd
# macOS/Linux: ./install.sh
```

Every run: cleans, installs packages, builds .exe, launches (~2-3 min).

#### What install.cmd Does (Windows, native cmd.exe)

1. Patches `python312._pth` (ensures pip + app imports work)
2. Cleans old `build/`, `dist/`, `__pycache__/`
3. Installs all packages from `src/wheelhouse/` (offline)
4. Installs PyInstaller from wheelhouse
5. Runs `build.py --quiet` (progress bar only)
6. Launches built `src/dist/TestingToolkit/TestingToolkit.exe`

#### Supported platforms

| Platform | Python Source | Architecture |
|----------|--------------|--------------|
| `win_amd64` | python.org embeddable zip | Intel/AMD x64 |
| `win_arm64` | python.org embeddable zip | Snapdragon ARM64 |
| `macosx_11_0_arm64` | python-build-standalone | Apple Silicon (M1-M4) |
| `macosx_10_9_x86_64` | python-build-standalone | Intel Mac |
| `linux_x86_64` | python-build-standalone | Linux x64 |

### Option 2: From Source (connected machine with Python 3.10+)

```bash
cd src
python build.py          # fully automated one-folder build
```

### Option 3: Wheelhouse-Only (Python pre-installed, no network)

```bash
# Machine A:
cd src && python make_wheelhouse.py

# Machine B (Python 3.10+ already installed):
cd src
python -m venv .venv
.venv\Scripts\activate
python -m pip install --no-index --find-links wheelhouse -r requirements.txt
python main.py
```

### Build Options

```bash
cd src
python build.py              # default: cleans + builds one-folder
python build.py --onefile    # single exe (slower startup)
python build.py --console    # keep console for debugging
python build.py --quiet      # progress bar + errors only (used by launchers)
```

### Dense Model Cache (Optional)

```bash
cd src
python fetch_models.py   # downloads embedding + reranker models
```

Models are bundled into the build automatically if present in `models/`.

---

## Hardware Utilization

The app auto-detects and uses all available hardware:

| Resource | Detection | Usage |
|----------|-----------|-------|
| CPU cores (physical) | `os.sched_getaffinity` / `psutil` / ARM heuristic | CPU-bound workers (embeddings, OCR) |
| CPU cores (logical) | `os.cpu_count()` | I/O-bound workers (HTTP, file ops) |
| Architecture | `platform.machine()` | ARM vs x86 detection |
| Apple Silicon | `sysctl machdep.cpu.brand_string` | M1/M2/M3/M4 identification |
| GPU (CUDA) | `torch.cuda` / onnxruntime providers | float16 inference |
| GPU (DirectML) | onnxruntime DML provider | Windows GPU fallback |
| GPU (Metal/CoreML) | Apple MPS / CoreML detection | macOS GPU acceleration |
| System RAM | `psutil` / `sysctl hw.memsize` / `/proc/meminfo` | Embedding batch sizing (16/32/64) |

All detection is fail-safe with conservative fallbacks. Missing GPU = CPU mode
with identical features (just slower). Set at startup via `core/hardware.py`.

**ONNX Provider Priority:** CUDA > CoreML (macOS) > DirectML (Windows) > CPU

Environment variables auto-set: `OMP_NUM_THREADS`, `MKL_NUM_THREADS`,
`OPENBLAS_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`, `NUMEXPR_NUM_THREADS`,
`ORT_NUM_THREADS`, `ACCELERATE_NUM_THREADS` (Apple Silicon).

---

## OCR and Indexing Parallelization

### Thread-Local OCR Engine Instances

RapidOCR's internal ONNX session may not be thread-safe. Each worker thread
gets its own engine instance via `threading.local()`.

### OCR Parallelization Strategy

1. **Rasterize sequentially**: `fitz.Document` (PyMuPDF) is NOT thread-safe.
2. **OCR in parallel**: Rasterized images submitted to ThreadPoolExecutor.
3. **Batch processing**: Pages processed in memory-aware batches (32/64/128).
4. **Ordered reassembly**: Results sorted by page index before joining.

### Memory-Aware Batch Sizing

| System RAM | OCR Batch Size | Extraction Batch |
|-----------|---------------|-----------------|
| <= 4 GB | 32 pages | 4 files |
| <= 8 GB | 64 pages | 8 files |
| > 8 GB | 128 pages | 16 files |

### File-Level Extraction

- Document files extracted in parallel via ThreadPoolExecutor
- Multimedia files processed sequentially (internal parallelism)
- `gc.collect()` called after each batch to release memory

---

## UI Implementation Details

### Panel State Persistence

| Panel | Preference Key | Default | Storage |
|-------|---------------|---------|---------|
| Navigation bar | `nav_visible` | Hidden | `ui_prefs.json` |
| Detail/Outputs | `detail_visible` | Hidden | `ui_prefs.json` |
| Log panel | `log_visible` | Hidden | `ui_prefs.json` |

### UI Transitions

- Panel show/hide: instant `setVisible()` (no animation)
- Splitter transitions: instant `setSizes()` (no animation)
- Dialog show: `AnimatedDialog` base class (auto-fit only, no fade)
- Only animation: `pulse_progress` on progress bars (opacity pulse)

### Responsive Design

| Window Width | Behavior |
|-------------|----------|
| < 800px | Nav panel auto-collapses, preference updated |
| < 1200px | Splitter proportions adjusted (70/30 ratio) |
| >= 1200px | Full layout with all proportional sizing |

### Display Formatting (UI only)

- Underscores replaced with spaces in project/board names
- Leading/trailing whitespace trimmed from all display strings (via `ui_display()`
  and `display_project_name()` central functions + card/tree rendering)
- No trailing ellipsis ("...") on button or label text
- Project prefix stripped from board labels (e.g., "Abbott 2026 Enhancements"
  becomes "2026 Enhancements" when project "Abbott" is selected)

### Detail Pane Auto-Show on Double-Click

- Double-clicking a work item (card or tree row) auto-opens the Detail pane
  if currently hidden, and loads the work item's details
- Clicking elsewhere (lane header, empty space, non-item) auto-hides the
  Detail pane if it was auto-opened by double-click
- Manual toggle via "Show/Hide Details" button clears the auto-open flag;
  manually opened panes are never auto-hidden
- Flag tracked via `_detail_auto_opened` instance attribute

### Project/Board Navigation (v2.2.0)

- Projects listed by their plain name (no source suffix)
- Board name stripping: project display name removed from board labels
- Sort order: API return order (ADO returns by lastUpdateTime descending)
- Work item header: `"<board> Work Items"` format

### Streaming ADO Fetch Architecture (v2.3.0)

Event-driven, progressive-loading architecture for all ADO data:

- **StreamingAsyncWorker**: QThread-based worker with a `batch_ready` Signal.
  The async coroutine receives a thread-safe `on_batch` callback; each batch
  emits immediately to the GUI thread via Qt signal/slot (no polling).
- **Projects**: Pages of up to 500 fetched via continuation tokens; each page
  renders in the left-rail list instantly. No waiting for all pages.
- **Work Items**: Fetched in 200-ID batches from `workitemsbatch` endpoint.
  Each batch emits a `batch_ready` signal; the UI shows a live counter
  ("Loading... 400 work items received"). Final structured grid/kanban renders
  once all batches arrive.
- **Memory-efficient encoding**: `sys.intern()` on low-cardinality fields
  (wi_type, state, board_column, area_path, iteration_path) deduplicates
  string allocations across 2000+ items. `@dataclass(slots=True)` on all ADO
  data classes eliminates per-instance `__dict__`.
- **Stale-result prevention**: Monotonic sequence counters (`_view_seq`,
  `_boards_seq`) discard late responses from superseded requests.
- **Concurrency**: `asyncio.Semaphore(6)` limits parallel team-board fetches;
  retry with exponential backoff on 429/503.

---

## Workspace Layout

```
~/TestingToolkit/
    settings.json              # base URL, model, org, prefix, TLS
    ui_prefs.json              # theme, window geometry, splitter state
    projects/
        <project>/
            system_prompt.txt  # custom RLM prompt
            kb/                # requirement documents
            kb_index.json      # cached chunk index
            templates/         # client Excel templates + specs
            generated/         # output payloads + review xlsx
    outputs/
        <project>/
            packets/           # PDF packaging output
                WI_123.pdf
                All_WIs_Combined.pdf
                Upload to KB/
                manifest.json
            testcases/         # test case review xlsx
        artifacts/             # chatbot artifact saves
        chat_history.json      # chatbot conversations
    logs/                      # rotating debug logs
```

---

## Runtime Dependencies

### Required (auto-installed by build.py)

| Package | Purpose |
|---------|---------|
| PySide6 | GUI framework |
| httpx | ADO + LLM API HTTP |
| certifi | TLS root certificates |
| truststore | OS trust store (Zscaler) |
| keyring | Secure credential storage |
| openpyxl | Excel read/write |
| selectolax | HTML parsing |
| pypdf | PDF text extraction |
| reportlab | PDF generation |
| Pillow | Image handling |
| python-docx | Word documents |
| python-pptx | PowerPoint documents |
| xlrd | Legacy .xls files |
| striprtf | RTF documents |
| numpy | BM25 + vector math |

### Feature Set (auto-installed by build.py)

| Package | Purpose |
|---------|---------|
| fastembed | Dense ONNX embeddings + reranker |
| onnxruntime | CPU inference backend |
| rapidocr-onnxruntime | OCR for scanned PDFs |
| PyMuPDF | PDF rasterizer for OCR pipeline |
| olefile | Legacy .doc/.ppt/.msg extraction |

### Optional (not bundled by default)

| Package | Purpose |
|---------|---------|
| faster-whisper | Audio/video transcription |
| pytesseract | Alternative image OCR |

---

## Security

- ADO PAT in OS keyring (Windows Credential Manager / macOS Keychain /
  Secret Service); encrypted file fallback for restricted environments
- Combined CA trust bundle for TLS-intercepting proxies
- No plaintext secrets on disk
- All API calls at temperature=0 for repeatability
- LLM guardrails: input pre-filter + system prompt suffix

### DPAPI-Encrypted .env

- `src/.env` contains LLM config: MODEL_SMALL, MODEL_MEDIUM, MODEL_LARGE,
  BASE_URL, API_KEY (service account key)
- At build time, `.env` is encrypted with Windows DPAPI -> `.env.enc`
- Frozen builds ship `.env.enc` only (plaintext `.env` never distributed)
- Runtime decrypts via `CryptUnprotectData` (CurrentUser scope)
- Dev mode falls back to plaintext `.env` if `.env.enc` is absent

---

## First-Run Setup

On first launch a wizard collects:

- **Azure DevOps**: PAT, organization

No LLM section (ships with service account key in encrypted `.env`).
Everything is editable later from Settings (gear icon in activity bar or
nav panel).

---

## Testing

```bash
cd src
python tests/generate_test_data.py   # generate 50+ fixture files (run once)
python tests/test_full_e2e.py        # run 57-check comprehensive E2E suite
python tests/test_refactor_v2_2.py   # v2.2.0 refactor-specific tests
```

The test suite covers:

- Hardware detection and thread environment (including ARM/Apple Silicon)
- Core modules (runtime config, LLM aliases, settings store)
- KB indexing (all file types: txt, md, csv, json, html, docx, xlsx, rtf)
- Testgen (payload parsing, validation, normalization, Excel round-trip)
- Defects (review Excel round-trip, uploader imports)
- ADO (auth headers, dataclasses, extract functions, MCP integration)
- Tools (office conversion, PDF packaging)
- UI (theme, main window, settings dialog, board grid, artifacts browser)
- Branding (no provider-specific labels in UI)
- Settings scenarios (env var overrides)
- Display formatting (underscore replacement, trimming, prefix stripping)

---

## Preflight Verification

Run `cd src && python doctor.py` to verify:

1. Python version (>= 3.10)
2. Hardware resources (CPU cores, RAM, GPU)
3. All required packages present
4. Feature packages active/inactive
5. OCR pipeline end-to-end
6. Multimedia backends
7. Offline model cache

---

## Architecture Rules

1. **Streaming-first / Memory-agnostic**: All data processing must work on 4GB
   RAM. Use Polars Lazy API or chunked processing.

2. **Hardware detection is fail-safe**: Every detection in `core/hardware.py`
   is wrapped in try/except with conservative fallbacks.

3. **Same features with or without GPU**: GPU provides speed, not capability.

4. **OS agnostic**: Code must run on Windows, macOS, and Linux. Use `pathlib`.

5. **No new dependencies for what a few lines can do**: The standing stack is
   PySide6, httpx, openpyxl, pypdf, reportlab, Pillow, python-docx, python-pptx,
   numpy, fastembed, onnxruntime.

---

## Branding Rules

6. **No Anthropic/Claude proper nouns in user-facing UI**: All labels, tooltips,
   error messages must say "LLM" or "AI". Model identifier strings acceptable.

7. **Backwards-compatible aliases**: Internal code keeps old names but also
   exports generic: `LLMClient`, `build_llm_client`, `DEFAULT_LLM_BASE_URL`.

8. **Settings keys stay stable**: Internal persistence keys like
   `anthropic_base_url` must NOT be renamed (breaks existing configs).

---

## Quality Rules

9. **Type hints on every function**: Arguments and returns. No exceptions.

10. **ASCII only**: No Unicode/emojis in code, comments, logs, or UI text.

11. **INPUT_PATH / OUTPUT_PATH at top**: Any script that reads/writes files
    declares paths at the absolute top.

12. **del + gc.collect() after heavy operations**: After processing large
    DataFrames, file loops, or model inference batches.

13. **Error handling at trust boundaries**: Input validation for user input,
    external API responses, and file I/O. Internal code trusts internal code.

---

## Known Constraints and Edge Cases

14. **os.getlogin() can raise OSError**: Always wrap with fallback.

15. **OCR init can fail permanently**: Once `_ocr_init_failed` is set True,
    don't retry.

16. **LanceDB cosine distance is [0, 2] not [0, 1]**: Similarity formula
    must be `max(0.0, 1.0 - dist)`.

17. **Window geometry from settings can be non-numeric**: Always wrap in
    try/except.

18. **File can disappear between glob and stat**: Always catch OSError.

19. **Embedding batch size is memory-aware**: 16 (<=4GB), 32 (<=8GB), 64 (>8GB).

20. **Whisper model loading**: Use `device="cuda", compute_type="float16"`
    when GPU available, else `device="cpu", compute_type="int8"`.

21. **Frame dedup in multimedia**: Single-word frames must not be dropped.

22. **python312._pth controls embedded Python's sys.path**: All five lines
    required: `python312.zip`, `.`, `..\src`, `Lib\site-packages`, `import site`.

23. **Qt/PySide6 cannot run in Git Bash mintty**: SEGFAULT. Launcher MUST be
    native cmd.exe on Windows.

24. **Wheelhouse Python version must match python-embed**: cp312 wheels only
    work in Python 3.12.

---

## Build Rules

25. **Single command build**: `python build.py` is the ONLY command needed.

26. **Build never fails on optional backends**: Missing fastembed, onnxruntime,
    rapidocr, or PyMuPDF = features inactive at runtime.

27. **Preflight auto-resolves**: Missing packages auto-installed on retry.

---

## Deployment Rules (Portable)

28. **install.cmd is native cmd.exe ONLY**: No Git Bash, no mintty.

29. **python312._pth must be PATCHED, never deleted**: Correct content exactly:
    `python312.zip`, `.`, `..\src`, `Lib\site-packages`, `import site`.

30. **No sentinel / skip logic in install scripts**: Every run = full clean build.

31. **Wheels must match Python version exactly**: No cross-version.

32. **PyInstaller must be in the wheelhouse**: Build-time dependency.

33. **build.py --quiet for launcher use**: Only progress bar and errors shown.

34. **Console must stay visible on error**: Use `pause` after failures.

35. **pythonw.exe hides ALL errors**: Never use for debugging.

---

## Portable Deployment Constraints (Hard-Won)

### Python Embeddable Distribution

| Constraint | Reason |
|------------|--------|
| `python312._pth` must be PATCHED, never deleted | Deleting breaks pip |
| `._pth` must contain `..\src` entry | Puts `src/` on sys.path |
| `._pth` must contain `import site` | Required for pip/site-packages |
| `._pth` must contain `Lib\site-packages` | Without it, packages invisible |

### PySide6 / Qt on Windows

| Constraint | Reason |
|------------|--------|
| NEVER run Qt apps in Git Bash (mintty) | SEGFAULT (0xC0000005) |
| `install.cmd` must be native cmd.exe only | Git Bash breaks Qt |
| `pythonw.exe` hides ALL errors | Use `python.exe` for debugging |
| Exit code `0xC0000409` | Qt STATUS_STACK_BUFFER_OVERRUN |
| Exit code `0xC0000005` | Access violation - Qt in mintty |

### Wheelhouse and Offline Install

| Constraint | Reason |
|------------|--------|
| Wheels are Python-version-specific | cp312 != cp313 |
| `--no-index --find-links wheelhouse` required | Prevents network access |
| `--force-reinstall` ensures clean state | Skips "already installed" |
| PyInstaller must be in wheelhouse | Build-time dep |
| `get-pip.py` must be in `python-embed/` | Bootstrap before pip exists |

### Build Pipeline

| Constraint | Reason |
|------------|--------|
| No sentinel / skip logic | Guaranteed clean install every run |
| Always clean build/dist/__pycache__ first | Stale bytecode = import errors |
| Build bundles models/ and assets/ | Offline dense retrieval |
| Console stays open on error (pause) | User can read error |
| `start "" "%EXE%"` for final launch | Detaches from console |

---

## Test Requirements

36. **E2E test suite must pass**: Run `python tests/test_full_e2e.py` before
    any release. All 57+ checks must be green.

37. **Compile check**: `python -m py_compile` on every .py file. Zero failures.

38. **No import errors**: Every module must import cleanly.

---

## Architecture Decisions

### Why Thread-Local OCR Engines

RapidOCR wraps ONNX Runtime sessions that may hold internal state. Thread-local
instances eliminate all sharing with minimal overhead (cores * 32MB).

### Why Streaming Over Polling for Chatbot

SSE streaming provides immediate feedback (first token ~200ms), accurate progress,
efficient resource use (single HTTP connection), and cancel capability.

### Why Keyring + File Fallback for Secrets

- Keyring first: OS credential manager (most secure, no file on disk)
- File fallback: headless/restricted environments where keyring is unavailable

### Model Router (Multi-Provider Request Routing)

Multi-provider routing via GenAI LiteLLM proxy. Not restricted to a single
provider - the best model is selected per task for peak quality and cost
optimization. Task-specific overrides take priority over tier fallback.

| Task | Model | Provider | Cost/M (in/out) | Rationale |
|------|-------|----------|-----------------|-----------|
| TC Generation | claude-opus-4-6 | Anthropic | $15/$75 | Deep reasoning determines output quality |
| Coverage Verify | claude-opus-4-6 | Anthropic | $15/$75 | Correctness-critical verification |
| Template Analysis | claude-opus-4-6 | Anthropic | $15/$75 | Complex structural understanding |
| Decomposition | claude-opus-4-6 | Anthropic | $15/$75 | Decomp quality gates TC quality |
| Chat Streaming | claude-sonnet-4-6 | Anthropic | $3/$15 | Fast interactive UX |
| Navigation | claude-sonnet-4-6 | Anthropic | $3/$15 | Speed+quality balance |
| Extraction | gpt-4o | OpenAI/Azure | $2.50/$10 | Strong structured JSON output |
| Defect Parsing | gpt-4o | OpenAI/Azure | $2.50/$10 | Reliable field extraction |
| Contextualize | gpt-4o-mini | OpenAI/Azure | $0.15/$0.60 | Trivial, 100x cheaper |
| Reranking | gpt-4o-mini | OpenAI/Azure | $0.15/$0.60 | Binary relevance judgment |
| OCR / Document | gpt-4o | OpenAI/Azure | $2.50/$10 | 128K context, vision-capable for large docs |

**Implementation**: `core/model_router.py` defines `Tier` and `Task` enums.
Each call site declares its task intent via `route(Task.X)` and the router
returns the optimal model ID. Task-specific overrides from `.env` take
priority; tier fallback is the safety net.

**Configuration**: `.env` supports both tier models (`MODEL_SMALL`,
`MODEL_MEDIUM`, `MODEL_LARGE`) and task-specific overrides (`MODEL_RERANK`,
`MODEL_EXTRACT`, `MODEL_GENERATE`, `MODEL_CHAT`, `MODEL_CONTEXTUALIZE`,
`MODEL_OCR`).

**Cost optimization**: GPT-4o-mini handles high-concurrency trivial tasks
at ~1/500th the cost of Opus. GPT-4o handles extraction at ~1/7.5th cost.
Only complex generation and verification use Opus.

### Network Status Indicator (NW)

Real-time network health in the status bar:

| State | Color | Meaning |
|-------|-------|---------|
| Online | Green | API call succeeded in last 30s |
| Idle | Yellow | No recent API activity |
| Offline | Red | Last API call failed |

Reports from both LLM client (`anthropic_client.py`) and embedding API
(`embeddings.py`). Polled every 2-5s by the metrics timer.

### KB Encryption at Rest

All KB source data is encrypted on disk using Windows DPAPI (CurrentUser
scope) with a machine-derived-key XOR fallback for portability:

| Data | File | Encrypted |
|------|------|-----------|
| Chunk text | chunks.jsonl | Yes (KBEV magic) |
| Dense vectors | vectors.npy | Yes (KBEV magic) |
| Vector IDs | vector_ids.json | Yes (KBEV magic) |
| BM25 index | bm25.json | Yes (KBEV magic) |
| Manifest | manifest.json | Yes (KBEV magic) |
| KB index cache | kb_index.json | Yes (KBEV magic) |
| Indexer checkpoint | partial | Yes (KBEV magic) |

Generated artifacts (test cases, reports, PDFs) remain plaintext as they
are user-facing output. Backward compatible: reads both encrypted (KBEV
header) and legacy plaintext files transparently.

---

## Residual Risks (Low Severity, Accepted)

1. **Thread-local OCR memory**: Each engine ~32MB. On 16-core = ~512MB.
   ponytail: cap max_workers at RAM/128MB if memory pressure detected.

2. **Stream cancellation is cooperative**: Flag checked between chunks.
   Very large single chunk delays cancellation.

3. **Hardcoded models may need update**: `_SEED_MODELS` requires code change
   for new model versions.
   ponytail: move to settings.json list if model churn becomes frequent.

4. **Guardrail pre-filter is conservative**: Sophisticated jailbreaks that
   avoid all patterns reach the model - system prompt guardrail is backstop.

---

## Offline Troubleshooting

- **install.cmd says "No Python found"** - `python-embed/` missing. Run
  `make_portable.py` on Machine A.

- **"get-pip.py not found"** - `make_portable.py` did not complete. Re-run.

- **"OCR engine MISSING"** - wheelhouse missing OCR wheels. Reinstall:
  `pip install --no-index --find-links wheelhouse rapidocr-onnxruntime PyMuPDF`

- **"no matching distribution"** - wheelhouse built for different OS/Python.
  Rebuild with `--plat`/`--pyver` matching Machine B.

- **"models/ cache not found"** - dense retrieval falls back to BM25.
  Run `fetch_models.py` on Machine A to enable dense offline.

- **ADO/LLM fetch fails at runtime** - network issue. Confirm endpoints
  allowlisted on Machine B.

---

## Enhancement Tracking

| ID | Description | Status | Added |
|----|-------------|--------|-------|
| E-001 | Full hardware utilization (CPU/GPU/NUMA) | Done | 2026-06-24 |
| E-002 | Remove splashscreen | Done | 2026-06-24 |
| E-003 | Generic LLM branding | Done | 2026-06-24 |
| E-004 | OS-agnostic builder | Done | 2026-06-24 |
| E-005 | Memory-aware embed batch sizing | Done | 2026-06-24 |
| E-006 | Comprehensive E2E test suite | Done | 2026-06-24 |
| E-007 | ARM/Apple Silicon (M-series) support | Done | 2026-06-24 |
| E-008 | Sprint filter uses System.BoardLane | Done | 2026-06-24 |
| E-009 | Nav panel icon buttons (Settings/KB/Hide) | Done | 2026-06-24 |
| E-010 | Hide button in action bar for logs | Done | 2026-06-24 |
| E-011 | Portable zero-install deployment | Done | 2026-06-24 |
| E-012 | VS Code-style collapsible activity bar | Done | 2026-06-24 |
| E-013 | Settings dialog auto-fit | Done | 2026-06-24 |
| E-014 | Cross-platform install.sh + install.cmd | Done | 2026-06-24 |
| E-015 | install.cmd builds .exe via PyInstaller | Done | 2026-06-24 |
| E-016 | build.py --quiet mode | Done | 2026-06-24 |
| E-017 | Always-clean-install (no sentinel) | Done | 2026-06-24 |
| E-018 | External tracker integration (removed in E-032) | Done | 2026-06-28 |
| E-019 | Custom Generate chatbot | Done | 2026-06-28 |
| E-020 | Parallelized OCR/indexing | Done | 2026-06-28 |
| E-021 | Project/Board navigation redesign | Done | 2026-07-04 |
| E-022 | Remove all animations (except progress pulse) | Done | 2026-07-04 |
| E-023 | Display formatting (underscore/trim/ellipsis) | Done | 2026-07-04 |
| E-024 | Double-click work item auto-opens Detail pane | Done | 2026-07-04 |
| E-025 | Custom Generate button blue/primary (matches Package PDFs) | Done | 2026-07-04 |
| E-026 | CPU metric normalized by core count (matches Task Manager) | Done | 2026-07-04 |
| E-027 | Request router: automatic model selection by task complexity | Done | 2026-07-05 |
| E-028 | Remove model selection from Settings (router owns routing) | Done | 2026-07-05 |
| E-029 | Custom user instructions for first-generation (not just regen) | Done | 2026-07-06 |
| E-030 | SIT/UAT auto-select all User Stories when no items ticked | Done | 2026-07-06 |
| E-031 | Implementation button greyed out until work items selected | Done | 2026-07-06 |
| E-032 | Remove external tracker integration (ADO-only) | Done | 2026-07-06 |
| E-033 | DPAPI-encrypted .env for service account shipping | Done | 2026-07-06 |
| E-034 | Remove project source suffix (plain names) | Done | 2026-07-06 |
| E-035 | Simplified settings (ADO PAT + Org only) | Done | 2026-07-06 |
| E-036 | Deferred ADO imports (lazy httpx/aiohttp for fast first paint) | Done | 2026-07-06 |
| E-037 | Adaptive metrics timer (5s/3s/2s by RAM tier) | Done | 2026-07-06 |
| E-038 | Disk scan on daemon thread (60s interval, never blocks UI) | Done | 2026-07-06 |
| E-039 | gc.collect() after board clear/load (free widget trees) | Done | 2026-07-06 |
| E-040 | Adaptive embedding batch size (16/32/64 by RAM) | Done | 2026-07-06 |
| E-041 | API-based embeddings (azure.text-embedding-3-small, 512-dim) | Done | 2026-07-06 |
| E-042 | Network status indicator (NW: green/yellow/red) | Done | 2026-07-06 |
| E-043 | Multi-provider model router (Opus + Sonnet + GPT-4o + GPT-4o-mini) | Done | 2026-07-06 |
| E-044 | KB encryption at rest (DPAPI + machine-key fallback) | Done | 2026-07-06 |
| E-045 | Task-specific model overrides in .env | Done | 2026-07-06 |
| E-046 | Deep project context understanding on KB upload | Done | 2026-07-07 |
| E-047 | Traceability matrix (coverage % in board grid + JSON sidecar) | Done | 2026-07-07 |
| E-048 | Quality scoring (rule-based TC quality grading post-generation) | Done | 2026-07-07 |
| E-049 | Test data enrichment (pattern-based test data suggestions) | Done | 2026-07-07 |
| E-050 | Bulk regeneration ("Regenerate All" action in main window) | Done | 2026-07-07 |
| E-051 | E2E execution history + "Re-run Failed" button | Done | 2026-07-07 |
| E-052 | Diff engine for comparing old vs new generated payloads | Done | 2026-07-07 |
| E-053 | Credential management dialog (secure vault per project) | Done | 2026-07-07 |
| E-054 | Master prompt (prompt.md) integration for all phases | Done | 2026-07-07 |
| E-055 | JSON sidecar alongside Excel output for E2E runner | Done | 2026-07-07 |
| E-056 | Board grid: Coverage + Execution Status columns | Done | 2026-07-07 |
| E-057 | ADO color icon in project list nav rail | Done | 2026-07-07 |
| E-058 | Run Test enables on WI selection (checkbox or double-click) | Done | 2026-07-07 |
| E-059 | E2E dialog: maximize window, WI ID + title in header | Done | 2026-07-07 |
| E-060 | Bulk E2E mode: left WI panel with aggregate status, per-TC status | Done | 2026-07-07 |

---

## Bug Tracking

| ID | Description | Severity | Status | Fixed |
|----|-------------|----------|--------|-------|
| B-001 | LanceDB cosine distance [0,2] not [0,1] | Critical | Fixed | 2026-06-24 |
| B-002 | os.getlogin() crash on headless | Critical | Fixed | 2026-06-24 |
| B-003 | API response type validation missing | Critical | Fixed | 2026-06-24 |
| B-004 | Window geometry ValueError on bad prefs | High | Fixed | 2026-06-24 |
| B-005 | File stat crash between glob and access | High | Fixed | 2026-06-24 |
| B-006 | OCR repeated init attempts on failure | High | Fixed | 2026-06-24 |
| B-007 | Frame dedup drops single-word frames | High | Fixed | 2026-06-24 |
| B-008 | Embedding batch hardcoded ignoring RAM | Medium | Fixed | 2026-06-24 |
| B-009 | Dedup signature too short (240 chars) | Medium | Fixed | 2026-06-24 |
| B-010 | PySide6 SEGFAULT in Git Bash mintty | Critical | Fixed | 2026-06-24 |
| B-011 | Deleting python312._pth breaks pip | Critical | Fixed | 2026-06-24 |
| B-012 | ModuleNotFoundError 'core' with embedded Python | Critical | Fixed | 2026-06-24 |
| B-013 | cp312 wheels fail on Python 3.13 | Critical | Fixed | 2026-06-24 |
| B-014 | Sentinel file causes install.cmd to skip install | High | Fixed | 2026-06-24 |
| B-015 | pythonw.exe hides all crashes | High | Fixed | 2026-06-24 |
| B-016 | Qt plugin path not found with embedded Python | High | Fixed | 2026-06-24 |
| B-017 | install.cmd calling install.sh via Git Bash | High | Fixed | 2026-06-24 |
| B-018 | build.py not called from install.cmd | High | Fixed | 2026-06-24 |
| B-019 | clean_old_installs verbose output floods quiet mode | Low | Fixed | 2026-06-24 |
| B-020 | _show_tools_menu crash (deleted Tools button refs) | High | Fixed | 2026-07-04 |

---

## Requirements

- **Portable mode**: Nothing pre-installed (Python bundled)
- **Source mode**: Python 3.10+ (3.12 recommended)
- Windows 10/11 (x64/ARM64), macOS (Intel/Apple Silicon), or Linux (x64)
- Azure DevOps PAT + Organization (entered on first launch)
- Optional: NVIDIA GPU with CUDA / Apple Metal for accelerated inference
