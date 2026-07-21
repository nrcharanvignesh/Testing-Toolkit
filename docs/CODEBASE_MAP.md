# CODEBASE_MAP.md — Testing Toolkit

> Generated 2026-07-21 from commit 638ba62 (v3.40.0 — Autonomous AI QA Agent)

---

## Product boundary

| Layer | Location | Tech |
|-------|----------|------|
| **Web app (primary frontend)** | `app/`, `components/`, `lib/` | Next.js 16, React 19, Tailwind v4, Vercel |
| **Shared backend agent** | `agent-bundle/src/` | Python 3.12, FastAPI @ 127.0.0.1:7842 |
| **Desktop-parity (secondary)** | Documented in `docs/desktop-parity/` | PySide6 (mirrors web UI, native cmd.exe only) |
| **Distribution** | `scripts/`, `install.cmd`, `install.sh`, GitHub `parts` branch | Installer + self-update manifest |

---

## Top-level modules

### Web app — `app/`

| Path | Responsibility |
|------|----------------|
| `app/page.tsx` | Root entry: polls agent `/health`, gates to onboarding vs. main shell |
| `app/layout.tsx` | Root layout + globals.css |
| `app/api/build-id/route.ts` | Vercel build-id API route |
| `app/api/installer/route.ts` | Installer download endpoint |
| `app/api/llm/[...path]/` | LLM proxy route (Vercel-side GenAI gateway) |
| `app/error.tsx`, `app/global-error.tsx` | Error boundaries |

### Web app — `components/`

| Path | Responsibility |
|------|----------------|
| `components/layout/AppShell.tsx` | Root three-pane layout |
| `components/layout/NavPanel.tsx` | Project/board navigator; All-Boards / All-Projects Excel export triggers |
| `components/layout/StatusBar.tsx` | Agent health + connection metric chips |
| `components/layout/ActivityBar.tsx` | Icon rail (AI Stack, Settings, KB, Help) |
| `components/board/BoardGrid.tsx` | Swim-lane work-item grid; column headers, resize, collapse, single-board export |
| `components/board/ActionBar.tsx` | Generate / Publish / Tools action groups |
| `components/board/DetailPane.tsx` | Work-item property grid + comments |
| `components/board/LogPanel.tsx` | Timestamped level-colored log feed |
| `components/dialogs/E2EDialog.tsx` | E2E flagship dialog: TC list, run control, progress, ReviewPanel (approve/reject/sign-off) |
| `components/dialogs/GenerateDialog.tsx` | Test-case generation dialog |
| `components/dialogs/ProjectKbDialog.tsx` | KB document management + indexing progress |
| `components/dialogs/SettingsDialog.tsx` | Connection, preferences, diagnostics |
| `components/dialogs/AiStackDialog.tsx` | 7-layer AI stack visualizer |
| `components/dialogs/*` | PackageDialog, DefectDialog, UploadDialog, RetrievalDialog, ChatDialog, CredentialsDialog, AboutDialog, ViewLogDialog |
| `components/onboarding/OnboardingScreen.tsx` | First-run installer prompt |
| `components/onboarding/AgentUpdateRequired.tsx` | Forced-update gate |
| `components/ui/` | Shared primitives: modal, dropdown, resizer, markdown, error-boundary, download-links, source-logo |

### Web app — `lib/`

| Path | Responsibility |
|------|----------------|
| `lib/agent-client.ts` | Typed fetch wrapper to `http://127.0.0.1:7842`; all request/response types |
| `lib/agent-version.ts` | `REQUIRED_AGENT_VERSION` handshake + semver compare |
| `lib/app-state.tsx` | React context: project, board, selection, logs, KB status, dialogs |
| `lib/board-columns.ts` | Column definitions (`BOARD_COLUMNS`), localStorage-backed resize/collapse |
| `lib/board-lanes.ts` | Swim-lane collapse state |
| `lib/board-utils.ts` | Board data helpers |
| `lib/export-board.ts` | `exportSingleBoard`, `exportAllBoards` — client-side ExcelJS workbook generation + browser download |
| `lib/event-bus.ts` | Global pub/sub event bus |
| `lib/preferences.ts` | localStorage preferences |
| `lib/theme.tsx` | Theme context (dark/light) |
| `lib/toast.ts` | Toast notification system |
| `lib/installer-template.ts` | Installer script template generation |
| `lib/reinstall.ts` | Reinstall flow logic |
| `lib/use-app-update.ts` | Auto-update hook |
| `lib/use-metrics.ts` | Metrics collection hook |
| `lib/use-web-freshness.ts` | Web app freshness check hook |
| `lib/utils.ts` | Shared utilities |

