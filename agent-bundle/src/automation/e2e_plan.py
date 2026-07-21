"""Compile generated test cases into the strict Playwright execution DSL."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final
from urllib.parse import urlparse

LogFn = Callable[[str], None]
SCHEMA_VERSION: Final[int] = 4
_ALLOWED_ACTIONS: Final[frozenset[str]] = frozenset({
    "navigate", "fill", "click", "type", "select", "check", "uncheck",
    "hover", "double_click", "press_key", "scroll", "wait",
    "wait_for_text", "wait_for_url", "assert_text", "assert_url",
    "assert_element", "assert_not_present", "screenshot", "clear",
    "wait_for_new_page", "assert_new_tab", "select_text",
})
_ALLOWED_LOCATORS: Final[frozenset[str]] = frozenset({
    "role", "label", "placeholder", "text", "test_id", "css",
})
_TARGET_ACTIONS: Final[frozenset[str]] = frozenset({
    "fill", "click", "type", "select", "check", "uncheck", "hover",
    "double_click", "assert_element", "assert_not_present", "clear",
    "select_text",
})
_VALUE_ACTIONS: Final[frozenset[str]] = frozenset({
    "navigate", "fill", "type", "select", "press_key", "wait_for_text",
    "wait_for_url", "assert_text", "assert_url",
})
_SECRET_TOKENS: Final[frozenset[str]] = frozenset({"{{password}}", "{{username}}"})

# Actions where missing target/value is a hard error (cannot possibly execute).
# Non-listed target actions (hover, double_click, select, check, uncheck)
# get soft manual_verification_needed -- partial automation is still valuable.
_HARD_REJECT_ACTIONS: Final[frozenset[str]] = frozenset({
    "navigate", "fill", "type", "click", "assert_text", "assert_url",
    "assert_element", "assert_not_present", "clear", "select_text",
})

_SYSTEM: Final[str] = (
    "You are a Senior QA Engineer compiling human test steps into a deterministic "
    "Playwright DSL. Return JSON only.\n"
    "Never emit credentials: use {{username}} and {{password}} placeholders.\n\n"
    "BEST-EFFORT COMPILATION (CRITICAL):\n"
    "- ALWAYS produce a steps array. NEVER return an errors array unless literally "
    "zero steps can be automated.\n"
    "- If a step cannot be perfectly represented, use the CLOSEST available action "
    "and prefix its expected field with '[MANUAL] ' to flag it for human review.\n"
    "- For new window/tab: use click then wait_for_url with the expected URL fragment.\n"
    "- For text selection: use select_text action on the target element.\n"
    "- A plan that automates 80%% of steps is far better than no plan.\n\n"
    "CONTEXT: You may receive description, acceptance_criteria, repro_steps, and environment "
    "fields. Use these to understand the INTENT of the test -- they inform which assertions "
    "and expected outcomes matter. Derive assertions from acceptance criteria when the human "
    "steps omit explicit expected results.\n\n"
    "APPLICATION KNOWLEDGE BASE: When project_context is provided, use it as the "
    "authoritative reference for URL paths, page names, navigation structure, element "
    "labels, terminology, and business rules. Prefer locators and URL fragments from "
    "this knowledge base over guessing. It describes the actual application under test.\n\n"
    "When page_snapshot is provided, use ONLY locators that match elements visible in the "
    "snapshot. The snapshot shows the accessibility tree with role:name pairs. Pick the most "
    "specific match.\n\n"
    "STEP SCHEMA (every step is an object with these fields):\n"
    "  action  - one of: navigate, fill, click, type, select, check, uncheck, hover, "
    "double_click, press_key, scroll, wait, wait_for_text, wait_for_url, assert_text, "
    "assert_url, assert_element, assert_not_present, screenshot, clear, "
    "wait_for_new_page, assert_new_tab, select_text\n"
    "  locator - the LOCATOR TYPE only, one of: role, label, placeholder, text, test_id, css\n"
    "  target  - the locator VALUE that Playwright receives. "
    "For role locators use 'roleName:Accessible Name' (e.g. 'button:Log In', 'textbox:Email'). "
    "For label/placeholder/text use the visible text. For test_id use the data-testid. "
    "For css use a CSS selector.\n"
    "  value   - text to fill/type/select, URL for navigate, key for press_key\n"
    "  expected - assertion text or URL fragment\n\n"
    "CRITICAL: 'locator' is ALWAYS a single word from the list above. Never put brackets, "
    "colons, or role names in the locator field. The role name and accessible name go in 'target'.\n"
    "Prefer locator strategy: role > label > placeholder > text > test_id > css.\n\n"
    "RELIABILITY: Add a wait_for_text or wait_for_url step before assertions that depend "
    "on async page loads. Add screenshot steps after key interactions for evidence. "
    "Prefer exact matches over partial when the text is known.\n\n"
    "LOCATOR GUIDANCE: When exact element names are unknown, use text locator with "
    "partial match from the test step description. Prefer get_by_role with accessible "
    "names from the knowledge base over inventing CSS selectors.\n\n"
    "NAVIGATION INFERENCE (CRITICAL): If a test step references a page, screen, or "
    "feature without an explicit navigation step preceding it, you MUST generate the "
    "intermediate navigation steps (clicks, menu selections, link follows) needed to "
    "reach that page from the current location. Use the project_context to determine "
    "the correct navigation path. Always start with a 'navigate' action to login_url "
    "if no explicit URL is given as the first step. Never assume the user is already "
    "on the target page -- generate the full click path to get there.\n\n"
    "LITERAL VALUES ONLY (CRITICAL -- violations cause 100%% test failure):\n"
    "- 'expected' for wait_for_text / assert_text MUST be the EXACT visible text "
    "string that appears on the page (a heading, button label, cell value, toast). "
    "WRONG: 'Activities page loaded' 'Contract form is visible' 'Section appears'. "
    "RIGHT: 'Activities' 'Create Contract Request' 'Request Details'.\n"
    "- 'expected' for wait_for_url / assert_url MUST be a literal URL substring. "
    "WRONG: 'iHub login page loads' 'navigate to contracting page'. "
    "RIGHT: '/suite/sites/ihub' '/contracting' 'appiancloud.com'.\n"
    "- 'value' for navigate MUST be a complete URL starting with https://. "
    "WRONG: 'Go to iHub homepage' 'Navigate to Activities'. "
    "RIGHT: 'https://abbott-test.appiancloud.com/suite/sites/ihub'.\n"
    "- 'target' for click/fill MUST reference the actual element text visible on the "
    "page. Use exact labels from project_context SCREENS or page_snapshot.\n"
    "- When exact text/URL is unknown from project_context or page_snapshot, prefix "
    "the expected field with '[MANUAL] ' to flag for human review. A flagged step is "
    "far better than a step with invented descriptions that always fails.\n"
    "- NEVER echo human step descriptions verbatim into expected or value fields. "
    "Translate the intent into a concrete page assertion using known labels/URLs."
)


_SNAPSHOT_MAX_CHARS: Final[int] = 12000

_INTERACTIVE_ROLES: Final[frozenset[str]] = frozenset({
    "button", "link", "textbox", "checkbox", "radio", "combobox",
    "menuitem", "tab", "switch", "searchbox", "slider", "spinbutton",
    "option", "menuitemcheckbox", "menuitemradio", "treeitem",
})


def _scrub_snapshot_node(node: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip values from password-like textbox nodes."""
    role = str(node.get("role", ""))
    name = str(node.get("name", ""))
    scrubbed: dict[str, Any] = dict(node)
    if role == "textbox" and "password" in name.lower():
        scrubbed.pop("value", None)
        scrubbed.pop("description", None)
    children = scrubbed.get("children")
    if isinstance(children, list):
        scrubbed["children"] = [_scrub_snapshot_node(c) for c in children if isinstance(c, dict)]
    return scrubbed


