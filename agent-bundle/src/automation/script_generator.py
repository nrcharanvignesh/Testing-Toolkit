"""
automation/script_generator.py
Generates rerunnable Playwright Python scripts from E2E test steps.

SECURITY: Password is NEVER embedded in generated scripts.
          Uses os.environ["E2E_PASSWORD"] placeholder instead.

The generated script structure:
    async def main() -> None:
        async with async_playwright() as pw:
            browser = ...           # 8-space block
            context = ...
            page = ...
            # Step 1: ...           # steps at 8-space indent
            await page.goto(...)
            ...
            await context.close()   # 8-space cleanup
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(value: str) -> str:
    """Escape a string for safe embedding in Python source."""
    return repr(value)


_SENSITIVE_KEYS = frozenset({"password", "passwd", "pwd", "secret", "token"})


def _is_password_target(target: str) -> bool:
    return any(s in target.lower() for s in _SENSITIVE_KEYS)


def _locator_expr(target: str, strategy: str, pf: str = "page") -> str:
    """Return the Playwright locator expression string (no await)."""
    if strategy == "role":
        if ":" in target:
            role, name = target.split(":", 1)
            return f"{pf}.get_by_role({_esc(role.strip())}, name={_esc(name.strip())})"
        return f"{pf}.get_by_role({_esc(target)})"
    elif strategy == "label":
        return f"{pf}.get_by_label({_esc(target)})"
    elif strategy == "placeholder":
        return f"{pf}.get_by_placeholder({_esc(target)})"
    elif strategy == "text":
        return f"{pf}.get_by_text({_esc(target)}, exact=False)"
    elif strategy == "test_id":
        return f"{pf}.get_by_test_id({_esc(target)})"
    elif strategy == "css":
        return f"{pf}.locator({_esc(target)})"
    else:
        if ":" in target:
            role, name = target.split(":", 1)
            return f"{pf}.get_by_role({_esc(role.strip())}, name={_esc(name.strip())})"
        return f"{pf}.get_by_text({_esc(target)}, exact=False)"


# ---------------------------------------------------------------------------
# Per-step code generation (4-space indent = caller doubles to 8 in body)
# ---------------------------------------------------------------------------

def _step_to_code(step: dict[str, Any], username: str) -> list[str]:
    """Convert one step dict to lines of Python at 4-space base indent.

    The caller prepends 4 more spaces so the lines land at 8-space inside the
    async with async_playwright() block where `page` is defined.
    """
    action = step.get("action", "").lower().strip()
    target = step.get("target", "")
    value = step.get("value", "")
    expected = step.get("expected", "")
    strategy = step.get("locator", "role")
    loc = _locator_expr(target, strategy)
    lines: list[str] = []

    if action == "navigate":
        url = value or target
        lines.append(f"    await page.goto({_esc(url)}, wait_until='domcontentloaded')")

    elif action == "fill":
        if _is_password_target(target) or value.lower() == "{{password}}":
            fill_val = "os.environ['E2E_PASSWORD']"
        elif value.lower() == "{{username}}":
            fill_val = _esc(username)
        else:
            fill_val = _esc(value)
        lines.append(f"    await {loc}.clear()")
        lines.append(f"    await {loc}.fill({fill_val})")

    elif action == "type":
        if _is_password_target(target) or value.lower() == "{{password}}":
            type_val = "os.environ['E2E_PASSWORD']"
        elif value.lower() == "{{username}}":
            type_val = _esc(username)
        else:
            type_val = _esc(value)
        lines.append(f"    await {loc}.press_sequentially({type_val}, delay=40)")

    elif action == "click":
        lines.append(f"    await {loc}.click()")

    elif action == "double_click":
        lines.append(f"    await {loc}.dblclick()")

    elif action == "hover":
        lines.append(f"    await {loc}.hover()")

    elif action == "select":
        lines.append(f"    await {loc}.select_option({_esc(value)})")

    elif action == "check":
        lines.append(f"    await {loc}.check()")

    elif action == "uncheck":
        lines.append(f"    await {loc}.uncheck()")

    elif action == "clear":
        lines.append(f"    await {loc}.clear()")

    elif action == "press_key":
        key = value or target
        lines.append(f"    await page.keyboard.press({_esc(key)})")

    elif action == "scroll":
        direction = value.lower() if value else "down"
        delta = 400 if direction == "down" else -400
        lines.append(f"    await page.mouse.wheel(0, {delta})")

    elif action == "wait":
        ms = value if str(value).isdigit() else "2000"
        lines.append(f"    await page.wait_for_timeout({ms})")

    elif action == "wait_for_text":
        text = value or expected
        lines.append(
            f"    await expect(page.get_by_text({_esc(text)}, exact=False).first)"
            f".to_be_visible()"
        )

    elif action == "wait_for_url":
        fragment = value or expected
        lines.append(f"    await page.wait_for_url('**{fragment}**')")

    elif action == "assert_text":
        text = value or expected
        lines.append(
            f"    await expect(page.get_by_text({_esc(text)}, exact=False).first)"
            f".to_be_visible()"
        )

    elif action == "assert_url":
        # Use the correct Playwright async expect API for URL assertions.
        fragment = value or expected
        lines.append(
            f"    await expect(page).to_have_url(re.compile({_esc(fragment)}))"
        )

    elif action == "assert_element":
        lines.append(f"    await expect({loc}).to_be_visible()")

    elif action == "assert_not_present":
        lines.append(f"    await expect({loc}).to_be_hidden()")

    elif action == "screenshot":
        lines.append(f"    await page.screenshot(path='screenshot_{action}.png')")

    else:
        lines.append(f"    # Unknown action: {action}")

    return lines


# ---------------------------------------------------------------------------
# Full script assembly
# ---------------------------------------------------------------------------

def generate_playwright_script(
    tc_id: str,
    title: str,
    steps: list[dict[str, Any]],
    login_url: str,
    username: str,
) -> str:
    """Generate a complete, rerunnable Playwright Python script.

    SECURITY: Password is replaced with os.environ["E2E_PASSWORD"].

    The script uses CDP attach to the user's real browser for SSO so that
    MFA/SSO state is preserved across runs.

    Args:
        tc_id:      Test case identifier.
        title:      Test case title.
        steps:      List of step dicts.
        login_url:  Starting URL.
        username:   Login username.

    Returns:
        Complete Python script string, validated with ast.parse.
    """
    header = [
        '"""',
        f"Auto-generated Playwright script for: {tc_id} - {title}",
        "",
        "SECURITY: Set E2E_PASSWORD environment variable before running.",
        "  $env:E2E_PASSWORD = 'your_password'   # PowerShell",
        "  export E2E_PASSWORD='your_password'    # Bash/zsh",
        "",
        "Requires the real browser running with --remote-debugging-port=9222:",
        "  chrome.exe --remote-debugging-port=9222 --user-data-dir=...",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import asyncio",
        "import os",
        "import re",
        "import sys",
        "",
        "from playwright.async_api import async_playwright, expect",
        "",
        "",
        f"TARGET_URL = {_esc(login_url)}",
        f"USERNAME = {_esc(username)}",
        "CDP_PORT = int(os.environ.get('CDP_PORT', '9222'))",
        "",
        "",
        "async def main() -> None:",
        '    """Execute test case steps via CDP attach."""',
        "    password = os.environ.get('E2E_PASSWORD')",
        "    if not password:",
        "        print('[ERROR] E2E_PASSWORD environment variable not set.')",
        "        sys.exit(1)",
        "",
        "    async with async_playwright() as pw:",
        "        browser = await pw.chromium.connect_over_cdp(",
        "            f'http://127.0.0.1:{CDP_PORT}'",
        "        )",
        "        context = await browser.new_context(",
        "            viewport={'width': 1920, 'height': 1080},",
        "        )",
        "        page = await context.new_page()",
        "",
    ]

    # Build step lines at 8-space indent (inside the async with block)
    body: list[str] = []
    for idx, step in enumerate(steps, start=1):
        action = step.get("action", "")
        target = step.get("target", "")
        # Comment at 8-space
        body.append(f"        # Step {idx}: {action} {target}".rstrip())
        # _step_to_code returns 4-space lines; we add 4 more to reach 8-space
        raw_lines = _step_to_code(step, username)
        for line in raw_lines:
            if line.strip():
                body.append("    " + line)   # 4 existing + 4 extra = 8 total
            else:
                body.append("")
        body.append("")

    footer = [
        "        # Cleanup",
        "        await context.close()",
        "        await browser.close()",
        "        print('[SUCCESS] Test completed.')",
        "",
        "",
        "if __name__ == '__main__':",
        "    asyncio.run(main())",
        "",
    ]

    script = "\n".join(header + body + footer)

    # Validate: if the script is unparseable, emit a safe stub instead
    try:
        ast.parse(script)
    except SyntaxError as exc:
        script = (
            f"# ERROR: Script generation failed for {tc_id}: {exc}\n"
            f"# Steps: {len(steps)}\n"
            f"# Fix the step definitions and regenerate.\n"
        )

    return script


