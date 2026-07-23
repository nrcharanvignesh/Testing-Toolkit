"""
automation/script_runner.py
Script-based E2E rerun with AI oversight and course-correction.

Executes previously generated Playwright replay scripts in a subprocess,
monitors stdout for [STEP:N]/[FAIL:N] markers, and uses the LLM to diagnose
and patch failures -- acting as a senior QA engineer overseeing automation.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger(__name__)

_STEP_RE = re.compile(r"^\[STEP:(\d+)\]\s*(.*)")
_FAIL_RE = re.compile(r"^\[FAIL:(\d+)\]\s*(.*)")
_MAX_AI_INTERVENTIONS = 3


@dataclass(slots=True)
class StepResult:
    step_num: int
    action: str
    expected: str
    actual: str
    status: str  # pass | fail | error
    screenshot_path: str = ""


@dataclass(slots=True)
class ScriptRunResult:
    tc_id: str
    title: str
    overall_status: str  # pass | fail | error
    duration_ms: int = 0
    steps: list[StepResult] = field(default_factory=list)
    script_path: str = ""
    video_path: str = ""
    patches_applied: int = 0


# -- AI Oversight Prompt --
_OVERSIGHT_SYSTEM = """\
You are a senior QA automation engineer overseeing a Playwright test script.
The script failed at a specific step. You have access to:
- The failing step's code
- The error message
- The current page accessibility snapshot
- The full page URL
- Prior user guidance messages from the original run

Your job: diagnose WHY the step failed and provide an EXACT code fix.
Output ONLY a JSON object:
{
  "diagnosis": "one-line explanation of root cause",
  "fixed_code": "the corrected Python code lines (replace the failing step body)",
  "confidence": "high|medium|low"
}

Common failure modes:
- Element moved/renamed: fix the locator (prefer get_by_role, get_by_text)
- Timing: add explicit wait_for_selector or wait_for_load_state
- New modal/dialog blocking: dismiss it first
- Navigation changed: update the URL or add intermediate nav step
- Auth expired: re-login step needed

