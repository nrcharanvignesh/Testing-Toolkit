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
SCHEMA_VERSION: Final[int] = 2
_ALLOWED_ACTIONS: Final[frozenset[str]] = frozenset({
    "navigate", "fill", "click", "type", "select", "check", "uncheck",
    "hover", "double_click", "press_key", "scroll", "wait",
    "wait_for_text", "wait_for_url", "assert_text", "assert_url",
    "assert_element", "assert_not_present", "screenshot", "clear",
})
_ALLOWED_LOCATORS: Final[frozenset[str]] = frozenset({
    "role", "label", "placeholder", "text", "test_id", "css",
})
_TARGET_ACTIONS: Final[frozenset[str]] = frozenset({
    "fill", "click", "type", "select", "check", "uncheck", "hover",
    "double_click", "assert_element", "assert_not_present", "clear",
})
_VALUE_ACTIONS: Final[frozenset[str]] = frozenset({
    "navigate", "fill", "type", "select", "press_key", "wait_for_text",
    "wait_for_url", "assert_text", "assert_url",
})
_SECRET_TOKENS: Final[frozenset[str]] = frozenset({"{{password}}", "{{username}}"})

_SYSTEM: Final[str] = (
    "You compile human QA steps into a deterministic Playwright DSL. Return JSON only.\n"
    "Never emit credentials: use {{username}} and {{password}} placeholders. Do not invent "
    "steps, selectors, or expected outcomes. If a step cannot be represented, return an "
    "errors array describing it.\n\n"
    "STEP SCHEMA (every step is an object with these fields):\n"
    "  action  - one of: navigate, fill, click, type, select, check, uncheck, hover, "
    "double_click, press_key, scroll, wait, wait_for_text, wait_for_url, assert_text, "
    "assert_url, assert_element, assert_not_present, screenshot, clear\n"
    "  locator - the LOCATOR TYPE only, one of: role, label, placeholder, text, test_id, css\n"
    "  target  - the locator VALUE that Playwright receives. "
    "For role locators use 'roleName:Accessible Name' (e.g. 'button:Log In', 'textbox:Email'). "
    "For label/placeholder/text use the visible text. For test_id use the data-testid. "
    "For css use a CSS selector.\n"
    "  value   - text to fill/type/select, URL for navigate, key for press_key\n"
    "  expected - assertion text or URL fragment\n\n"
    "CRITICAL: 'locator' is ALWAYS a single word from the list above. Never put brackets, "
    "colons, or role names in the locator field. The role name and accessible name go in 'target'.\n"
    "Prefer locator strategy: role > label > placeholder > text > test_id > css."
)


class PlanValidationError(ValueError):
    """The generated test case cannot be executed safely and deterministically."""


@dataclass(slots=True)
class CompiledPlan:
    test_case: dict[str, Any]
    cache_hit: bool
    model: str = ""


def _cache_key(tc: dict[str, Any], login_url: str, ai_instructions: str) -> str:
    payload = {
        "schema": SCHEMA_VERSION,
        "test_case": tc,
        "login_url": login_url,
        "ai_instructions": ai_instructions,
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
    if action in _TARGET_ACTIONS and not target:
        raise PlanValidationError(f"Step {index} action '{action}' requires target")
    if action in _VALUE_ACTIONS and not (value or expected or target):
        raise PlanValidationError(f"Step {index} action '{action}' requires value")
    if action == "navigate" and not _is_safe_url(value or target):
        raise PlanValidationError(f"Step {index} has unsafe or invalid navigation URL")
    if action == "wait":
        try:
            wait_ms = int(value or "2000")
        except ValueError as exc:
            raise PlanValidationError(f"Step {index} wait value must be milliseconds") from exc
        if not 0 <= wait_ms <= 30000:
            raise PlanValidationError(f"Step {index} wait exceeds 30000ms ceiling")
        value = str(wait_ms)
    for field_name, field_value in (("target", target), ("value", value), ("expected", expected)):
        if any(token in field_value.lower() for token in ("password=", "passwd=", "secret=")):
            raise PlanValidationError(f"Step {index} {field_name} may contain a secret")
    return {"action": action, "target": target, "value": value, "expected": expected, "locator": locator}


def validate_steps(steps: Any) -> list[dict[str, Any]]:
    """Validate and normalize executable steps, failing closed on empty plans."""
    if not isinstance(steps, list) or not steps:
        raise PlanValidationError("Executable plan has no steps")
    validated = [_validate_step(step, i) for i, step in enumerate(steps, 1)]
    if not any(step["action"] != "screenshot" for step in validated):
        raise PlanValidationError("Executable plan contains no action or assertion")
    return validated


def validate_plan(tc: dict[str, Any]) -> dict[str, Any]:
    return {
        **tc,
        "steps": validate_steps(tc.get("steps")),
        "plan_schema_version": SCHEMA_VERSION,
    }


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
) -> CompiledPlan:
    """Return a validated plan. Passwords are intentionally not accepted."""
    log = on_log or (lambda _msg: None)
    if _already_structured(tc):
        plan = validate_plan(tc)
        log(f"[DEBUG] E2E plan structured fast path: {len(plan['steps'])} step(s)")
        return CompiledPlan(plan, cache_hit=False)

    key = _cache_key(tc, login_url, ai_instructions)
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

    source = {
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
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        raise PlanValidationError("; ".join(str(item) for item in errors[:5]))
    plan = validate_plan({**tc, "steps": data.get("steps", [])})
    plan["compiler_model"] = model
    _write_cache(cache_path, plan)
    log(f"[INFO] E2E plan compiled with {model}: {len(plan['steps'])} executable step(s)")
    return CompiledPlan(plan, cache_hit=False, model=model)
