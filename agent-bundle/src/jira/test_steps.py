"""
jira/test_steps.py
Fetch and parse the REAL steps of linked JIRA test issues so they can be listed
and RUN in the E2E dialog.

JIRA has no single native "test steps" model -- steps live in add-ons. This
module tries, in order, the most common providers and degrades gracefully:

  1. Xray (Server/DC)  -> GET /rest/raven/1.0/api/test/{key}/step
  2. Zephyr Scale      -> GET /rest/atm/1.0/testcase/{key}   (testScript.steps)
  3. Fallback          -> the test issue's summary + description as one step

The returned shape matches the E2E runner's expectation:

    {"id": <parent issue key>, "tc_id": <test issue key>, "title": ...,
     "steps": [{"action": ..., "expected": ...}], "category": "linked",
     "source": "jira"}

Because test-management APIs vary widely by tenant, the network paths are
best-effort (any provider error falls through to the next), and the pure
parsers below are what the unit tests pin down.
"""
from __future__ import annotations

import re
from html import unescape
from typing import Any

import httpx

from core.http_retry import ssl_exception_types
from core.runtime_config import RuntimeConfig
from jira.api import build_auth_header

_TAG_RE = re.compile(r"<[^>]+>")


def _text(value: Any) -> str:
    """Coerce a JIRA text field to plain text. Handles plain strings, HTML
    (Server/DC), Xray {raw,rendered} objects, and Atlassian Document Format
    (ADF) dicts used by JIRA Cloud."""
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", value))).strip()
    if isinstance(value, dict):
        # Xray step field: prefer the raw wiki/plain text.
        if "raw" in value or "rendered" in value:
            return _text(value.get("raw") or value.get("rendered") or "")
        # ADF document: concatenate all descendant text nodes.
        if value.get("type") == "doc" or "content" in value:
            return _adf_text(value)
    if isinstance(value, list):
        return " ".join(_text(v) for v in value).strip()
    return str(value)


def _adf_text(node: Any) -> str:
    """Recursively extract text from an Atlassian Document Format node."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        parts = [_adf_text(c) for c in node.get("content", []) or []]
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
    if isinstance(node, list):
        return " ".join(_adf_text(c) for c in node).strip()
    return ""


def parse_xray_steps(payload: Any) -> list[dict[str, str]]:
    """Parse the Xray Server/DC ``/test/{key}/step`` response into steps.

    Each entry has ``step`` (action), ``data`` (optional test data), and
    ``result`` (expected). Values may be plain strings or {raw,rendered}."""
    steps: list[dict[str, str]] = []
    if not isinstance(payload, list):
        return steps
    for row in payload:
        if not isinstance(row, dict):
            continue
        action = _text(row.get("step"))
        data = _text(row.get("data"))
        expected = _text(row.get("result"))
        if data:
            action = f"{action} (data: {data})" if action else f"data: {data}"
        if action or expected:
            steps.append({"action": action, "expected": expected})
    return steps


def parse_zephyr_scale_steps(payload: Any) -> list[dict[str, str]]:
    """Parse a Zephyr Scale ``/testcase/{key}`` response (testScript.steps)."""
    steps: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return steps
    script = payload.get("testScript") or {}
    for row in script.get("steps", []) or []:
        if not isinstance(row, dict):
            continue
        action = _text(row.get("description"))
        data = _text(row.get("testData"))
        expected = _text(row.get("expectedResult"))
        if data:
            action = f"{action} (data: {data})" if action else f"data: {data}"
        if action or expected:
            steps.append({"action": action, "expected": expected})
    return steps


def _is_test_issue(issue_type: str, link_type: str) -> bool:
    return "test" in (issue_type or "").lower() or "test" in (link_type or "").lower()


def _linked_test_keys(fields: dict[str, Any]) -> list[str]:
    """Collect linked test issue keys from a parent issue's links + subtasks,
    matching the board's discovery heuristic. De-duplicated, order-preserving."""
    keys: list[str] = []
    seen: set[str] = set()

    def _add(key: str) -> None:
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        link_type = str((link.get("type") or {}).get("name", ""))
        linked = link.get("inwardIssue") or link.get("outwardIssue") or {}
        if not isinstance(linked, dict):
            continue
        itype = str(
            ((linked.get("fields") or {}).get("issuetype") or {}).get("name", "")
        )
        if _is_test_issue(itype, link_type):
            _add(str(linked.get("key", "")))
    for sub in fields.get("subtasks") or []:
        if not isinstance(sub, dict):
            continue
        itype = str(
            ((sub.get("fields") or {}).get("issuetype") or {}).get("name", "")
        )
        if _is_test_issue(itype, ""):
            _add(str(sub.get("key", "")))
    return keys


async def _fetch_steps_for_test(
    client: httpx.AsyncClient, key: str, cfg: RuntimeConfig,
) -> tuple[str, list[dict[str, str]]]:
    """Return (title, steps) for one test issue, trying Xray then Zephyr Scale
    then falling back to summary/description. Best-effort."""
    ssl_errors = ssl_exception_types()
    title = key
    # Title from the base issue (also the fallback source).
    summary = ""
    description: Any = ""
    try:
        r = await client.get(
            f"/rest/api/2/issue/{key}",
            params={"fields": "summary,description"},
        )
        if r.status_code == 200:
            f = r.json().get("fields") or {}
            summary = _text(f.get("summary"))
            description = f.get("description")
            title = summary or key
    except (httpx.HTTPError, *ssl_errors):
        pass

    # 1) Xray Server/DC.
    try:
        r = await client.get(f"/rest/raven/1.0/api/test/{key}/step")
        if r.status_code == 200:
            steps = parse_xray_steps(r.json())
            if steps:
                return title, steps
    except (httpx.HTTPError, *ssl_errors):
        pass

    # 2) Zephyr Scale.
    try:
        r = await client.get(f"/rest/atm/1.0/testcase/{key}")
        if r.status_code == 200:
            steps = parse_zephyr_scale_steps(r.json())
            if steps:
                return title, steps
    except (httpx.HTTPError, *ssl_errors):
        pass

    # 3) Fallback: summary + description as a single step.
    body = _text(description)
    action = summary or key
    if body:
        action = f"{action}. {body}"
    return title, [{"action": action, "expected": ""}]


async def fetch_linked_test_cases(
    url: str, user: str, pat: str, project: str,
    issue_keys: list[str], cfg: RuntimeConfig,
) -> list[dict[str, Any]]:
    """Return runnable linked test cases for the given parent issue keys.

    Best-effort: any error yields fewer results rather than raising."""
    if not issue_keys:
        return []
    ssl_errors = ssl_exception_types()
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        headers=build_auth_header(user, pat),
        timeout=httpx.Timeout(cfg.http_timeout_sec),
        verify=cfg.build_ssl(),
        base_url=url.rstrip("/"),
    ) as client:
        for parent in issue_keys:
            try:
                r = await client.get(
                    f"/rest/api/2/issue/{parent}",
                    params={"fields": "issuelinks,subtasks"},
                )
                if r.status_code != 200:
                    continue
                fields = r.json().get("fields") or {}
            except (httpx.HTTPError, *ssl_errors):
                continue
            for test_key in _linked_test_keys(fields):
                title, steps = await _fetch_steps_for_test(client, test_key, cfg)
                if not steps:
                    continue
                out.append({
                    "id": str(parent),
                    "tc_id": test_key,
                    "title": title,
                    "steps": steps,
                    "category": "linked",
                    "source": "jira",
                })
    return out