def _is_interactive_subtree(node: dict[str, Any]) -> bool:
    """True if this node or any descendant has an interactive role."""
    if str(node.get("role", "")) in _INTERACTIVE_ROLES:
        return True
    for child in node.get("children", []):
        if isinstance(child, dict) and _is_interactive_subtree(child):
            return True
    return False


def _prune_non_interactive(node: dict[str, Any], depth: int = 0) -> dict[str, Any] | None:
    """Prune subtrees with no interactive elements beyond depth 3."""
    role = str(node.get("role", ""))
    if role in _INTERACTIVE_ROLES:
        return node
    children = node.get("children", [])
    if not isinstance(children, list):
        return node if depth <= 2 else None
    if depth > 3 and not _is_interactive_subtree(node):
        return None
    pruned_children: list[dict[str, Any]] = []
    for child in children:
        if isinstance(child, dict):
            pruned = _prune_non_interactive(child, depth + 1)
            if pruned is not None:
                pruned_children.append(pruned)
    if not pruned_children and depth > 2 and role not in _INTERACTIVE_ROLES:
        return None
    result = dict(node)
    result["children"] = pruned_children
    return result


def _render_node(node: dict[str, Any], depth: int = 0) -> str:
    """Render a single accessibility node as an indented role:name line."""
    role = node.get("role", "")
    name = node.get("name", "")
    value = node.get("value", "")
    parts: list[str] = []
    indent = "  " * depth
    label = f"{role}:{name}" if name else role
    if value:
        label += f" [{value}]"
    parts.append(f"{indent}{label}")
    for child in node.get("children", []):
        if isinstance(child, dict):
            parts.append(_render_node(child, depth + 1))
    return "\n".join(parts)


