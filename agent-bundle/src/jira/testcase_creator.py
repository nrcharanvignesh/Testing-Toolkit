"""
jira/testcase_creator.py
Creates test case issues in JIRA Server/DC.
If Xray plugin is detected, uses Xray steps format.
Otherwise creates standard issues with steps in description (Markdown table).
"""

from __future__ import annotations

import asyncio
import gc
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from core.http_retry import request_with_retry, ssl_exception_types
from core.runtime_config import RuntimeConfig
from jira.api import build_auth_header


# ------------------------------------------------------------------
# Result types
# ------------------------------------------------------------------
@dataclass(slots=True)
class JiraCreateResult:
    parent_key: str
    title: str
    created_key: str | None = None
    created_url: str | None = None
    ok: bool = True
    error: str = ""


@dataclass(slots=True)
class JiraBatchResult:
    results: list[JiraCreateResult] = field(default_factory=list)
    n_ok: int = 0
    n_failed: int = 0


# ------------------------------------------------------------------
# Xray detection
# ------------------------------------------------------------------
async def detect_xray(
    url: str, user: str, pat: str, cfg: RuntimeConfig,
) -> bool:
    """Check if Xray Test Management plugin is installed.

    Probes the Xray REST endpoint. Returns True if responsive.
    """
    endpoint = f"{url.rstrip('/')}/rest/raven/1.0/api/settings/testTypes"
    headers = build_auth_header(user, pat)
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(min(cfg.http_timeout_sec, 15.0)),
            verify=cfg.build_ssl(),
        ) as client:
            r = await client.get(endpoint)
            return r.status_code == 200
    except Exception:
        return False


