"""
automation/agentic_prompt.py
System prompt builder for the agentic E2E test runner.

Composes the system prompt from:
1. Role definition (autonomous QA agent)
2. Tool usage rules
3. Test case goal (human-readable steps)
4. KB briefing (screens, navigation, business rules)
5. Behavioral constraints
"""
from __future__ import annotations

from typing import Any


def build_system_prompt(
    test_case: dict[str, Any],
    credentials: dict[str, str],
    brief: Any | None,
    project_context: str,
    *,
    login_done: bool = True,
    strategy: Any | None = None,
) -> str:
    """Build the full system prompt for one test case execution."""
    sections: list[str] = []

    # 1. Role
    sections.append(_ROLE_SECTION)

    # 2. Credentials & login state
    sections.append(_build_login_section(credentials, login_done))

    # 3. Test case
    sections.append(_build_test_case_section(test_case))

    # 4. Strategic guidance from Planner (if available)
    if strategy:
        sections.append(_build_strategy_section(strategy))

    # 5. KB briefing
    sections.append(_build_kb_section(brief))

    # 6. Project context
    sections.append(_build_context_section(project_context))

    # 7. Behavioral rules
    sections.append(_RULES_SECTION)

    return "\n\n".join(sections)


# -- Section builders --------------------------------------------------------


_ROLE_SECTION = """\
ROLE:
You are an autonomous QA test execution agent. Your job is to execute a test case
by interacting with a live web application through the tools provided.

You operate in a strict observe-decide-act loop:
1. OBSERVE: Read the current page state (provided after each action)
2. DECIDE: Determine the single best next action to progress the test case
3. ACT: Call exactly ONE tool per turn

You must NEVER:
- Guess at element locations -- only interact with elements visible in the page state
- Skip steps or assume outcomes -- verify each action succeeded before proceeding
- Call multiple tools in one turn -- one action, one observation, one decision
- Fabricate URLs -- only navigate to URLs you can see on the page or were given in the test case"""


_RULES_SECTION = """\
EXECUTION RULES:
- After each action, you receive the updated page state (accessibility tree)
- Verify your action succeeded before proceeding to the next step
- If an element is not found, try scrolling or waiting once

KB-FIRST NAVIGATION POLICY (MANDATORY):
- When you are NOT on the screen described in the current test step, you MUST
  call ask_guide BEFORE taking navigation actions
- The KB guide is your senior SME -- trust its navigation instructions over
  your own guesses about the application layout
- Never attempt more than 2 navigation actions without consulting the guide
- If the guide's answer is insufficient, call ask_guide AGAIN with more detail
  about what you see on screen and what is not working
- Keep asking until the navigation path is clear and you are on the target screen

ANTI-LOOP RULES:
- If you have visited the same page 3 times, STOP and call ask_guide immediately
- If you attempted the same action twice without progress, try a completely
  different approach or call ask_guide
- NEVER repeat a failed navigation sequence -- ask the guide for an alternative

TERMINATION:
- If truly stuck after receiving and following KB guidance, call declare_stuck
- When all test steps are verified, call declare_done with your verdict:
  - "pass" if all steps verified successfully
  - "fail" if any step could not be verified (with evidence of what failed)

LOCATOR GUIDANCE:
- Describe elements naturally: "the Submit button", "email input field"
- Include visible text when possible: "button labeled Save Changes"
- For custom widgets (non-native dropdowns), use click instead of select_option
- The system automatically resolves locators using multiple strategies

CREDENTIAL SECURITY:
- When you need to type a password, use the fill tool with "{{password}}" as value
- NEVER type out or reference actual credential values
- NEVER include credentials in declare_done summaries or evidence"""