### Shared backend agent — `agent-bundle/src/agent/`

| Path | Responsibility |
|------|----------------|
| `agent/server.py` | FastAPI app creation, TLS, middleware |
| `agent/__main__.py` | Entry point (`python -m agent.server`) |
| `agent/version.py` | `AGENT_VERSION = "3.0.0"`, `AGENT_PORT = 7842` |
| `agent/jobs.py` | Background job scheduler |
| `agent/updater.py` | Self-update from manifest |
| `agent/model_loader.py` | LLM model routing/loading |
| `agent/middleware_trace.py` | Request tracing middleware |
| `agent/routes/` | FastAPI route modules: `ado.py`, `artifacts.py`, `chat.py`, `credentials.py`, `defects.py`, `e2e.py`, `events.py`, `generate.py`, `health.py`, `jira.py`, `jobs.py`, `kb.py`, `llm.py`, `settings.py`, `sources.py`, `tools.py`, `update.py` |

### Shared backend agent — `agent-bundle/src/ado/`

| Path | Responsibility |
|------|----------------|
| `ado/api.py` | Low-level ADO REST client (WIQL, WI fetch, attachment proxy) |
| `ado/boards.py` | Board/team/column/work-item data model + streaming fetch; `load_board_view_async`, `download_attachment` |
| `ado/extract.py` | Attachment extraction + inline image download (concurrent) |
| `ado/test_steps.py` | ADO test-step read/write |
| `ado/testcase_creator.py` | Upload generated test cases to ADO |

### Shared backend agent — `agent-bundle/src/automation/`

| Path | Responsibility |
|------|----------------|
| `automation/parallel_runner.py` | **NEW v3.40** — 1 Browser -> N isolated BrowserContexts (max 3), per-WI cancel |
| `automation/kb_briefing.py` | **NEW v3.40** — KB-driven TestBrief builder (screens, preconditions, business rules) |
| `automation/page_observer.py` | **NEW v3.40** — Post-action a11y tree analysis, confidence scoring, ObservationDelta |
| `automation/report_pdf.py` | **NEW v3.40** — Per-WI PDF report generation (reportlab) |
| `automation/e2e_runner.py` | Self-healing Playwright E2E engine; `run_e2e_tests` + `run_e2e_slot` (parallel) |
| `automation/e2e_plan.py` | LLM-compiled execution plan; `compile_test_case` (line 390), `validate_plan`, `CompiledPlan` |
| `automation/script_generator.py` | `generate_playwright_script` (line 187), `generate_mcp_replay_script` (line 518), `save_mcp_replay_script` (line 631) |
| `automation/playwright_bridge.py` | Browser lifecycle management (launch maximized, context, video recording) |
| `automation/report_excel.py` | `write_e2e_report` (line 83) — openpyxl E2E report (append mode) |
| `automation/artifact_collector.py` | Per-TC artifact tree |
| `automation/credential_vault.py` | OS-level credential vault for E2E passwords |
| `automation/execution_store.py` | Persistent execution state |
| `automation/bug_tracker.py` | Defect auto-filing from E2E failures |
| `automation/dashboard_report.py` | Dashboard-level reporting |
| `automation/healing_guardrails.py` | Self-healing locator waterfall (6 strategies) |
| `automation/screenshot_annotator.py` | Screenshot annotation (legacy, replaced by video in v3.40) |

### Shared backend agent — `agent-bundle/src/testgen/`

| Path | Responsibility |
|------|----------------|
| `testgen/rlm.py` | Recursive Language Model pipeline (generation core) |
| `testgen/testcase_excel.py` | `payload_to_xlsx` (line 87), `xlsx_to_payload` (line 194) — review workbook round-trip |
| `testgen/testcase_template.py` | Template-driven TC generation |
| `testgen/template_analyzer.py` | Template quality analysis |
| `testgen/quality_scorer.py` | TC quality scoring |
| `testgen/diff_engine.py` | TC diff/delta computation |
| `testgen/gen_cache.py` | Generation cache |
| `testgen/traceability.py` | Requirement-to-TC traceability |
| `testgen/test_data.py` | Test data generation |
| `testgen/tc_types.py` | Type definitions for TCs |