# ------------------------------------------------------------------
# Step formatting
# ------------------------------------------------------------------
def build_steps_description(steps: list[dict[str, str]]) -> str:
    """Build a Markdown table of test steps for the description field.

    Output format:
        || # || Action || Expected ||
        | 1 | Do X | Y happens |
        | 2 | Do Z | W happens |

    Uses JIRA wiki markup table syntax (pipes) which renders in both
    JIRA Server rich-text and plain views.
    """
    if not steps:
        return ""
    lines: list[str] = []
    lines.append("|| # || Action || Expected ||")
    for i, step in enumerate(steps, start=1):
        action = str(step.get("action", "")).replace("|", "/").replace("\n", " ")
        expected = str(step.get("expected", "")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {i} | {action} | {expected} |")
    return "\n".join(lines)


def _build_xray_steps(steps: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Build Xray-format step objects for the test steps custom field."""
    xray_steps: list[dict[str, Any]] = []
    for i, step in enumerate(steps, start=1):
        xray_steps.append({
            "index": i,
            "action": str(step.get("action", "")),
            "result": str(step.get("expected", "")),
            "data": "",
        })
    return xray_steps


# ------------------------------------------------------------------
# Internal: create a single test case
# ------------------------------------------------------------------
def _client(url: str, user: str, pat: str, cfg: RuntimeConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=build_auth_header(user, pat),
        timeout=httpx.Timeout(cfg.http_timeout_sec),
        verify=cfg.build_ssl(),
        base_url=url.rstrip("/"),
    )


async def _create_one_standard(
    client: httpx.AsyncClient,
    project_key: str,
    parent_key: str,
    tc: dict[str, Any],
    on_log: Callable[[str], None] | None,
) -> JiraCreateResult:
    """Create a standard JIRA issue as a test case (no Xray)."""
    title = str(tc.get("title", "")).strip()
    res = JiraCreateResult(parent_key=parent_key, title=title)

    steps = tc.get("steps") or []
    description_parts: list[str] = []

    preconditions = str(tc.get("preconditions", "") or "").strip()
    if preconditions:
        description_parts.append(f"*Preconditions:* {preconditions}")
        description_parts.append("")

    if steps:
        description_parts.append(build_steps_description(steps))

    tags = tc.get("tags") or []
    labels = [str(t).strip().replace(" ", "-") for t in tags if str(t).strip()]

    body: dict[str, Any] = {
        "fields": {
            "project": {"key": project_key},
            "summary": title,
            "issuetype": {"name": "Test"},
            "description": "\n".join(description_parts),
        },
    }
    if labels:
        body["fields"]["labels"] = labels

    # Link to parent issue
    # ponytail: uses issuelinks in create; direct link-API call if this fails
    if parent_key:
        body["update"] = {
            "issuelinks": [{
                "add": {
                    "type": {"name": "Tests"},
                    "outwardIssue": {"key": parent_key},
                },
            }],
        }

    try:
        r = await request_with_retry(
            client, "POST", "/rest/api/2/issue",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code not in (200, 201):
            res.ok = False
            res.error = f"HTTP {r.status_code}: {r.text[:400]}"
            if on_log:
                on_log(f"[ERROR] '{title}': {res.error}")
            return res
        data = r.json()
        res.created_key = str(data.get("key", ""))
        res.created_url = str(data.get("self", ""))
        if on_log:
            on_log(
                f"[SUCCESS] Created {res.created_key} for parent "
                f"{parent_key}: '{title}'"
            )
    except Exception as e:
        res.ok = False
        res.error = f"{type(e).__name__}: {e}"
        if on_log:
            on_log(f"[ERROR] '{title}': {res.error}")
    return res


async def _create_one_xray(
    client: httpx.AsyncClient,
    project_key: str,
    parent_key: str,
    tc: dict[str, Any],
    on_log: Callable[[str], None] | None,
) -> JiraCreateResult:
    """Create a test case using Xray REST API for steps."""
    title = str(tc.get("title", "")).strip()
    res = JiraCreateResult(parent_key=parent_key, title=title)

    steps = tc.get("steps") or []
    preconditions = str(tc.get("preconditions", "") or "").strip()
    tags = tc.get("tags") or []
    labels = [str(t).strip().replace(" ", "-") for t in tags if str(t).strip()]

    # Step 1: create the Test issue type (Xray registers "Test" as issue type)
    body: dict[str, Any] = {
        "fields": {
            "project": {"key": project_key},
            "summary": title,
            "issuetype": {"name": "Test"},
        },
    }
    if preconditions:
        body["fields"]["description"] = f"*Preconditions:* {preconditions}"
    if labels:
        body["fields"]["labels"] = labels
    if parent_key:
        body["update"] = {
            "issuelinks": [{
                "add": {
                    "type": {"name": "Tests"},
                    "outwardIssue": {"key": parent_key},
                },
            }],
        }

    try:
        r = await request_with_retry(
            client, "POST", "/rest/api/2/issue",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code not in (200, 201):
            res.ok = False
            res.error = f"HTTP {r.status_code}: {r.text[:400]}"
            if on_log:
                on_log(f"[ERROR] '{title}': {res.error}")
            return res
        data = r.json()
        created_key = str(data.get("key", ""))
        res.created_key = created_key
        res.created_url = str(data.get("self", ""))
    except Exception as e:
        res.ok = False
        res.error = f"{type(e).__name__}: {e}"
        if on_log:
            on_log(f"[ERROR] '{title}': {res.error}")
        return res

    # Step 2: add Xray test steps concurrently (semaphore-bounded)
    if steps and res.created_key:
        xray_steps = _build_xray_steps(steps)
        steps_endpoint = (
            f"/rest/raven/1.0/api/test/{res.created_key}/step"
        )
        sem = asyncio.Semaphore(4)

        async def _post_step(xstep: dict[str, Any]) -> None:
            async with sem:
                try:
                    await request_with_retry(
                        client, "POST", steps_endpoint,
                        json={
                            "action": xstep["action"],
                            "result": xstep["result"],
                            "data": xstep["data"],
                        },
                        headers={"Content-Type": "application/json"},
                    )
                except Exception:
                    # Best-effort step creation; log but don't fail the TC
                    if on_log:
                        on_log(
                            f"[WARN] Failed to add step {xstep['index']} to "
                            f"{res.created_key}"
                        )

        await asyncio.gather(*[_post_step(s) for s in xray_steps])

    if on_log and res.ok:
        on_log(
            f"[SUCCESS] Created {res.created_key} (Xray) for parent "
            f"{parent_key}: '{title}'"
        )
    return res


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------
async def create_test_cases(
    payload: dict[str, Any],
    url: str,
    user: str,
    pat: str,
    project_key: str,
    cfg: RuntimeConfig,
    *,
    on_progress: Callable[[str, int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> JiraBatchResult:
    """Main entry: iterate payload stories -> test_cases, create each in JIRA.

    Payload format mirrors the ADO testcase_creator schema:
    {
      "schema_version": 1,
      "stories": [
        {
          "parent_key": "PROJ-123",
          "parent_title": "...",
          "test_cases": [ { "title", "steps", "tags", ... } ]
        }
      ]
    }
    """
    batch = JiraBatchResult()
    stories = payload.get("stories") or []
    total = sum(len(s.get("test_cases") or []) for s in stories)

    if on_log:
        on_log(
            f"[INFO] Creating {total} test case(s) across {len(stories)} "
            f"story/stories in project {project_key}"
        )

    # Detect Xray availability
    use_xray = await detect_xray(url, user, pat, cfg)
    if on_log:
        mode = "Xray" if use_xray else "standard (description table)"
        on_log(f"[INFO] Test case format: {mode}")

    sem = asyncio.Semaphore(max(1, cfg.concurrency))
    progress = {"n": 0}

    async with _client(url, user, pat, cfg) as client:
        # Build flat task list preserving order
        tasks: list[tuple[str, dict[str, Any]]] = []
        for story in stories:
            parent_key = str(story.get("parent_key", "")).strip()
            parent_title = str(story.get("parent_title", "") or "").strip()
            if on_log and parent_title:
                on_log(f"[INFO] Parent {parent_key}: {parent_title}")
            for tc in story.get("test_cases") or []:
                tasks.append((parent_key, tc))

        async def _run_one(
            parent_key: str, tc: dict[str, Any],
        ) -> JiraCreateResult:
            async with sem:
                if on_progress:
                    on_progress("create", progress["n"], total)
                if use_xray:
                    r = await _create_one_xray(
                        client, project_key, parent_key, tc, on_log,
                    )
                else:
                    r = await _create_one_standard(
                        client, project_key, parent_key, tc, on_log,
                    )
                progress["n"] += 1
                return r

        results = await asyncio.gather(
            *(_run_one(pk, tc) for pk, tc in tasks)
        )
        for r in results:
            batch.results.append(r)
            if r.ok:
                batch.n_ok += 1
            else:
                batch.n_failed += 1

    if on_progress:
        on_progress("create", total, total)
    gc.collect()
    return batch
