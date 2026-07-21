"""
automation/agentic_runner.py
Core orchestration module for the agentic E2E test runner.

Drives the observe->decide->act loop with an LLM in the loop.
Replaces the compile-then-execute architecture with a live reasoning agent
that interprets page state, picks actions, and self-corrects.

SECURITY: Credentials are NEVER logged. Substitution happens inside the tool
executor (agentic_tools.py). This module only passes them through.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agentic_tools import AgenticToolExecutor, get_accessibility_tree
from .agentic_prompt import (
    build_initial_user_message,
    build_observation_message,
    build_system_prompt,
)
from .e2e_runner import StepResult, TestCaseResult

_log = logging.getLogger(__name__)
LogFn = Callable[[str], None]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgenticConfig:
    """Tuning knobs for the agentic loop."""

    max_steps: int = 40                 # hard limit per test case
    max_consecutive_fails: int = 3      # stuck detection threshold
    history_window: int = 8             # full messages kept in context
    screenshot_every_action: bool = True
    observation_max_chars: int = 12000  # a11y tree truncation
    temperature: float = 0.2           # low temp for deterministic actions
    max_tokens: int = 4096             # per-turn output limit


# ---------------------------------------------------------------------------
# History compression
# ---------------------------------------------------------------------------


def _compress_history(
    messages: list[dict[str, Any]],
    keep_recent: int,
) -> list[dict[str, Any]]:
    """Compress older messages to prevent context blowup.

    Keeps the most recent `keep_recent` messages intact.
    Compresses older ones into a single summary message.
    """
    if len(messages) <= keep_recent:
        return messages

    old = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    summary_lines: list[str] = []
    step_num = 0
    i = 0
    while i < len(old):
        msg = old[i]
        # Look for assistant messages with tool_use content
        if msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        step_num += 1
                        tool_name = block.get("name", "unknown")
                        # Look at the next message for tool_result status
                        status = "executed"
                        if i + 1 < len(old):
                            next_msg = old[i + 1]
                            next_content = next_msg.get("content", "")
                            if isinstance(next_content, str):
                                if "error" in next_content.lower():
                                    status = "error"
                                elif "success" in next_content.lower():
                                    status = "success"
                            elif isinstance(next_content, list):
                                for nb in next_content:
                                    if isinstance(nb, dict):
                                        nc = nb.get("content", "")
                                        if isinstance(nc, str):
                                            if "error" in nc.lower():
                                                status = "error"
                                            elif "success" in nc.lower():
                                                status = "success"
                        summary_lines.append(
                            f"Step {step_num}: {tool_name} -> {status}"
                        )
                        break
        i += 1

    if not summary_lines:
        return messages

    compressed_msg: dict[str, Any] = {
        "role": "user",
        "content": "Previous actions summary:\n" + "\n".join(summary_lines),
    }
    return [compressed_msg] + recent


# ---------------------------------------------------------------------------
# Core agentic loop
# ---------------------------------------------------------------------------


async def run_agentic_test_case(
    page: Any,
    context: Any,
    test_case: dict[str, Any],
    credentials: Any,
    brief: Any | None,
    project_context: str,
    llm_client: Any,
    model: str,
    output_dir: Path,
    *,
    config: AgenticConfig | None = None,
    stop_fn: Callable[[], bool] | None = None,
    on_log: LogFn | None = None,
    on_step: Callable[[StepResult], None] | None = None,
) -> TestCaseResult:
    """Execute a single test case using the LLM-driven observe->decide->act loop.

    Returns a TestCaseResult with all step results collected during execution.
    """
    from core.anthropic_client import AnthropicError

    cfg = config or AgenticConfig()
    log = on_log or (lambda _: None)
    tc_id = test_case.get("tc_id", "TC-unknown")
    title = test_case.get("title", "Untitled")
    start_time = time.perf_counter()

    log(f"[INFO] Agentic run: {tc_id} - {title}")

    # Ensure output directory exists
    screenshot_dir = output_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    # Build system prompt
    cred_dict: dict[str, str] = {
        "login_url": getattr(credentials, "login_url", ""),
        "ai_instructions": getattr(credentials, "ai_instructions", ""),
    }
    system = build_system_prompt(
        test_case, cred_dict, brief, project_context, login_done=True,
    )

    # Create tool executor
    executor = AgenticToolExecutor(page, context, credentials, screenshot_dir)
    tools = executor.tool_definitions()

    # Initial observation
    try:
        initial_observation = await get_accessibility_tree(
            page, max_chars=cfg.observation_max_chars,
        )
    except Exception as exc:
        log(f"[ERROR] Failed to get initial page observation: {exc}")
        initial_observation = f"Error capturing page state: {exc}"

    # Build initial message
    initial_msg = build_initial_user_message(initial_observation)
    messages: list[dict[str, Any]] = [{"role": "user", "content": initial_msg}]

    # Loop state
    steps: list[StepResult] = []
    step_num = 0
    consecutive_fails = 0
    final_status = "error"
    final_summary = ""

    try:
        while step_num < cfg.max_steps:
            # Check external stop signal
            if stop_fn and stop_fn():
                log("[INFO] Stop signal received, ending test case")
                final_status = "blocked"
                final_summary = "User cancelled execution"
                break

            # Call LLM
            try:
                result = await llm_client.stream_message_with_tools_async(
                    model=model,
                    messages=messages,
                    system=system,
                    tools=tools,
                    max_tokens=cfg.max_tokens,
                    temperature=cfg.temperature,
                )
            except AnthropicError as exc:
                log(f"[ERROR] LLM call failed: {exc}")
                final_status = "error"
                final_summary = f"LLM error: {exc}"
                break

            if result.has_tool_use:
                # Extract the first tool call
                tool_call = result.tool_calls[0]
                tool_name: str = tool_call.get("name", "")
                tool_input: dict[str, Any] = tool_call.get("input", {})
                tool_id: str = tool_call.get("id", "")

                step_num += 1

                # Check for termination tools
                if tool_name == "declare_done":
                    verdict = tool_input.get("status", "pass")
                    evidence = tool_input.get("evidence", "")
                    final_status = verdict
                    final_summary = evidence
                    log(f"[SUCCESS] Agent declared done: {verdict}")
                    # Record the declaration as a step
                    steps.append(StepResult(
                        step_num=step_num,
                        action="declare_done",
                        expected="Test completion",
                        actual=f"{verdict}: {evidence}",
                        status=verdict,
                        duration_ms=0,
                    ))
                    if on_step:
                        on_step(steps[-1])
                    break

                if tool_name == "declare_stuck":
                    reason = tool_input.get("reason", "Unknown")
                    final_status = "error"
                    final_summary = f"Agent stuck: {reason}"
                    log(f"[WARN] Agent declared stuck: {reason}")
                    steps.append(StepResult(
                        step_num=step_num,
                        action="declare_stuck",
                        expected="Continue test",
                        actual=f"Stuck: {reason}",
                        status="error",
                        duration_ms=0,
                    ))
                    if on_step:
                        on_step(steps[-1])
                    break

                # Execute the tool
                step_start = time.perf_counter()
                try:
                    observation_text, step_result = await executor.execute(
                        tool_name, tool_input, step_num,
                    )
                    consecutive_fails = 0
                except Exception as exc:
                    observation_text = f"Tool execution error: {exc}"
                    step_result = StepResult(
                        step_num=step_num,
                        action=tool_name,
                        expected="Successful execution",
                        actual=f"Error: {exc}",
                        status="error",
                        duration_ms=int(
                            (time.perf_counter() - step_start) * 1000
                        ),
                    )
                    consecutive_fails += 1

                step_result.duration_ms = int(
                    (time.perf_counter() - step_start) * 1000
                )
                steps.append(step_result)
                if on_step:
                    on_step(step_result)

                # Get fresh page observation after action
                try:
                    page_obs = await get_accessibility_tree(
                        page, max_chars=cfg.observation_max_chars,
                    )
                except Exception:
                    page_obs = "(Page observation unavailable)"

                # Build observation message
                obs_msg = build_observation_message(
                    tool_name, observation_text, page_obs, step_num,
                )

                # Append assistant response and tool result to messages
                messages.append({"role": "assistant", "content": result.content})
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": obs_msg,
                        }
                    ],
                })

                # Consecutive fail check
                if consecutive_fails >= cfg.max_consecutive_fails:
                    log(
                        f"[WARN] {cfg.max_consecutive_fails} consecutive "
                        "failures, ending test case"
                    )
                    final_status = "error"
                    final_summary = (
                        f"Stuck: {cfg.max_consecutive_fails} consecutive "
                        "tool failures"
                    )
                    break

                # History management
                if len(messages) > cfg.history_window * 2:
                    messages = _compress_history(messages, cfg.history_window)

            else:
                # No tool use -- agent returned text only (unexpected)
                messages.append({"role": "assistant", "content": result.content})
                messages.append({
                    "role": "user",
                    "content": (
                        "You must call a tool. Either continue the test or "
                        "call declare_done/declare_stuck."
                    ),
                })
                # Count as a wasted turn
                consecutive_fails += 1
                if consecutive_fails >= cfg.max_consecutive_fails:
                    log("[WARN] Agent not calling tools, ending test case")
                    final_status = "error"
                    final_summary = "Agent stopped calling tools"
                    break

        else:
            # max_steps exhausted
            log(f"[WARN] Max steps reached ({cfg.max_steps})")
            final_status = "error"
            final_summary = f"Max steps ({cfg.max_steps}) exhausted"

    except Exception as exc:
        log(f"[ERROR] Unexpected error in agentic loop: {exc}")
        final_status = "error"
        final_summary = f"Unexpected error: {exc}"

    # Build final result
    duration_ms = int((time.perf_counter() - start_time) * 1000)
    tc_result = TestCaseResult(
        tc_id=tc_id,
        title=title,
        steps=steps,
        overall_status=final_status,
        duration_ms=duration_ms,
    )

    log(
        f"[INFO] TC {tc_id} finished: {final_status} "
        f"({len(steps)} steps, {duration_ms}ms)"
    )

    del messages
    gc.collect()

    return tc_result


# ---------------------------------------------------------------------------
# Suite orchestrator
# ---------------------------------------------------------------------------


async def run_agentic_suite(
    test_cases: list[dict[str, Any]],
    credentials: Any,
    briefs: dict[str, Any],
    project_context: str,
    llm_client: Any,
    model: str,
    fallback_model: str,
    output_dir: Path,
    page: Any,
    context: Any,
    *,
    config: AgenticConfig | None = None,
    stop_fn: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: LogFn | None = None,
    on_tc_done: Callable[[TestCaseResult], None] | None = None,
    on_step: Callable[[StepResult], None] | None = None,
) -> list[TestCaseResult]:
    """Run a suite of test cases sequentially on one browser page.

    Strategy: primary model first. If TC ends in "error" (stuck), retry with
    the fallback model (typically Opus for deeper reasoning).
    """
    from core.anthropic_client import AnthropicError

    cfg = config or AgenticConfig()
    log = on_log or (lambda _: None)
    results: list[TestCaseResult] = []

    for i, tc in enumerate(test_cases):
        if stop_fn and stop_fn():
            log("[INFO] Stop signal received, ending suite")
            break

        if on_progress:
            on_progress(i, len(test_cases))

        tc_id = tc.get("tc_id", f"TC-{i + 1}")
        title = tc.get("title", "Untitled")
        log(f"[INFO] Starting TC {i + 1}/{len(test_cases)}: {title}")

        brief = briefs.get(tc_id)
        tc_output = output_dir / tc_id
        tc_output.mkdir(parents=True, exist_ok=True)

        # Primary attempt
        result = await run_agentic_test_case(
            page, context, tc, credentials, brief, project_context,
            llm_client, model, tc_output, config=cfg,
            stop_fn=stop_fn, on_log=log, on_step=on_step,
        )

        # Fallback retry if stuck and a different model is available
        if result.overall_status == "error" and fallback_model != model:
            log(
                f"[INFO] Retrying TC '{title}' with advanced reasoning model"
            )
            # Navigate to a known state before retry
            try:
                await page.goto(
                    getattr(credentials, "login_url", ""), timeout=30000,
                )
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass

            retry_output = tc_output / "retry"
            retry_output.mkdir(parents=True, exist_ok=True)

            result = await run_agentic_test_case(
                page, context, tc, credentials, brief, project_context,
                llm_client, fallback_model, retry_output, config=cfg,
                stop_fn=stop_fn, on_log=log, on_step=on_step,
            )
            if result.overall_status != "error":
                log(f"[SUCCESS] Fallback retry succeeded for '{title}'")

        results.append(result)
        if on_tc_done:
            on_tc_done(result)

        # Navigate to start URL between test cases
        try:
            await page.goto(
                getattr(credentials, "login_url", ""), timeout=30000,
            )
            await page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

        gc.collect()

    return results


# ---------------------------------------------------------------------------
# Slot runner (for parallel execution)
# ---------------------------------------------------------------------------


async def run_agentic_slot(
    page: Any,
    context: Any,
    test_cases: list[dict[str, Any]],
    credentials: Any,
    briefs: dict[str, Any],
    project_context: str,
    llm_client: Any,
    model: str,
    fallback_model: str,
    output_dir: Path,
    *,
    config: AgenticConfig | None = None,
    stop_fn: Callable[[], bool] | None = None,
    on_log: LogFn | None = None,
    on_tc_done: Callable[[TestCaseResult], None] | None = None,
    on_step: Callable[[StepResult], None] | None = None,
) -> list[TestCaseResult]:
    """Slot function for ParallelRunner -- page already created, runs a TC subset."""
    return await run_agentic_suite(
        test_cases, credentials, briefs, project_context,
        llm_client, model, fallback_model, output_dir,
        page, context,
        config=config, stop_fn=stop_fn, on_log=on_log,
        on_tc_done=on_tc_done, on_step=on_step,
    )
