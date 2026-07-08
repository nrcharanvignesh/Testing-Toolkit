"""
automation/script_generator.py
Generates rerunnable Playwright Python scripts from test steps.

SECURITY: Password is NEVER embedded in generated scripts.
Uses os.environ["E2E_PASSWORD"] placeholder.
"""

from __future__ import annotations

import ast
from typing import Any


def _escape_str(value: str) -> str:
    """Escape a string for safe embedding in Python source."""
    return repr(value)


def _locator_code(target: str, locator_type: str) -> str:
    """Generate Playwright locator code from target + locator type."""
    if locator_type == "text":
        return f"page.get_by_text({_escape_str(target)})"
    elif locator_type == "label":
        return f"page.get_by_label({_escape_str(target)})"
    elif locator_type == "role":
        if ":" in target:
            role, name = target.split(":", 1)
            return f"page.get_by_role({_escape_str(role.strip())}, name={_escape_str(name.strip())})"
        return f"page.get_by_role({_escape_str(target)})"
    elif locator_type == "placeholder":
        return f"page.get_by_placeholder({_escape_str(target)})"
    elif locator_type == "test_id":
        return f"page.get_by_test_id({_escape_str(target)})"
    elif locator_type == "css":
        return f"page.locator({_escape_str(target)})"
    else:
        if ":" in target:
            role, name = target.split(":", 1)
            return f"page.get_by_role({_escape_str(role.strip())}, name={_escape_str(name.strip())})"
        return f"page.get_by_text({_escape_str(target)})"


def _step_to_code(step: dict[str, Any], username: str) -> list[str]:
    """Convert a step dict to lines of Python code."""
    action = step.get("action", "").lower().strip()
    target = step.get("target", "")
    value = step.get("value", "")
    expected = step.get("expected", "")
    locator_type = step.get("locator", "role")
    lines: list[str] = []

    if action == "navigate":
        url = value or target
        lines.append(f"    await page.goto({_escape_str(url)}, wait_until='domcontentloaded')")

    elif action == "fill":
        loc = _locator_code(target, locator_type)
        # Determine fill value
        if value.lower() == "{{password}}" or _is_password_target(target):
            lines.append(f"    await {loc}.fill(os.environ['E2E_PASSWORD'])")
        elif value.lower() == "{{username}}":
            lines.append(f"    await {loc}.fill({_escape_str(username)})")
        else:
            lines.append(f"    await {loc}.fill({_escape_str(value)})")

    elif action == "click":
        loc = _locator_code(target, locator_type)
        lines.append(f"    await {loc}.click()")

    elif action == "select":
        loc = _locator_code(target, locator_type)
        lines.append(f"    await {loc}.select_option({_escape_str(value)})")

    elif action == "check":
        loc = _locator_code(target, locator_type)
        lines.append(f"    await {loc}.check()")

    elif action == "wait":
        ms = value if value.isdigit() else "2000"
        lines.append(f"    await page.wait_for_timeout({ms})")

    elif action == "assert_text":
        text = value or expected
        lines.append(f"    await expect(page.get_by_text({_escape_str(text)})).to_be_visible()")

    elif action == "assert_url":
        url_fragment = value or expected
        lines.append(f"    expect(page.url).to_contain({_escape_str(url_fragment)})")

    elif action == "assert_element":
        loc = _locator_code(target, locator_type)
        lines.append(f"    await expect({loc}).to_be_visible()")

    elif action == "screenshot":
        lines.append(f"    await page.screenshot(path='screenshot.png')")

    else:
        lines.append(f"    # Unknown action: {action}")

    return lines


_SENSITIVE_FIELDS = frozenset({"password", "passwd", "pwd", "secret", "token"})


def _is_password_target(target: str) -> bool:
    """Check if target refers to a password field."""
    return any(s in target.lower() for s in _SENSITIVE_FIELDS)


def generate_playwright_script(
    tc_id: str,
    title: str,
    steps: list[dict[str, Any]],
    login_url: str,
    username: str,
) -> str:
    """Generate a complete, rerunnable Playwright Python script.

    SECURITY: Password is replaced with os.environ["E2E_PASSWORD"].
    Uses CDP attach to the user's real browser for SSO.

    Args:
        tc_id: Test case identifier.
        title: Test case title.
        steps: List of step dicts.
        login_url: Starting URL.
        username: Login username.

    Returns:
        Complete Python script as a string (ast.parse-valid).
    """
    header = [
        '"""',
        f"Auto-generated Playwright script for: {tc_id} - {title}",
        "",
        "SECURITY: Set E2E_PASSWORD environment variable before running.",
        f"  $env:E2E_PASSWORD = 'your_password'  # PowerShell",
        f"  export E2E_PASSWORD='your_password'   # Bash",
        "",
        "Uses CDP attach to your real browser profile for SSO.",
        "Ensure your browser is running with --remote-debugging-port=9222",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import asyncio",
        "import os",
        "import sys",
        "",
        "from playwright.async_api import async_playwright, expect",
        "",
        "",
        f"TARGET_URL = {_escape_str(login_url)}",
        f"USERNAME = {_escape_str(username)}",
        "CDP_PORT = int(os.environ.get('CDP_PORT', '9222'))",
        "",
        "",
        "async def main() -> None:",
        '    """Execute test case steps."""',
        "    password = os.environ.get('E2E_PASSWORD')",
        "    if not password:",
        "        print('[ERROR] E2E_PASSWORD environment variable not set.')",
        "        sys.exit(1)",
        "",
        "    async with async_playwright() as pw:",
        "        browser = await pw.chromium.connect_over_cdp(f'http://127.0.0.1:{CDP_PORT}')",
        "        context = await browser.new_context(",
        "            viewport={'width': 1920, 'height': 1080},",
        "        )",
        "        page = await context.new_page()",
        "",
    ]

    body: list[str] = []
    for idx, step in enumerate(steps, start=1):
        action = step.get("action", "")
        target = step.get("target", "")
        body.append(f"    # Step {idx}: {action} {target}")
        body.extend(_step_to_code(step, username))
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

    # Validate the generated script is parseable
    try:
        ast.parse(script)
    except SyntaxError:
        # ponytail: fallback to comment-only script if generation has a bug
        script = (
            f"# ERROR: Script generation failed for {tc_id}\n"
            f"# Steps: {len(steps)}\n"
            f"# Regenerate after fixing the step definitions.\n"
        )

    return script
