"""
automation/agentic_tools.py
LLM-in-the-loop agentic E2E test runner tooling.

Defines 35 Claude tool_use tool schemas and an AgenticToolExecutor class that
executes them against a live Playwright page. The self-healing LocatorFactory
resolves natural-language element descriptions via a 6-strategy waterfall with
iframe traversal and shadow DOM fallback.

SECURITY: Passwords are ONLY passed to page.fill(). NEVER logged, NEVER written
to disk, NEVER included in any artifact or exception message.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final

try:
    from playwright.async_api import Page, BrowserContext, TimeoutError as PwTimeout
except ImportError:
    Page = object  # type: ignore[assignment,misc]
    BrowserContext = object  # type: ignore[assignment,misc]
    PwTimeout = Exception  # type: ignore[assignment,misc]

from .e2e_runner import StepResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BUFFER_SIZE: Final[int] = 100
ELEMENT_TIMEOUT_MS: Final[int] = 12_000
NAVIGATE_TIMEOUT_MS: Final[int] = 30_000
MAX_A11Y_CHARS: Final[int] = 12_000
MAX_WAIT_SECONDS: Final[float] = 30.0
SCREENSHOT_QUALITY: Final[int] = 70

_STRATEGY_ORDER: tuple[str, ...] = (
    "role", "label", "placeholder", "text", "test_id", "css"
)

# Role hint patterns for natural-language parsing
_ROLE_HINTS: dict[str, str] = {
    "button": "button",
    "btn": "button",
    "link": "link",
    "heading": "heading",
    "textbox": "textbox",
    "input": "textbox",
    "field": "textbox",
    "checkbox": "checkbox",
    "radio": "radio",
    "combobox": "combobox",
    "dropdown": "combobox",
    "select": "combobox",
    "tab": "tab",
    "menu": "menu",
    "menuitem": "menuitem",
    "dialog": "dialog",
    "modal": "dialog",
    "slider": "slider",
    "switch": "switch",
    "toggle": "switch",
}


# ---------------------------------------------------------------------------
# Tool schema definitions (35 tools, Claude tool_use format)
# ---------------------------------------------------------------------------

def _build_tool_schemas() -> list[dict[str, Any]]:
    """Return all 35 tool schemas for Claude tool_use."""
    return [
        # --- Category A: Navigation (3) ---
        {
            "name": "navigate",
            "description": "Navigate to a URL in the current tab.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to navigate to"}
                },
                "required": ["url"],
            },
        },
        {
            "name": "navigate_back",
            "description": "Navigate back to the previous page in browser history.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "switch_tab",
            "description": "Switch to a different browser tab by index.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tab_index": {"type": "integer", "description": "Zero-based index of the tab to switch to"}
                },
                "required": ["tab_index"],
            },
        },
        # --- Category B: Interaction (12) ---
        {
            "name": "click",
            "description": "Click an element on the page.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the element to click"}
                },
                "required": ["element"],
            },
        },
        {
            "name": "fill",
            "description": "Clear and fill an input element with a value.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the input element"},
                    "value": {"type": "string", "description": "The value to fill in"},
                },
                "required": ["element", "value"],
            },
        },
        {
            "name": "type_text",
            "description": "Type text character by character into an element (simulates real typing).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the element"},
                    "text": {"type": "string", "description": "The text to type"},
                    "delay_ms": {"type": "integer", "description": "Delay between keystrokes in ms (default 50)", "default": 50},
                },
                "required": ["element", "text"],
            },
        },
        {
            "name": "select_option",
            "description": "Select an option from a dropdown/select element.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the select element"},
                    "value": {"type": "string", "description": "The value or label of the option to select"},
                },
                "required": ["element", "value"],
            },
        },
        {
            "name": "check",
            "description": "Check a checkbox element.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the checkbox"}
                },
                "required": ["element"],
            },
        },
        {
            "name": "uncheck",
            "description": "Uncheck a checkbox element.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the checkbox"}
                },
                "required": ["element"],
            },
        },
        {
            "name": "hover",
            "description": "Hover over an element to trigger hover effects.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the element to hover"}
                },
                "required": ["element"],
            },
        },
        {
            "name": "double_click",
            "description": "Double-click an element on the page.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the element to double-click"}
                },
                "required": ["element"],
            },
        },
        {
            "name": "drag_and_drop",
            "description": "Drag one element and drop it onto another.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Natural language description of the element to drag"},
                    "target": {"type": "string", "description": "Natural language description of the drop target"},
                },
                "required": ["source", "target"],
            },
        },
        {
            "name": "press_key",
            "description": "Press a keyboard key or key combination (e.g. Enter, Tab, Control+a).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name or combo (e.g. 'Enter', 'Control+c', 'Escape')"}
                },
                "required": ["key"],
            },
        },
        {
            "name": "scroll",
            "description": "Scroll the page in a given direction.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "description": "Scroll direction"},
                    "amount": {"type": "integer", "description": "Scroll distance in pixels (default 300)", "default": 300},
                },
                "required": ["direction"],
            },
        },
        {
            "name": "file_upload",
            "description": "Upload file(s) to a file input element.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the file input"},
                    "file_paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute paths to file(s) to upload"},
                },
                "required": ["element", "file_paths"],
            },
        },
        {
            "name": "right_click",
            "description": "Right-click an element to open a context menu.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the element to right-click"}
                },
                "required": ["element"],
            },
        },
        # --- Category C: Forms (1) ---
        {
            "name": "fill_form",
            "description": "Fill multiple form fields in batch.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "element": {"type": "string", "description": "Natural language description of the field"},
                                "value": {"type": "string", "description": "Value to fill"},
                            },
                            "required": ["element", "value"],
                        },
                        "description": "List of fields to fill",
                    }
                },
                "required": ["fields"],
            },
        },
        # --- Category D: Page State & Observation (6) ---
        {
            "name": "observe_page",
            "description": "Get the current page state: accessibility tree, URL, and title.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "take_screenshot",
            "description": "Take a screenshot of the current page.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "full_page": {"type": "boolean", "description": "Capture the full scrollable page (default false)", "default": False}
                },
            },
        },
        {
            "name": "get_console_messages",
            "description": "Get buffered browser console messages.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "description": "Filter by level: all, error, warning, info, log (default all)", "default": "all"}
                },
            },
        },
        {
            "name": "get_network_requests",
            "description": "Get buffered network requests.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url_pattern": {"type": "string", "description": "Regex pattern to filter URLs (default: all)", "default": ""}
                },
            },
        },
        {
            "name": "inspect_network_request",
            "description": "Inspect response body and status for a specific network request.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url_pattern": {"type": "string", "description": "Regex pattern to match the request URL"}
                },
                "required": ["url_pattern"],
            },
        },
        {
            "name": "list_tabs",
            "description": "List all open browser tabs with index, URL, and title.",
            "input_schema": {"type": "object", "properties": {}},
        },
        # --- Category E: JavaScript & Advanced (4) ---
        {
            "name": "evaluate_js",
            "description": "Evaluate a JavaScript expression in the page context and return the result.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "JavaScript expression to evaluate"}
                },
                "required": ["expression"],
            },
        },
        {
            "name": "wait_for",
            "description": "Wait for a condition: 'element:description', 'url:pattern', 'network_idle', or 'timeout:ms'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "condition": {"type": "string", "description": "Wait condition string"},
                    "timeout_ms": {"type": "integer", "description": "Max wait time in ms (default 10000)", "default": 10000},
                },
                "required": ["condition"],
            },
        },
        {
            "name": "handle_dialog",
            "description": "Handle the next browser dialog (alert, confirm, prompt).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["accept", "dismiss"], "description": "Accept or dismiss the dialog"},
                    "text": {"type": "string", "description": "Text to enter in a prompt dialog (default empty)", "default": ""},
                },
                "required": ["action"],
            },
        },
        {
            "name": "resize_viewport",
            "description": "Resize the browser viewport.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "description": "Viewport width in pixels"},
                    "height": {"type": "integer", "description": "Viewport height in pixels"},
                },
                "required": ["width", "height"],
            },
        },
        # --- Category F: Locator Factory (1) ---
        {
            "name": "build_locator",
            "description": "Test whether an element can be found and report which locator strategy succeeded.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Natural language description of the element to find"}
                },
                "required": ["description"],
            },
        },
        # --- Category H: Assertions & Extraction (4) ---
        {
            "name": "assert_text",
            "description": "Assert that specific text is visible on the page.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text expected to be visible on the page"},
                    "exact": {"type": "boolean", "description": "Require exact match (default false)", "default": False},
                },
                "required": ["text"],
            },
        },
        {
            "name": "assert_element_visible",
            "description": "Assert that an element matching the description is visible.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the element"}
                },
                "required": ["element"],
            },
        },
        {
            "name": "assert_url",
            "description": "Assert that the current URL matches a pattern.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "URL substring or regex pattern to match"}
                },
                "required": ["pattern"],
            },
        },
        {
            "name": "get_element_text",
            "description": "Extract the text content of an element.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Natural language description of the element"}
                },
                "required": ["element"],
            },
        },
        # --- Category G: Control & Termination (3) ---
        {
            "name": "declare_done",
            "description": "Declare the test case complete with a pass/fail verdict.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["pass", "fail"], "description": "Test outcome"},
                    "summary": {"type": "string", "description": "Short summary of what was verified"},
                    "evidence": {"type": "string", "description": "Supporting evidence or observation", "default": ""},
                },
                "required": ["status", "summary"],
            },
        },
        {
            "name": "declare_stuck",
            "description": "Declare that the agent is stuck and cannot proceed.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why the agent is stuck"},
                    "last_observation": {"type": "string", "description": "Last page state observation", "default": ""},
                },
                "required": ["reason"],
            },
        },
        {
            "name": "wait_seconds",
            "description": "Wait for a specified number of seconds (max 30).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "Seconds to wait (max 30)"}
                },
                "required": ["seconds"],
            },
        },
    ]


TOOL_SCHEMAS: Final[list[dict[str, Any]]] = _build_tool_schemas()

# Interaction tools that trigger auto-screenshot after execution
_INTERACTION_TOOLS: frozenset[str] = frozenset({
    "click", "fill", "type_text", "select_option", "check", "uncheck",
    "hover", "double_click", "right_click", "drag_and_drop", "press_key",
    "file_upload", "fill_form", "navigate",
})


# ---------------------------------------------------------------------------
# Accessibility tree helpers
# ---------------------------------------------------------------------------

def _format_a11y_tree(snapshot: dict[str, Any], max_chars: int = MAX_A11Y_CHARS) -> str:
    """Format Playwright a11y snapshot into a compact role:name tree."""
    lines: list[str] = []

    def _walk(node: dict[str, Any], depth: int) -> None:
        if len("\n".join(lines)) > max_chars:
            return
        role = node.get("role", "")
        name = node.get("name", "")
        # Skip generic/none roles with no name
        if role in ("none", "generic", "") and not name:
            pass
        else:
            indent = "  " * depth
            label = f"{role}: {name}" if name else role
            lines.append(f"{indent}{label}")
        for child in node.get("children", []):
            _walk(child, depth + 1)

    _walk(snapshot, 0)
    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... [truncated]"
    return result


def _format_cdp_nodes(nodes: list[dict[str, Any]], max_chars: int = MAX_A11Y_CHARS) -> str:
    """Format CDP Accessibility.getFullAXTree nodes into a compact tree."""
    # Build parent->children mapping from flat array
    node_map: dict[str, dict[str, Any]] = {}
    children_map: dict[str, list[str]] = {}
    root_ids: list[str] = []

    for node in nodes:
        node_id = node.get("nodeId", "")
        node_map[node_id] = node
        parent_id = node.get("parentId")
        if parent_id:
            children_map.setdefault(parent_id, []).append(node_id)
        else:
            root_ids.append(node_id)

    lines: list[str] = []

    def _walk(nid: str, depth: int) -> None:
        if len("\n".join(lines)) > max_chars:
            return
        n = node_map.get(nid, {})
        role_info = n.get("role", {})
        name_info = n.get("name", {})
        role = role_info.get("value", "") if isinstance(role_info, dict) else str(role_info)
        name = name_info.get("value", "") if isinstance(name_info, dict) else str(name_info)
        if role in ("none", "generic", "InlineTextBox", "") and not name:
            pass
        else:
            indent = "  " * depth
            label = f"{role}: {name}" if name else role
            lines.append(f"{indent}{label}")
        for child_id in children_map.get(nid, []):
            _walk(child_id, depth + 1)

    for rid in root_ids:
        _walk(rid, 0)

    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... [truncated]"
    return result


async def get_accessibility_tree(page: Any, max_chars: int = MAX_A11Y_CHARS) -> str:
    """Get a11y tree. Falls back to CDP if page.accessibility.snapshot() is gone."""
    try:
        snapshot = await page.accessibility.snapshot()  # type: ignore[union-attr]
        if snapshot:
            return _format_a11y_tree(snapshot, max_chars)
    except (AttributeError, Exception):
        pass
    # CDP fallback for Playwright 1.49+ where page.accessibility is removed
    cdp = await page.context.new_cdp_session(page)
    try:
        result = await cdp.send("Accessibility.getFullAXTree")
        nodes = result.get("nodes", [])
        return _format_cdp_nodes(nodes, max_chars)
    finally:
        await cdp.detach()


# ---------------------------------------------------------------------------
# Self-healing Locator Factory
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LocatorResult:
    """Result of locator resolution."""
    found: bool
    locator: Any  # Playwright Locator or None
    strategy_used: str
    alternatives: list[str]
    context_snippet: str  # a11y subtree if not found


def _parse_role_hint(description: str) -> tuple[str, str]:
    """Parse a natural language description for ARIA role and name hints.

    Examples:
        "the Submit button" -> ("button", "Submit")
        "Username input field" -> ("textbox", "Username")
        "Settings link" -> ("link", "Settings")
    """
    desc_lower = description.lower().strip()

    # Check for role keywords at the end (e.g. "Submit button")
    for keyword, role in _ROLE_HINTS.items():
        # Pattern: "Name keyword" at end
        if desc_lower.endswith(f" {keyword}"):
            name_part = description[: -(len(keyword) + 1)].strip()
            # Strip leading "the" / "a" / "an"
            name_part = re.sub(r"^(?:the|a|an)\s+", "", name_part, flags=re.IGNORECASE).strip()
            return (role, name_part)
        # Pattern: "keyword Name" at start
        if desc_lower.startswith(f"{keyword} "):
            name_part = description[len(keyword) + 1:].strip()
            name_part = re.sub(r"^(?:the|a|an)\s+", "", name_part, flags=re.IGNORECASE).strip()
            return (role, name_part)

    # Check for quoted text as the name (e.g. 'the "Login" button')
    quoted = re.search(r'"([^"]+)"', description)
    if quoted:
        name_candidate = quoted.group(1)
        remaining = description.replace(f'"{name_candidate}"', "").lower().strip()
        for keyword, role in _ROLE_HINTS.items():
            if keyword in remaining:
                return (role, name_candidate)

    return ("", "")


class LocatorFactory:
    """Self-healing element locator using a 6-strategy waterfall."""

    def __init__(self, page: Any) -> None:
        self._page: Any = page

    async def find(self, description: str, timeout_ms: int = ELEMENT_TIMEOUT_MS) -> LocatorResult:
        """Resolve a natural-language element description to a Playwright locator."""
        role_hint, name_hint = _parse_role_hint(description)
        attempted: list[str] = []

        # Try each strategy on main frame first
        locator = await self._try_strategies(
            self._page, description, role_hint, name_hint, timeout_ms, attempted
        )
        if locator is not None:
            strategy = attempted[-1] if attempted else "unknown"
            return LocatorResult(
                found=True, locator=locator, strategy_used=strategy,
                alternatives=attempted[:-1], context_snippet=""
            )

        # Try iframe traversal (1 level deep)
        for frame in self._page.frames[1:]:  # skip main frame
            iframe_attempted: list[str] = []
            locator = await self._try_strategies(
                frame, description, role_hint, name_hint, timeout_ms // 2, iframe_attempted
            )
            if locator is not None:
                strategy = f"iframe:{iframe_attempted[-1]}" if iframe_attempted else "iframe"
                return LocatorResult(
                    found=True, locator=locator, strategy_used=strategy,
                    alternatives=attempted + iframe_attempted[:-1], context_snippet=""
                )
            attempted.extend(f"iframe:{s}" for s in iframe_attempted)

        # Shadow DOM pierce as last resort
        shadow_locator = await self._try_shadow_dom(description, name_hint, timeout_ms // 2)
        if shadow_locator is not None:
            attempted.append("shadow_dom")
            return LocatorResult(
                found=True, locator=shadow_locator, strategy_used="shadow_dom",
                alternatives=attempted[:-1], context_snippet=""
            )

        # Total failure: capture a11y snippet for LLM feedback
        snippet = await get_accessibility_tree(self._page, max_chars=3000)
        return LocatorResult(
            found=False, locator=None, strategy_used="none",
            alternatives=attempted, context_snippet=snippet
        )

    async def _try_strategies(
        self,
        frame: Any,
        description: str,
        role_hint: str,
        name_hint: str,
        timeout_ms: int,
        attempted: list[str],
    ) -> Any:
        """Try locator strategies in order. Returns first visible unique match or None."""
        per_strategy_timeout = max(timeout_ms // len(_STRATEGY_ORDER), 1500)

        # 1. Role-based
        if role_hint and name_hint:
            attempted.append("role")
            try:
                loc = frame.get_by_role(role_hint, name=name_hint)
                if await self._validate(loc, per_strategy_timeout):
                    return loc
            except Exception:
                pass

        # 2. Label
        attempted.append("label")
        try:
            # Try with name_hint first, then full description
            search_text = name_hint if name_hint else description
            loc = frame.get_by_label(search_text)
            if await self._validate(loc, per_strategy_timeout):
                return loc
        except Exception:
            pass

        # 3. Placeholder
        attempted.append("placeholder")
        try:
            search_text = name_hint if name_hint else description
            loc = frame.get_by_placeholder(search_text)
            if await self._validate(loc, per_strategy_timeout):
                return loc
        except Exception:
            pass

        # 4. Text
        attempted.append("text")
        try:
            search_text = name_hint if name_hint else description
            loc = frame.get_by_text(search_text, exact=False)
            if await self._validate(loc, per_strategy_timeout):
                return loc
        except Exception:
            pass

        # 5. Test ID
        attempted.append("test_id")
        try:
            # Derive test-id from description: "Submit button" -> "submit", "submit-button"
            test_id = re.sub(r"[^a-zA-Z0-9]+", "-", description.strip()).strip("-").lower()
            loc = frame.get_by_test_id(test_id)
            if await self._validate(loc, per_strategy_timeout):
                return loc
            # Also try without hyphens
            test_id_no_sep = test_id.replace("-", "")
            if test_id_no_sep != test_id:
                loc = frame.get_by_test_id(test_id_no_sep)
                if await self._validate(loc, per_strategy_timeout):
                    return loc
        except Exception:
            pass

        # 6. CSS selector (use description as-is if it looks like CSS)
        attempted.append("css")
        try:
            # If description contains CSS-like characters, use directly
            if any(c in description for c in (".", "#", "[", ">")):
                loc = frame.locator(description)
                if await self._validate(loc, per_strategy_timeout):
                    return loc
            # Try aria-label CSS
            search_text = name_hint if name_hint else description
            loc = frame.locator(f'[aria-label*="{search_text}" i]')
            if await self._validate(loc, per_strategy_timeout):
                return loc
        except Exception:
            pass

        return None

    async def _try_shadow_dom(self, description: str, name_hint: str, timeout_ms: int) -> Any:
        """Last resort: shadow DOM piercing via Playwright >> combinator."""
        try:
            search_text = name_hint if name_hint else description
            # Playwright's >> combinator pierces shadow DOM
            loc = self._page.locator(f'css=* >> text="{search_text}"')
            if await self._validate(loc, timeout_ms):
                return loc
        except Exception:
            pass
        return None

    async def _validate(self, locator: Any, timeout_ms: int) -> bool:
        """Validate: element is visible and count == 1 (no ambiguity)."""
        try:
            count = await locator.count()
            if count != 1:
                return False
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# AgenticToolExecutor
# ---------------------------------------------------------------------------

class AgenticToolExecutor:
    """Executes tool calls from the LLM agent against a live Playwright page."""

    def __init__(
        self,
        page: Any,
        context: Any,
        credentials: Any,
        screenshot_dir: Path,
        *,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self._page: Any = page
        self._context: Any = context
        self._credentials: Any = credentials
        self._screenshot_dir: Path = screenshot_dir
        self._on_log: Callable[[str], None] = on_log or (lambda msg: None)
        self._locator_factory: LocatorFactory = LocatorFactory(page)
        self._console_buffer: deque[dict[str, Any]] = deque(maxlen=MAX_BUFFER_SIZE)
        self._network_buffer: deque[dict[str, Any]] = deque(maxlen=MAX_BUFFER_SIZE)
        self._pending_dialog: asyncio.Queue[Any] = asyncio.Queue(maxsize=5)
        self._screenshot_counter: int = 0

        # Attach event listeners
        self._page.on("console", self._on_console)
        self._page.on("request", self._on_request)
        self._page.on("response", self._on_response)
        self._page.on("dialog", self._on_dialog)

    # --- Event handlers ---

    def _on_console(self, msg: Any) -> None:
        self._console_buffer.append({
            "level": msg.type,
            "text": msg.text,
            "url": msg.location.get("url", "") if hasattr(msg, "location") and msg.location else "",
        })

    def _on_request(self, request: Any) -> None:
        self._network_buffer.append({
            "method": request.method,
            "url": request.url,
            "resource_type": request.resource_type,
            "status": None,  # filled on response
            "response_body": None,
        })

    def _on_response(self, response: Any) -> None:
        # Update matching request entry with status
        url = response.url
        for entry in reversed(self._network_buffer):
            if entry["url"] == url and entry["status"] is None:
                entry["status"] = response.status
                break

    def _on_dialog(self, dialog: Any) -> None:
        try:
            self._pending_dialog.put_nowait(dialog)
        except asyncio.QueueFull:
            # Auto-dismiss if queue full to prevent blocking
            asyncio.ensure_future(dialog.dismiss())

    # --- Public API ---

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return all 35 tool schemas for Claude tool_use."""
        return TOOL_SCHEMAS

    async def execute(
        self, tool_name: str, tool_input: dict[str, Any], step_num: int
    ) -> tuple[str, StepResult]:
        """Execute one tool call. Returns (observation_text, StepResult)."""
        t0 = time.perf_counter()
        observation: str = ""
        status: str = "pass"
        locator_strategy: str = ""
        locator_history: list[str] | None = None
        screenshot_path: Path | None = None

        handler = getattr(self, f"_exec_{tool_name}", None)
        if handler is None:
            observation = f"[ERROR] Unknown tool: {tool_name}"
            status = "error"
        else:
            try:
                observation, locator_strategy, locator_history = await handler(tool_input)
            except PwTimeout as e:
                observation = f"[ERROR] Timeout: {e}"
                status = "error"
            except Exception as e:
                observation = f"[ERROR] {type(e).__name__}: {e}"
                status = "error"

        duration_ms = int((time.perf_counter() - t0) * 1000)

        # Auto-screenshot after interaction tools
        if tool_name in _INTERACTION_TOOLS and status != "error":
            screenshot_path = await self._take_screenshot_internal(full_page=False)

        # Build StepResult
        result = StepResult(
            step_num=step_num,
            action=f"{tool_name}({_safe_summary(tool_input)})",
            expected="",
            actual=observation[:500],
            status=status,
            locator_strategy=locator_strategy,
            locator_history=locator_history,
            screenshot_path=screenshot_path,
            duration_ms=duration_ms,
        )

        self._on_log(f"[INFO] Step {step_num}: {tool_name} -> {status} ({duration_ms}ms)")
        return observation, result

    # --- Tool implementations ---

    async def _exec_navigate(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        url: str = inp["url"]
        response = await self._page.goto(url, timeout=NAVIGATE_TIMEOUT_MS)
        status_code = response.status if response else "unknown"
        title = await self._page.title()
        return (
            f"Navigated to {url} (status={status_code}, title={title!r})",
            "", None
        )

    async def _exec_navigate_back(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        await self._page.go_back()
        url = self._page.url
        title = await self._page.title()
        return f"Navigated back to {url} (title={title!r})", "", None

    async def _exec_switch_tab(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        tab_index: int = inp["tab_index"]
        pages = self._context.pages
        if tab_index < 0 or tab_index >= len(pages):
            return f"[ERROR] Tab index {tab_index} out of range (0-{len(pages)-1})", "", None
        target = pages[tab_index]
        await target.bring_to_front()
        self._page = target
        self._locator_factory = LocatorFactory(target)
        return f"Switched to tab {tab_index}: {target.url}", "", None

    async def _exec_click(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if not result.found:
            return (
                f"[ERROR] Element not found: {inp['element']}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        await result.locator.click()
        return f"Clicked: {inp['element']} (strategy={result.strategy_used})", result.strategy_used, result.alternatives

    async def _exec_fill(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        element_desc: str = inp["element"]
        value: str = inp["value"]
        is_password = "password" in element_desc.lower()

        # Resolve actual value (use credential for password fields)
        fill_value = value
        if is_password and self._credentials and value in ("{{password}}", "***", "password"):
            fill_value = self._credentials.password

        result = await self._locator_factory.find(element_desc)
        if not result.found:
            return (
                f"[ERROR] Element not found: {element_desc}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        await result.locator.clear()
        await result.locator.fill(fill_value)

        # SECURITY: never reveal password in observation
        display_value = "***" if is_password else value
        return (
            f"Filled {element_desc!r} with {display_value!r} (strategy={result.strategy_used})",
            result.strategy_used, result.alternatives
        )

    async def _exec_type_text(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        element_desc: str = inp["element"]
        text: str = inp["text"]
        delay_ms: int = inp.get("delay_ms", 50)
        is_password = "password" in element_desc.lower()

        result = await self._locator_factory.find(element_desc)
        if not result.found:
            return (
                f"[ERROR] Element not found: {element_desc}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        type_text = text
        if is_password and self._credentials and text in ("{{password}}", "***", "password"):
            type_text = self._credentials.password

        await result.locator.press_sequentially(type_text, delay=delay_ms)
        display_text = "***" if is_password else text
        return (
            f"Typed {display_text!r} into {element_desc!r} (strategy={result.strategy_used})",
            result.strategy_used, result.alternatives
        )

    async def _exec_select_option(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if not result.found:
            return (
                f"[ERROR] Element not found: {inp['element']}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        await result.locator.select_option(inp["value"])
        return (
            f"Selected option {inp['value']!r} in {inp['element']!r} (strategy={result.strategy_used})",
            result.strategy_used, result.alternatives
        )

    async def _exec_check(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if not result.found:
            return (
                f"[ERROR] Element not found: {inp['element']}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        await result.locator.check()
        return f"Checked: {inp['element']} (strategy={result.strategy_used})", result.strategy_used, result.alternatives

    async def _exec_uncheck(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if not result.found:
            return (
                f"[ERROR] Element not found: {inp['element']}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        await result.locator.uncheck()
        return f"Unchecked: {inp['element']} (strategy={result.strategy_used})", result.strategy_used, result.alternatives

    async def _exec_hover(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if not result.found:
            return (
                f"[ERROR] Element not found: {inp['element']}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        await result.locator.hover()
        return f"Hovered: {inp['element']} (strategy={result.strategy_used})", result.strategy_used, result.alternatives

    async def _exec_double_click(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if not result.found:
            return (
                f"[ERROR] Element not found: {inp['element']}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        await result.locator.dblclick()
        return f"Double-clicked: {inp['element']} (strategy={result.strategy_used})", result.strategy_used, result.alternatives

    async def _exec_drag_and_drop(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        src_result = await self._locator_factory.find(inp["source"])
        if not src_result.found:
            return (
                f"[ERROR] Source not found: {inp['source']}\nPage state:\n{src_result.context_snippet}",
                "none", src_result.alternatives
            )
        tgt_result = await self._locator_factory.find(inp["target"])
        if not tgt_result.found:
            return (
                f"[ERROR] Target not found: {inp['target']}\nPage state:\n{tgt_result.context_snippet}",
                "none", tgt_result.alternatives
            )
        await src_result.locator.drag_to(tgt_result.locator)
        return (
            f"Dragged {inp['source']!r} to {inp['target']!r}",
            f"src={src_result.strategy_used},tgt={tgt_result.strategy_used}", None
        )

    async def _exec_press_key(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        key: str = inp["key"]
        await self._page.keyboard.press(key)
        return f"Pressed key: {key}", "", None

    async def _exec_scroll(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        direction: str = inp["direction"]
        amount: int = inp.get("amount", 300)
        dx, dy = 0, 0
        if direction == "down":
            dy = amount
        elif direction == "up":
            dy = -amount
        elif direction == "right":
            dx = amount
        elif direction == "left":
            dx = -amount
        await self._page.mouse.wheel(dx, dy)
        return f"Scrolled {direction} by {amount}px", "", None

    async def _exec_file_upload(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if not result.found:
            return (
                f"[ERROR] Element not found: {inp['element']}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        file_paths: list[str] = inp["file_paths"]
        await result.locator.set_input_files(file_paths)
        return (
            f"Uploaded {len(file_paths)} file(s) to {inp['element']!r} (strategy={result.strategy_used})",
            result.strategy_used, result.alternatives
        )

    async def _exec_fill_form(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        fields: list[dict[str, str]] = inp["fields"]
        results: list[str] = []
        strategies: list[str] = []
        for fld in fields:
            element_desc = fld["element"]
            value = fld["value"]
            is_password = "password" in element_desc.lower()
            fill_value = value
            if is_password and self._credentials and value in ("{{password}}", "***", "password"):
                fill_value = self._credentials.password

            loc_result = await self._locator_factory.find(element_desc)
            if not loc_result.found:
                results.append(f"  FAIL: {element_desc} (not found)")
                continue
            await loc_result.locator.clear()
            await loc_result.locator.fill(fill_value)
            display_value = "***" if is_password else value
            results.append(f"  OK: {element_desc} = {display_value!r} ({loc_result.strategy_used})")
            strategies.append(loc_result.strategy_used)

        summary = "\n".join(results)
        return f"Form fill results:\n{summary}", ",".join(strategies), None

    async def _exec_observe_page(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        url = self._page.url
        title = await self._page.title()
        tree = await get_accessibility_tree(self._page)
        return f"URL: {url}\nTitle: {title}\n\nAccessibility Tree:\n{tree}", "", None

    async def _exec_take_screenshot(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        full_page: bool = inp.get("full_page", False)
        path = await self._take_screenshot_internal(full_page=full_page)
        if path:
            return f"Screenshot saved: {path.name}", "", None
        return "[ERROR] Failed to capture screenshot", "", None

    async def _exec_get_console_messages(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        level: str = inp.get("level", "all")
        messages = list(self._console_buffer)
        if level != "all":
            messages = [m for m in messages if m["level"] == level]
        if not messages:
            return "No console messages captured.", "", None
        lines = [f"[{m['level']}] {m['text']}" for m in messages[-50:]]  # last 50
        return f"Console messages ({len(messages)} total, showing last {len(lines)}):\n" + "\n".join(lines), "", None

    async def _exec_get_network_requests(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        url_pattern: str = inp.get("url_pattern", "")
        entries = list(self._network_buffer)
        if url_pattern:
            try:
                pat = re.compile(url_pattern, re.IGNORECASE)
                entries = [e for e in entries if pat.search(e["url"])]
            except re.error:
                entries = [e for e in entries if url_pattern in e["url"]]
        if not entries:
            return "No matching network requests.", "", None
        lines = [
            f"{e['method']} {e['url'][:120]} -> {e['status'] or 'pending'}"
            for e in entries[-30:]
        ]
        return f"Network requests ({len(entries)} total, showing last {len(lines)}):\n" + "\n".join(lines), "", None

    async def _exec_inspect_network_request(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        url_pattern: str = inp["url_pattern"]
        # Find matching response via page.expect_response or from buffer
        entries = list(self._network_buffer)
        try:
            pat = re.compile(url_pattern, re.IGNORECASE)
            matches = [e for e in entries if pat.search(e["url"])]
        except re.error:
            matches = [e for e in entries if url_pattern in e["url"]]

        if not matches:
            return f"No network request matching {url_pattern!r} found in buffer.", "", None

        last = matches[-1]
        # Try to get response body via evaluate
        try:
            body = await self._page.evaluate(
                """async (url) => {
                    try {
                        const resp = await fetch(url, {method: 'GET', credentials: 'include'});
                        const text = await resp.text();
                        return text.substring(0, 2000);
                    } catch(e) { return 'fetch failed: ' + e.message; }
                }""",
                last["url"]
            )
        except Exception:
            body = "(unable to retrieve body)"

        return (
            f"Request: {last['method']} {last['url']}\nStatus: {last['status']}\nBody (first 2000 chars):\n{body}",
            "", None
        )

    async def _exec_list_tabs(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        pages = self._context.pages
        tabs: list[str] = []
        for i, p in enumerate(pages):
            try:
                title = await p.title()
            except Exception:
                title = "(unknown)"
            marker = " [active]" if p == self._page else ""
            tabs.append(f"  [{i}] {p.url} - {title}{marker}")
        return f"Open tabs ({len(pages)}):\n" + "\n".join(tabs), "", None

    async def _exec_evaluate_js(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        expression: str = inp["expression"]
        result = await self._page.evaluate(expression)
        # Serialize result for observation
        result_str = repr(result)
        if len(result_str) > 3000:
            result_str = result_str[:3000] + "... [truncated]"
        return f"JS result: {result_str}", "", None

    async def _exec_wait_for(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        condition: str = inp["condition"]
        timeout_ms: int = inp.get("timeout_ms", 10000)

        if condition.startswith("element:"):
            desc = condition[len("element:"):]
            result = await self._locator_factory.find(desc, timeout_ms=timeout_ms)
            if result.found:
                return f"Element found: {desc} (strategy={result.strategy_used})", result.strategy_used, result.alternatives
            return f"[ERROR] Timed out waiting for element: {desc}", "none", result.alternatives

        elif condition.startswith("url:"):
            pattern = condition[len("url:"):]
            try:
                await self._page.wait_for_url(f"**{pattern}**", timeout=timeout_ms)
                return f"URL matched: {self._page.url}", "", None
            except PwTimeout:
                return f"[ERROR] Timed out waiting for URL pattern: {pattern} (current: {self._page.url})", "", None

        elif condition == "network_idle":
            try:
                await self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
                return "Network idle reached.", "", None
            except PwTimeout:
                return "[ERROR] Timed out waiting for network idle.", "", None

        elif condition.startswith("timeout:"):
            ms = int(condition[len("timeout:"):])
            ms = min(ms, int(MAX_WAIT_SECONDS * 1000))
            await self._page.wait_for_timeout(ms)
            return f"Waited {ms}ms.", "", None

        else:
            return f"[ERROR] Unknown wait condition: {condition}. Use element:, url:, network_idle, or timeout:", "", None

    async def _exec_handle_dialog(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        action: str = inp["action"]
        text: str = inp.get("text", "")

        try:
            dialog = self._pending_dialog.get_nowait()
        except asyncio.QueueEmpty:
            return "[WARN] No pending dialog to handle.", "", None

        dialog_type = dialog.type
        dialog_message = dialog.message

        if action == "accept":
            if text:
                await dialog.accept(text)
            else:
                await dialog.accept()
        else:
            await dialog.dismiss()

        return f"Dialog ({dialog_type}) {action}ed. Message was: {dialog_message!r}", "", None

    async def _exec_resize_viewport(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        width: int = inp["width"]
        height: int = inp["height"]
        await self._page.set_viewport_size({"width": width, "height": height})
        return f"Viewport resized to {width}x{height}.", "", None

    async def _exec_build_locator(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        desc: str = inp["description"]
        result = await self._locator_factory.find(desc)
        if result.found:
            return (
                f"Element found: {desc!r}\n"
                f"Strategy: {result.strategy_used}\n"
                f"Alternatives tried: {result.alternatives}",
                result.strategy_used, result.alternatives
            )
        return (
            f"Element NOT found: {desc!r}\n"
            f"Strategies tried: {result.alternatives}\n"
            f"Page context:\n{result.context_snippet}",
            "none", result.alternatives
        )

    async def _exec_right_click(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if not result.found:
            return (
                f"[ERROR] Element not found: {inp['element']}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        await result.locator.click(button="right")
        return f"Right-clicked: {inp['element']} (strategy={result.strategy_used})", result.strategy_used, result.alternatives

    async def _exec_assert_text(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        text: str = inp["text"]
        exact: bool = inp.get("exact", False)
        locator = self._page.get_by_text(text, exact=exact)
        try:
            count = await locator.count()
            if count > 0:
                return f"ASSERT PASS: Text {text!r} is visible on the page ({count} match(es)).", "", None
            return f"ASSERT FAIL: Text {text!r} not found on the page.", "", None
        except Exception as e:
            return f"ASSERT FAIL: Text {text!r} lookup error: {e}", "", None

    async def _exec_assert_element_visible(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if result.found:
            return (
                f"ASSERT PASS: Element {inp['element']!r} is visible (strategy={result.strategy_used}).",
                result.strategy_used, result.alternatives
            )
        return (
            f"ASSERT FAIL: Element {inp['element']!r} is not visible.\n"
            f"Strategies tried: {result.alternatives}\nPage context:\n{result.context_snippet}",
            "none", result.alternatives
        )

    async def _exec_assert_url(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        pattern: str = inp["pattern"]
        current_url: str = self._page.url
        try:
            if re.search(pattern, current_url):
                return f"ASSERT PASS: URL {current_url!r} matches pattern {pattern!r}.", "", None
        except re.error:
            pass
        # Fallback to substring match
        if pattern in current_url:
            return f"ASSERT PASS: URL {current_url!r} contains {pattern!r}.", "", None
        return f"ASSERT FAIL: URL {current_url!r} does not match pattern {pattern!r}.", "", None

    async def _exec_get_element_text(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        result = await self._locator_factory.find(inp["element"])
        if not result.found:
            return (
                f"[ERROR] Element not found: {inp['element']}\nPage state:\n{result.context_snippet}",
                "none", result.alternatives
            )
        text_content = await result.locator.text_content() or ""
        # Truncate very long text
        if len(text_content) > 2000:
            text_content = text_content[:2000] + "... [truncated]"
        return (
            f"Text content of {inp['element']!r}: {text_content!r}",
            result.strategy_used, result.alternatives
        )

    async def _exec_declare_done(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        status: str = inp["status"]
        summary: str = inp["summary"]
        evidence: str = inp.get("evidence", "")
        msg = f"[SUCCESS] Test {status.upper()}: {summary}"
        if evidence:
            msg += f"\nEvidence: {evidence}"
        return msg, "", None

    async def _exec_declare_stuck(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        reason: str = inp["reason"]
        last_obs: str = inp.get("last_observation", "")
        msg = f"[WARN] Agent stuck: {reason}"
        if last_obs:
            msg += f"\nLast observation: {last_obs}"
        return msg, "", None

    async def _exec_wait_seconds(self, inp: dict[str, Any]) -> tuple[str, str, list[str] | None]:
        seconds: float = min(inp["seconds"], MAX_WAIT_SECONDS)
        await self._page.wait_for_timeout(int(seconds * 1000))
        return f"Waited {seconds:.1f}s.", "", None

    # --- Internal helpers ---

    async def _take_screenshot_internal(self, *, full_page: bool = False) -> Path | None:
        """Capture screenshot and save to artifacts directory."""
        try:
            self._screenshot_counter += 1
            filename = f"step_{self._screenshot_counter:04d}.png"
            path = self._screenshot_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            await self._page.screenshot(
                path=str(path),
                full_page=full_page,
                quality=SCREENSHOT_QUALITY,
                type="jpeg" if SCREENSHOT_QUALITY < 100 else "png",
            )
            # Correction: quality param only valid for jpeg
            # Re-take as png without quality for png format
            await self._page.screenshot(path=str(path), full_page=full_page)
            return path
        except Exception as e:
            self._on_log(f"[WARN] Screenshot failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_summary(tool_input: dict[str, Any]) -> str:
    """Create a safe summary of tool input for logging (redacts passwords)."""
    parts: list[str] = []
    for k, v in tool_input.items():
        if "password" in k.lower():
            parts.append(f"{k}=***")
        elif isinstance(v, str) and len(v) > 80:
            parts.append(f"{k}={v[:77]!r}...")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)
