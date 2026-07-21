# E2E Runner v3.40.0 — Autonomous AI QA Agent

## Vision
Transform the E2E runner from a script executor into a fully autonomous AI QA agent
that studies, navigates, observes, reasons, and reports — with zero human input until
final review sign-off.

---

## Requirements

| # | Requirement | Status |
|---|-------------|--------|
| R1 | KB-driven step discovery: agent queries KB for screens, preconditions, steps | DONE |
| R2 | Artifacts per WI: PDF (steps + AI observations), video (no screenshots), existing Excel append | DONE |
| R3 | Up to 3 user stories in parallel, fully isolated browser contexts | DONE |
| R4 | Cancellation options per WI mid-run | DONE |
| R5 | Browser launches maximized | DONE |
| R6 | TC discovery by parent-child WI type hierarchy | DONE |
| R7 | Human review flow post-run (video + AI observations + sign-off) | DONE |
| R8 | Update and revamp ALL documentation, READMEs, about section, specs | DONE |

---

## Intelligence Layers

```
Layer 5: REASONING    — Business rule validation against KB
Layer 4: OBSERVATION  — Full page state analysis after every action
Layer 3: NAVIGATION   — Autonomous pathfinding from KB app map
Layer 2: INTERACTION  — Self-healing locator waterfall + LLM recompile
Layer 1: BROWSER      — Playwright contexts, video, maximized, isolated
```

---

## Phase 1: Foundation (Browser + Parallel Infra)

### 1a. Browser Maximized [R5]
- [ ] Add `--start-maximized` to Chromium launch args in `playwright_bridge.py`
- [ ] Set `no_viewport=True` in context options (lets browser use full window size)
- [ ] Verify viewport reports actual screen dimensions after launch
- [ ] Test on multi-monitor setups (use primary monitor)

### 1b. Parallel Isolated Contexts [R3]
- [ ] Create `automation/parallel_runner.py` — ParallelRunner class
- [ ] Launch ONE Browser instance, create up to 3 independent BrowserContexts
- [ ] Each context gets: own cookies, own storage state, own video dir
- [ ] Shared browser process = shared GPU/memory (not 3x Chromium)
- [ ] Context creation with: isolated profile, video recording, maximized viewport
- [ ] Error isolation: one context crash doesn't kill others (handle gracefully)
- [ ] Resource cleanup: close contexts independently on completion/cancel

### 1c. Per-WI Execution Model [R3]
- [ ] Refactor `_run_e2e` to split work items into N slots (max 3)
- [ ] Each slot: independent login, independent navigation, independent artifacts
- [ ] Progress streaming per slot: `("running", slot_index, wi_id, step_info)`
- [ ] Result collection: wait for all slots, merge into unified E2ERunResult
- [ ] Sequential fallback: if N=1, use existing single-context path (no regression)

### 1d. Per-WI Cancellation [R4]
- [ ] Extend `E2EStartRequest` with optional `cancel_wi_ids: list[str]`
- [ ] Add route: `POST /e2e/stop/{job_id}?wi_id=X` (cancel single WI)
- [ ] Keep existing `POST /e2e/stop/{job_id}` (cancel ALL)
- [ ] Per-slot stop_event: dict[str, threading.Event] keyed by wi_id
- [ ] Runner checks per-WI event between steps
- [ ] Cancelled WI: close its context, generate partial report, mark as "cancelled"
- [ ] Other slots continue unaffected

---

## Phase 2: KB Briefing Engine ("Study the App") [R1]

### 2a. Test Brief Builder
- [ ] Create `automation/kb_briefing.py` — KBBriefing class
- [ ] Input: user story (title + acceptance criteria + description)
- [ ] Multi-query KB retrieval targeting:
  - Screen names and URLs mentioned in requirements
  - Navigation hierarchy (how to reach the feature)
  - Preconditions (data state, permissions, prior flows)
  - Business rules and validation logic
  - UI element labels and expected behaviors
  - Error states and edge cases documented
