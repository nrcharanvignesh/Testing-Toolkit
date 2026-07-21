# E2E SPEC: Autonomous AI QA Agent (BINDING)

## AUTHORITY AND PRECEDENCE

This is binding behavioral law for the `automation/` package, the
product's flagship feature. CLAUDE_EVIDENCE_PROTOCOL.md governs HOW every
requirement here must be verified before being called complete - read it
in full first. If any requirement here ever conflicts with Claude's core
safety guidelines or Anthropic's usage policies, THE SAFETY GUIDELINES
WIN, without exception. This document has authority over feature
behavior ONLY.

## 0. What this feature is, in one sentence

Given a set of work items (from ANY supported source - Section 1.1 of
MASTER_SPEC.md) or manually authored test cases, the autonomous AI QA
agent WILL study the project knowledge base, discover test cases via
parent-child WI hierarchy, execute up to 3 user stories in parallel with
fully isolated browser contexts, observe page state after every action,
self-heal when the target UI has drifted, and produce per-WI PDF reports
with video recordings and Excel audit trail - all without human input
until final review sign-off.

Grounded in the real modules already in this codebase
(automation/agentic_tools.py, agentic_prompt.py, agentic_runner.py,
e2e_runner.py, parallel_runner.py, kb_briefing.py, page_observer.py,
report_pdf.py, script_generator.py, playwright_bridge.py,
healing_guardrails.py, report_excel.py). This document is the definition
of "correct and complete" for that code - not a wishlist.

**Architecture (v3.50):** The E2E system uses an LLM-in-the-loop agentic
architecture. The LLM observes page state after each action, decides the
next action in real-time, and self-corrects. There is NO upfront plan
compilation. Core modules: agentic_tools.py (35 tools + self-healing
locator factory), agentic_prompt.py (system prompt builder),
agentic_runner.py (the loop + suite orchestrator). The old
compile-then-execute path (e2e_plan.py) is retained for reference but no
longer invoked by the route.

---

## 1. Inputs

1. A project plus one or more work items from ANY supported source
   (Azure DevOps or Jira), OR a manually authored test case.
2. KB context for the project - used to ground step generation in real
   field names, real screen names, real workflows. Hallucinated field
   names are FORBIDDEN when grounded context exists.
3. Environment target: base URL and credentials, via the existing
   credential vault ONLY. Plaintext credentials anywhere in this pipeline
   are FORBIDDEN.
4. Existing template/pattern library, when the project has run E2E
   before - the system WILL learn from prior successful selectors/flows
   rather than regenerating from zero every run.
5. Parent-child WI hierarchy: when the selected WI is a User Story,
   Feature, or Epic, the system WILL traverse child links to discover
   associated Test Case work items automatically (R6).

## 2. Pipeline stages - EVERY REQUIREMENT BELOW IS MANDATORY

### Stage A - Plan compilation (e2e_plan.py)

- Converts test-case steps into a structured, ordered plan of
  {action, target description, expected outcome} objects.
- MUST have a bounded, defined, type-hinted retry constant for
  LLM-based compilation. An undefined or unbounded retry loop is
  FORBIDDEN, without exception - this exact defect has already produced a
  hard crash blocking every E2E run at the first step tonight.
- On compilation failure after retries: fail loudly, naming the specific
  test case and step that failed. Silently skipping a test case and
  continuing as if it passed is FORBIDDEN.
- Compiled plans MUST be inspectable/loggable before execution so a human
  can review intent before the AI acts on it.

### Stage B - Script generation (script_generator.py)

- Translates the compiled plan into Playwright actions.
- MUST prefer stable, user-facing locators in this order:
  `get_by_role`, `get_by_label`, `get_by_text`. Preferring CSS selectors
  or XPath over these when a user-facing locator is available is
  FORBIDDEN - this is the actual mechanical difference between
  "human-like" and "brittle," and it WILL be enforced as a review rule on
  every generated script.
- WILL use exact field/button/screen names from KB context when
  available, rather than guessing from a test case's natural-language
  phrasing.
- Every generated action MUST carry a plain-language intent description
  alongside its technical locator - this becomes the human-readable trace
  in the final report and the self-healing diagnostic.