def _format_snapshot(snapshot: dict[str, Any] | None) -> str:
    """Format a Playwright accessibility snapshot into a compact role tree.

    Security: strips values from textbox nodes whose name contains 'password'.
    Smart pruning: non-interactive subtrees beyond depth 3 are dropped to keep
    the LLM focused on actionable elements.
    Bounded to 12000 chars to balance context usage vs. coverage.
    Returns empty string for None input.
    """
    if snapshot is None:
        return ""
    scrubbed = _scrub_snapshot_node(snapshot)
    # Smart pruning: prioritize interactive elements for large pages
    pruned = _prune_non_interactive(scrubbed)
    if pruned is None:
        pruned = scrubbed
    rendered = _render_node(pruned)
    if len(rendered) > _SNAPSHOT_MAX_CHARS:
        # Fallback: if still too large after pruning, truncate at a line boundary
        lines = rendered.split("\n")
        truncated: list[str] = []
        char_count = 0
        for line in lines:
            if char_count + len(line) + 1 > _SNAPSHOT_MAX_CHARS - 20:
                truncated.append("... (truncated)")
                break
            truncated.append(line)
            char_count += len(line) + 1
        rendered = "\n".join(truncated)
    return rendered


class PlanValidationError(ValueError):
    """The generated test case cannot be executed safely and deterministically."""


@dataclass(slots=True)
class CompiledPlan:
    test_case: dict[str, Any]
    cache_hit: bool
    model: str = ""