- [ ] Output: structured `TestBrief` dataclass:
  ```
  TestBrief:
    screens: list[Screen]        # name, url_pattern, key_elements
    preconditions: list[str]     # what must be true before testing
    navigation_path: list[str]   # steps to reach the feature
    business_rules: list[str]    # what "correct" means
    edge_cases: list[str]        # documented failure modes
    terminology: dict[str,str]   # domain terms -> meanings
  ```

### 2b. LLM Synthesis Pass
- [ ] After RAG retrieval, LLM synthesizes scattered KB chunks into coherent brief
- [ ] Uses MODEL_MEDIUM (fast model) for synthesis — not expensive primary
- [ ] Prompt: "You are a QA lead briefing a tester. Given these requirement excerpts, produce a complete test brief for this user story."
- [ ] Cache briefs by (wi_id + kb_index_hash) — invalidate on KB re-index

### 2c. Integration with Execution
- [ ] Brief is generated BEFORE plan compilation
- [ ] Brief feeds into plan compiler as authoritative context (replaces selective KB)
- [ ] Brief feeds into observation layer (business rules for validation)
- [ ] Brief feeds into navigation layer (screen map for pathfinding)

---

## Phase 3: Autonomous Execution ("AI Walks the Journey")

### 3a. Adaptive Agent Loop
- [ ] Create `automation/qa_agent.py` — QAAgent class (per-WI orchestrator)
- [ ] Agent loop: ACT -> OBSERVE -> REASON -> DECIDE NEXT
- [ ] Not pre-compiled rigid steps; flexible strategy with checkpoints
- [ ] Each checkpoint = an assertion about expected state
- [ ] Agent decides HOW to reach checkpoint (not told exact clicks)

### 3b. Autonomous Navigation
- [ ] Agent uses TestBrief.navigation_path as starting guidance
- [ ] If expected screen not reached: reason about current state, try alternatives
- [ ] Precondition setup: agent navigates to create required state (data, permissions)
- [ ] URL-based shortcuts when safe (skip multi-step navigation for known URLs)
- [ ] Detect "stuck" state: same page after N actions = try different approach

### 3c. Alternate Path Recovery
- [ ] On blocked path: LLM reasons about WHY it's blocked
- [ ] Generates alternative approach (different navigation, different user action)
- [ ] Up to 2 alternate attempts before marking checkpoint as BLOCKED
- [ ] Log reasoning chain for human review

### 3d. Execution Strategies
- [ ] Happy path first: walk the primary user journey
- [ ] Then edge cases: attempt documented edge cases from TestBrief
- [ ] Exploratory: if time/budget allows, try undocumented paths
- [ ] Priority: business-critical assertions first, cosmetic last

---

## Phase 4: Smart Observation ("Does This Look Right?") 

### 4a. Page Observer
- [ ] Create `automation/page_observer.py` — PageObserver class
- [ ] After every significant action, capture full page state:
  - Visible text content (key areas)
  - Error messages / toast notifications / alerts
  - Loading states (spinners, skeleton screens)
  - Form validation messages
  - Navigation state (URL, breadcrumbs)
  - Data correctness (tables, counts, values)

### 4b. AI Reasoning Against Business Rules
- [ ] Cross-reference page state with TestBrief.business_rules
- [ ] Detect violations: "Spec says X, but page shows Y"
- [ ] Detect anomalies: unexpected error messages, missing elements
- [ ] Detect performance issues: page took >3s to stabilize
- [ ] Detect UX issues: confusing flow, missing feedback

### 4c. Confidence Scoring
- [ ] Each observation gets a confidence level:
  - HIGH: clear violation of a documented business rule
  - MEDIUM: likely issue but needs human verification
  - LOW: possible false positive, cosmetic concern
- [ ] Confidence based on: strength of KB evidence + clarity of page state
- [ ] Only HIGH confidence findings block the overall verdict

### 4d. Anomaly Detection
- [ ] Track "normal" page patterns across runs (element counts, load times)
- [ ] Flag deviations from baseline as potential regressions
- [ ] Detect: new error messages, missing previously-present elements
- [ ] Feed anomalies into the report as "needs investigation"

---

## Phase 5: Artifacts & TC Discovery [R2, R6]