# ---------------------------------------------------------------------------
# MCP replay script generation (with self-healing locator waterfall)
# ---------------------------------------------------------------------------

_SENSITIVE_ARGS = frozenset({"password", "passwd", "pwd", "secret", "token"})

# The self-healing helper embedded verbatim in every generated replay script.
# It mirrors the e2e_runner waterfall: CSS -> role -> label -> text -> iframe.
_SELF_HEAL_HELPER = '''
async def _find_element(page, selector, *, name="", role="", timeout=12000):
    """Self-healing locator waterfall: CSS -> role -> label -> text -> iframe.

    Falls back through multiple strategies if the primary CSS selector breaks
    due to page changes between recording and replay.
    """
    import asyncio as _aio

    deadline = _aio.get_event_loop().time() + timeout / 1000

    async def _try_visible(root, loc):
        remaining = max(100, int((deadline - _aio.get_event_loop().time()) * 1000))
        if remaining <= 100:
            return None
        try:
            await loc.first.wait_for(state="visible", timeout=min(remaining, 800))
            if await loc.count() >= 1:
                return loc.first
        except Exception:
            pass
        return None

    # Strategy 1: Original CSS selector (most specific)
    if selector:
        loc = await _try_visible(page, page.locator(selector))
        if loc:
            return loc

    # Strategy 2: Role + name (accessible, resilient to DOM changes)
    if role and name:
        loc = await _try_visible(page, page.get_by_role(role, name=name))
        if loc:
            return loc

    # Strategy 3: Label match
    if name:
        loc = await _try_visible(page, page.get_by_label(name, exact=True))
        if loc:
            return loc

    # Strategy 4: Placeholder match
    if name:
        loc = await _try_visible(page, page.get_by_placeholder(name, exact=True))
        if loc:
            return loc

    # Strategy 5: Text match (loose)
    if name:
        loc = await _try_visible(page, page.get_by_text(name, exact=False))
        if loc:
            return loc

    # Strategy 6: Iframe traversal
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        if selector:
            loc = await _try_visible(frame, frame.locator(selector))
            if loc:
                return loc
        if role and name:
            loc = await _try_visible(frame, frame.get_by_role(role, name=name))
            if loc:
                return loc
        if name:
            loc = await _try_visible(frame, frame.get_by_text(name, exact=False))
            if loc:
                return loc

    # Exhausted: fall back to raw locator (will throw if missing)
    if selector:
        return page.locator(selector).first
    raise RuntimeError(f"Element not found: selector={selector!r} name={name!r} role={role!r}")

'''


