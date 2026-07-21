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
) -> str:
    """Build the full system prompt for one test case execution."""
    sections: list[str] = []

    # 1. Role
    sections.append(_ROLE_SECTION)

    # 2. Credentials & login state
    sections.append(_build_login_section(credentials, login_done))

    # 3. Test case
    sections.append(_build_test_case_section(test_case))

    # 4. KB briefing
    sections.append(_build_kb_section(brief))

    # 5. Project context
    sections.append(_build_context_section(project_context))

    # 6. Behavioral rules
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
- After each action, you will receive the updated page state (accessibility tree)
- Use this to verify your action succeeded before proceeding to the next step
- If an element is not found, try scrolling or waiting -- do NOT immediately declare stuck
- If you encounter an error state (error message, 404, etc.), note it and try to recover
- If you are truly stuck after 3 attempts at the same action, call declare_stuck
- When all test steps are complete, call declare_done with your verdict:
  - "pass" if all steps verified successfully
  - "fail" if any step could not be verified (with evidence of what failed)

LOCATOR GUIDANCE:
- Describe elements naturally: "the Submit button", "email input field", "Settings link"
- Include visible text when possible: "button labeled Save Changes"
- For ambiguous elements, add context: "the first Delete button in the table"
- The system will automatically find elements using multiple strategies

CREDENTIAL SECURITY:
- When you need to type a password, use the fill tool with "{{password}}" as the value
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
    ai_instructions = credentials.get("ai_instructions", "Standard login form")
    return (
        "LOGIN STATE: You must log in first.\n"
        f"Login URL: {login_url}\n"
        "Username: {{username}}\n"
        "Password: {{password}}\n"
        f"AI Instructions for login: {ai_instructions}\n\n"
        'Use the fill tool with value "{{username}}" for the username field '
        'and "{{password}}" for the password field.\n'
        "The system will automatically substitute the real credentials."
    )


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