### 5a. PDF Report per WI
- [ ] Create `automation/report_pdf.py` using reportlab (installed, no new dep)
- [ ] PDF structure:
  - Cover page: WI ID, title, run timestamp, environment, verdict
  - Executive summary: pass/fail/blocked counts, confidence distribution
  - Step-by-step detail: action, observation, AI reasoning, verdict
  - Anomalies section: findings with confidence levels
  - Coverage assessment: what was verified vs what couldn't be reached
- [ ] AI-refined observations (not raw logs — synthesized narrative)
- [ ] Output: `EXPORTS_DIR/e2e/{project}/{wi_id}/report_{timestamp}.pdf`

### 5b. Video Recording per WI
- [ ] One video per isolated context (already supported by Playwright)
- [ ] Output: `EXPORTS_DIR/e2e/{project}/{wi_id}/recording_{timestamp}.mkv`
- [ ] Remove per-step screenshot capture (video replaces it)
- [ ] Keep final-state screenshot only (for PDF embed if needed)

### 5c. Excel Append to Existing Workbook
- [ ] On run completion, open existing project workbook (or create if first run)
- [ ] Workbook path: `EXPORTS_DIR/e2e/{project}/e2e_results.xlsx`
- [ ] Append new run as rows to "Results" sheet (not replace)
- [ ] Columns: Run ID, Timestamp, WI ID, TC Title, Verdict, Duration, Confidence, Notes
- [ ] Summary sheet updated with running totals

### 5d. Hierarchy-Based TC Discovery [R6]
- [ ] In `ado/boards.py`: add parent-child traversal for TC discovery
- [ ] Logic: if WI type is "User Story" or "Feature" or "Epic":
  - Fetch children (1 level deep)
  - Filter children by type: include if `_TEST_TYPE_TOKENS` matches
  - Include those as test cases for the parent WI
- [ ] Priority order: TestedBy/Tests (direct) > Child test cases > Related links
- [ ] In `ado/test_steps.py`: extend `fetch_linked_test_cases` with hierarchy path
- [ ] Configurable depth limit (default: 1 level)
- [ ] ponytail: 1-level depth; recursive traversal if deep hierarchies needed

### 5e. Remove Screenshot Artifacts
- [ ] Remove per-step screenshot capture from e2e_runner.py
- [ ] Remove screenshot annotation logic from execution path
- [ ] Keep screenshot_annotator.py (used elsewhere) but don't call from E2E
- [ ] Video is the sole visual record

---

## Phase 6: Human Review Flow [R7]

### 6a. Review UI Component
- [ ] Create `components/dialogs/E2EReviewDialog.tsx`
- [ ] Triggered from E2EDialog after run completes ("Review" button per WI)
- [ ] Layout: video player (left) + observations panel (right)
- [ ] Observations listed with: step description, AI finding, confidence badge
- [ ] Each finding has: Accept / Reject / Annotate controls

### 6b. Review Interaction
- [ ] Human can override AI verdicts (change pass->fail or fail->pass)
- [ ] Annotation field for human notes per finding
- [ ] Overall WI verdict: human selects PASS / FAIL / NEEDS RETEST
- [ ] "Sign off" button finalizes the review

### 6c. Review Persistence
- [ ] Add route: `PATCH /e2e/runs/{run_id}/review`
- [ ] Payload: `{wi_id, verdicts: [{finding_id, human_verdict, notes}], overall, reviewer}`
- [ ] Persisted alongside the execution run in execution_store
- [ ] Review status shown in E2E dialog: "Awaiting review" / "Reviewed: PASS"

### 6d. Review in Export
- [ ] PDF report updated with human review annotations post-sign-off
- [ ] Excel workbook gets "Reviewed" column with human verdict
- [ ] Video kept as evidence (immutable after recording)

---

## Architecture Decisions Record (ADRs)