### Shared backend agent — `agent-bundle/src/kb/`

| Path | Responsibility |
|------|----------------|
| `kb/indexer.py` | `build_index_resumable` (line 277) — crash-safe, checkpoint-based indexer |
| `kb/store.py` | Chunk storage (KbChunk model, sorted deterministic index) |
| `kb/vector_store.py` | LanceDB vector store |
| `kb/embeddings.py` | API-based dense embeddings (memory-aware batch sizing) |
| `kb/bm25.py` | Sparse BM25 retrieval (NumPy, no model weights) |
| `kb/retrieval.py` | Hybrid retrieval (BM25 + dense + reranker) |
| `kb/retrieval_config.py` | Retrieval hyperparameters |
| `kb/reranker.py` | Cohere-format reranking with LLM-as-judge fallback |
| `kb/contextual.py` | Context enrichment for chunks |
| `kb/context_summary.py` | Project-level context summary |
| `kb/bundle.py` | KB bundle packaging |
| `kb/file_sig.py` | File signature for change detection |
| `kb/legacy_docs.py` | Legacy document format support |
| `kb/model_bundle.py` | Model bundle management |
| `kb/multimedia.py` | Video/audio extraction |
| `kb/ocr.py` | OCR via API vision |
| `kb/kb_crypto.py` | KB encryption |

### Shared backend agent — `agent-bundle/src/core/`

| Path | Responsibility |
|------|----------------|
| `core/app_config.py` | `APP_SLUG`, `WEB_WORKSPACE_SLUG`, workspace path (`~/TestingToolkitWeb`), env loading |
| `core/anthropic_client.py` | Anthropic-format LLM client |
| `core/openai_transport.py` | OpenAI-format transport (dual-format wire translation) |
| `core/model_router.py` | Model routing (primary/fast/fallback) |
| `core/credential_envelope.py` | AES-256-GCM .env.enc authenticated envelope |
| `core/credential_store.py` | OS secret store integration |
| `core/settings_store.py` | Persistent settings (JSON) |
| `core/prefs_store.py` | UI preferences store |
| `core/project_store.py` | Project lifecycle management |
| `core/orchestrator.py` | Job orchestration |
| `core/diagnostics.py` | `run_doctor` system diagnostics |
| `core/hardware.py` | GPU/NUMA/RAM detection (fail-safe) |
| `core/app_logging.py` | Rotating log to `%LOCALAPPDATA%/TestingToolkit/logs` |
| `core/guardrails.py` | Safety guardrails |
| `core/http_retry.py` | Retry-aware HTTP client |
| `core/network_status.py` | Connectivity detection |
| `core/tls_helper.py` | TLS certificate management |
| `core/mcp_bridge.py` | MCP server bridge |
| `core/trace.py` | Distributed tracing |
| `core/process_metrics.py` | Process-level metrics |
| `core/runtime_config.py` | Runtime configuration model |
| `core/pat_store.py` | PAT (Personal Access Token) store |
| `core/source_registry.py`, `core/source_resolver.py`, `core/source_types.py` | Multi-source (ADO/JIRA) registry |
| `core/protocols.py` | Protocol definitions |
| `core/chat_tools.py` | Chat tool definitions |

### Shared backend agent — `agent-bundle/src/defects/`

| Path | Responsibility |
|------|----------------|
| `defects/parser.py` | Bulk defect text parsing |
| `defects/review_excel.py` | Defect review workbook |
| `defects/ado_uploader.py` | Batch upload defects to ADO |

### Shared backend agent — `agent-bundle/src/jira/`

| Path | Responsibility |
|------|----------------|
| `jira/api.py` | JIRA REST client |
| `jira/boards.py` | JIRA board data model |
| `jira/adf.py` | Atlassian Document Format conversion |
| `jira/test_steps.py` | JIRA test-step handling |
| `jira/testcase_creator.py` | JIRA test-case upload |

### Shared backend agent — `agent-bundle/src/tools/`

| Path | Responsibility |
|------|----------------|
| `tools/combine_pdf.py` | PDF merge/packaging |
| `tools/pdf_packager.py` | Per-WI PDF packet builder |
| `tools/office_convert.py` | Office document conversion |
| `tools/visio_convert.py` | Visio diagram conversion |

