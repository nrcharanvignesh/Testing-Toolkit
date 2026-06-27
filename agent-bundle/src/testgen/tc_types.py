"""
tc_types.py
The three test-case generation phases the action bar exposes:
Implementation, SIT (System Integration Testing), and UAT (User
Acceptance Testing).

Each phase has:
  * a stable key (used in filenames and stored prompt file names),
  * a short display label (used on buttons and dialog titles),
  * a default system prompt that EXTENDS the canonical strict TC contract
    (ado_testcase_creator.SYSTEM_PROMPT) with a phase-specific preamble.

The preamble only steers WHICH behaviors to cover and HOW to phrase
steps; the canonical schema, determinism rules, and category vocabulary
are reused verbatim so the generated JSON still validates and round-trips
through the reviewer Excel and the ADO creator unchanged.

NOTE: the change request referenced an attached system prompt for these
phases, but the uploaded .docx carried only the two annotated UI
screenshots - no prompt document. The defaults below are sensible,
phase-appropriate starting points; tune them per project in Project KB
(each phase has its own editable prompt).
"""

from __future__ import annotations

from typing import Final

from ado.testcase_creator import SYSTEM_PROMPT as _CANONICAL_PROMPT

# Stable keys (also used as filename infixes and prompt file suffixes).
TC_IMPLEMENTATION: Final[str] = "implementation"
TC_SIT: Final[str] = "sit"
TC_UAT: Final[str] = "uat"

TC_TYPES: Final[tuple[str, ...]] = (TC_IMPLEMENTATION, TC_SIT, TC_UAT)

# Button / dialog labels.
DISPLAY_NAMES: Final[dict[str, str]] = {
    TC_IMPLEMENTATION: "Implementation",
    TC_SIT: "SIT",
    TC_UAT: "UAT",
}

# Short button captions for the action bar.
BUTTON_LABELS: Final[dict[str, str]] = {
    TC_IMPLEMENTATION: "Implementation",
    TC_SIT: "SIT",
    TC_UAT: "UAT",
}


_IMPLEMENTATION_PREAMBLE: Final[str] = r"""
TEST PHASE: IMPLEMENTATION (developer / functional verification)
================================================================
You are authoring Implementation-phase test cases - the checks a build
team runs against a single application/component as features are
implemented, before system integration. Audience: developers and QA
engineers verifying the feature behaves to spec in isolation.

EMPHASIS FOR THIS PHASE
- Functional correctness of each acceptance criterion in the story
  (one "Positive" TC per criterion).
- Field-level input validation and boundaries ("Data Validation"),
  and invalid-input handling where the requirements define an error
  ("Negative" / "Error Handling").
- Direct backend calls the feature makes ("API Validation" happy path;
  "Error Handling" for documented failures).
- UI state, labels, defaults, and visible controls for the feature
  ("GUI Validation").
- Keep each TC scoped to THIS component; do not assume behavior of
  upstream/downstream systems unless the requirements state it.
- Prefer categories: Positive, Negative, Data Validation, GUI
  Validation, Error Handling, API Validation.
""".strip()


_SIT_PREAMBLE: Final[str] = r"""
TEST PHASE: SIT - SYSTEM INTEGRATION TESTING
============================================
You are authoring System Integration Testing test cases - the checks
that verify this work item's behavior ACROSS integrated systems and
interfaces, end to end. Audience: integration testers validating that
modules, services, and external systems work together.

EMPHASIS FOR THIS PHASE
- End-to-end flows that cross module / service / system boundaries:
  data produced by one system is consumed correctly by the next
  ("Integration", "Data Validation").
- Interface and contract validation between systems, including request
  and response payloads ("API Validation"), and documented failure /
  timeout / retry behavior ("Error Handling").
- Data integrity and state consistency after a flow completes across
  systems ("Data Validation", "Regression").
- Re-verification that an integration change did not break adjacent,
  previously working flows ("Regression").
- Each TC should name the systems / interfaces involved and the data
  handed between them, using only the integration points stated in the
  inputs.
- Prefer categories: Integration, API Validation, Data Validation,
  Error Handling, Regression.
""".strip()


_UAT_PREAMBLE: Final[str] = r"""
TEST PHASE: UAT - USER ACCEPTANCE TESTING
=========================================
You are authoring User Acceptance Testing test cases - the checks a
business user runs to confirm the delivered feature meets real-world
business needs. Audience: business users / product owners signing off
the release. Steps must read in plain business language, describing what
the user does and sees - not internal field names, APIs, or technical
jargon.

EMPHASIS FOR THIS PHASE
- Real business scenarios mapped directly to the story's acceptance
  criteria, framed as the journey a user actually performs
  (category "UAT", with "Positive" for the core happy path).
- The visible user experience: screens, labels, prompts, confirmations,
  and outcomes the user can observe ("GUI Validation").
- Business-rule outcomes the user must be able to trust (correct
  totals, statuses, approvals, notifications).
- Accessibility expectations ONLY if the requirements reference them
  ("Accessibility").
- Do NOT write low-level unit, API-payload, or code-path checks - those
  belong to earlier phases. Keep every TC at the level a non-technical
  user can execute and judge.
- Prefer categories: UAT, Positive, GUI Validation, Accessibility.
- Set category "UAT" on the primary acceptance scenarios.
""".strip()


_PREAMBLES: Final[dict[str, str]] = {
    TC_IMPLEMENTATION: _IMPLEMENTATION_PREAMBLE,
    TC_SIT: _SIT_PREAMBLE,
    TC_UAT: _UAT_PREAMBLE,
}


def is_valid(tc_type: str) -> bool:
    return tc_type in TC_TYPES


def display_name(tc_type: str) -> str:
    return DISPLAY_NAMES.get(tc_type, tc_type.upper())


def button_label(tc_type: str) -> str:
    return BUTTON_LABELS.get(tc_type, str(tc_type).title())


def default_prompt(tc_type: str) -> str:
    """Phase-specific default system prompt: a steering preamble followed
    by the canonical strict TC contract (schema + determinism + step
    rules), so generated JSON stays schema-valid for every phase."""
    preamble = _PREAMBLES.get(tc_type)
    if not preamble:
        return _CANONICAL_PROMPT
    return f"{preamble}\n\n{_CANONICAL_PROMPT}"