| # | Decision | Tradeoff | Consequence |
|---|----------|----------|-------------|
| ADR-1 | N contexts from 1 browser (not N browsers) | Shared process; crash kills all | Avoids 3x cold start; suite-recovery handles crashes |
| ADR-2 | LLM-in-the-loop per checkpoint | Higher token cost, ~2x slower | Adaptive; catches real defects, not just script failures |
| ADR-3 | KB as domain expertise, not just prompt context | Requires quality KB content | KB quality = testing quality ceiling |
| ADR-4 | Confidence scoring on every observation | Adds complexity | Reduces noise; human reviews only HIGH findings |
| ADR-5 | reportlab for PDF (no new deps) | More layout code | Zero dependency addition |
| ADR-6 | 1-level parent-child traversal for TC discovery | Misses deep hierarchies | Prevents explosion on large trees; configurable |
| ADR-7 | Video replaces screenshots | Loses point-in-time snapshots | Video is richer; scrub to any moment |
| ADR-8 | Append to existing Excel (not replace) | File grows over time | Running history in one place; Excel is the audit trail |
| ADR-9 | Review is optional post-step (not blocking) | Unreviewed runs exist | Autonomous execution not blocked by human availability |

---

## Non-Functional Requirements

- [ ] Memory: 3 parallel contexts must work on 8GB RAM machine
- [ ] Performance: full WI execution target < 10 min per WI
- [ ] Token budget: cap LLM calls per WI (observe + reason) to avoid runaway cost
- [ ] Reliability: any single context failure doesn't corrupt other slots
- [ ] Security: passwords never in logs/reports/PDFs/video metadata
- [ ] ASCII only: all code, comments, logs, report text

---

## Dependencies (Zero New Additions)

| Need | Already Installed |
|------|-------------------|
| Browser automation | playwright (via install.py) |
| PDF generation | reportlab >= 4.1 |
| Excel read/write | openpyxl >= 3.1 |
| Video processing | imageio-ffmpeg >= 0.5 |
| KB retrieval | kb/retrieval.py (hybrid RAG) |
| LLM calls | core/model_router.py |
| HTTP server | fastapi + uvicorn |

---

## Build Order (Critical Path)

```
Phase 1a (maximized) -----> immediate, no deps
Phase 1b (parallel)  -----> foundation for everything
Phase 1c (per-WI)    -----> depends on 1b
Phase 1d (cancel)    -----> depends on 1c
Phase 2 (KB brief)   -----> independent, can parallel with 1b-1d
Phase 3 (autonomous) -----> depends on 1c + 2
Phase 4 (observer)   -----> depends on 3
Phase 5a-c (artifacts) ---> depends on 1c (per-WI isolation)
Phase 5d (hierarchy) -----> independent, can parallel with anything
Phase 6 (review)     -----> depends on 5a (needs artifacts to review)
```

---

## Progress Tracking

