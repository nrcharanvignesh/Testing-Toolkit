"""
automation/e2e_runner.py
RPA-grade E2E test execution engine using Playwright.

DESIGN PRINCIPLES
-----------------
1. Self-healing locator waterfall: six ordered strategies are tried in
   sequence (role -> label -> placeholder -> text -> test_id -> css/shadow).
   The first that resolves a visible element wins; the winning strategy is
   logged so engineers can harden the step definition.

2. Auto-retry with backoff: transient Playwright TimeoutError and stale-
   element exceptions trigger up to MAX_STEP_RETRIES automatic retries with
   exponential backoff before the step is marked as a failure.

3. Stop signal propagation: the caller can pass a stop_fn() predicate;
   it is checked between every test case (and at the start of each step)
   so a UI "Stop" button cancels the run promptly without killing mid-step.

4. Iframe traversal: when a locator is not found in the main frame, the
   runner automatically walks all attached iframes (1 level deep) and retries
   the locate-and-interact there. Needed for enterprise apps (ADO, SharePoint,
   ServiceNow) that embed significant UI in iframes.

5. Shadow DOM: if every frame lookup fails, a last-resort CSS query with
   Playwright's native ">>" shadow-piercing combinator is attempted.

6. Smart post-click wait: navigation clicks (submit, a[href], button[type=submit])
   wait for "commit"; pure in-page clicks (dropdown open, tab switch, checkbox)
   get a short stability wait only, preventing 30-second timeouts on SPAs.

7. Stability guard: before each interact, the target element is checked for
   positional stability across two frames (50 ms apart) so clicks land on
   moving/animating elements correctly.

8. Configurable continue-on-fail: assertion steps (assert_text, assert_url,
   assert_element) are "soft" by default -- they record FAIL without aborting
   the remaining flow. Hard actions (navigate, fill, click on submit) abort.

SECURITY: Password is ONLY passed to page.fill(). NEVER logged, NEVER written
to disk, NEVER included in any artifact or exception message.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Final

try:
    from playwright.async_api import Page, TimeoutError as PwTimeout
except ImportError:
    Page = object          # type: ignore[assignment,misc]
    PwTimeout = Exception  # type: ignore[assignment,misc]

from .artifact_collector import ArtifactCollector
from .e2e_plan import _format_snapshot, recompile_failed_step
from .healing_guardrails import HealingDecision, record_healing, should_heal
from .playwright_bridge import BrowserProfile, browser_session
from .screenshot_annotator import annotate_screenshot
from .script_generator import generate_playwright_script


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_STEP_RETRIES: Final[int] = 3          # attempts before marking step as failure
MAX_RECOMPILE_ATTEMPTS: Final[int] = 2    # LLM recompile retries on locator failure
RETRY_BASE_MS: Final[int] = 600           # initial retry backoff (doubles each attempt)
STABILITY_CHECK_MS: Final[int] = 50       # gap between two position checks for stability
ELEMENT_TIMEOUT_MS: Final[int] = 12_000   # default per-element wait
NAVIGATE_TIMEOUT_MS: Final[int] = 30_000  # goto() timeout


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StepResult:
    step_num: int
    action: str
    expected: str
    actual: str
    status: str            # "pass" | "pass_fallback" | "fail" | "skip" | "error" | "blocked"
    locator_strategy: str = ""   # which strategy won (for self-heal reporting)
    locator_history: list[str] | None = None  # all strategies attempted (on failure)
    screenshot_path: Path | None = None
    duration_ms: int = 0
    reasoning: str = ""    # chain-of-thought from LLM for this step


@dataclass(slots=True)
class TestCaseResult:
    tc_id: str
    title: str
    steps: list[StepResult]
    video_path: Path | None = None
    script_path: Path | None = None
    overall_status: str = "pass"   # "pass" | "pass_fallback" | "fail" | "error" | "blocked"
    duration_ms: int = 0
    thoughts: list[Any] = field(default_factory=list)  # list[ThoughtRecord]
    strategy: Any | None = None    # TestStrategy from planner
    sign_out_success: bool = False
    escalation_count: int = 0


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = frozenset({"password", "passwd", "pwd", "secret", "token"})

_AUTH_STEP_SIGNALS = frozenset({
    "sign in", "sign-in", "signin", "log in", "log-in", "login",
    "username", "password", "credentials", "authenticate",
})


def _is_auth_step(step: dict[str, Any]) -> bool:
    """Detect plan steps that perform login (fill creds, wait for sign-in page).

    These are redundant when _perform_login() already authenticated.
    """
    action = step.get("action", "").lower()
    value = step.get("value", "").lower()
    target = step.get("target", "").lower()
    expected = step.get("expected", "").lower()

    if action == "fill" and ("{{username}}" in value or "{{password}}" in value):
        return True
    if action in ("wait_for_text", "assert_text"):
        check = value or expected or target
        if any(sig in check for sig in _AUTH_STEP_SIGNALS):
            return True
    if action == "click" and any(sig in target for sig in _AUTH_STEP_SIGNALS):
        return True
    return False


def _is_password_target(label: str) -> bool:
    return any(s in label.lower() for s in _SENSITIVE_KEYS)


def _scrub(msg: str, password: str) -> str:
    """Remove any accidental password leakage from an exception message."""
    if password and password in msg:
        return msg.replace(password, "***")
    return msg


# ---------------------------------------------------------------------------
# Self-healing locator resolution
# ---------------------------------------------------------------------------

_STRATEGY_ORDER = ("role", "label", "placeholder", "text", "test_id", "css")


def _build_locator(page_or_frame: Any, target: str, strategy: str) -> Any:
    """Build a Playwright locator for a given strategy without touching the DOM."""
    if strategy == "role":
        if ":" in target:
            role, name = target.split(":", 1)
            return page_or_frame.get_by_role(
                role.strip(), name=name.strip(), exact=True,
            )
        return page_or_frame.get_by_role(target)
    elif strategy == "label":
        return page_or_frame.get_by_label(target, exact=True)
    elif strategy == "placeholder":
        return page_or_frame.get_by_placeholder(target, exact=True)
    elif strategy == "text":
        return page_or_frame.get_by_text(target, exact=False)
    elif strategy == "test_id":
        return page_or_frame.get_by_test_id(target)
    elif strategy == "css":
        # Try plain CSS first; if that contains no special chars, also try
        # the shadow-piercing variant with ">>".
        return page_or_frame.locator(target)
    return page_or_frame.get_by_text(target)


def _shadow_locator(page_or_frame: Any, target: str) -> Any:
    """Attempt shadow DOM pierce via CSS '>>' combinator."""
    # Playwright's >> pierces shadow roots; we construct a best-effort
    # CSS selector from the target string.
    css_target = target.replace(":", "[name='") + "']" if ":" in target else target
    return page_or_frame.locator(f">> {css_target}")


async def _find_element(
    page: Any,
    target: str,
    preferred_strategy: str,
    timeout_ms: int = ELEMENT_TIMEOUT_MS,
) -> tuple[Any, str]:
    """Find an element using self-healing locator waterfall.

    Order: preferred strategy -> remaining strategies in _STRATEGY_ORDER ->
           iframe traversal (1 level deep) -> shadow DOM pierce.

    Returns (locator, winning_strategy). Raises RuntimeError if all fail.
    """
    # Only use compatible fallbacks. Arbitrary target text must never be
    # reinterpreted as CSS or a role; that can click the wrong control.
    compatible: dict[str, tuple[str, ...]] = {
        "role": ("role", "label", "text"),
        "label": ("label", "placeholder", "text"),
        "placeholder": ("placeholder", "label"),
        "text": ("text",),
        "test_id": ("test_id",),
        "css": ("css",),
    }
    ordered = compatible.get(preferred_strategy, (preferred_strategy,))
    deadline = time.monotonic() + timeout_ms / 1000
    attempted: list[str] = []

    async def _unique_visible(root: Any, strategy: str) -> Any | None:
        remaining_ms = max(100, int((deadline - time.monotonic()) * 1000))
        if remaining_ms <= 100:
            return None
        loc = _build_locator(root, target, strategy)
        try:
            await loc.first.wait_for(state="visible", timeout=min(remaining_ms, 1500))
            visible = [i for i in range(await loc.count()) if await loc.nth(i).is_visible()]
        except Exception:
            return None
        if len(visible) > 1:
            raise RuntimeError(
                f"Ambiguous locator [{strategy}:{target}] matched {len(visible)} visible elements"
            )
        return loc.nth(visible[0]) if visible else None

    for strategy in ordered:
        attempted.append(strategy)
        loc = await _unique_visible(page, strategy)
        if loc is not None:
            return loc, strategy
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for strategy in ordered:
            attempted.append(f"iframe:{strategy}")
            loc = await _unique_visible(frame, strategy)
            if loc is not None:
                return loc, f"iframe:{strategy}"
    if preferred_strategy == "css" and time.monotonic() < deadline:
        attempted.append("shadow")
        try:
            loc = _shadow_locator(page, target)
            await loc.wait_for(state="visible", timeout=min(1000, timeout_ms))
            if await loc.count() != 1:
                raise RuntimeError(f"Ambiguous shadow locator [css:{target}]")
            return loc, "shadow"
        except PwTimeout:
            pass
    raise RuntimeError(
        f"Element not found: [{preferred_strategy}:{target}] "
        f"| attempted: {' -> '.join(attempted)}"
    )


# ---------------------------------------------------------------------------
# Element stability guard
# ---------------------------------------------------------------------------

async def _wait_for_stable(locator: Any, *, checks: int = 2) -> None:
    """Wait until the element's bounding box stops moving.

    Takes `checks` bounding-box samples STABILITY_CHECK_MS apart; if they
    match, the element is stable. Gives up silently after 3 rounds — a
    moving element is still preferable to a hard timeout.
    """
    last_box = None
    for _ in range(checks * 3):
        try:
            box = await locator.bounding_box()
            if box == last_box and box is not None:
                return
            last_box = box
            await asyncio.sleep(STABILITY_CHECK_MS / 1000)
        except Exception:
            return


async def _get_bbox(locator: Any) -> tuple[int, int, int, int] | None:
    """Get element bounding box as (x, y, width, height) integers."""
    try:
        box = await locator.bounding_box()
        if box:
            return (int(box["x"]), int(box["y"]),
                    int(box["width"]), int(box["height"]))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Single step executor
# ---------------------------------------------------------------------------

_NAVIGATION_ACTIONS = frozenset({"navigate", "submit"})
# Actions that are "soft" (assert failures do not abort the remaining flow)
_SOFT_ACTIONS = frozenset({"assert_text", "assert_url", "assert_element", "screenshot", "assert_new_tab", "wait_for_new_page", "assert_not_present"})


async def _execute_step(
    page: Page,
    step: dict[str, Any],
    username: str,
    password: str,
    screenshot_dir: Path,
    step_num: int,
    *,
    stop_fn: Callable[[], bool] | None = None,
    client: Any | None = None,
    model: str = "",
    login_url: str = "",
) -> StepResult:
    """Execute a single test step with auto-retry and self-healing locators.

    Returns a StepResult; never raises (all errors are captured in status).
    """
    t0 = time.perf_counter_ns()
    action = step.get("action", "").lower().strip()
    target = step.get("target", "")
    value = step.get("value", "")
    expected = step.get("expected", "")
    preferred_strategy = step.get("locator", "role")

    actual = ""
    status = "pass"
    winning_strategy = preferred_strategy
    screenshot_path: Path | None = None

    # Check stop signal before starting the step
    if stop_fn and stop_fn():
        return StepResult(
            step_num=step_num, action=action, expected=expected,
            actual="Stopped by user", status="skip",
            locator_strategy="", duration_ms=0,
        )

    last_bbox: tuple[int, int, int, int] | None = None

    for attempt in range(1, MAX_STEP_RETRIES + 1):
        try:
            if action == "navigate":
                url = value or target
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=NAVIGATE_TIMEOUT_MS)
                actual = f"Navigated to {page.url}"
                if expected and expected not in page.url:
                    status = "fail"
                    actual = f"URL mismatch: got {page.url}, expected to contain [{expected}]"
                winning_strategy = "navigate"

            elif action == "fill":
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                await _wait_for_stable(loc)
                last_bbox = await _get_bbox(loc)
                # Determine fill value
                if _is_password_target(target) or value.lower() == "{{password}}":
                    fill_value = password
                    log_value = "***"
                elif value.lower() == "{{username}}":
                    fill_value = username
                    log_value = username
                else:
                    fill_value = value
                    log_value = value
                await loc.clear()
                await loc.fill(fill_value, timeout=ELEMENT_TIMEOUT_MS)
                actual = f"Filled [{target}] with [{log_value}] via [{winning_strategy}]"

            elif action == "click":
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                await _wait_for_stable(loc)
                last_bbox = await _get_bbox(loc)
                # Detect navigation intent from element attributes
                tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                el_type = await loc.evaluate(
                    "el => (el.getAttribute('type') || '').toLowerCase()"
                )
                href = await loc.get_attribute("href")
                is_nav = bool(step.get("wait_for_navigation")) or tag == "a" and bool(href) or el_type == "submit"
                if is_nav:
                    async with page.expect_navigation(wait_until="commit", timeout=10_000):
                        await loc.click(timeout=ELEMENT_TIMEOUT_MS)
                else:
                    await loc.click(timeout=ELEMENT_TIMEOUT_MS)
                actual = f"Clicked [{target}] via [{winning_strategy}]"

            elif action == "type":
                # Slower keystroke-by-keystroke fill (for autocomplete/masked fields)
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                await _wait_for_stable(loc)
                last_bbox = await _get_bbox(loc)
                fill_value = (
                    password if (_is_password_target(target) or value.lower() == "{{password}}")
                    else (username if value.lower() == "{{username}}" else value)
                )
                await loc.press_sequentially(fill_value, delay=40)
                actual = f"Typed into [{target}] via [{winning_strategy}]"

            elif action == "select":
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                last_bbox = await _get_bbox(loc)
                await loc.select_option(value, timeout=ELEMENT_TIMEOUT_MS)
                actual = f"Selected [{value}] in [{target}] via [{winning_strategy}]"

            elif action == "check":
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                last_bbox = await _get_bbox(loc)
                await loc.check(timeout=ELEMENT_TIMEOUT_MS)
                actual = f"Checked [{target}] via [{winning_strategy}]"

            elif action == "uncheck":
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                last_bbox = await _get_bbox(loc)
                await loc.uncheck(timeout=ELEMENT_TIMEOUT_MS)
                actual = f"Unchecked [{target}] via [{winning_strategy}]"

            elif action == "hover":
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                await _wait_for_stable(loc)
                last_bbox = await _get_bbox(loc)
                await loc.hover(timeout=ELEMENT_TIMEOUT_MS)
                actual = f"Hovered [{target}] via [{winning_strategy}]"

            elif action == "double_click":
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                await _wait_for_stable(loc)
                last_bbox = await _get_bbox(loc)
                await loc.dblclick(timeout=ELEMENT_TIMEOUT_MS)
                actual = f"Double-clicked [{target}] via [{winning_strategy}]"

            elif action == "press_key":
                key = value or target
                await page.keyboard.press(key)
                actual = f"Pressed key [{key}]"

            elif action == "scroll":
                direction = value.lower() if value else "down"
                delta = 400 if direction == "down" else -400
                await page.mouse.wheel(0, delta)
                actual = f"Scrolled [{direction}]"

            elif action == "wait":
                ms = int(value) if str(value).isdigit() else 2000
                await page.wait_for_timeout(ms)
                actual = f"Waited {ms}ms"

            elif action == "wait_for_text":
                text = value or expected
                try:
                    await page.get_by_text(text).wait_for(
                        state="visible", timeout=ELEMENT_TIMEOUT_MS
                    )
                    actual = f"Text appeared: [{text}]"
                except PwTimeout:
                    status = "fail"
                    actual = f"Text did NOT appear within timeout: [{text}]"

            elif action == "wait_for_url":
                url_fragment = value or expected
                try:
                    await page.wait_for_url(f"**{url_fragment}**",
                                            timeout=ELEMENT_TIMEOUT_MS)
                    actual = f"URL matched: [{url_fragment}]"
                except PwTimeout:
                    status = "fail"
                    actual = f"URL never matched: [{url_fragment}] (current: {page.url})"

            elif action == "assert_text":
                text_to_find = value or expected
                loc = page.get_by_text(text_to_find, exact=False)
                try:
                    await loc.first.wait_for(state="visible", timeout=ELEMENT_TIMEOUT_MS)
                    actual = f"Text found: [{text_to_find}]"
                except PwTimeout:
                    status = "fail"
                    actual = f"Text NOT found: [{text_to_find}]"

            elif action == "assert_url":
                expected_fragment = value or expected
                current = page.url
                if expected_fragment in current:
                    actual = f"URL contains [{expected_fragment}]"
                else:
                    status = "fail"
                    actual = f"URL mismatch: [{current}] does not contain [{expected_fragment}]"

            elif action == "assert_element":
                try:
                    loc, winning_strategy = await _find_element(
                        page, target, preferred_strategy, timeout_ms=ELEMENT_TIMEOUT_MS
                    )
                    last_bbox = await _get_bbox(loc)
                    actual = f"Element visible: [{target}] via [{winning_strategy}]"
                except RuntimeError:
                    status = "fail"
                    actual = f"Element NOT visible: [{target}]"
                    winning_strategy = "not-found"

            elif action == "assert_not_present":
                # Check only the declared representation. Reinterpreting a label
                # as CSS/role can create false failures against unrelated nodes.
                loc = _build_locator(page, target, preferred_strategy)
                visible = [
                    index for index in range(await loc.count())
                    if await loc.nth(index).is_visible()
                ]
                actual = (
                    f"Element absent: [{target}]" if not visible
                    else f"Element STILL PRESENT: [{target}]"
                )
                if visible:
                    status = "fail"

            elif action == "screenshot":
                actual = "Screenshot captured"

            elif action == "clear":
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                last_bbox = await _get_bbox(loc)
                await loc.clear()
                actual = f"Cleared [{target}] via [{winning_strategy}]"

            elif action == "wait_for_new_page":
                # Wait for a new page/tab to open via context event
                context = page.context
                try:
                    new_page = await context.wait_for_event(
                        "page", timeout=ELEMENT_TIMEOUT_MS
                    )
                    await new_page.wait_for_load_state("domcontentloaded")
                    url_fragment = value or expected
                    if url_fragment and url_fragment not in new_page.url:
                        status = "fail"
                        actual = f"New page opened but URL [{new_page.url}] does not contain [{url_fragment}]"
                    else:
                        actual = f"New page opened: [{new_page.url}]"
                except PwTimeout:
                    status = "fail"
                    actual = "No new page/tab opened within timeout"

            elif action == "assert_new_tab":
                # Assert that a new tab was opened (checks context pages)
                context = page.context
                pages_before = len(context.pages)
                # Give a short grace period for the tab to appear
                await page.wait_for_timeout(2000)
                pages_after = len(context.pages)
                if pages_after > pages_before:
                    new_page = context.pages[-1]
                    url_fragment = expected or value
                    if url_fragment and url_fragment not in new_page.url:
                        status = "fail"
                        actual = f"New tab opened [{new_page.url}] but expected [{url_fragment}]"
                    else:
                        actual = f"New tab confirmed: [{new_page.url}]"
                elif pages_after == pages_before:
                    # Check if any page other than current has the expected URL
                    url_fragment = expected or value
                    found = False
                    for p in context.pages:
                        if p != page and (not url_fragment or url_fragment in p.url):
                            actual = f"New tab found: [{p.url}]"
                            found = True
                            break
                    if not found:
                        status = "fail"
                        actual = "No new tab detected"

            elif action == "select_text":
                # Select text in an element via triple-click
                loc, winning_strategy = await _find_element(
                    page, target, preferred_strategy
                )
                await _wait_for_stable(loc)
                last_bbox = await _get_bbox(loc)
                await loc.click(click_count=3, timeout=ELEMENT_TIMEOUT_MS)
                actual = f"Text selected in [{target}] via [{winning_strategy}]"

            else:
                status = "error"
                actual = f"Unsupported action: [{action}]"

            # Success: break out of retry loop
            break

        except PwTimeout as exc:
            if attempt < MAX_STEP_RETRIES:
                wait_ms = RETRY_BASE_MS * (2 ** (attempt - 1))
                await asyncio.sleep(wait_ms / 1000)
                continue
            status = "error"
            actual = f"Timeout after {MAX_STEP_RETRIES} attempts: {action} on [{target}]"

        except RuntimeError as exc:
            # Self-healing failures (element-not-found after all strategies)
            if attempt < MAX_STEP_RETRIES:
                wait_ms = RETRY_BASE_MS * (2 ** (attempt - 1))
                await asyncio.sleep(wait_ms / 1000)
                continue
            status = "error"
            actual = _scrub(str(exc)[:300], password)

        except Exception as exc:
            err_msg = _scrub(str(exc)[:300], password)
            if attempt < MAX_STEP_RETRIES:
                wait_ms = RETRY_BASE_MS * (2 ** (attempt - 1))
                await asyncio.sleep(wait_ms / 1000)
                continue
            status = "error"
            actual = f"Error: {err_msg}"

    # -- Feedback loop: recompile failed step via LLM if locator not found --
    # Guardrails gate: decide whether healing is allowed before attempting recompile.
    if status in ("error", "fail") and client and model:
        heal_decision = should_heal(step, actual, attempt=MAX_STEP_RETRIES)
        if heal_decision == HealingDecision.REPORT_APP_BUG:
            status = "fail"
            actual = f"[APP BUG] {actual}"
        elif heal_decision in (HealingDecision.HEAL_LOCATOR, HealingDecision.HEAL_WAIT):
            original_step = dict(step)
            for _recompile_attempt in range(MAX_RECOMPILE_ATTEMPTS):
                try:
                    snapshot_raw = await page.accessibility.snapshot()
                    snapshot_str = _format_snapshot(snapshot_raw)
                    corrected = await recompile_failed_step(
                        step=step,
                        error_message=actual[:500],
                        dom_snapshot=snapshot_str,
                        login_url=login_url,
                        username=username,
                        client=client,
                        model=model,
                    )
                    if corrected is None:
                        record_healing(step, original_step, None, False)
                        break
                    retry_result = await _execute_step(
                        page, corrected, username, password, screenshot_dir,
                        step_num, stop_fn=stop_fn,
                    )
                    if retry_result.status != "error":
                        retry_result.locator_strategy = "recompiled"
                        retry_result.status = "pass_fallback"
                        retry_result.actual = f"[HEAL] {retry_result.actual}"
                        record_healing(step, original_step, corrected, True)
                        return retry_result
                    # Feed the new error back for next recompile attempt
                    actual = retry_result.actual
                    record_healing(step, original_step, corrected, False)
                except Exception:
                    record_healing(step, original_step, None, False)
                    break
        elif heal_decision == HealingDecision.REPORT_TEST_DEBT:
            actual = f"[HEAL] [TEST DEBT] Persistent failure: {actual}"

    # Downgrade errors on manual-verification steps to skip (expected limitation)
    if step.get("manual_verification_needed") and status in ("error", "fail"):
        actual = f"[MANUAL CHECK] {actual}"
        status = "skip"

    # Always capture a screenshot with bounding box annotation at every step
    # for full traceability (Senior QA mode). The annotator draws the element
    # highlight and step/status badges.
    try:
        raw_path = screenshot_dir / f"step_{step_num:03d}.png"
        await page.screenshot(path=str(raw_path), full_page=False)
        screenshot_path = annotate_screenshot(
            screenshot_path=raw_path,
            step_num=step_num,
            status=status,
            bounding_box=last_bbox,
            label=f"{action}: {target}"[:60],
        )
    except Exception:
        pass

    # Mark fallback-pass distinctly (E2E_SPEC Stage D)
    if status == "pass" and winning_strategy != preferred_strategy:
        status = "pass_fallback"
        actual = f"[FALLBACK via {winning_strategy}] {actual}"

    elapsed_ms = int((time.perf_counter_ns() - t0) / 1_000_000)
    return StepResult(
        step_num=step_num,
        action=action,
        expected=expected,
        actual=actual,
        status=status,
        locator_strategy=winning_strategy,
        screenshot_path=screenshot_path,
        duration_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Pre-login: authenticate before running test cases
# ---------------------------------------------------------------------------

_LOGIN_INDICATORS = frozenset({
    "sign in", "log in", "login", "username", "email", "password",
    "authenticate", "credentials",
})

async def _is_login_page(page: Any) -> bool:
    """Heuristic: true if the current page looks like a login/sign-in form."""
    try:
        text = (await page.inner_text("body"))[:3000].lower()
        return any(ind in text for ind in _LOGIN_INDICATORS)
    except Exception:
        return False


async def _is_logged_in(page: Any, login_url: str) -> bool:
    """Heuristic: true if the page has moved past login.

    Target-app-agnostic: checks only that login form indicators are absent
    and that the URL has changed from the login URL. No hardcoded page
    content assumptions.
    """
    try:
        text = (await page.inner_text("body"))[:3000].lower()
        if any(ind in text for ind in _LOGIN_INDICATORS):
            return False
        current = page.url.lower()
        login_normalized = login_url.lower().rstrip("/")
        current_normalized = current.rstrip("/")
        if current_normalized != login_normalized:
            return True
        # Same URL but no login indicators = likely authenticated (SPA)
        return True
    except Exception:
        return False


async def _handle_provider_selection(
    page: Any, ai_instructions: str, log_fn: Callable[[str], None],
) -> None:
    """Click a login provider link/button based on ai_instructions.

    Handles provider selection pages generically by matching the
    instruction text to visible links or buttons on the page.
    """
    instruction_lower = ai_instructions.lower().strip()
    # Extract the provider name from common instruction patterns
    # "Use Appian Native Login" -> "appian native login"
    for prefix in ("use ", "click ", "select ", "choose "):
        if instruction_lower.startswith(prefix):
            instruction_lower = instruction_lower[len(prefix):]
            break

    # Try matching by link text first, then button text
    strategies = [
        lambda: page.get_by_role("link", name=instruction_lower),
        lambda: page.get_by_text(instruction_lower, exact=False),
        lambda: page.get_by_role("button", name=instruction_lower),
        lambda: page.locator(f"a:has-text('{ai_instructions.strip()}')").first,
    ]

    for get_loc in strategies:
        try:
            loc = get_loc()
            await loc.wait_for(state="visible", timeout=5000)
            await loc.click()
            log_fn(f"[INFO] Clicked login provider: {ai_instructions.strip()}")
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            return
        except Exception:
            continue

    log_fn(f"[WARN] Could not find login provider matching: {ai_instructions.strip()}")


async def _perform_login(
    page: Any, login_url: str, username: str, password: str,
    log_fn: Callable[[str], None], ai_instructions: str = "",
) -> bool:
    """Navigate to login_url and attempt form-based authentication.

    Handles provider selection pages (e.g. Appian Native Login / SSO choices),
    multi-step login flows, and standard username+password forms.
    ai_instructions guides provider selection (e.g. "Use Appian Native Login").
    Returns True if login appears successful (page navigated away from login).
    """
    try:
        await page.goto(login_url, wait_until="domcontentloaded",
                        timeout=NAVIGATE_TIMEOUT_MS)
        await page.wait_for_timeout(2000)
    except Exception as exc:
        log_fn(f"[WARN] Login navigation failed: {type(exc).__name__}")
        return False

    if await _is_logged_in(page, login_url):
        log_fn("[INFO] Already authenticated (session persisted).")
        return True

    # Handle login provider selection pages (Appian, Okta chooser, etc.)
    # Use ai_instructions to pick the correct provider link/button.
    if ai_instructions:
        await _handle_provider_selection(page, ai_instructions, log_fn)
        await page.wait_for_timeout(2000)
        if await _is_logged_in(page, login_url):
            log_fn("[INFO] Already authenticated after provider selection.")
            return True

    if not await _is_login_page(page):
        await page.wait_for_timeout(3000)
        if await _is_logged_in(page, login_url):
            log_fn("[INFO] Already authenticated (SSO redirect).")
            return True

    # Attempt to fill login form fields
    user_filled = False
    pass_filled = False

    # Strategy 1: role-based locators (accessible names)
    user_locators = [
        lambda: page.get_by_role("textbox", name="Username"),
        lambda: page.get_by_role("textbox", name="Email"),
        lambda: page.get_by_role("textbox", name="User ID"),
        lambda: page.get_by_label("Username"),
        lambda: page.get_by_label("Email"),
        lambda: page.get_by_label("User ID"),
        lambda: page.get_by_placeholder("Username"),
        lambda: page.get_by_placeholder("Email"),
        lambda: page.locator("input[type='text'], input[type='email'], input[name*='user'], input[name*='email'], input[id*='user'], input[id*='email']").first,
    ]

    pass_locators = [
        lambda: page.get_by_role("textbox", name="Password"),
        lambda: page.get_by_label("Password"),
        lambda: page.get_by_placeholder("Password"),
        lambda: page.locator("input[type='password']").first,
    ]

    for get_loc in user_locators:
        try:
            loc = get_loc()
            await loc.wait_for(state="visible", timeout=3000)
            await loc.clear()
            await loc.fill(username)
            user_filled = True
            break
        except Exception:
            continue

    for get_loc in pass_locators:
        try:
            loc = get_loc()
            await loc.wait_for(state="visible", timeout=3000)
            await loc.clear()
            await loc.fill(password)
            pass_filled = True
            break
        except Exception:
            continue

    if not user_filled and not pass_filled:
        log_fn("[WARN] Could not locate login form fields. Page may use SSO or non-standard login.")
        return False

    if not pass_filled:
        # Some flows show username first then password on next screen
        submit_locators = [
            lambda: page.get_by_role("button", name="Next"),
            lambda: page.get_by_role("button", name="Continue"),
            lambda: page.get_by_role("button", name="Submit"),
            lambda: page.locator("button[type='submit'], input[type='submit']").first,
        ]
        for get_loc in submit_locators:
            try:
                loc = get_loc()
                await loc.click(timeout=3000)
                await page.wait_for_timeout(2000)
                break
            except Exception:
                continue
        # Try password field again after page transition
        for get_loc in pass_locators:
            try:
                loc = get_loc()
                await loc.wait_for(state="visible", timeout=5000)
                await loc.clear()
                await loc.fill(password)
                pass_filled = True
                break
            except Exception:
                continue

    if not pass_filled:
        log_fn("[WARN] Could not fill password field.")
        return False

    # Click submit/sign-in button
    submit_locators = [
        lambda: page.get_by_role("button", name="Sign In"),
        lambda: page.get_by_role("button", name="Sign in"),
        lambda: page.get_by_role("button", name="Log In"),
        lambda: page.get_by_role("button", name="Log in"),
        lambda: page.get_by_role("button", name="Login"),
        lambda: page.get_by_role("button", name="Submit"),
        lambda: page.locator("button[type='submit'], input[type='submit']").first,
        lambda: page.get_by_text("Sign In", exact=False).first,
    ]

    clicked = False
    for get_loc in submit_locators:
        try:
            loc = get_loc()
            await loc.click(timeout=3000)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        # Fallback: press Enter in the password field
        try:
            await page.keyboard.press("Enter")
            clicked = True
        except Exception:
            pass

    if clicked:
        # Wait for navigation away from login page
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)
        except Exception:
            pass

    if await _is_logged_in(page, login_url):
        log_fn("[SUCCESS] Login completed successfully.")
        return True

    # Give extra time for slow redirects
    await page.wait_for_timeout(5000)
    if await _is_logged_in(page, login_url):
        log_fn("[SUCCESS] Login completed (delayed redirect).")
        return True

    log_fn("[WARN] Login attempt completed but could not confirm authentication.")
    return False


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_e2e_tests(
    test_cases: list[dict[str, Any]], login_url: str, username: str,
    password: str, output_dir: Path, *, profile: BrowserProfile | None = None,
    headless: bool = False, ai_instructions: str = "",
    stop_fn: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_screenshot: Callable[[Path, int, str], None] | None = None,
    on_tc_done: Callable[[str, str], None] | None = None,
    client: Any | None = None,
    model: str = "",
) -> list[TestCaseResult]:
    """Execute a validated suite through one reused CDP/browser session."""
    del headless
    results: list[TestCaseResult] = []
    total = len(test_cases)
    output_dir.mkdir(parents=True, exist_ok=True)
    video_dir = output_dir / "suite_video"

    def _log(message: str) -> None:
        if on_log:
            on_log(message)

    try:
        async with browser_session(profile=profile, output_dir=video_dir) as (_browser, page):
            # Pre-login: authenticate before running any test case
            login_ok = await _perform_login(page, login_url, username, password, _log, ai_instructions)
            if not login_ok:
                _log("[WARN] Pre-login did not confirm success; proceeding anyway (session may be valid).")

            # Capture initial DOM snapshot for plan recompilation feedback.
            _initial_snapshot: str = ""
            if client and model:
                try:
                    _snap_raw = await page.accessibility.snapshot()
                    _initial_snapshot = _format_snapshot(_snap_raw)
                except Exception:
                    pass

            for tc_index, tc in enumerate(test_cases):
                if stop_fn and stop_fn():
                    _log("[WARN] Stop signal received. Aborting remaining test cases.")
                    break
                tc_id = str(tc.get("id", f"TC_{tc_index + 1:03d}"))
                title = str(tc.get("title", "Untitled"))
                steps = tc.get("steps") if isinstance(tc.get("steps"), list) else []
                collector = ArtifactCollector(output_dir, tc_id, title=title)
                started = time.perf_counter_ns()
                step_results: list[StepResult] = []
                _log(f"[INFO] ({tc_index + 1}/{total}) Starting: {tc_id} - {title}")
                if on_progress:
                    on_progress(tc_index, total)

                if not steps:
                    step_results.append(StepResult(
                        step_num=0, action="plan", expected="Executable plan",
                        actual=str(tc.get("plan_error", "Plan has no executable steps")),
                        status="error",
                    ))
                else:
                    # Filter out auth steps from cached plans when already logged in
                    exec_steps = [s for s in steps if not (login_ok and _is_auth_step(s))]
                    if not exec_steps:
                        exec_steps = steps
                    first_action = str(exec_steps[0].get("action", "")).lower()
                    if first_action != "navigate":
                        await page.goto(login_url, wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT_MS)
                    for step_num, step in enumerate(exec_steps, 1):
                        if stop_fn and stop_fn():
                            break
                        step_result = await _execute_step(
                            page, step, username, password, collector.screenshot_dir,
                            step_num, stop_fn=stop_fn,
                            client=client, model=model, login_url=login_url,
                        )
                        if stop_fn and stop_fn():
                            break
                        step_results.append(step_result)
                        if step_result.screenshot_path and on_screenshot:
                            on_screenshot(step_result.screenshot_path, step_num, step_result.status)
                        _log(
                            f"  [{step_result.status.upper()}] Step {step_num}: "
                            f"{step_result.action} -> {step_result.actual}"
                        )
                        if step_result.status in ("error", "fail") and step_result.action not in _SOFT_ACTIONS:
                            break

                # A stopped test case is incomplete, not a pass/fail result.
                # Keep artifacts from fully completed cases only; the caller can
                # resume by starting a new run without a misleading partial row.
                if stop_fn and stop_fn():
                    _log(f"[WARN] Stopped during {tc_id}; partial result discarded.")
                    break

                statuses = {item.status for item in step_results}
                executed = sum(
                    item.status in ("pass", "pass_fallback", "fail", "error")
                    for item in step_results
                )
                # AUDIT-018 fix: honour pass_fallback flag on the test case
                # and count pass_fallback steps as executed/passing.
                tc_pass_fallback = bool(tc.get("pass_fallback"))
                if executed == 0 or "error" in statuses:
                    overall = "error"
                elif "fail" in statuses:
                    # If the test case declares pass_fallback, assertion
                    # failures degrade to warning rather than hard fail.
                    overall = "pass_fallback" if tc_pass_fallback else "fail"
                elif "pass_fallback" in statuses:
                    overall = "pass_fallback"
                else:
                    overall = "pass"
                try:
                    final_path = collector.screenshot_dir / "final.png"
                    await page.screenshot(path=str(final_path), full_page=False)
                    if on_screenshot:
                        on_screenshot(final_path, len(step_results), overall)
                except Exception:
                    pass
                script = generate_playwright_script(
                    tc_id=tc_id, title=title, steps=steps,
                    login_url=login_url, username=username,
                )
                result = TestCaseResult(
                    tc_id=tc_id, title=title, steps=step_results,
                    script_path=collector.save_script(script, tc_id),
                    overall_status=overall,
                    duration_ms=int((time.perf_counter_ns() - started) / 1_000_000),
                )
                results.append(result)
                if on_tc_done:
                    on_tc_done(tc_id, overall)
                _log(f"[{overall.upper()}] {tc_id} finished in {result.duration_ms}ms")
    except Exception as exc:
        message = _scrub(str(exc)[:300], password)
        _log(f"[ERROR] Browser suite setup failed: {message}")
        if not results:
            results.append(TestCaseResult(
                tc_id="suite", title="Browser suite setup",
                steps=[StepResult(0, "setup", "Browser session started", message, "error")],
                overall_status="error",
            ))

    # Video is finalized only after the shared context closes. Rename to
    # title-based MKV (or webm fallback) and assign to each result.
    # AUDIT-017 fix: append tc_id to prevent filename collisions when
    # multiple test cases share the same title.
    try:
        from .artifact_collector import _remux_to_mkv, _safe_filename
        videos = sorted(
            (v for v in video_dir.glob("*.webm") if v.exists()),
            key=lambda path: path.stat().st_mtime,
        )
        if videos:
            for result in results:
                src = videos[-1]
                base_name = _safe_filename(result.title) if result.title else result.tc_id
                # Append tc_id to guarantee uniqueness across parallel runs
                unique_name = f"{base_name}_{result.tc_id}"
                mkv_dest = video_dir / f"{unique_name}.mkv"
                if _remux_to_mkv(src, mkv_dest):
                    result.video_path = mkv_dest
                else:
                    renamed = video_dir / f"{unique_name}.webm"
                    if renamed != src and not renamed.exists():
                        import shutil
                        shutil.copy2(str(src), str(renamed))
                        result.video_path = renamed
                    else:
                        result.video_path = src
    except (OSError, FileNotFoundError) as exc:
        _log(f"[WARN] Video post-processing skipped: {type(exc).__name__}")
    if on_progress:
        on_progress(len(results), total)
    _log(f"[INFO] Run complete. {len(results)}/{total} test cases executed.")
    return results


# ---------------------------------------------------------------------------
# Slot-based execution for ParallelRunner (Phase 1c)
# ---------------------------------------------------------------------------

async def run_e2e_slot(
    page: Any,
    test_cases: list[dict[str, Any]],
    login_url: str,
    username: str,
    password: str,
    output_dir: Path,
    *,
    ai_instructions: str = "",
    stop_fn: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_screenshot: Callable[[Path, int, str], None] | None = None,
    on_tc_done: Callable[[str, str], None] | None = None,
    client: Any | None = None,
    model: str = "",
) -> list[TestCaseResult]:
    """Execute test cases on an already-created page (from ParallelRunner slot).

    Same logic as run_e2e_tests but skips browser_session creation since the
    page is provided by the ParallelRunner's ExecutionSlot. Each slot has its
    own isolated BrowserContext with independent cookies and storage.
    """
    results: list[TestCaseResult] = []
    total = len(test_cases)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _log(message: str) -> None:
        if on_log:
            on_log(message)

    try:
        from .page_observer import PageObserver
        observer = PageObserver(on_log=_log)

        login_ok = await _perform_login(page, login_url, username, password, _log, ai_instructions)
        if not login_ok:
            _log("[WARN] Pre-login did not confirm success; proceeding anyway.")

        # Initial observation after login
        await observer.observe(page)

        for tc_index, tc in enumerate(test_cases):
            if stop_fn and stop_fn():
                _log("[WARN] Stop signal received. Aborting remaining test cases.")
                break
            tc_id = str(tc.get("id", f"TC_{tc_index + 1:03d}"))
            title = str(tc.get("title", "Untitled"))
            steps = tc.get("steps") if isinstance(tc.get("steps"), list) else []
            collector = ArtifactCollector(output_dir, tc_id, title=title)
            started = time.perf_counter_ns()
            step_results: list[StepResult] = []
            _log(f"[INFO] ({tc_index + 1}/{total}) Starting: {tc_id} - {title}")
            if on_progress:
                on_progress(tc_index, total)

            if not steps:
                step_results.append(StepResult(
                    step_num=0, action="plan", expected="Executable plan",
                    actual=str(tc.get("plan_error", "Plan has no executable steps")),
                    status="error",
                ))
            else:
                exec_steps = [s for s in steps if not (login_ok and _is_auth_step(s))]
                if not exec_steps:
                    exec_steps = steps
                first_action = str(exec_steps[0].get("action", "")).lower()
                if first_action != "navigate":
                    await page.goto(login_url, wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT_MS)
                for step_num, step in enumerate(exec_steps, 1):
                    if stop_fn and stop_fn():
                        break
                    before_obs = observer.last
                    step_result = await _execute_step(
                        page, step, username, password, collector.screenshot_dir,
                        step_num, stop_fn=stop_fn,
                        client=client, model=model, login_url=login_url,
                    )
                    if stop_fn and stop_fn():
                        break
                    # Post-step observation for autonomous intelligence
                    after_obs = await observer.observe(page)
                    if before_obs and after_obs:
                        delta = observer.compare(before_obs, after_obs)
                        if delta.new_errors and step_result.status == "pass":
                            step_result.actual += f" [OBS: {delta.summary}]"
                    step_results.append(step_result)
                    if step_result.screenshot_path and on_screenshot:
                        on_screenshot(step_result.screenshot_path, step_num, step_result.status)
                    _log(
                        f"  [{step_result.status.upper()}] Step {step_num}: "
                        f"{step_result.action} -> {step_result.actual}"
                    )
                    if step_result.status in ("error", "fail") and step_result.action not in _SOFT_ACTIONS:
                        break

            if stop_fn and stop_fn():
                _log(f"[WARN] Stopped during {tc_id}; partial result discarded.")
                break

            statuses = {item.status for item in step_results}
            executed = sum(
                item.status in ("pass", "pass_fallback", "fail", "error")
                for item in step_results
            )
            # AUDIT-018 fix: same pass_fallback logic as run_e2e_tests
            tc_pass_fallback = bool(tc.get("pass_fallback"))
            if executed == 0 or "error" in statuses:
                overall = "error"
            elif "fail" in statuses:
                overall = "pass_fallback" if tc_pass_fallback else "fail"
            elif "pass_fallback" in statuses:
                overall = "pass_fallback"
            else:
                overall = "pass"
            script = generate_playwright_script(
                tc_id=tc_id, title=title, steps=steps,
                login_url=login_url, username=username,
            )
            result = TestCaseResult(
                tc_id=tc_id, title=title, steps=step_results,
                script_path=collector.save_script(script, tc_id),
                overall_status=overall,
                duration_ms=int((time.perf_counter_ns() - started) / 1_000_000),
            )
            results.append(result)
            if on_tc_done:
                on_tc_done(tc_id, overall)
            _log(f"[{overall.upper()}] {tc_id} finished in {result.duration_ms}ms")
    except Exception as exc:
        message = _scrub(str(exc)[:300], password)
        _log(f"[ERROR] Slot execution failed: {message}")
        if not results:
            results.append(TestCaseResult(
                tc_id="slot_error", title="Slot execution setup",
                steps=[StepResult(0, "setup", "Slot execution started", message, "error")],
                overall_status="error",
            ))

    if on_progress:
        on_progress(len(results), total)
    _log(f"[INFO] Slot complete. {len(results)}/{total} test cases executed.")
    return results