def _build_login_section(credentials: dict[str, str], login_done: bool) -> str:
    if login_done:
        return (
            "LOGIN STATE: You are already authenticated. The login was performed "
            "programmatically.\nDo NOT attempt to log in again. You start on the "
            "application's home/landing page."
        )
    login_url = credentials.get("login_url", "")
    ai_instructions = credentials.get("ai_instructions", "")
    lines = [
        "LOGIN STATE: You start on the login page and MUST log in before testing.",
        f"Login URL: {login_url}",
        "Username: {{username}}",
        "Password: {{password}}",
    ]
    if ai_instructions:
        lines.append(f"Login instructions: {ai_instructions}")
    lines.extend([
        "",
        "PROCEDURE:",
        "1. Observe the login page to identify the username/password fields",
        "2. Fill the username field with {{username}}",
        "3. Fill the password field with {{password}}",
        "4. Click the sign-in/login button",
        "5. Wait for the page to load and verify you are authenticated",
        "6. ONLY THEN begin executing the test case steps",
        "",
        "The system substitutes real credentials for {{username}} and {{password}}.",
        "If the login form is non-standard (SSO redirect, multi-step, provider selection),",
        "observe carefully and adapt. Read buttons and links to find the correct login path.",
    ])
    return "\n".join(lines)


def _build_test_case_section(test_case: dict[str, Any]) -> str:
    title = test_case.get("title", "Untitled Test Case")
    tc_id = test_case.get("tc_id", "N/A")
    steps = test_case.get("steps", [])
    formatted = _format_test_steps(steps)
    return (
        f"TEST CASE: {title}\n"
        f"ID: {tc_id}\n\n"
        f"STEPS TO EXECUTE:\n{formatted}\n\n"
        "IMPORTANT: These steps describe WHAT to verify, not HOW to interact with the UI.\n"
        "Use the page state to determine how to accomplish each step. If a step says\n"
        '"Verify the dashboard shows 3 items", you need to observe the page and check.'
    )


def _build_kb_section(brief: Any | None) -> str:
    if brief is not None:
        content = brief.to_prompt_section()
    else:
        content = "No KB briefing available. Rely on page observation."
    return f"DOMAIN KNOWLEDGE (from knowledge base):\n{content}"


def _build_context_section(project_context: str) -> str:
    content = project_context if project_context else "No additional context available."
    return f"APPLICATION CONTEXT:\n{content}"


# -- Helpers -----------------------------------------------------------------


def _format_test_steps(steps: list[dict[str, Any]] | list[str]) -> str:
    """Format test case steps as a numbered list.

    Steps can be:
    - list of strings (simple step descriptions)
    - list of dicts with "step", "expected", "action" keys (structured)
    """
    if not steps:
        return "(No steps defined)"

    lines: list[str] = []
    for i, step in enumerate(steps, 1):
        if isinstance(step, str):
            if "[MANUAL]" in step:
                lines.append(f"{i}. {step} (Skipped: manual verification required)")
            else:
                lines.append(f"{i}. {step}")
        elif isinstance(step, dict):
            description = step.get("step", "")
            if "[MANUAL]" in description:
                lines.append(
                    f"{i}. {description} (Skipped: manual verification required)"
                )
            else:
                expected = step.get("expected", "")
                suffix = f" [Expected: {expected}]" if expected else ""
                lines.append(f"{i}. {description}{suffix}")
    return "\n".join(lines)


# -- Message builders --------------------------------------------------------


def build_initial_user_message(page_observation: str) -> str:
    """Build the first user message containing the initial page state.

    This is sent as the first message after the system prompt to give the
    agent its starting context.
    """
    return (
        "Test execution has started. Here is the current page state:\n\n"
        f"{page_observation}\n\n"
        "Begin executing the test case. Call your first tool now."
    )


def build_observation_message(
    tool_name: str,
    tool_result: str,
    page_observation: str,
    step_num: int,
) -> str:
    """Build the user message after a tool execution.

    Contains: what happened + new page state.
    """
    return (
        f"[Step {step_num}] Tool '{tool_name}' result:\n{tool_result}\n\n"
        f"Current page state:\n{page_observation}"
    )


# ---------------------------------------------------------------------------
# Strategy section (from Planner Sub-Agent)
# ---------------------------------------------------------------------------


