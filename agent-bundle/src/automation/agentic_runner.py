"""
automation/agentic_runner.py
Core orchestration module for the multi-agent E2E test runner (v3.70.0).

Architecture:
  1. Planner Sub-Agent (pre-TC) - strategizes before execution
  2. Executor Agent (main loop) - observe->decide->act with CoT capture
  3. KB Consultant Sub-Agent (on-demand) - advises when stuck
  4. Sign-Out Agent (post-TC) - attempts logout after each TC
  5. Report Synthesizer (post-suite) - human-readable narrative

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
    build_planner_user_message,
    build_consultant_user_message,
    PLANNER_SYSTEM_PROMPT,
    KB_CONSULTANT_SYSTEM_PROMPT,
    SIGNOUT_SYSTEM_PROMPT,
)
from .e2e_runner import StepResult, TestCaseResult
from .sub_agents import (
    ThoughtRecord,
    TestStrategy,
    parse_test_strategy,
    summarize_tool_input,
)

_log = logging.getLogger(__name__)
LogFn = Callable[[str], None]

# ponytail: 30-min cap per TC; configurable timeout if throughput matters
_TC_TIME_CAP_SECONDS: int = 1800
_MAX_ESCALATIONS: int = 3
_SIGNOUT_MAX_STEPS: int = 10


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgenticConfig:
    """Tuning knobs for the agentic loop."""

    max_consecutive_fails: int = 3      # triggers KB escalation
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
        if msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        step_num += 1
                        tool_name = block.get("name", "unknown")
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
# Sub-Agent: Planner
# ---------------------------------------------------------------------------


async def _run_planner_agent(
    llm_client: Any,
    test_case: dict[str, Any],
    brief: Any | None,
    project_context: str,
    log: LogFn,
) -> TestStrategy | None:
    """Planner Sub-Agent: produce a test strategy before execution."""
    from core.model_router import Task, route

    planner_model = route(Task.E2E_PLANNER)
    user_msg = build_planner_user_message(test_case, brief, project_context)

    try:
        result = await llm_client.complete_async(
            model=planner_model,
            system=PLANNER_SYSTEM_PROMPT,
            user=user_msg,
            max_tokens=1024,
            temperature=0.1,
        )
        strategy = parse_test_strategy(result)
        if strategy:
            log(
                f"[INFO] Planner: {strategy.estimated_complexity} complexity, "
                f"{len(strategy.navigation_hints)} nav hints"
            )
        return strategy
    except Exception as exc:
        log(f"[WARN] Planner sub-agent failed (non-fatal): {exc}")
        return None


# ---------------------------------------------------------------------------
# Sub-Agent: KB Consultant
# ---------------------------------------------------------------------------


async def _consult_kb(
    llm_client: Any,
    kb_engine: Any,
    test_case: dict[str, Any],
    recent_thoughts: list[ThoughtRecord],
    page: Any,
    cfg: AgenticConfig,
    log: LogFn,
) -> str | None:
    """KB Consultant Sub-Agent: query KB with failure context, get advice."""
    from core.model_router import Task, route

    try:
        page_obs = await get_accessibility_tree(page, max_chars=4000)
    except Exception:
        page_obs = "(page observation unavailable)"

    failures = [t.reasoning_text[:300] for t in recent_thoughts[-3:] if t.reasoning_text]
    actions_tried = [t.tool_chosen for t in recent_thoughts[-5:]]

    # Query KB with a failure-specific question
    kb_chunks_text = ""
    if kb_engine:
        try:
            query = (
                f"How to {test_case.get('title', '')}? "
                f"Agent is stuck. Tried: {', '.join(actions_tried[-3:])}. "
                f"Current page shows: {page_obs[:500]}"
            )
            kb_chunks_text = kb_engine.query_for_stuck_agent(query, top_k=5)
        except Exception:
            pass

    tc_title = test_case.get("title", "")
    current_step_text = ""
    steps = test_case.get("steps", [])
    if steps:
        s = steps[0] if isinstance(steps[0], str) else steps[0].get("step", "")
        current_step_text = s

    consultant_model = route(Task.E2E_KB_CONSULTANT)
    user_msg = build_consultant_user_message(
        current_goal=tc_title,
        test_step_text=current_step_text,
        failures=failures,
        actions_tried=actions_tried,
        page_state=page_obs,
        kb_chunks=kb_chunks_text,
        tc_title=tc_title,
    )

    try:
        result = await llm_client.complete_async(
            model=consultant_model,
            system=KB_CONSULTANT_SYSTEM_PROMPT,
            user=user_msg,
            max_tokens=1024,
            temperature=0.3,
        )
        if result and result.strip():
            log(f"[INFO] KB Consultant advice: {result[:150]}...")
            return result
        return None
    except Exception as exc:
        log(f"[WARN] KB Consultant failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Sub-Agent: Sign-Out
# ---------------------------------------------------------------------------


async def _run_sign_out_agent(
    page: Any,
    context: Any,
    credentials: Any,
    llm_client: Any,
    log: LogFn,
) -> bool:
    """Sign-Out Sub-Agent: attempt to sign out after TC completion."""
    from core.model_router import Task, route

    sign_out_model = route(Task.E2E_PLANNER)  # SMALL tier for cheap sign-out
    screenshot_dir = Path("/tmp/signout")
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    executor = AgenticToolExecutor(page, context, credentials, screenshot_dir)
    tools = executor.get_tool_definitions()

    try:
        page_obs = await get_accessibility_tree(page, max_chars=8000)
    except Exception:
        log("[WARN] Sign-out: cannot observe page")
        return False

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"Current page:\n{page_obs}\n\nSign out now."}
    ]

    for turn in range(_SIGNOUT_MAX_STEPS):
        try:
            result = await llm_client.stream_message_with_tools_async(
                model=sign_out_model,
                messages=messages,
                system=SIGNOUT_SYSTEM_PROMPT,
                tools=tools,
                max_tokens=1024,
                temperature=0.1,
            )
        except Exception as exc:
            log(f"[WARN] Sign-out LLM error: {exc}")
            return False

        if not result.has_tool_use:
            break

        tool_call = result.tool_calls[0]
        tool_name = tool_call.get("name", "")
        tool_input = tool_call.get("input", {})
        tool_id = tool_call.get("id", "")

        if tool_name == "declare_done":
            verdict = tool_input.get("status", "fail")
            log(f"[INFO] Sign-out: {verdict}")
            return verdict == "pass"

        if tool_name == "declare_stuck":
            log("[WARN] Sign-out agent stuck")
            return False

        # Execute tool
        try:
            obs_text, _ = await executor.execute(tool_name, tool_input, 0)
        except Exception:
            obs_text = "Tool execution failed"

        # Get fresh page state
        try:
            fresh_obs = await get_accessibility_tree(page, max_chars=6000)
        except Exception:
            fresh_obs = "(unavailable)"

        messages.append({"role": "assistant", "content": result.content})
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": f"{obs_text}\n\nCurrent page:\n{fresh_obs}",
                }
            ],
        })

    log("[WARN] Sign-out: max steps reached without completion")
    return False


# ---------------------------------------------------------------------------
# Core agentic loop (enhanced with CoT + KB escalation)
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
    kb_engine: Any | None = None,
) -> TestCaseResult:
    """Execute a single test case using the multi-agent architecture.

    Flow: Planner -> Executor loop (with KB escalation) -> Sign-Out
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

    # --- PHASE 1: PLANNER SUB-AGENT ---
    strategy = await _run_planner_agent(
        llm_client, test_case, brief, project_context, log,
    )

    # Build system prompt (with strategy if available)
    cred_dict: dict[str, str] = {
        "login_url": getattr(credentials, "login_url", ""),
        "ai_instructions": getattr(credentials, "ai_instructions", ""),
    }
    system = build_system_prompt(
        test_case, cred_dict, brief, project_context,
        login_done=False, strategy=strategy,
    )

    # Create tool executor
    executor = AgenticToolExecutor(page, context, credentials, screenshot_dir)
    tools = executor.get_tool_definitions()

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

    # --- PHASE 2: EXECUTOR MAIN LOOP ---
    steps: list[StepResult] = []
    thoughts: list[ThoughtRecord] = []
    step_num = 0
    consecutive_fails = 0
    escalation_count = 0
    final_status = "error"
    final_summary = ""

    try:
        while True:
            # Time-based safety cap (replaces max_steps)
            elapsed = time.perf_counter() - start_time
            if elapsed > _TC_TIME_CAP_SECONDS:
                log(f"[WARN] Time cap reached ({_TC_TIME_CAP_SECONDS}s)")
                final_status = "error"
                final_summary = f"Time limit ({_TC_TIME_CAP_SECONDS // 60}min) exceeded"
                break

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

            # --- CAPTURE CHAIN-OF-THOUGHT ---
            cot_text = result.text or ""
            if cot_text.strip():
                log(f"[THOUGHT] {cot_text[:200]}")

            if result.has_tool_use:
                tool_call = result.tool_calls[0]
                tool_name: str = tool_call.get("name", "")
                tool_input: dict[str, Any] = tool_call.get("input", {})
                tool_id: str = tool_call.get("id", "")

                step_num += 1

                # Store thought record
                thought = ThoughtRecord(
                    step_num=step_num,
                    reasoning_text=cot_text,
                    tool_chosen=tool_name,
                    tool_input_summary=summarize_tool_input(tool_input),
                )
                thoughts.append(thought)

                # Check for termination tools
                if tool_name == "declare_done":
                    verdict = tool_input.get("status", "pass")
                    evidence = tool_input.get("evidence", "")
                    final_status = verdict
                    final_summary = evidence
                    log(f"[SUCCESS] Agent declared done: {verdict}")
                    steps.append(StepResult(
                        step_num=step_num,
                        action="declare_done",
                        expected="Test completion",
                        actual=f"{verdict}: {evidence}",
                        status=verdict,
                        duration_ms=0,
                        reasoning=cot_text,
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
                        reasoning=cot_text,
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
                step_result.reasoning = cot_text
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

                # --- KB ESCALATION (before declaring stuck) ---
                if consecutive_fails >= cfg.max_consecutive_fails:
                    if escalation_count < _MAX_ESCALATIONS and kb_engine:
                        log(
                            f"[INFO] Escalating to KB Consultant "
                            f"(attempt {escalation_count + 1}/{_MAX_ESCALATIONS})"
                        )
                        advice = await _consult_kb(
                            llm_client, kb_engine, test_case,
                            thoughts, page, cfg, log,
                        )
                        if advice:
                            messages.append({
                                "role": "user",
                                "content": (
                                    "[KB CONSULTANT ADVICE]\n"
                                    "A senior QA expert reviewed your situation "
                                    "and suggests:\n\n"
                                    f"{advice}\n\n"
                                    "Try these alternative approaches. "
                                    "Call a tool now."
                                ),
                            })
                            consecutive_fails = 0
                            escalation_count += 1
                            # Mark next thought as escalation response
                            continue

                    # Escalation exhausted or no KB -- end TC
                    log(
                        f"[WARN] {cfg.max_consecutive_fails} consecutive "
                        f"failures (after {escalation_count} KB consultations), "
                        "ending test case"
                    )
                    final_status = "error"
                    final_summary = (
                        f"Stuck: {cfg.max_consecutive_fails} consecutive "
                        f"tool failures (KB consulted {escalation_count}x)"
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
                consecutive_fails += 1
                if consecutive_fails >= cfg.max_consecutive_fails:
                    # Try KB escalation even for no-tool-use stuck
                    if escalation_count < _MAX_ESCALATIONS and kb_engine:
                        log("[INFO] Agent not calling tools, consulting KB...")
                        advice = await _consult_kb(
                            llm_client, kb_engine, test_case,
                            thoughts, page, cfg, log,
                        )
                        if advice:
                            messages.append({
                                "role": "user",
                                "content": (
                                    "[KB CONSULTANT ADVICE]\n"
                                    f"{advice}\n\n"
                                    "Use a tool now to try these suggestions."
                                ),
                            })
                            consecutive_fails = 0
                            escalation_count += 1
                            continue

                    log("[WARN] Agent not calling tools, ending test case")
                    final_status = "error"
                    final_summary = "Agent stopped calling tools"
                    break

    except Exception as exc:
        log(f"[ERROR] Unexpected error in agentic loop: {exc}")
        final_status = "error"
        final_summary = f"Unexpected error: {exc}"

    # --- PHASE 3: SIGN-OUT SUB-AGENT ---
    sign_out_success = False
    try:
        log("[INFO] Attempting sign-out...")
        sign_out_success = await _run_sign_out_agent(
            page, context, credentials, llm_client, log,
        )
    except Exception as exc:
        log(f"[WARN] Sign-out failed (non-fatal): {exc}")

    # Build final result
    duration_ms = int((time.perf_counter() - start_time) * 1000)
    tc_result = TestCaseResult(
        tc_id=tc_id,
        title=title,
        steps=steps,
        overall_status=final_status,
        duration_ms=duration_ms,
        thoughts=thoughts,
        strategy=strategy,
        sign_out_success=sign_out_success,
        escalation_count=escalation_count,
    )

    log(
        f"[INFO] TC {tc_id} finished: {final_status} "
        f"({len(steps)} steps, {duration_ms}ms, "
        f"{escalation_count} KB consultations, "
        f"sign-out={'ok' if sign_out_success else 'skipped'})"
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
    kb_engine: Any | None = None,
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
            kb_engine=kb_engine,
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
                kb_engine=kb_engine,
            )
            if result.overall_status != "error":
                log(f"[SUCCESS] Fallback retry succeeded for '{title}'")

        results.append(result)
        if on_tc_done:
            on_tc_done(result)

        # Navigate to start URL between test cases (sign-out already attempted)
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
    kb_engine: Any | None = None,
) -> list[TestCaseResult]:
    """Slot function for ParallelRunner -- page already created, runs a TC subset."""
    return await run_agentic_suite(
        test_cases, credentials, briefs, project_context,
        llm_client, model, fallback_model, output_dir,
        page, context,
        config=config, stop_fn=stop_fn, on_log=on_log,
        on_tc_done=on_tc_done, on_step=on_step,
        kb_engine=kb_engine,
    )
