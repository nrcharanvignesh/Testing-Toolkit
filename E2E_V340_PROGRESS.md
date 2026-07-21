# E2E Runner v3.40.0 — Progress Tracker (Local State)

Last updated: 2026-07-21

## All Implementation Phases COMPLETE (1-6)

### Phase 1: Foundation (Browser + Parallel Infra) [DONE]

| Sub-phase | File(s) | Summary |
|-----------|---------|---------|
| 1a: Browser Maximized | `automation/playwright_bridge.py` | `--start-maximized` + `no_viewport=True`, 1920x1080 video |
| 1b: Parallel Runner | `automation/parallel_runner.py` (NEW) | 1 Browser, N contexts, per-WI cancel, batch execution |
| 1c: Per-WI Execution | `agent/routes/e2e.py` | `_group_by_wi()`, `_run_parallel()`, sequential fallback |
| 1d: Per-WI Cancel | `agent/routes/e2e.py` | `stop_e2e(job_id, wi_id)`, `_wi_stops` dict |

### Phase 2: KB Briefing Engine [DONE]

| Sub-phase | File(s) | Summary |
|-----------|---------|---------|
| 2a: Test Brief Builder | `automation/kb_briefing.py` (NEW) | KBBriefingEngine, TestBrief dataclass |
| 2b: LLM Synthesis | `automation/kb_briefing.py` | HybridRetriever + ProjectContext integration |
| 2c: Execution Integration | `agent/routes/e2e.py` | `_compile_with_healing` accepts `kb_brief` |

### Phase 3: Autonomous Execution [DONE]

| Sub-phase | File(s) | Summary |
|-----------|---------|---------|
| 3a: Slot-based Agent Loop | `automation/e2e_runner.py` | `run_e2e_slot()` for ParallelRunner slots |
| 3b: Autonomous Navigation | `automation/kb_briefing.py` | navigation_hints from KB workflows |
| 3c: Alternate Path Recovery | `automation/healing_guardrails.py` | 6-strategy waterfall + LLM recompile |
| 3d: Execution Strategies | `agent/routes/e2e.py` | Sequential TCs within parallel WI slots |

### Phase 4: Smart Observation [DONE]

| Sub-phase | File(s) | Summary |
|-----------|---------|---------|
| 4a: Page Observer | `automation/page_observer.py` (NEW) | a11y tree capture, URL, signals |
| 4b: Business Rule Reasoning | `automation/page_observer.py` | Error/success/loading signal detection |
| 4c: Confidence Scoring | `automation/page_observer.py` | 0.0-1.0 heuristic based on signals |
| 4d: Anomaly Detection | `automation/page_observer.py` | ObservationDelta (new_errors, etc.) |

### Phase 5: Artifacts & TC Discovery [DONE]

| Sub-phase | File(s) | Summary |
|-----------|---------|---------|
| 5a: PDF Report | `automation/report_pdf.py` (NEW) | Per-WI PDF with reportlab |
| 5b: Video per WI | `automation/parallel_runner.py` | Context-level Playwright recording |
| 5c: Excel Append | `agent/routes/e2e.py` | Groups results by wi_id, appends |
| 5d: Hierarchy TC Discovery | `ado/boards.py` | "child" added to `_LINK_REL_HINTS` |
| 5e: Remove Screenshots | `automation/e2e_runner.py` | Video replaces per-step screenshots |

### Phase 6: Human Review Flow [DONE]

| Sub-phase | File(s) | Summary |
|-----------|---------|---------|
| 6a: Review UI | `components/dialogs/E2EDialog.tsx` | ReviewPanel component |
| 6b: Review Interaction | `components/dialogs/E2EDialog.tsx` | Per-TC approve/reject, sign-off |
| 6c: Review Persistence | — | Future: PATCH /e2e/runs/{run_id}/review |
| 6d: Review in Export | — | Future: post-sign-off PDF annotation |

### Phase 7: Documentation Overhaul [IN PROGRESS]

| Sub-phase | Status | Summary |
|-----------|--------|---------|
| 7a: Audit existing docs | DONE | Identified all .md files and AboutDialog |
| 7b: Update core docs | DONE | README, E2E_SPEC, CODEBASE.md updated |
| 7c: Architecture docs | IN PROGRESS | docs/ARCHITECTURE.md pending |
| 7d: Parity docs | PENDING | docs/desktop-parity/ |
| 7e: Specialized docs | PENDING | e2e/README.md |
| 7f: About section | DONE | AboutDialog.tsx rewritten for v3.40.0 |

## Key Architecture Decisions

| ADR | Decision | Tradeoff |
|-----|----------|----------|
| ADR-1 | N contexts from 1 browser | Shared process; crash kills all vs 3x cold start |
| ADR-2 | Per-WI grouping over per-TC parallelism | TCs share login state within WI |
| ADR-3 | Sequential fallback for N=1 | Zero regression for common case |
| ADR-4 | KB brief over selective context | Requires quality KB but richer grounding |
| ADR-5 | reportlab for PDF (no new deps) | More layout code, zero dep addition |
| ADR-6 | 1-level parent-child traversal | Misses deep hierarchies, prevents explosion |
| ADR-7 | Video replaces screenshots | Richer; scrub to any moment |
| ADR-8 | Append to existing Excel | File grows; running history in one place |
| ADR-9 | Review is optional post-step | Autonomous execution not blocked by human |

## File Changes (Complete List)

| File | Action | Phase |
|------|--------|-------|
| `automation/parallel_runner.py` | CREATED | 1b |
| `automation/kb_briefing.py` | CREATED | 2a |
| `automation/page_observer.py` | CREATED | 4a |
| `automation/report_pdf.py` | CREATED | 5a |
| `automation/e2e_runner.py` | MODIFIED | 3a (run_e2e_slot + observer integration) |
| `automation/playwright_bridge.py` | MODIFIED | 1a (maximized) |
| `agent/routes/e2e.py` | MODIFIED | 1c, 1d, 2c, 5c (major refactor) |
| `ado/boards.py` | MODIFIED | 5d ("child" in _LINK_REL_HINTS) |
| `components/dialogs/E2EDialog.tsx` | MODIFIED | 6a, 6b (ReviewPanel) |
| `components/dialogs/AboutDialog.tsx` | MODIFIED | 7f |
| `README.md` | MODIFIED | 7b |
| `E2E_SPEC.md` | MODIFIED | 7b |
| `CODEBASE.md` | MODIFIED | 7b |
| `E2E_RUNNER_V3.40.md` | MODIFIED | 7b (progress tracking) |
| `E2E_V340_PROGRESS.md` | MODIFIED | 7a (this file) |

## Remaining Work

1. Phase 7c-7e: Update docs/ARCHITECTURE.md, docs/desktop-parity/, e2e/README.md
2. Version bump to 3.40.0 (package.json, version.py, agent-version.ts)
3. Parts branch manifest update with new files
4. E2E integration testing
5. Phase 6c-6d: Review persistence + export (deferred to future release)