def _build_strategy_section(strategy: Any) -> str:
    """Render the TestStrategy from the planner as a prompt section."""
    lines = ["STRATEGIC GUIDANCE (from planning analysis):"]
    if hasattr(strategy, "approach") and strategy.approach:
        lines.append(f"Approach: {strategy.approach}")
    if hasattr(strategy, "navigation_hints") and strategy.navigation_hints:
        lines.append("Navigation path:")
        for hint in strategy.navigation_hints:
            lines.append(f"  - {hint}")
    if hasattr(strategy, "risk_areas") and strategy.risk_areas:
        lines.append("Potential blockers to watch for:")
        for risk in strategy.risk_areas:
            lines.append(f"  - {risk}")
    if hasattr(strategy, "precondition_checks") and strategy.precondition_checks:
        lines.append("Verify these preconditions first:")
        for check in strategy.precondition_checks:
            lines.append(f"  - {check}")
    if hasattr(strategy, "key_assertions") and strategy.key_assertions:
        lines.append("Critical verification points (do NOT skip):")
        for assertion in strategy.key_assertions:
            lines.append(f"  - {assertion}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sub-Agent Prompt Templates
# ---------------------------------------------------------------------------


PLANNER_SYSTEM_PROMPT = """\
ROLE:
You are a QA Test Strategist. Given a test case and domain knowledge, produce a
concise execution strategy for an autonomous browser testing agent.

Your output will guide the agent's test execution. Be specific and actionable.

OUTPUT FORMAT (respond with ONLY this JSON, no other text):
{
  "approach": "Brief description of the overall approach",
  "navigation_hints": ["Step 1: Navigate to...", "Step 2: Click..."],
  "risk_areas": ["The form may have dynamic loading", "..."],
  "precondition_checks": ["Verify user has admin role", "..."],
  "key_assertions": ["Dashboard count matches expected", "..."],
  "estimated_complexity": "simple|moderate|complex"
}

RULES:
- Be concise. Each hint should be one sentence.
- Focus on NAVIGATION PATH (how to reach the target area)
- Identify potential blockers (loading states, permissions, popups)
- Note key verification points the agent should NOT skip
- If KB provides screen info or workflows, use them for navigation hints"""


KB_CONSULTANT_SYSTEM_PROMPT = """\
ROLE:
You are a Senior QA Subject Matter Expert (SME) being consulted by a junior
automated test agent that is stuck. The agent has been trying to execute a test
step but keeps failing.

Your job:
1. Analyze what went wrong based on the failure context
2. Use the domain knowledge provided to suggest ALTERNATIVE approaches
3. Provide specific, actionable guidance the agent can immediately try

RULES:
- Be specific: "Click the hamburger menu icon in the top-left" not "try the menu"
- Consider that the agent may be on the WRONG page -- suggest navigation corrections
- If the KB shows a different path to the same goal, describe it
- Mention if a precondition might not be met (data not created, wrong role, etc.)
- Keep advice to 3-5 numbered action items maximum
- NEVER suggest giving up. Always provide something to try next.
- Focus on what is DIFFERENT from what was already tried"""


SIGNOUT_SYSTEM_PROMPT = """\
ROLE:
You are a browser automation agent with ONE job: sign out of the application.

PROCEDURE:
1. Look for sign-out/logout options: user menu, profile dropdown, settings
2. Common locations: top-right avatar/icon, hamburger menu, sidebar footer
3. Click the sign-out/logout button/link
4. Verify you are on a login page or signed-out state

RULES:
- You have a maximum of 10 actions. Be efficient.
- If you cannot find a sign-out option within 5 actions, call declare_done with
  status="fail" and evidence="Could not locate sign-out button"
- Do NOT navigate to arbitrary URLs. Only interact with visible UI elements.
- After clicking logout, wait briefly for the page to load.
- Once on a login/signed-out page, call declare_done with status="pass"
- NEVER type credentials. You are ONLY signing out."""


REPORT_SYNTHESIZER_SYSTEM_PROMPT = """\
ROLE:
You are a QA Report Writer. Given raw execution data (steps taken, reasoning,
outcomes), produce a clear, human-readable narrative report.

Your audience is a human QA manager or product owner who wants to understand:
- What happened during each test case execution
- Whether the application behaved correctly
- What issues were found and their business impact
- Any patterns across multiple test cases

OUTPUT FORMAT (respond with ONLY this JSON, no other text):
{
  "executive_summary": "One paragraph summarizing the entire test run...",
  "patterns_observed": ["Pattern 1...", "Pattern 2..."],
  "recommendations": ["Recommendation 1...", "Recommendation 2..."],
  "tc_narratives": [
    {
      "tc_id": "...",
      "tc_title": "...",
      "summary": "2-3 sentence summary of what happened...",
      "approach_taken": "What strategy was used...",
      "key_findings": ["Finding 1", "Finding 2"],
      "challenges_encountered": ["Challenge 1"],
      "verdict_reasoning": "Why this test passed/failed..."
    }
  ]
}

RULES:
- Write for a NON-TECHNICAL audience. No element locators or CSS selectors.
- Use business language: "the user profile page" not "div.profile-container"
- Be honest about failures and their likely root cause
- Keep each TC narrative to 4-6 sentences
- The executive summary should be 3-5 sentences max
- Patterns should be cross-cutting observations (not TC-specific)"""


# ---------------------------------------------------------------------------
# Sub-Agent message builders
# ---------------------------------------------------------------------------


def build_planner_user_message(
    test_case: dict[str, Any],
    brief: Any | None,
    project_context: str,
) -> str:
    """Build the user prompt for the planner sub-agent."""
    parts = [
        f"TEST CASE: {test_case.get('title', '')}",
        f"ID: {test_case.get('tc_id', '')}",
        "",
        "STEPS:",
    ]
    for i, step in enumerate(test_case.get("steps", []), 1):
        if isinstance(step, str):
            parts.append(f"  {i}. {step}")
        elif isinstance(step, dict):
            parts.append(f"  {i}. {step.get('step', step.get('description', ''))}")
    parts.append("")
    if brief is not None:
        try:
            parts.append(f"DOMAIN KNOWLEDGE:\n{brief.to_prompt_section()[:3000]}")
        except Exception:
            pass
    if project_context:
        parts.append(f"\nAPPLICATION CONTEXT:\n{project_context[:2000]}")
    parts.append("\nProduce the JSON strategy now.")
    return "\n".join(parts)


def build_consultant_user_message(
    current_goal: str,
    test_step_text: str,
    failures: list[str],
    actions_tried: list[str],
    page_state: str,
    kb_chunks: str,
    tc_title: str,
) -> str:
    """Build the user prompt for the KB Consultant."""
    parts = [
        f"TEST CASE: {tc_title}",
        f"CURRENT GOAL: {current_goal}",
        f"TEST STEP: {test_step_text}",
        "",
        "WHAT WAS TRIED (all failed):",
    ]
    for a in actions_tried[-5:]:
        parts.append(f"  - {a}")
    parts.append("")
    parts.append("FAILURE DETAILS:")
    for f in failures[-3:]:
        parts.append(f"  - {f[:300]}")
    parts.append("")
    parts.append(f"CURRENT PAGE STATE (truncated):\n{page_state[:4000]}")
    if kb_chunks:
        parts.append(f"\nRELEVANT DOMAIN KNOWLEDGE:\n{kb_chunks}")
    parts.append("\nWhat should the agent try next? Be specific and actionable.")
    return "\n".join(parts)


def build_synthesizer_user_message(
    results: list[Any],
    total_duration_ms: int,
) -> str:
    """Build the user prompt for the Report Synthesizer."""
    parts = [
        f"E2E TEST RUN RESULTS ({len(results)} test cases, "
        f"{total_duration_ms / 1000:.0f}s total)",
        "",
    ]
    for r in results:
        tc_id = getattr(r, "tc_id", "?")
        title = getattr(r, "title", "Untitled")
        status = getattr(r, "overall_status", "?")
        steps = getattr(r, "steps", [])
        duration = getattr(r, "duration_ms", 0)
        thoughts = getattr(r, "thoughts", [])
        escalations = getattr(r, "escalation_count", 0)

        parts.append(f"--- TC: {tc_id} - {title} [{status.upper()}] ({duration}ms) ---")

        # Include key reasoning from thoughts (max 5 per TC)
        if thoughts:
            parts.append("Agent reasoning highlights:")
            for t in thoughts[-5:]:
                reasoning = getattr(t, "reasoning_text", "")[:200]
                tool = getattr(t, "tool_chosen", "")
                if reasoning:
                    parts.append(f"  [{tool}] {reasoning}")
            parts.append("")

        # Step summary
        passed = sum(1 for s in steps if getattr(s, "status", "") == "pass")
        failed = sum(1 for s in steps if getattr(s, "status", "") in ("fail", "error"))
        parts.append(f"  Steps: {len(steps)} total, {passed} passed, {failed} failed")

        if escalations:
            parts.append(f"  KB consultations needed: {escalations}")
        parts.append("")

    parts.append("Produce the JSON narrative report now.")
    return "\n".join(parts)