def _extract_element_hints(args: dict[str, Any]) -> tuple[str, str, str]:
    """Extract (selector, name, role) hints from MCP tool arguments.

    @playwright/mcp passes:
      - selector: CSS selector
      - element: description like "Submit button" or "Email input"
      - ref: element reference id (e.g. "e12")
      - ariaLabel: accessible label
      - role: ARIA role
    """
    selector = args.get("selector", args.get("element", ""))
    name = args.get("ariaLabel", args.get("name", ""))
    role = args.get("role", "")
    # Infer name from element description if no explicit ariaLabel
    if not name and isinstance(selector, str):
        # If selector looks like a CSS selector (has # . [ > :) keep it;
        # otherwise treat it as a text/name hint
        if not any(c in selector for c in "#.[>:~+"):
            name = selector
            selector = ""
    # Infer from ref description stored in element field
    element_desc = args.get("element", "")
    if not name and element_desc and element_desc != selector:
        name = element_desc
    return selector, name, role


def _mcp_call_to_code(call: dict[str, Any], step_num: int) -> list[str]:
    """Convert one recorded MCP tool call to Playwright code lines using
    the self-healing _find_element waterfall."""
    tool = call.get("tool", "")
    args = call.get("arguments", {})
    lines: list[str] = []

    lines.append(f"    # Step {step_num}: {tool}")

    if tool == "browser_navigate":
        url = args.get("url", "")
        lines.append(f"    await page.goto({_esc(url)}, wait_until='domcontentloaded')")

    elif tool == "browser_navigate_back":
        lines.append("    await page.go_back()")

    elif tool == "browser_click":
        selector, name, role = _extract_element_hints(args)
        lines.append(
            f"    el = await _find_element(page, {_esc(selector)}, name={_esc(name)}, role={_esc(role)})"
        )
        lines.append("    await el.click()")

    elif tool == "browser_type":
        selector, name, role = _extract_element_hints(args)
        text = args.get("text", "")
        is_sensitive = _is_password_target(selector) or _is_password_target(name)
        text_expr = "os.environ['E2E_PASSWORD']" if is_sensitive else _esc(text)
        lines.append(
            f"    el = await _find_element(page, {_esc(selector)}, name={_esc(name)}, role={_esc(role)})"
        )
        lines.append(f"    await el.press_sequentially({text_expr}, delay=40)")

    elif tool == "browser_fill_form":
        values = args.get("values", [])
        for entry in values:
            if isinstance(entry, dict):
                sel = entry.get("selector", entry.get("ariaLabel", ""))
                entry_name = entry.get("ariaLabel", entry.get("name", ""))
                val = entry.get("value", "")
                is_sensitive = _is_password_target(sel) or _is_password_target(str(entry_name))
                val_expr = "os.environ['E2E_PASSWORD']" if is_sensitive else _esc(val)
                lines.append(
                    f"    el = await _find_element(page, {_esc(sel)}, name={_esc(entry_name)})"
                )
                lines.append(f"    await el.fill({val_expr})")

    elif tool == "browser_hover":
        selector, name, role = _extract_element_hints(args)
        lines.append(
            f"    el = await _find_element(page, {_esc(selector)}, name={_esc(name)}, role={_esc(role)})"
        )
        lines.append("    await el.hover()")

    elif tool == "browser_press_key":
        key = args.get("key", "")
        lines.append(f"    await page.keyboard.press({_esc(key)})")

    elif tool == "browser_select_option":
        selector, name, role = _extract_element_hints(args)
        value = args.get("value", args.get("values", ""))
        lines.append(
            f"    el = await _find_element(page, {_esc(selector)}, name={_esc(name)}, role={_esc(role)})"
        )
        lines.append(f"    await el.select_option({_esc(str(value))})")

    elif tool == "browser_take_screenshot":
        lines.append(f"    await page.screenshot(path='mcp_screenshot_{step_num}.png')")

    elif tool == "browser_wait_for":
        selector, name, role = _extract_element_hints(args)
        state = args.get("state", "visible")
        timeout = args.get("timeout", 10000)
        if selector or name:
            lines.append(
                f"    el = await _find_element(page, {_esc(selector)}, name={_esc(name)}, role={_esc(role)}, timeout={timeout})"
            )
            lines.append(f"    await el.wait_for(state={_esc(state)}, timeout={timeout})")
        else:
            lines.append(f"    await page.wait_for_timeout({timeout})")

    elif tool == "browser_evaluate":
        expression = args.get("expression", args.get("script", ""))
        lines.append(f"    await page.evaluate({_esc(expression)})")

    elif tool == "browser_snapshot":
        lines.append("    # accessibility snapshot (informational, no replay action)")

    elif tool == "browser_resize":
        width = args.get("width", 1920)
        height = args.get("height", 1080)
        lines.append(f"    await page.set_viewport_size({{'width': {width}, 'height': {height}}})")

    elif tool == "browser_tabs":
        lines.append("    # tabs query (informational, no replay action)")

    elif tool == "browser_console_messages":
        lines.append("    # console messages query (informational, no replay action)")

    elif tool == "browser_close":
        lines.append("    # browser_close (handled in cleanup)")

    else:
        lines.append(f"    # Unrecognized MCP tool: {tool}({json.dumps(args, default=str)[:200]})")

    return lines