def _cache_key(
    tc: dict[str, Any], login_url: str, ai_instructions: str,
    project_context: str = "",
) -> str:
    payload = {
        "schema": SCHEMA_VERSION,
        "test_case": tc,
        "login_url": login_url,
        "ai_instructions": ai_instructions,
        "ctx_hash": hashlib.md5(project_context.encode()).hexdigest() if project_context else "",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _is_safe_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


_COMPOUND_LOCATOR_RE = re.compile(
    r"^([a-zA-Z_]+)"        # locator type prefix
    r"[:\s]+"               # separator (colon or whitespace)
    r"(.+)$",               # the rest is the target value
)
_BRACKET_NAME_RE = re.compile(
    r"^(\w+)\[name=['\"](.+?)['\"]\]$",
)


def _normalize_locator(locator: str, target: str) -> tuple[str, str]:
    """Recover when the LLM jams type+value into the locator field.

    Examples of malformed locator fields this fixes:
      "role:button[name='Log In']" -> locator="role", target="button:Log In"
      "role:button"                -> locator="role", target="button" (if target empty)
      "label:Email Address"        -> locator="label", target="Email Address"
    """
    if locator.lower() in _ALLOWED_LOCATORS:
        return locator, target
    m = _COMPOUND_LOCATOR_RE.match(locator)
    if not m:
        return locator, target
    loc_type, loc_value = m.group(1), m.group(2).strip()
    if loc_type.lower() not in _ALLOWED_LOCATORS:
        return locator, target
    # Extract accessible name from bracket syntax: button[name='X'] -> button:X
    bm = _BRACKET_NAME_RE.match(loc_value)
    if bm:
        role_name, accessible_name = bm.group(1), bm.group(2)
        resolved_target = f"{role_name}:{accessible_name}"
    else:
        resolved_target = loc_value
    # Only override target if it was empty or identical to the compound value
    if not target or target.lower() == locator:
        target = resolved_target
    return loc_type, target


def _validate_step(raw: Any, index: int) -> dict[str, Any]:
    """Validate a single step. Returns the normalized step dict.

    Hard rejections (PlanValidationError) only for security violations and
    completely unrecoverable structural issues. Soft issues get flagged with
    manual_verification_needed instead of rejecting the whole plan.
    """
    if not isinstance(raw, dict):
        raise PlanValidationError(f"Step {index} must be an object")
    action = str(raw.get("action", "")).strip().lower()
    if action not in _ALLOWED_ACTIONS:
        raise PlanValidationError(f"Step {index} has unsupported action '{action or '<empty>'}'")
    target = str(raw.get("target", "")).strip()
    value = str(raw.get("value", "")).strip()
    expected = str(raw.get("expected", "")).strip()
    raw_locator = str(raw.get("locator", "role")).strip() or "role"
    locator, target = _normalize_locator(raw_locator, target)
    locator = locator.lower()
    if locator not in _ALLOWED_LOCATORS:
        raise PlanValidationError(f"Step {index} has unsupported locator '{locator}'")
    # Security: always hard-reject secrets in fields
    for field_name, field_value in (("target", target), ("value", value), ("expected", expected)):
        if any(token in field_value.lower() for token in ("password=", "passwd=", "secret=")):
            raise PlanValidationError(f"Step {index} {field_name} may contain a secret")
    # Security: navigate URL safety is non-negotiable
    if action == "navigate" and not _is_safe_url(value or target):
        raise PlanValidationError(f"Step {index} has unsafe or invalid navigation URL")
    # Soft validation: flag issues instead of rejecting
    manual_flag = bool(raw.get("manual_verification_needed"))
    warning = ""
    if action in _TARGET_ACTIONS and not target:
        if action in _HARD_REJECT_ACTIONS:
            raise PlanValidationError(f"Step {index} action '{action}' requires target")
        manual_flag = True
        warning = f"action '{action}' missing target"
    if action in _VALUE_ACTIONS and not (value or expected or target):
        if action in _HARD_REJECT_ACTIONS:
            raise PlanValidationError(f"Step {index} action '{action}' requires value")
        manual_flag = True
        warning = f"action '{action}' missing value"
    if action == "wait":
        try:
            wait_ms = int(value or "2000")
        except ValueError as exc:
            raise PlanValidationError(f"Step {index} wait value must be milliseconds") from exc
        if not 0 <= wait_ms <= 30000:
            raise PlanValidationError(f"Step {index} wait exceeds 30000ms ceiling")
        value = str(wait_ms)
    step_out: dict[str, Any] = {
        "action": action, "target": target, "value": value,
        "expected": expected, "locator": locator,
    }
    if manual_flag:
        step_out["manual_verification_needed"] = True
    if warning:
        step_out["validation_warning"] = warning
    # Preserve optional note field for manual-verification flagging
    note = str(raw.get("note", "")).strip()
    if note:
        step_out["note"] = note[:200]
    return step_out


def validate_steps(steps: Any) -> list[dict[str, Any]]:
    """Validate and normalize executable steps.

    Permissive: accepts partial plans where some steps need manual verification.
    Only rejects if zero steps can be automated or the plan is structurally empty.
    """
    if not isinstance(steps, list) or not steps:
        raise PlanValidationError("Executable plan has no steps")
    validated: list[dict[str, Any]] = []
    for i, step in enumerate(steps, 1):
        try:
            validated.append(_validate_step(step, i))
        except PlanValidationError:
            # Step-level hard rejection: skip this step but keep going
            pass
    if not validated:
        raise PlanValidationError("Executable plan has no valid steps")
    # A plan of only screenshots is useless -- but only if none have assertions
    automatable = [
        s for s in validated
        if s["action"] != "screenshot" and not s.get("manual_verification_needed")
    ]
    if not automatable and not any(s["action"] != "screenshot" for s in validated):
        raise PlanValidationError("Executable plan contains no action or assertion")
    return validated


def validate_plan(tc: dict[str, Any]) -> dict[str, Any]:
    validated_steps = validate_steps(tc.get("steps"))
    result: dict[str, Any] = {
        **tc,
        "steps": validated_steps,
        "plan_schema_version": SCHEMA_VERSION,
    }
    # Surface step-level warnings at plan level for observability
    manual_count = sum(1 for s in validated_steps if s.get("manual_verification_needed"))
    if manual_count:
        result["manual_steps_count"] = manual_count
    return result


def _already_structured(tc: dict[str, Any]) -> bool:
    steps = tc.get("steps")
    return bool(steps) and all(
        isinstance(step, dict)
        and str(step.get("action", "")).strip().lower() in _ALLOWED_ACTIONS
        and ("target" in step or "value" in step or str(step.get("action", "")).lower() == "screenshot")
        for step in steps
    )


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
    os.replace(tmp, path)


async def compile_test_case(
    tc: dict[str, Any], *, login_url: str, username: str, ai_instructions: str,
    cache_dir: Path, client: Any | None, model: str, on_log: LogFn | None = None,
    dom_snapshot: str = "", project_context: str = "",
) -> CompiledPlan:
    """Return a validated plan. Passwords are intentionally not accepted."""
    log = on_log or (lambda _msg: None)
    if _already_structured(tc):
        plan = validate_plan(tc)
        log(f"[DEBUG] E2E plan structured fast path: {len(plan['steps'])} step(s)")
        return CompiledPlan(plan, cache_hit=False)

    key = _cache_key(tc, login_url, ai_instructions, project_context)
    cache_path = cache_dir / f"{key}.json"
    cached = _read_cache(cache_path)
    if cached is not None:
        try:
            plan = validate_plan(cached)
            log(f"[DEBUG] E2E plan cache hit: {key[:12]}")
            return CompiledPlan(plan, cache_hit=True, model=str(cached.get("compiler_model", "")))
        except PlanValidationError:
            cache_path.unlink(missing_ok=True)
    log(f"[DEBUG] E2E plan cache miss: {key[:12]}")
    if client is None or not model:
        raise PlanValidationError("Human-readable steps require the configured LLM plan compiler")

    source: dict[str, Any] = {
        "title": str(tc.get("title", "Untitled")),
        "preconditions": tc.get("preconditions", ""),
        "steps": tc.get("steps", []),
        "expected_results": tc.get("expected_results", tc.get("expected", "")),
        "login_url": login_url,
        "username_placeholder": "{{username}}" if username else "",
        "ai_instructions": ai_instructions[:4000],
        "schema": {
            "actions": sorted(_ALLOWED_ACTIONS),
            "locators": sorted(_ALLOWED_LOCATORS),
            "step": {"action": "", "target": "", "value": "", "expected": "", "locator": "role"},
        },
    }
    if dom_snapshot:
        source["page_snapshot"] = dom_snapshot
    # Inject rich WI context (acceptance criteria, description, repro steps)
    # so the compiler produces deterministic plans grounded in actual requirements.
    if tc.get("description"):
        source["description"] = str(tc["description"])[:3000]
    if tc.get("acceptance_criteria"):
        source["acceptance_criteria"] = str(tc["acceptance_criteria"])[:3000]
    if tc.get("repro_steps"):
        source["repro_steps"] = str(tc["repro_steps"])[:2000]
    if tc.get("environment"):
        source["environment"] = str(tc["environment"])[:500]
    if project_context:
        source["project_context"] = project_context[:16000]
    try:
        result = await client.complete_async(
            model=model, system=_SYSTEM, user=json.dumps(source, ensure_ascii=True),
            max_tokens=4096, temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        raise PlanValidationError(
            f"Plan compiler request failed: {type(exc).__name__}"
        ) from exc
    try:
        raw = str(getattr(result, "text", "") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(raw)
    except (json.JSONDecodeError, IndexError) as exc:
        raise PlanValidationError("Plan compiler returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise PlanValidationError("Plan compiler response must be an object")
    # Permissive: accept partial plans even when the LLM reports some errors,
    # as long as executable steps were also returned.
    errors = data.get("errors")
    raw_steps = data.get("steps", [])
    if isinstance(errors, list) and errors and not raw_steps:
        # Only reject if the LLM returned errors with NO steps at all
        raise PlanValidationError("; ".join(str(item) for item in errors[:5]))
    if isinstance(errors, list) and errors and raw_steps:
        # Partial plan: log errors as warnings but proceed with available steps
        log(f"[WARN] Plan compiler noted limitations: {'; '.join(str(e) for e in errors[:3])}")
    plan = validate_plan({**tc, "steps": raw_steps})
    plan["compiler_model"] = model
    if isinstance(errors, list) and errors:
        plan["compiler_warnings"] = [str(e) for e in errors[:5]]
    _write_cache(cache_path, plan)
    manual = plan.get("manual_steps_count", 0)
    suffix = f" ({manual} need manual verification)" if manual else ""
    log(f"[INFO] E2E plan compiled with {model}: {len(plan['steps'])} executable step(s){suffix}")
    return CompiledPlan(plan, cache_hit=False, model=model)


_RECOMPILE_SYSTEM: Final[str] = (
    "You are a Senior QA Engineer fixing a broken Playwright test step. "
    "Return a single JSON step object (not an array, not wrapped). "
    "Use ONLY locators visible in the provided page snapshot. "
    "The snapshot shows the accessibility tree with role:name pairs."
)


async def recompile_failed_step(
    step: dict[str, Any],
    error_message: str,
    dom_snapshot: str,
    *,
    login_url: str,
    username: str,
    client: Any | None,
    model: str,
    on_log: LogFn | None = None,
) -> dict[str, Any] | None:
    """Recompile a single failed step using error context and current DOM state.

    Returns a corrected step dict, or None if recompilation fails.
    """
    log = on_log or (lambda _msg: None)
    if client is None or not model:
        return None
    prompt = json.dumps({
        "failed_step": step,
        "error": error_message[:500],
        "page_snapshot": dom_snapshot,
        "login_url": login_url,
        "username_placeholder": "{{username}}" if username else "",
        "instruction": (
            "This step failed with the error above. Here is the current page state. "
            "Rewrite ONLY this one step to fix the locator/action. "
            "Return a single step object with fields: action, locator, target, value, expected."
        ),
    }, ensure_ascii=True)
    try:
        result = await client.complete_async(
            model=model, system=_RECOMPILE_SYSTEM, user=prompt,
            max_tokens=1024, temperature=0.0,
        )
        raw = str(getattr(result, "text", "") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        log("[WARN] Recompile LLM call failed")
        return None
    if not isinstance(data, dict):
        log("[WARN] Recompile returned non-object")
        return None
    try:
        validated = _validate_step(data, 0)
    except PlanValidationError as exc:
        log(f"[WARN] Recompiled step invalid: {exc}")
        return None
    log(f"[INFO] Step recompiled: {validated['action']} -> {validated['target']}")
    return validated