Never guess blindly. If confidence is "low", set fixed_code to empty string.
"""


class ScriptRunner:
    """Execute replay scripts with AI oversight for course-correction."""

    def __init__(
        self,
        llm_client: Any,
        model: str,
        project_context: str,
        output_dir: Path,
        on_log: Callable[[str], None],
        stop_fn: Callable[[], bool],
    ) -> None:
        self._llm = llm_client
        self._model = model
        self._project_context = project_context
        self._output_dir = output_dir
        self._log = on_log
        self._stop_fn = stop_fn

    async def run_suite(
        self,
        test_cases: list[dict[str, Any]],
        credentials: Any,
        page: Any,
        context: Any,
        on_progress: Callable[[int, int], None] | None = None,
        on_tc_done: Callable[[ScriptRunResult], None] | None = None,
        user_messages: Callable[[], list[str]] | None = None,
    ) -> list[ScriptRunResult]:
        """Run all test cases in script mode with AI oversight."""
        results: list[ScriptRunResult] = []
        total = len(test_cases)

        for i, tc in enumerate(test_cases):
            if self._stop_fn():
                break

            tc_id = tc.get("tc_id", f"tc_{i}")
            title = tc.get("title", "Unknown")

            if on_progress:
                on_progress(i, total)

            self._log(f"[INFO] [{i+1}/{total}] Script mode: {title}")

            script_path = self._find_script(tc_id)
            if not script_path:
                self._log(f"[WARN] No replay script found for {tc_id}. Skipping.")
                results.append(ScriptRunResult(
                    tc_id=tc_id,
                    title=title,
                    overall_status="skip",
                ))
                continue

            result = await self._run_single(
                tc_id=tc_id,
                title=title,
                script_path=script_path,
                page=page,
                credentials=credentials,
                user_messages=user_messages,
            )
            results.append(result)
            if on_tc_done:
                on_tc_done(result)

        if on_progress:
            on_progress(total, total)

        return results

    def _find_script(self, tc_id: str) -> Path | None:
        """Locate the replay script for a test case.

        Searches: output_dir/{tc_id}/scripts/{tc_id}_replay.py,
        then patched variant, then any _replay.py in tc folder.
        """
        tc_dir = self._output_dir / tc_id / "scripts"
        patched = tc_dir / f"{tc_id}_replay_patched.py"
        if patched.exists():
            return patched
        original = tc_dir / f"{tc_id}_replay.py"
        if original.exists():
            return original
        # Broad search in output_dir
        for p in self._output_dir.rglob(f"{tc_id}_replay*.py"):
            return p
        return None

    async def _run_single(
        self,
        tc_id: str,
        title: str,
        script_path: Path,
        page: Any,
        credentials: Any,
        user_messages: Callable[[], list[str]] | None = None,
    ) -> ScriptRunResult:
        """Run a single script with retry-step-then-restart strategy."""
        start = time.perf_counter()
        patches_applied = 0
        current_script = script_path.read_text(encoding="utf-8")
        steps_completed: list[StepResult] = []

        for attempt in range(_MAX_AI_INTERVENTIONS + 1):
            if self._stop_fn():
                break

            # Drain any user messages for context
            extra_guidance = ""
            if user_messages:
                msgs = user_messages()
                if msgs:
                    extra_guidance = "\n".join(f"[USER GUIDANCE]: {m}" for m in msgs)
                    self._log(f"[INFO] Incorporating {len(msgs)} user guidance message(s).")

            exit_code, stdout, stderr, fail_step, fail_error = await self._execute_script(
                current_script, tc_id, credentials
            )

            if exit_code == 0:
                self._log(f"[SUCCESS] {title} passed (script mode, {patches_applied} patches).")
                # Save patched script as new baseline
                if patches_applied > 0:
                    patched_path = script_path.parent / f"{tc_id}_replay_patched.py"
                    patched_path.write_text(current_script, encoding="utf-8")
                    self._log(f"[INFO] Patched script saved: {patched_path.name}")

                duration_ms = int((time.perf_counter() - start) * 1000)
                return ScriptRunResult(
                    tc_id=tc_id,
                    title=title,
                    overall_status="pass",
                    duration_ms=duration_ms,
                    steps=self._parse_steps_from_stdout(stdout),
                    script_path=str(script_path),
                    patches_applied=patches_applied,
                )

            if attempt >= _MAX_AI_INTERVENTIONS:
                self._log(f"[ERROR] {title}: max AI interventions reached ({_MAX_AI_INTERVENTIONS}).")
                break

            # AI diagnosis
            self._log(f"[WARN] Script failed at step {fail_step}: {fail_error[:120]}")
            self._log(f"[INFO] AI diagnosing failure (attempt {attempt + 1}/{_MAX_AI_INTERVENTIONS})...")

            # Get page state for diagnosis
            page_url = page.url if page else "unknown"
            page_snapshot = ""
            try:
                page_snapshot = await page.accessibility.snapshot() or ""
                if isinstance(page_snapshot, dict):
                    import json
                    page_snapshot = json.dumps(page_snapshot, indent=2)[:4000]
            except Exception:
                pass

            fix = await self._ai_diagnose(
                script_content=current_script,
                fail_step=fail_step,
                fail_error=fail_error,
                page_url=page_url,
                page_snapshot=page_snapshot,
                extra_guidance=extra_guidance,
                stderr=stderr,
            )

            if not fix or not fix.get("fixed_code"):
                self._log("[WARN] AI could not produce a fix. Stopping retries.")
                break

            self._log(f"[INFO] AI diagnosis: {fix.get('diagnosis', 'unknown')}")

            # Apply the patch
            patched = self._apply_patch(current_script, fail_step, fix["fixed_code"])
            if patched == current_script:
                self._log("[WARN] Patch did not change the script. Stopping retries.")
                break

            current_script = patched
            patches_applied += 1
            self._log(f"[INFO] Patch applied to step {fail_step}. Retrying...")

            # Strategy: first retry just continues (script re-runs from top)
            # On second+ failure of same step, full restart is implicit since
            # the subprocess always runs the full script

        duration_ms = int((time.perf_counter() - start) * 1000)
        return ScriptRunResult(
            tc_id=tc_id,
            title=title,
            overall_status="fail",
            duration_ms=duration_ms,
            steps=self._parse_steps_from_stdout(stdout if "stdout" in dir() else ""),
            script_path=str(script_path),
            patches_applied=patches_applied,
        )

    async def _execute_script(
        self,
        script_content: str,
        tc_id: str,
        credentials: Any,
    ) -> tuple[int, str, str, int, str]:
        """Run script in subprocess. Returns (exit_code, stdout, stderr, fail_step, fail_error)."""
        import tempfile

        # Write script to temp file
        tmp = Path(tempfile.mktemp(suffix=".py", prefix=f"e2e_{tc_id}_"))
        tmp.write_text(script_content, encoding="utf-8")

        env_vars = {
            "E2E_PASSWORD": credentials.password,
            "E2E_HEADED": "1",  # headed so AI can screenshot if needed
        }

        import os
        full_env = {**os.environ, **env_vars}

        try:
            proc = await asyncio.create_subprocess_exec(
                "python", str(tmp),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=300
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0

            # Stream stdout to logs
            for line in stdout.splitlines():
                if line.strip():
                    self._log(f"[SCRIPT] {line}")

            # Parse failure
            fail_step = 0
            fail_error = ""
            for line in stdout.splitlines():
                m = _FAIL_RE.match(line)
                if m:
                    fail_step = int(m.group(1))
                    fail_error = m.group(2)

            if exit_code != 0 and not fail_error:
                fail_error = stderr[-500:] if stderr else "Non-zero exit with no [FAIL] marker"

            return exit_code, stdout, stderr, fail_step, fail_error
        except asyncio.TimeoutError:
            return 1, "", "Script execution timed out (300s)", 0, "Timeout"
        finally:
            tmp.unlink(missing_ok=True)

    async def _ai_diagnose(
        self,
        script_content: str,
        fail_step: int,
        fail_error: str,
        page_url: str,
        page_snapshot: str,
        extra_guidance: str,
        stderr: str,
    ) -> dict[str, str] | None:
        """Ask AI to diagnose the failure and suggest a fix."""
        # Extract the failing step's code
        step_code = self._extract_step_code(script_content, fail_step)

        user_msg = (
            f"Script failed at step {fail_step}.\n\n"
            f"## Failing step code:\n```python\n{step_code}\n```\n\n"
            f"## Error:\n{fail_error}\n\n"
            f"## Current page URL:\n{page_url}\n\n"
            f"## Page accessibility snapshot (truncated):\n{page_snapshot[:3000]}\n\n"
            f"## Stderr (last 500 chars):\n{stderr[-500:]}\n\n"
        )
        if extra_guidance:
            user_msg += f"## User guidance from this session:\n{extra_guidance}\n\n"
        if self._project_context:
            user_msg += f"## Project context:\n{self._project_context[:2000]}\n\n"

        user_msg += "Provide your diagnosis and fix as JSON."

        try:
            response = await self._llm.complete_async(
                model=self._model,
                system=_OVERSIGHT_SYSTEM,
                user=user_msg,
                max_tokens=4096,
                temperature=0.2,
            )
            # Parse JSON from response
            import json
            # Extract JSON from possibly markdown-wrapped response
            json_match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except Exception as exc:
            self._log(f"[WARN] AI diagnosis failed: {exc}")
        return None

    def _extract_step_code(self, script: str, step_num: int) -> str:
        """Extract the code block for a specific step number."""
        lines = script.splitlines()
        start_idx = None
        end_idx = None

        for i, line in enumerate(lines):
            if f"print(f'[STEP:{step_num}]" in line or f'print(f"[STEP:{step_num}]' in line:
                start_idx = i
            elif start_idx is not None and (
                f"print(f'[STEP:{step_num + 1}]" in line
                or f'print(f"[STEP:{step_num + 1}]' in line
                or line.strip().startswith("# Cleanup")
            ):
                end_idx = i
                break

        if start_idx is None:
            return f"# Step {step_num} not found in script"

        if end_idx is None:
            end_idx = min(start_idx + 20, len(lines))

        return "\n".join(lines[start_idx:end_idx])

    def _apply_patch(self, script: str, step_num: int, fixed_code: str) -> str:
        """Replace the failing step's body with the AI-provided fix."""
        lines = script.splitlines()
        start_idx = None
        end_idx = None

        for i, line in enumerate(lines):
            if f"print(f'[STEP:{step_num}]" in line or f'print(f"[STEP:{step_num}]' in line:
                start_idx = i
            elif start_idx is not None and (
                f"print(f'[STEP:{step_num + 1}]" in line
                or f'print(f"[STEP:{step_num + 1}]' in line
                or line.strip().startswith("# Cleanup")
            ):
                end_idx = i
                break

        if start_idx is None:
            return script  # Can't find step, return unchanged

        if end_idx is None:
            end_idx = min(start_idx + 20, len(lines))

        # Keep the STEP marker line, replace the try block
        step_marker = lines[start_idx]
        indent = "        "

        # Build replacement block
        replacement = [step_marker, f"{indent}try:"]
        for code_line in fixed_code.splitlines():
            if code_line.strip():
                # Ensure proper indentation (12 spaces for inside try)
                stripped = code_line.lstrip()
                replacement.append(f"{indent}    {stripped}")
            else:
                replacement.append("")
        replacement.append(f"{indent}except Exception as _e{step_num}:")
        replacement.append(f"{indent}    print(f'[FAIL:{step_num}] {{_e{step_num}}}')")
        replacement.append(f"{indent}    raise")

        # Validate the patch parses
        test_script = "\n".join(lines[:start_idx] + replacement + lines[end_idx:])
        try:
            ast.parse(test_script)
        except SyntaxError:
            self._log("[WARN] AI fix produced invalid Python. Rejecting patch.")
            return script

        return test_script

    def _parse_steps_from_stdout(self, stdout: str) -> list[StepResult]:
        """Parse [STEP:N] and [FAIL:N] markers into StepResult list."""
        steps: list[StepResult] = []
        failed_steps: dict[int, str] = {}

        for line in stdout.splitlines():
            m = _FAIL_RE.match(line)
            if m:
                failed_steps[int(m.group(1))] = m.group(2)

        for line in stdout.splitlines():
            m = _STEP_RE.match(line)
            if m:
                num = int(m.group(1))
                desc = m.group(2)
                status = "fail" if num in failed_steps else "pass"
                steps.append(StepResult(
                    step_num=num,
                    action=desc,
                    expected="Step completes successfully",
                    actual=failed_steps.get(num, "Passed"),
                    status=status,
                ))

        return steps