### Stage C - Execution (e2e_runner.py + playwright_bridge.py)

- MUST run in Playwright's own bundled, isolated Chromium with a
  dedicated automation profile. Running in the user's real installed
  browser or the user's real profile directory is FORBIDDEN, absolutely,
  without exception - this is a hard security/isolation requirement, not
  a preference, and a real production failure has already resulted from
  violating it.
- The automation browser is fully owned by the run: launched fresh (or
  reattached only within its own dedicated profile) and explicitly killed
  on exit, success or failure. Leaving a process running as a side effect
  is FORBIDDEN.
- Before every launch, the system WILL sweep for and kill orphaned
  processes - identified ONLY by executable path under the automation's
  own bundled browser cache directory. Killing by port alone or by
  generic process name is FORBIDDEN - path-based identification only,
  making it structurally impossible to ever touch the user's real
  browser by accident.
- Headless by default for unattended execution. Headed mode is an
  explicit opt-in for debugging only, NEVER the default for an automated
  run.
- CDP connection retries MUST use real backoff with a window tuned to
  actual observed browser startup variance on this machine - a fixed
  "3 attempts" that has already been observed failing in practice MUST be
  re-tuned based on real timing data, not assumed sufficient.

### Stage D - Self-healing ("human-like" behavior) (healing_guardrails.py)

This is the literal, enforceable definition of "human-like" for this
feature:

- If a primary locator fails, the system WILL fall back through a defined
  strategy order (e.g. exact text -> partial text -> role+nearby-label)
  BEFORE declaring a step failed - mirroring how a human tester
  visually re-locates a moved or reworded element.
- Waits for async/loading content with a real, bounded timeout and
  visible progress. Failing instantly or hanging forever are both
  FORBIDDEN.
- If the fallback chain is exhausted: the step MUST be marked FAILED with
  the exact locators attempted and a failure-point screenshot. Silently
  marking it passed or silently skipping it is FORBIDDEN, absolutely -
  this is the same failure class (a genuine failure indistinguishable
  from a success) already found and fixed once elsewhere in this
  codebase tonight (the board-fetch degradation-signal fix). It is
  FORBIDDEN to reintroduce that exact class of ambiguity here.