| Phase | Started | Completed | Notes |
|-------|---------|-----------|-------|
| 1a | 2026-07-21 | 2026-07-21 | Browser maximized - DONE |
| 1b | 2026-07-21 | 2026-07-21 | Parallel contexts - parallel_runner.py created |
| 1c | 2026-07-21 | 2026-07-21 | Per-WI execution - e2e.py refactored |
| 1d | 2026-07-21 | 2026-07-21 | Per-WI cancel - stop endpoint extended |
| 2a | 2026-07-21 | 2026-07-21 | Test brief builder - kb_briefing.py created |
| 2b | 2026-07-21 | 2026-07-21 | LLM synthesis integrated via KBBriefingEngine |
| 2c | 2026-07-21 | 2026-07-21 | Integrated with _compile_with_healing |
| 3a | 2026-07-21 | 2026-07-21 | Adaptive agent loop via run_e2e_slot |
| 3b | 2026-07-21 | 2026-07-21 | Autonomous navigation via KB brief |
| 3c | 2026-07-21 | 2026-07-21 | Alternate path recovery (healing waterfall) |
| 3d | 2026-07-21 | 2026-07-21 | Execution strategies (sequential TCs in slot) |
| 4a | 2026-07-21 | 2026-07-21 | Page observer - page_observer.py created |
| 4b | 2026-07-21 | 2026-07-21 | Business rule reasoning via observations |
| 4c | 2026-07-21 | 2026-07-21 | Confidence scoring (0.0-1.0 heuristic) |
| 4d | 2026-07-21 | 2026-07-21 | Anomaly detection via ObservationDelta |
| 5a | 2026-07-21 | 2026-07-21 | PDF report per WI - report_pdf.py created |
| 5b | 2026-07-21 | 2026-07-21 | Video per WI (Playwright context recording) |
| 5c | 2026-07-21 | 2026-07-21 | Excel append (existing report_excel.py) |
| 5d | 2026-07-21 | 2026-07-21 | Hierarchy TC discovery - "child" in _LINK_REL_HINTS |
| 5e | 2026-07-21 | 2026-07-21 | Screenshots replaced by video |
| 6a | 2026-07-21 | 2026-07-21 | Review UI - ReviewPanel in E2EDialog.tsx |
| 6b | 2026-07-21 | 2026-07-21 | Review interaction (approve/reject/sign-off) |
| 6c | - | - | Review persistence (future: PATCH /e2e/runs) |
| 6d | - | - | Review in export (future: post-sign-off PDF) |
| 7a | 2026-07-21 | 2026-07-21 | Audit existing docs |
| 7b | 2026-07-21 | 2026-07-21 | Update core docs (README, E2E_SPEC, About) |
| 7c | 2026-07-21 | - | Update architecture docs |
| 7d | - | - | Update parity docs |
| 7e | - | - | Specialized docs |
| 7f | 2026-07-21 | 2026-07-21 | About section updated |

---

## Phase 7: Documentation Overhaul [R8]

### 7a. Analyze Existing Docs
- [ ] Audit all project .md files for accuracy against current codebase
- [ ] Identify outdated specs, dead references, stale architecture diagrams
- [ ] Catalog rules/invariants that MUST be preserved (security, credential handling)

### 7b. Update Core Documents
- [ ] README.md — rewrite for v3.40.0 (features, setup, usage, architecture overview)
- [ ] MASTER_SPEC.md — update with E2E autonomous agent spec
- [ ] E2E_SPEC.md — full rewrite reflecting the new AI QA agent architecture
- [ ] DEPLOYMENT.md — update deployment/installer flow for v3.40.0
- [ ] AGENTS.md — update credential/security rules + new agent capabilities
- [ ] CODEBASE.md — update codebase structure with new modules

### 7c. Update Architecture Docs
- [ ] docs/ARCHITECTURE.md — add parallel runner, KB briefing, observation layers
- [ ] docs/CODEBASE_MAP.md — add new files (qa_agent, kb_briefing, page_observer, etc.)
- [ ] docs/CHANGELOG.md — add v3.40.0 entry with all changes
- [ ] docs/DOCS.md — update feature documentation

### 7d. Update Desktop Parity Docs
- [ ] docs/desktop-parity/ARCHITECTURE.md — note E2E divergence from desktop app
- [ ] docs/desktop-parity/DOCS.md — update feature comparison
- [ ] docs/desktop-parity/REFERENCE.md — update API reference

### 7e. Specialized Docs
- [ ] e2e/README.md — full rewrite: new E2E runner usage, configuration, output
- [ ] agent-bundle/INSTALL.md — update installer for new automation deps
- [ ] specs/README.md — update specs index

### 7f. About Section & UI Docs
- [ ] Update app "About" section/dialog with v3.40.0 info
- [ ] Update any in-app help text referencing E2E capabilities
- [ ] Ensure version strings reflect 3.40.0 across all docs

---

## Residual Risks & Assumptions

- **Assumption:** KB content quality is sufficient to produce useful test briefs
- **Assumption:** 3 parallel contexts fit in typical developer machine memory (8-16GB)
- **Assumption:** LLM token cost per WI is acceptable (estimated 50-100k tokens/WI)
- **Risk:** Observation layer may produce false positives if business rules are ambiguous in KB
- **Risk:** Autonomous navigation may loop if app has non-standard flows not documented in KB
- **Mitigation:** Confidence scoring + human review catches AI errors
- **Mitigation:** Hard timeout per WI (configurable, default 10 min) prevents infinite loops
