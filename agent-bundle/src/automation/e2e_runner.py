"""
automation/e2e_runner.py
Orchestrator for E2E test execution via Playwright.

SECURITY: Password is ONLY passed to page.fill(). NEVER logged, NEVER written
to disk, NEVER included in any artifact or exception message.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Page, TimeoutError as PwTimeout

from .artifact_collector import ArtifactCollector
from .playwright_bridge import BrowserProfile, browser_session
from .screenshot_annotator import annotate_screenshot
from .script_generator import generate_playwright_script


# -------------------------------------------------------------------
# Result dataclasses
# -------------------------------------------------------------------

@dataclass(slots=True)
class StepResult:
    step_num: int
    action: str
    expected: str
    actual: str
    status: str  # "pass" | "fail" | "skip" | "error"
    screenshot_path: Path | None = None
    duration_ms: int = 0


@dataclass(slots=True)
class TestCaseResult:
    tc_id: str
    title: str
    steps: list[StepResult]
    video_path: Path | None = None
    script_path: Path | None = None
    overall_status: str = "pass"  # "pass" | "fail" | "error"
    duration_ms: int = 0


# -------------------------------------------------------------------
# Step actions
# -------------------------------------------------------------------

_SENSITIVE_FIELDS = frozenset({"password", "passwd", "pwd", "secret", "token"})


def _is_password_field(field_label: str) -> bool:
    """Check if a form field is a password/secret field."""
    return any(s in field_label.lower() for s in _SENSITIVE_FIELDS)


async def _execute_step(
    page: Page,
    step: dict[str, Any],
    username: str,
    password: str,
    screenshot_dir: Path,
    step_num: int,
) -> StepResult:
    """Execute a single test step and return the result.

    Supported actions: navigate, fill, click, select, check, wait,
    assert_text, assert_url, assert_element, screenshot.
    """
    t0 = time.perf_counter_ns()
    action = step.get("action", "").lower().strip()
    target = step.get("target", "")
    value = step.get("value", "")
    expected = step.get("expected", "")
    locator_type = step.get("locator", "role")  # role | text | label | css

    actual = ""
    status = "pass"
    screenshot_path: Path | None = None

    try:
        if action == "navigate":
            url = value or target
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            actual = f"Navigated to {page.url}"
            if expected and expected not in page.url:
                status = "fail"
                actual = f"URL mismatch: got {page.url}"

        elif action == "fill":
            element = _resolve_locator(page, target, locator_type)
            # Determine fill value -- password fields use vault password
            if _is_password_field(target):
                fill_value = password
            elif value.lower() == "{{username}}":
                fill_value = username
            elif value.lower() == "{{password}}":
                fill_value = password
            else:
                fill_value = value
            await element.fill(fill_value, timeout=10000)
            # Log what was filled (NEVER the actual password)
            if _is_password_field(target) or value.lower() == "{{password}}":
                actual = f"Filled password field [{target}]"
            else:
                actual = f"Filled [{target}] with [{fill_value}]"

        elif action == "click":
            element = _resolve_locator(page, target, locator_type)
            await element.click(timeout=10000)
            actual = f"Clicked [{target}]"
            # Brief wait for navigation/state change
            await page.wait_for_load_state("domcontentloaded", timeout=10000)

        elif action == "select":
            element = _resolve_locator(page, target, locator_type)
            await element.select_option(value, timeout=10000)
            actual = f"Selected [{value}] in [{target}]"

        elif action == "check":
            element = _resolve_locator(page, target, locator_type)
            await element.check(timeout=10000)
            actual = f"Checked [{target}]"

        elif action == "wait":
            ms = int(value) if value.isdigit() else 2000
            await page.wait_for_timeout(ms)
            actual = f"Waited {ms}ms"

        elif action == "assert_text":
            text_to_find = value or expected
            locator = page.get_by_text(text_to_find)
            try:
                await locator.wait_for(state="visible", timeout=10000)
                actual = f"Text found: [{text_to_find}]"
            except PwTimeout:
                status = "fail"
                actual = f"Text NOT found: [{text_to_find}]"

        elif action == "assert_url":
            expected_url = value or expected
            current_url = page.url
            if expected_url in current_url:
                actual = f"URL contains [{expected_url}]"
            else:
                status = "fail"
                actual = f"URL [{current_url}] does not contain [{expected_url}]"

        elif action == "assert_element":
            element = _resolve_locator(page, target, locator_type)
            try:
                await element.wait_for(state="visible", timeout=10000)
                actual = f"Element visible: [{target}]"
            except PwTimeout:
                status = "fail"
                actual = f"Element NOT visible: [{target}]"

        elif action == "screenshot":
            # Explicit screenshot request (always taken anyway below)
            actual = "Screenshot captured"

        else:
            status = "skip"
            actual = f"Unknown action: [{action}]"

    except PwTimeout as exc:
        status = "error"
        actual = f"Timeout: {action} on [{target}]"
    except Exception as exc:
        status = "error"
        # Strip any accidental password leakage from exception messages
        err_msg = str(exc)
        if password and password in err_msg:
            err_msg = err_msg.replace(password, "***")
        actual = f"Error: {err_msg[:200]}"

    # Take screenshot after every step
    try:
        raw_path = screenshot_dir / f"step_{step_num:03d}.png"
        await page.screenshot(path=str(raw_path), full_page=False)
        # Annotate
        screenshot_path = annotate_screenshot(
            screenshot_path=raw_path,
            step_num=step_num,
            status=status,
            label=f"{action}: {target}"[:60],
        )
    except Exception:
        pass  # Screenshot failure should not fail the step

    elapsed_ms = int((time.perf_counter_ns() - t0) / 1_000_000)
    return StepResult(
        step_num=step_num,
        action=action,
        expected=expected,
        actual=actual,
        status=status,
        screenshot_path=screenshot_path,
        duration_ms=elapsed_ms,
    )


def _resolve_locator(page: Page, target: str, locator_type: str):
    """Resolve a page locator using stable strategies."""
    if locator_type == "text":
        return page.get_by_text(target)
    elif locator_type == "label":
        return page.get_by_label(target)
    elif locator_type == "role":
        # target format: "button:Submit" or "textbox:Email"
        if ":" in target:
            role, name = target.split(":", 1)
            return page.get_by_role(role.strip(), name=name.strip())
        return page.get_by_role(target)
    elif locator_type == "placeholder":
        return page.get_by_placeholder(target)
    elif locator_type == "test_id":
        return page.get_by_test_id(target)
    elif locator_type == "css":
        return page.locator(target)
    else:
        # Default: try role if colon present, else text
        if ":" in target:
            role, name = target.split(":", 1)
            return page.get_by_role(role.strip(), name=name.strip())
        return page.get_by_text(target)


# -------------------------------------------------------------------
# Main runner
# -------------------------------------------------------------------

async def run_e2e_tests(
    test_cases: list[dict[str, Any]],
    login_url: str,
    username: str,
    password: str,
    output_dir: Path,
    *,
    profile: BrowserProfile | None = None,
    headless: bool = False,
    ai_instructions: str = "",  # ponytail: wired but unused; feed to AI login handler when built
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_screenshot: Callable[[Path, int, str], None] | None = None,
    on_tc_done: Callable[[str, str], None] | None = None,
) -> list[TestCaseResult]:
    """Execute E2E tests using the user's real browser via CDP.

    Args:
        test_cases: List of dicts with keys: id, title, steps (list of step dicts).
        login_url: Starting URL for the test session.
        username: Login username.
        password: Login password (from vault, NEVER logged).
        output_dir: Base directory for all artifacts.
        profile: Browser profile to use (auto-detect if None).
        headless: Ignored (CDP attach uses visible browser for SSO).
        on_progress: Callback(current, total) for progress reporting.
        on_log: Callback(message) for log output.
        on_screenshot: Callback(path, step_num, status) called immediately after each screenshot is saved.

    Returns:
        List of TestCaseResult, one per test case.
    """
    results: list[TestCaseResult] = []
    total = len(test_cases)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _log(msg: str) -> None:
        if on_log:
            on_log(msg)

    for idx, tc in enumerate(test_cases):
        tc_id = str(tc.get("id", f"TC_{idx+1:03d}"))
        title = str(tc.get("title", "Untitled"))
        steps_data: list[dict[str, Any]] = tc.get("steps", [])

        _log(f"[INFO] Starting test case {tc_id}: {title}")
        if on_progress:
            on_progress(idx, total)

        collector = ArtifactCollector(output_dir, tc_id)
        tc_start = time.perf_counter_ns()
        step_results: list[StepResult] = []
        video_path: Path | None = None

        try:
            async with browser_session(
                profile=profile,
                output_dir=collector.video_dir,
            ) as (browser_inst, page):

                for step_idx, step in enumerate(steps_data, start=1):
                    result = await _execute_step(
                        page=page,
                        step=step,
                        username=username,
                        password=password,
                        screenshot_dir=collector.screenshot_dir,
                        step_num=step_idx,
                    )
                    step_results.append(result)

                    # Notify UI immediately when screenshot is saved
                    if result.screenshot_path and on_screenshot:
                        try:
                            on_screenshot(result.screenshot_path, step_idx, result.status)
                        except Exception:
                            pass

                    # Stream-save video: copy in-progress .webm to local after each step
                    try:
                        video_src = page.video
                        if video_src:
                            src_path = await video_src.path()
                            if src_path and Path(src_path).exists():
                                dest = collector.video_dir / "recording_live.webm"
                                shutil.copy2(src_path, dest)
                    except Exception:
                        pass  # Video copy failure must not break the test

                    status_tag = result.status.upper()
                    # Never include password in log
                    _log(
                        f"  [{status_tag}] Step {step_idx}: "
                        f"{result.action} -> {result.actual}"
                    )

                    # Stop on first error/fail if configured
                    if result.status in ("error", "fail"):
                        # Mark remaining steps as skipped
                        for skip_idx in range(step_idx + 1, len(steps_data) + 1):
                            skip_step = steps_data[skip_idx - 1]
                            step_results.append(StepResult(
                                step_num=skip_idx,
                                action=skip_step.get("action", ""),
                                expected=skip_step.get("expected", ""),
                                actual="Skipped (prior step failed)",
                                status="skip",
                            ))
                        break

                # Collect video after context close
                # (video file is finalized on context.close())

        except Exception as exc:
            err_msg = str(exc)
            if password and password in err_msg:
                err_msg = err_msg.replace(password, "***")
            _log(f"[ERROR] Test case {tc_id} crashed: {err_msg[:200]}")
            if not step_results:
                step_results.append(StepResult(
                    step_num=0,
                    action="setup",
                    expected="Browser session started",
                    actual=f"Crash: {err_msg[:200]}",
                    status="error",
                ))

        # Determine overall status
        statuses = [s.status for s in step_results]
        if "error" in statuses:
            overall = "error"
        elif "fail" in statuses:
            overall = "fail"
        else:
            overall = "pass"

        tc_elapsed_ms = int((time.perf_counter_ns() - tc_start) / 1_000_000)

        # Collect video file
        video_path = collector.collect_video()

        # Generate rerunnable script
        script_content = generate_playwright_script(
            tc_id=tc_id,
            title=title,
            steps=steps_data,
            login_url=login_url,
            username=username,
        )
        script_path = collector.save_script(script_content, tc_id)

        tc_result = TestCaseResult(
            tc_id=tc_id,
            title=title,
            steps=step_results,
            video_path=video_path,
            script_path=script_path,
            overall_status=overall,
            duration_ms=tc_elapsed_ms,
        )
        results.append(tc_result)
        if on_tc_done:
            on_tc_done(tc_id, overall)
        _log(f"[{overall.upper()}] Test case {tc_id} completed in {tc_elapsed_ms}ms")

    if on_progress:
        on_progress(total, total)
    _log(f"[INFO] All {total} test cases executed.")
    return results