### Shared backend agent — `agent-bundle/src/scripts/`

| Path | Responsibility |
|------|----------------|
| `scripts/discover_test_cases.py` | TC discovery utility |

### Distribution & build

| Path | Responsibility |
|------|----------------|
| `scripts/build-agent-update-manifest.mjs` | Builds `agent-update.json` manifest on `parts` branch |
| `install.cmd` | Windows native cmd.exe installer (MUST NOT use Git Bash) |
| `install.sh` | macOS/Linux installer |
| `vercel.json` | Vercel deployment config |
| `playwright.config.ts` | Playwright E2E config for web specs |

### Tests

| Path | Responsibility |
|------|----------------|
| `__tests__/` | Vitest unit tests (3 files: app-state, ProjectKbDialog, StatusBar) |
| `e2e/` | Playwright web E2E specs (6 specs + helpers + seed) |
| `agent-bundle/` (pytest) | 177+ Python tests for agent modules |
| `tests/test_full_e2e.py` | 57+ integration checks (full system) |

---

## Hot spots (pinned file + symbol)

### (a) All-Projects / All-Boards Excel export

| What | File | Symbol |
|------|------|--------|
| Single-board export | `lib/export-board.ts:716` | `exportSingleBoard()` |
| All-boards export | `lib/export-board.ts:753` | `exportAllBoards()` |
| Testgen review xlsx (write) | `agent-bundle/src/testgen/testcase_excel.py:87` | `payload_to_xlsx()` |
| Testgen review xlsx (read) | `agent-bundle/src/testgen/testcase_excel.py:194` | `xlsx_to_payload()` |
| ADO board/WI fetch | `agent-bundle/src/ado/boards.py:509` | `load_board_view_async()` |
| NavPanel trigger | `components/layout/NavPanel.tsx:59` | `exportAllBoards()` call |

### (b) Web grid + column-header handling

| What | File | Symbol |
|------|------|--------|
| Grid component | `components/board/BoardGrid.tsx` | `BoardGrid` (default export, line 1) |
| Column definitions | `lib/board-columns.ts:39` | `BOARD_COLUMNS` (8 columns: id, title, type, state, assignee, sprint, tests, lastRun) |
| Column types | `lib/board-columns.ts:19` | `BoardColumnId` type |
| Collapse width | `lib/board-columns.ts:51` | `COLLAPSED_WIDTH = 30` |
| Desktop-parity grid | `docs/desktop-parity/REFERENCE.md` | (PySide6 QTableView mirror; same column set) |

### (c) E2E flagship

| What | File | Symbol |
|------|------|--------|
| E2E runner (execution) | `agent-bundle/src/automation/e2e_runner.py:682` | `run_e2e_tests()` |
| Plan compiler (LLM) | `agent-bundle/src/automation/e2e_plan.py:390` | `compile_test_case()` |
| Plan validator | `agent-bundle/src/automation/e2e_plan.py:351` | `validate_plan()` |
| Step validator | `agent-bundle/src/automation/e2e_plan.py:324` | `validate_steps()` |
| Script generator | `agent-bundle/src/automation/script_generator.py:187` | `generate_playwright_script()` |
| MCP replay script | `agent-bundle/src/automation/script_generator.py:518` | `generate_mcp_replay_script()` |
| E2E dialog (web) | `components/dialogs/E2EDialog.tsx` | TC list + run control |
| E2E route (agent) | `agent-bundle/src/agent/routes/e2e.py` | HTTP endpoints |
| Compiled plan model | `agent-bundle/src/automation/e2e_plan.py:201` | `class CompiledPlan` |

### (d) KB ingestion pipeline