def generate_mcp_replay_script(
    recorded_calls: list[dict[str, Any]],
    *,
    title: str = "MCP Session Replay",
    login_url: str = "",
) -> str:
    """Generate a rerunnable Playwright script from recorded MCP tool calls.

    The generated script includes a self-healing locator waterfall that tries
    CSS -> role -> label -> text -> iframe traversal, so replays survive DOM
    changes between recording and execution.

    SECURITY: Passwords are replaced with os.environ["E2E_PASSWORD"].

    Args:
        recorded_calls: List of {tool, arguments, result/error} dicts from
                        MCPServerManager.stop_recording().
        title: Descriptive title for the script header.
        login_url: Optional starting URL (informational).

    Returns:
        Complete Python script string, validated with ast.parse.
    """
    # Filter out non-browser tools and informational-only calls
    browser_calls = [c for c in recorded_calls if c.get("tool", "").startswith("browser_")]
    if not browser_calls:
        return (
            f"# No browser actions recorded for: {title}\n"
            "# The MCP session contained no replayable Playwright actions.\n"
        )

    header = [
        '"""',
        f"MCP Session Replay: {title}",
        "",
        "Auto-generated from Playwright MCP tool call recording.",
        "Includes self-healing locator waterfall (CSS -> role -> label -> text -> iframe).",
        "SECURITY: Set E2E_PASSWORD environment variable before running.",
        "",
        "Requires the real browser running with --remote-debugging-port=9222:",
        "  chrome.exe --remote-debugging-port=9222 --user-data-dir=...",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import asyncio",
        "import os",
        "import sys",
        "",
        "from playwright.async_api import async_playwright",
        "",
    ]

    # Embed the self-healing helper
    helper_lines = _SELF_HEAL_HELPER.strip().split("\n")

    main_start = [
        "",
        "CDP_PORT = int(os.environ.get('CDP_PORT', '9222'))",
        "",
        "",
        "async def main() -> None:",
        '    """Replay recorded MCP session via CDP attach."""',
        "    async with async_playwright() as pw:",
        "        browser = await pw.chromium.connect_over_cdp(",
        "            f'http://127.0.0.1:{CDP_PORT}'",
        "        )",
        "        context = await browser.new_context(",
        "            viewport={'width': 1920, 'height': 1080},",
        "        )",
        "        page = await context.new_page()",
        "",
    ]

    body: list[str] = []
    step_num = 0
    for call in browser_calls:
        tool = call.get("tool", "")
        # Skip informational tools that produce no replay action
        if tool in ("browser_snapshot", "browser_tabs", "browser_console_messages"):
            continue
        step_num += 1
        raw_lines = _mcp_call_to_code(call, step_num)
        for line in raw_lines:
            body.append("    " + line if line.strip() else "")
        body.append("")

    footer = [
        "        # Cleanup",
        "        await context.close()",
        "        await browser.close()",
        "        print('[SUCCESS] MCP session replay completed.')",
        "",
        "",
        "if __name__ == '__main__':",
        "    asyncio.run(main())",
        "",
    ]

    script = "\n".join(header + helper_lines + main_start + body + footer)

    try:
        ast.parse(script)
    except SyntaxError as exc:
        script = (
            f"# ERROR: MCP replay script generation failed: {exc}\n"
            f"# Recorded calls: {len(browser_calls)}\n"
            "# Review the recorded tool calls and regenerate.\n"
        )

    return script


def save_mcp_replay_script(
    recorded_calls: list[dict[str, Any]],
    output_dir: Path,
    *,
    title: str = "MCP Session Replay",
    login_url: str = "",
) -> Path:
    """Generate and save an MCP replay script to the output directory.

    Returns the path to the saved script file.
    """
    script = generate_mcp_replay_script(
        recorded_calls, title=title, login_url=login_url,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path = output_dir / "mcp_replay.py"
    script_path.write_text(script, encoding="utf-8")
    return script_path
