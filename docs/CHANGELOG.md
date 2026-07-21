# Changelog

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