- Any time a fallback strategy succeeds, log it distinctly (e.g. "PASSED
  VIA FALLBACK: partial text match"), never identically to a clean pass -
  this is valuable UI-drift signal, not noise to suppress.

### Stage E - Artifact collection and reporting

- Every run produces, per work item:
  - One PDF report (steps + AI observations + confidence scores)
  - One video recording (Playwright context-level, full session)
  - Excel row append to the project's E2E results workbook
- Video is the sole visual artifact - per-step screenshots are replaced
  by continuous video recording.
- The report MUST distinguish, per test case: PASSED / PASSED VIA
  FALLBACK / FAILED (with reason and evidence) / BLOCKED
  (environment/setup issue, distinct from a genuine test failure).
- Output path: single, deterministic location. Duplication across two
  folders is FORBIDDEN (Section 4.3 of MASTER_SPEC.md).

### Stage F - Parallel execution (parallel_runner.py)

- ONE shared Browser process, up to 3 independent BrowserContexts.
- Each context: own cookies, own storage state, own video directory.
- Work items execute in parallel; test cases within a WI execute
  sequentially (shared login state).
- Per-WI cancellation: any single WI can be stopped mid-run without
  affecting other slots.
- Sequential fallback: if N=1, use the existing single-context path
  (zero regression risk).
- Error isolation: one context crash does not kill others.

### Stage G - KB Briefing (kb_briefing.py)

- Before plan compilation, the KB Briefing Engine builds a TestBrief:
  - Screens (from ProjectContext.screens)
  - Preconditions (test_data_needs + actors)
  - Business rules
  - Navigation hints (workflows)
- The brief is authoritative context for the plan compiler (preferred
  over selective KB retrieval).
- Falls back gracefully when KB has no results for the query.

### Stage H - Page observation (page_observer.py)

- After every significant action, captures full page state via a11y tree.
- Detects: error signals, loading states, success indicators.
- Produces ObservationDelta (what changed between before/after).
- Confidence scoring (0.0-1.0) based on signal clarity.
- Feeds anomalies into the PDF report as AI observations.

### Stage I - Human review flow (E2EDialog ReviewPanel)

- Post-execution: per-TC approve/reject controls with visual state.
- Video playback alongside AI observations panel.
- Sign-off button (enabled only when all TCs reviewed).
- Review is optional - does not block autonomous execution.

## 3. Reliability requirements - "RUNS NO MATTER WHAT" IS LITERAL

- MUST be triggerable fully unattended - scheduled or batch - with zero
  requirement for a human to babysit browser windows or click through
  prompts.
- Every external dependency call (source fetch, KB lookup, LLM call) MUST
  have bounded retry with backoff and MUST surface a distinguishable
  error on exhaustion. A silent empty result indistinguishable from
  "nothing to do" is FORBIDDEN - this is the single most important
  lesson from tonight's board-export investigation and it applies here
  with equal force: a genuinely empty test suite and a suite that failed
  to load must never look the same.
- A single test case's failure MUST NOT crash the whole run. Each test
  case executes in isolation; failures are caught, logged with full
  context, and the runner proceeds.
- The run MUST produce a report even if killed partway through - partial
  results with explicit "run incomplete" framing, never silent total
  loss.

## 4. Non-goals / explicit boundaries

- NOT responsible for defeating CAPTCHA or genuinely adversarial
  anti-automation defenses - correct behavior on encountering these is a
  clear "environment not automatable" failure, never an attempt to defeat
  them.
- NOT responsible for generating test cases from zero input - it compiles
  and executes existing test cases; authorship is a separate concern.
- MUST NOT modify the AUT's data destructively without the user's
  explicit environment choice - never assume it is safe to run against a
  real production data source without the user having intentionally
  selected that environment.
- MUST remain fully agnostic across operating system, work-item source
  (Azure DevOps and Jira), and target application under test. No
  client-specific, project-specific, or app-specific field name, URL, or
  screen name may be hardcoded anywhere in the plan compiler, script
  generator, or runner - every such value MUST be derived from that run's
  actual KB context and test case input. An implementation detail that
  only works for one specific client's app, one specific board source, or
  one specific OS is a DEFECT, regardless of how thoroughly it was tested
  against the one case in front of the implementer.

## 5. Acceptance criteria - WHAT "DONE" LITERALLY MEANS FOR THIS FEATURE

- [ ] A real run, triggered normally, completes end-to-end against a real
      login-gated environment with zero manual browser intervention and
      zero interference with a separately open user browser session -
      VERIFIED by running one with the user's real browser open at the
      same time, not merely architecturally reasoned.
- [ ] A test case with a since-changed UI element is caught and passed
      via a logged fallback strategy, never silently failed, never
      silently passed without note.
- [ ] A genuinely broken step (element removed entirely) is reported
      FAILED with screenshot and exact locator history - never as 0
      results, never silently skipped.
- [ ] The final report clearly separates PASSED / PASSED VIA FALLBACK /
      FAILED / BLOCKED, each with evidence.
- [ ] Running the same suite twice back-to-back, zero manual cleanup in
      between, succeeds both times - proving isolation is real, not
      theoretical.
- [ ] Killing the run mid-way still produces a partial report.

A task claiming this feature is "done" without every checkbox above
independently verified, per CLAUDE_EVIDENCE_PROTOCOL.md, is a violation of
that Protocol's Section 1 (labeling discipline) and Section 3 (tests are
not proof).

## 6. Open questions - MUST BE RESOLVED, NOT ASSUMED, BEFORE CLAIMING FULL COMPLETION

- Does first use of the new isolated automation profile require a
  one-time manual login step for apps requiring real SSO/MFA? If so, this
  MUST be documented as an explicit onboarding step, never discovered as
  a surprise on first run.
- What is the actual, tuned CDP-connection retry count and backoff window
  based on THIS machine's real observed browser startup time - not a
  value already observed insufficient once in production?
- Is there a defined maximum run duration / step timeout, and what
  happens to in-flight browser resources if it is exceeded?

These questions are not rhetorical. They MUST be answered with evidence
before this feature is reported as fully complete.
