# Changelog

## 3.44.0 — 2026-07-21 (E2E System Overhaul)

### Fixes

- **PDF Packager regression:** Detail View PDF button now correctly downloads the
  WI-packaged PDF instead of the E2E test report. Root cause: artifact listing
  only scanned EXPORTS_DIR; now also scans OUTPUTS_DIR/packets with distinct
  `kind` tagging.
- **E2E Dialog overlay:** fixed unbounded grid growth causing TC list, log panel,
  and review panel to overlap. Proper flex height chain with `min-h-0` + bounded
  overflow on maximized modals.
- **PDF report truncation:** removed hard 20/30/40-char truncation. Table cells
  now use reportlab Paragraph wrapping with expanded column widths (18.1cm full
  A4 usable width).
- **Video letterboxing:** removed hardcoded 1920x1080 `record_video_size` when
  maximized. Playwright now auto-detects actual viewport dimensions.
- **KB navigation depth:** raised raw_context limit (4000->8000), plan compiler
  context (6000->12000), added project_context to cache key (KB changes
  invalidate stale plans), added NAVIGATION INFERENCE instruction to compiler
  prompt, included description/acceptance_criteria in TC sidecar.

---

## 3.43.0 — 2026-07-21 (Responsive Grid + Export Sort)

### New capabilities

- **Responsive grid autofit:** columns fill the available viewport width via
  ResizeObserver, responding dynamically to nav panel, log panel, detail pane,
  and window resize events. Manually drag-resized columns retain their width.
- **Column data-source switching:** right-click any column header to remap its
  data source to any WorkItemRow field (board column, board lane, iteration path,
  area path, tags, created date, etc.). Persisted per user in localStorage.
- **Excel export sort by created date:** within the same WI type group, rows are
  now sorted by created date descending (newest first), then by WI ID ascending
  as a tiebreaker.
- **Created date field:** `System.CreatedDate` (ADO) and `fields.created` (JIRA)
  now flow through the full stack — backend dataclass, route serializer, frontend
  interface, column field picker, and export sort logic.
- **TC count fix (v3.41-3.42):** `testCaseCountsByWorkItem()` now only counts
  linked test cases from tracker relations (ADO TestedBy / JIRA test links);
  sidecar-generated TCs no longer inflate the count.

### Fixes

- Board columns now include `fieldMap` in persisted state (test + type fixes).
- `_GRID_FIELDS` includes `System.CreatedDate` for ADO batch fetch.

---

## 3.40.0 — 2026-07-21 (Autonomous AI QA Agent)

Major overhaul transforming the E2E runner from a script executor into a fully
autonomous AI QA agent.

### New capabilities

- **KB-driven step discovery (R1):** agent queries project KB for screens,
  preconditions, business rules, and navigation hints before plan compilation.
  New module: `automation/kb_briefing.py` (KBBriefingEngine + TestBrief).
- **Artifacts per WI (R2):** each work item now produces a PDF report (steps +
  AI observations), video recording (Playwright context-level), and Excel row
  append. New module: `automation/report_pdf.py`.
- **Parallel execution (R3):** up to 3 user stories run simultaneously in fully
  isolated BrowserContexts sharing one Chromium process. New module:
  `automation/parallel_runner.py` (ParallelRunner + ExecutionSlot + SlotResult).
- **Per-WI cancellation (R4):** `POST /e2e/stop/{job_id}?wi_id=X` stops a single
  work item's slot while others continue.
- **Browser maximized (R5):** `--start-maximized` + `no_viewport=True` in
  `playwright_bridge.py`.
- **TC discovery by hierarchy (R6):** "child" added to `_LINK_REL_HINTS` in
  `ado/boards.py` for parent-child WI type traversal.
- **Human review flow (R7):** ReviewPanel in E2EDialog with per-TC approve/reject
  buttons, video links, and sign-off workflow.
- **Page observation:** new `automation/page_observer.py` captures a11y tree after
  every action, detects error/success/loading signals, scores confidence 0.0-1.0,
  and computes ObservationDelta.
- **Slot-based execution:** new `run_e2e_slot()` function in `e2e_runner.py`
  accepts a pre-created page from ParallelRunner.

### Breaking changes

- E2E video replaces per-step screenshots as the sole visual record.
- Dead duplicate code removed from `agent/routes/e2e.py` (old non-healing paths).

### Documentation (R8)

- README.md rewritten for v3.40.0 autonomous agent capabilities.
- E2E_SPEC.md expanded with Stages F-I (parallel, KB, observation, review).
- CODEBASE.md updated with Section 4.8 (E2E Automation modules).
- docs/ARCHITECTURE.md Section 6 fully rewritten.
- AboutDialog.tsx rewritten with v3.40.0 feature list.
- This changelog entry.

### Architecture decisions

- ADR-1: N contexts from 1 browser (shared process, not N cold starts)
- ADR-2: Per-WI grouping over per-TC parallelism (TCs share login state)
- ADR-3: Sequential fallback for N=1 (zero regression risk)
- ADR-5: reportlab for PDF (zero new dependencies)
- ADR-6: 1-level parent-child traversal (prevents explosion on large trees)
- ADR-7: Video replaces screenshots (richer, scrub to any moment)

---

## 3.0.1 — 2026-07-20 (stabilization)

Overnight autonomous run: 8 units completed, all suites green.

### Fixes

- **Export All Projects/Boards**: fixed false-negative where boards with work
  items outside per-team area paths were reported as empty. Root cause:
  `load_board_view_async` applied `scope_to_team_area=True` unconditionally;
  project-wide exports now pass `scope_to_team_area=False`. (unit-01)
- **KB context pipeline**: 2 documents failing due to retry timeout too short
  for small docs. `_timeout_for_doc` now doubles base timeout on retries.
  51/51 documents fully contexted. (unit-06)
- **E2E test harness**: added missing `MAX_PLAN_COMPILE_RETRIES` constant in
  `agent/routes/e2e.py`; fixed NameError on plan compilation retry path. (unit-08)
- **Playwright specs**: bumped mock agent version 2.23.0 -> 3.0.0 to match
  `REQUIRED_AGENT_VERSION`; added `{ exact: true }` to ambiguous OK button
  selector in dialogs spec. (unit-08)

### Documentation

- Produced `overnight/CODEBASE_MAP.md` with module map and hot-spot pins. (unit-00)
- Log triage: no high/critical issues found across 5 log files. (unit-07)
- Fixed version drift in ARCHITECTURE.md and DOCS.md (were showing stale
  2.8.2 / 2.16.x / 2.28.0 / 3.6.0; corrected to 3.0.1). (unit-09)

### Regression

- py_compile: 120/120 OK
- Vitest: 389/389 passed
- Playwright: 19/19 passed
- Pytest: 1070/1070 passed
- E2E flagship: harness clean; live run blocked (no ADO PAT in env)
