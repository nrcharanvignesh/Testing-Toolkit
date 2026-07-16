"""
jira/boards.py
JIRA Agile boards, sprints, and issue fetching.
Uses /rest/agile/1.0/board and /rest/api/2/search (JQL).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from core.http_retry import request_with_retry, ssl_exception_types
from core.runtime_config import RuntimeConfig
from jira.api import build_auth_header


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------
@dataclass(slots=True)
class JiraBoard:
    id: int
    name: str
    board_type: str  # "scrum" or "kanban"
    project_key: str


@dataclass(slots=True)
class JiraIssue:
    key: str          # e.g. "PROJ-123"
    issue_id: int
    summary: str
    issue_type: str   # "Story", "Bug", "Task", etc.
    status: str
    assignee: str
    priority: str
    sprint: str
    labels: list[str] = field(default_factory=list)
    # Count of linked Test issues (issue links whose target is a Test/Test Case
    # issue type or whose link type mentions "test"). 0 when none/unavailable.
    test_case_count: int = 0


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------
def _client(url: str, user: str, pat: str, cfg: RuntimeConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=build_auth_header(user, pat),
        timeout=httpx.Timeout(cfg.http_timeout_sec),
        verify=cfg.build_ssl(),
        base_url=url.rstrip("/"),
    )


def _jql_escape(value: str) -> str:
    """Escape a value for embedding in a JQL string literal.

    JQL string literals are single-quoted; internal single quotes,
    backslashes, and reserved chars are escaped with a backslash.
    """
    # Backslash must be escaped first to avoid double-escaping.
    s = value.replace("\\", "\\\\")
    s = s.replace("'", "\\'")
    s = s.replace('"', '\\"')
    return s


def _parse_issue(raw: dict[str, Any]) -> JiraIssue:
    """Parse a JIRA issue JSON object into a JiraIssue dataclass."""
    fields = raw.get("fields") or {}
    assignee_obj = fields.get("assignee") or {}
    assignee_name = (
        assignee_obj.get("displayName", "")
        if isinstance(assignee_obj, dict) else str(assignee_obj)
    )
    priority_obj = fields.get("priority") or {}
    priority_name = (
        priority_obj.get("name", "")
        if isinstance(priority_obj, dict) else str(priority_obj)
    )
    # Sprint: try the sprint field (customfield varies); fallback to empty
    sprint_name = ""
    sprint_field = fields.get("sprint")
    if isinstance(sprint_field, dict):
        sprint_name = sprint_field.get("name", "")
    labels = fields.get("labels") or []
    issue_type_obj = fields.get("issuetype") or {}
    status_obj = fields.get("status") or {}
    # Versatile linked-test-case discovery, tolerant of any team's JIRA setup.
    # Counts DISTINCT test issues reached via EITHER (a) an issue link whose
    # linked issue type OR link-type name mentions "test" (covers "Tests" /
    # "Tested By" link types and Test / Test Case / Xray-Zephyr test issue
    # types like "Test", "Test Execution", "Test Set"), OR (b) a subtask whose
    # type mentions "test" (teams that model tests as subtasks). De-duplicated
    # by issue key so a test reachable both ways is counted once.
    test_keys: set[str] = set()
    _n = 0
    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        link_type = str((link.get("type") or {}).get("name", "")).lower()
        linked = link.get("inwardIssue") or link.get("outwardIssue") or {}
        if not isinstance(linked, dict):
            continue
        linked_type = str(
            ((linked.get("fields") or {}).get("issuetype") or {}).get("name", "")
        ).lower()
        if "test" in linked_type or "test" in link_type:
            key = str(linked.get("key", ""))
            if not key:
                _n += 1
                key = f"link#{_n}"
            test_keys.add(key)
    for sub in fields.get("subtasks") or []:
        if not isinstance(sub, dict):
            continue
        sub_type = str(
            ((sub.get("fields") or {}).get("issuetype") or {}).get("name", "")
        ).lower()
        if "test" in sub_type:
            key = str(sub.get("key", ""))
            if not key:
                _n += 1
                key = f"sub#{_n}"
            test_keys.add(key)
    test_links = len(test_keys)
    return JiraIssue(
        key=str(raw.get("key", "")),
        issue_id=int(raw.get("id", 0) or 0),
        summary=str(fields.get("summary", "")).strip(),
        issue_type=(
            issue_type_obj.get("name", "")
            if isinstance(issue_type_obj, dict) else str(issue_type_obj)
        ),
        status=(
            status_obj.get("name", "")
            if isinstance(status_obj, dict) else str(status_obj)
        ),
        assignee=assignee_name,
        priority=priority_name,
        sprint=sprint_name,
        labels=list(labels),
        test_case_count=test_links,
    )


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------
async def list_boards(
    url: str, user: str, pat: str, project_key: str, cfg: RuntimeConfig,
) -> list[JiraBoard]:
    """GET /rest/agile/1.0/board?projectKeyOrId=<key>

    Paginates through all boards for the given project.
    """
    boards: list[JiraBoard] = []
    start_at = 0
    max_results = 50
    try:
        async with _client(url, user, pat, cfg) as client:
            while True:
                endpoint = (
                    f"/rest/agile/1.0/board"
                    f"?projectKeyOrId={_jql_escape(project_key)}"
                    f"&startAt={start_at}&maxResults={max_results}"
                )
                r = await request_with_retry(client, "GET", endpoint)
                if r.status_code != 200:
                    break
                data: dict[str, Any] = r.json()
                for b in data.get("values", []) or []:
                    loc = b.get("location") or {}
                    boards.append(JiraBoard(
                        id=int(b.get("id", 0) or 0),
                        name=str(b.get("name", "")).strip(),
                        board_type=str(b.get("type", "")).lower(),
                        project_key=str(
                            loc.get("projectKey", project_key)
                        ).strip(),
                    ))
                # Pagination check
                is_last = data.get("isLast", True)
                if is_last:
                    break
                start_at += max_results
    except Exception:
        # Best-effort; return whatever was collected
        pass
    return boards


async def search_issues(
    url: str,
    user: str,
    pat: str,
    jql: str,
    cfg: RuntimeConfig,
    max_results: int = 200,
    on_batch: Callable[[list[JiraIssue]], None] | None = None,
) -> list[JiraIssue]:
    """POST /rest/api/2/search with JQL. Paginated via startAt.

    Fetches up to max_results issues total. Emits batches via on_batch
    as each page arrives for progressive UI rendering.
    """
    issues: list[JiraIssue] = []
    start_at = 0
    page_size = min(max_results, 100)
    fields_requested = [
        "summary", "issuetype", "status", "assignee",
        "priority", "labels", "sprint", "issuelinks", "subtasks",
    ]
    try:
        async with _client(url, user, pat, cfg) as client:
            while start_at < max_results:
                body = {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": min(page_size, max_results - start_at),
                    "fields": fields_requested,
                }
                r = await request_with_retry(
                    client, "POST", "/rest/api/2/search",
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code != 200:
                    break
                data: dict[str, Any] = r.json()
                raw_issues = data.get("issues", []) or []
                if not raw_issues:
                    break
                batch: list[JiraIssue] = [
                    _parse_issue(ri) for ri in raw_issues
                ]
                issues.extend(batch)
                if on_batch and batch:
                    on_batch(batch)
                total = int(data.get("total", 0) or 0)
                start_at += len(raw_issues)
                if start_at >= total:
                    break
    except Exception:
        pass
    return issues


async def get_issue_detail(
    url: str, user: str, pat: str, issue_key: str, cfg: RuntimeConfig,
) -> dict[str, Any]:
    """GET /rest/api/2/issue/{key}?expand=renderedFields,names

    Returns the full issue JSON dict. Returns empty dict on failure.
    """
    endpoint = (
        f"/rest/api/2/issue/{_jql_escape(issue_key)}"
        f"?expand=renderedFields,names"
    )
    try:
        async with _client(url, user, pat, cfg) as client:
            r = await request_with_retry(client, "GET", endpoint)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {}


# ------------------------------------------------------------------
# Work-item dump (feeds the generation pipeline)
# ------------------------------------------------------------------
# Common JIRA custom-field names used for acceptance criteria across
# templates. Checked in order; first non-empty wins. Best-effort only.
_AC_FIELD_HINTS: tuple[str, ...] = (
    "Acceptance Criteria",
    "Acceptance criteria",
    "AcceptanceCriteria",
)


def _issue_meta(detail: dict[str, Any]) -> dict[str, Any]:
    """Flatten one raw JIRA issue JSON (from get_issue_detail) into the
    duck-typed record shape build_work_item_dump() consumes. The issue KEY
    is used as the identifier so the generator emits it as the parent id,
    which the JIRA upload path then maps straight back to parent_key."""
    fields = detail.get("fields") or {}
    rendered = detail.get("renderedFields") or {}
    names = detail.get("names") or {}

    key = str(detail.get("key", "")).strip()
    itype = fields.get("issuetype") or {}
    status = fields.get("status") or {}

    # Description: prefer plain wiki-markup text (API v2) over rendered HTML.
    desc = str(fields.get("description") or "").strip()
    if not desc:
        desc = str(rendered.get("description") or "").strip()

    # Acceptance criteria: scan custom fields by display name (best-effort).
    ac = ""
    for field_id, disp in names.items():
        if any(h.lower() == str(disp).lower() for h in _AC_FIELD_HINTS):
            val = fields.get(field_id)
            if isinstance(val, str) and val.strip():
                ac = val.strip()
                break

    # Comments -> [{text, author}] shape build_work_item_dump understands.
    comments: list[dict[str, str]] = []
    comment_obj = fields.get("comment") or {}
    for c in comment_obj.get("comments", []) or []:
        author = ((c.get("author") or {}).get("displayName", "")) or ""
        body = str(c.get("body", "") or "").strip()
        if body:
            comments.append({"author": author, "text": body})

    return {
        "wi_id": key,                       # string id; not cast to int here
        "title": str(fields.get("summary", "")).strip(),
        "wi_type": (
            itype.get("name", "") if isinstance(itype, dict) else str(itype)
        ),
        "state": (
            status.get("name", "")
            if isinstance(status, dict) else str(status)
        ),
        "area_path": "",
        "iteration_path": "",
        "tags": list(fields.get("labels") or []),
        "description_text": desc,
        "acceptance_text": ac,
        "comments": comments,
    }


def build_jira_work_item_dump(details: list[dict[str, Any]]) -> str:
    """Format raw JIRA issue JSON into the same 'work item dump' text the
    generation pipeline expects, keyed by issue KEY (e.g. PROJ-123).

    Mirrors testgen.rlm.build_work_item_dump but keeps the string key as the
    identifier (JIRA keys are not integers), so the generated payload's
    parent_work_item_id carries the key for a clean round-trip on upload.
    """
    blocks: list[str] = []
    for detail in details:
        m = _issue_meta(detail)
        key = m["wi_id"]
        if not key:
            continue
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append(
            f"WORK ITEM #{key} [{m['wi_type']}] - {m['title']}"
        )
        lines.append(f"State: {m['state'] or 'n/a'}")
        if m["tags"]:
            lines.append("Tags: " + ", ".join(str(t) for t in m["tags"]))
        lines.append("")
        lines.append("DESCRIPTION:")
        lines.append(m["description_text"] or "(none)")
        lines.append("")
        lines.append("ACCEPTANCE CRITERIA:")
        lines.append(m["acceptance_text"] or "(none)")
        if m["comments"]:
            lines.append("")
            lines.append("COMMENTS:")
            for c in m["comments"]:
                who = c.get("author", "") or "unknown"
                lines.append(f"- ({who}) {c.get('text', '')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