| What | File | Symbol |
|------|------|--------|
| Indexer (main entry) | `agent-bundle/src/kb/indexer.py:277` | `build_index_resumable()` |
| Chunk store | `agent-bundle/src/kb/store.py` | `KbChunk`, sorted deterministic index |
| Vector store | `agent-bundle/src/kb/vector_store.py` | LanceDB embedded store |
| Embeddings (API) | `agent-bundle/src/kb/embeddings.py` | Memory-aware batch sizing (32/64/128) |
| BM25 sparse | `agent-bundle/src/kb/bm25.py` | NumPy-only, no model weights |
| Hybrid retrieval | `agent-bundle/src/kb/retrieval.py` | BM25 + dense + reranker |
| Reranker | `agent-bundle/src/kb/reranker.py` | Cohere API + LLM-as-judge fallback |
| Context enrichment | `agent-bundle/src/kb/contextual.py` | Per-chunk contextual enrichment |
| Multimedia (audio/video) | `agent-bundle/src/kb/multimedia.py` | Whisper API transcription |
| OCR | `agent-bundle/src/kb/ocr.py` | API vision (GPT-4o) |
| KB dialog (web) | `components/dialogs/ProjectKbDialog.tsx` | Upload + indexing progress |
| KB route (agent) | `agent-bundle/src/agent/routes/kb.py` | HTTP endpoints |

### (e) User-facing output / Downloads paths

| What | File | Symbol |
|------|------|--------|
| Browser download (blob) | `lib/export-board.ts:969-981` | `URL.createObjectURL(blob)` + anchor click + `agent.uploadArtifact()` |
| Agent workspace root | `agent-bundle/src/core/app_config.py:134` | `WEB_WORKSPACE_SLUG = "TestingToolkitWeb"` -> `~/TestingToolkitWeb/` |
| Workspace override env | `agent-bundle/src/core/app_config.py:144` | `TT_WORKSPACE_DIR` |
| Desktop credential vault | `agent-bundle/src/automation/credential_vault.py:27` | `Path.home() / "TestingToolkit" / "vaults"` |
| Log directory | `agent-bundle/src/core/app_logging.py:16` | `%LOCALAPPDATA%/TestingToolkit/logs` |
| E2E report output | `agent-bundle/src/automation/report_excel.py:83` | `write_e2e_report()` |
| E2E artifact tree | `agent-bundle/src/automation/artifact_collector.py:21` | `output_dir / tc_id` subdirs |
| Generated TCs | `agent-bundle/src/core/app_config.py:13` | `projects/<name>/generated/` |

### (f) Version constants

| What | File | Symbol | Value |
|------|------|--------|-------|
| Web app version | `package.json:3` | `"version"` | `"3.0.0"` |
| Agent version (Python) | `agent-bundle/src/agent/version.py:2` | `AGENT_VERSION` | `"3.0.0"` |
| Required agent version (web) | `lib/agent-version.ts:16` | `REQUIRED_AGENT_VERSION` | `"3.0.0"` |

---

## Version / AI-stack drift (docs vs. code)

| Document | Claims | Code reality | Drift |
|----------|--------|-------------|-------|
| `docs/ARCHITECTURE.md` header (line 3) | "Web v3.0.0 / Agent v2.16.x" | Web 3.0.0 / Agent 3.0.0 | Agent version wrong in header |
| `docs/ARCHITECTURE.md` body (line 39) | "agent 2.8.2" | Agent 3.0.0 | Body still says 2.8.2 |
| `docs/DOCS.md` header (line 3) | "Web v3.6.0 / Agent v2.28.0" | Web 3.0.0 / Agent 3.0.0 | Both wrong (DOCS claims higher web, lower agent) |
| `docs/ARCHITECTURE.md` AI stack (line 136) | "As of agent 2.8.0, all AI/ML..." | Agent 3.0.0; API-first is accurate | Version reference stale, architecture claim correct |
| `docs/ARCHITECTURE.md` body | LiteLLM proxy, no local ONNX | Code confirms: `core/anthropic_client.py` + `core/openai_transport.py`, no ONNX imports | Correct — API-first is reality |

**Summary of drift:** ARCHITECTURE.md has 2 stale agent version references (header: 2.16.x, body: 2.8.2). DOCS.md has entirely wrong version pair (3.6.0/2.28.0). The AI-stack description (API-first, no local models) is accurate.

---

## Entry points

| Entry | Path | How |
|-------|------|-----|
| Web app (Vercel) | `app/page.tsx` | `pnpm dev` / Vercel deploy |
| Agent server | `agent-bundle/src/agent/__main__.py` | `python -m agent.server` |
| Agent port | 7842 | Hardcoded in `agent/version.py` |
| Installer (Win) | `install.cmd` | Double-click (native cmd.exe only) |
| Installer (Unix) | `install.sh` | `bash install.sh` |
| Update manifest builder | `scripts/build-agent-update-manifest.mjs` | `node scripts/build-agent-update-manifest.mjs` |
