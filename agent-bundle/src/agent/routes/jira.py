"""
agent/routes/jira.py
JIRA source endpoints, shaped to match the ADO routes so the web frontend can
render JIRA boards/issues in the same board grid.

Auth: HTTP Basic (username + PAT) against JIRA Server/Data Center REST.
Connection params come from the settings store (jira_url / jira_user + the
JIRA PAT in the keyring).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.trace import trace

router = APIRouter()


def _jira_conn():
    """Return (url, user, pat, cfg) or raise 400 if JIRA is not configured."""
    from core.settings_store import (
        get_setting,
        is_jira_configured,
        build_jira_runtime_config,
        KEY_JIRA_URL,
        KEY_JIRA_USER,
    )

    if not is_jira_configured():
        raise HTTPException(400, "JIRA is not configured")
    url = get_setting(KEY_JIRA_URL)
    user = get_setting(KEY_JIRA_USER)
    cfg = build_jira_runtime_config()
    return url, user, cfg.pat, cfg


class VerifyResponse(BaseModel):
    ok: bool
    detail: str = ""


@router.get("/verify")
@trace
async def verify_jira() -> VerifyResponse:
    """Verify stored JIRA credentials via /rest/api/2/myself."""
    from core.settings_store import (
        get_setting,
        load_jira_pat,
        build_jira_runtime_config,
        KEY_JIRA_URL,
        KEY_JIRA_USER,
    )
    from jira.api import verify_connection

    url = get_setting(KEY_JIRA_URL)
    user = get_setting(KEY_JIRA_USER)
    pat = load_jira_pat()
    if not url or not user or not pat:
        return VerifyResponse(ok=False, detail="JIRA URL, user, or token not configured")
    ok, detail = await verify_connection(url, user, pat, build_jira_runtime_config())
    return VerifyResponse(ok=ok, detail=detail)


@router.get("/projects")
@trace
async def list_projects() -> list[str]:
    """List JIRA project keys."""
    from jira.api import list_projects as _list_projects

    url, user, pat, cfg = _jira_conn()
    try:
        return await _list_projects(url, user, pat, cfg)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.get("/boards/{project}")
@trace
async def list_boards(project: str) -> list[dict[str, Any]]:
    """List JIRA agile boards for a project, shaped like ADO boards."""
    from core.source_types import strip_source_suffix
    from jira.boards import list_boards as _list_boards

    bare, _st = strip_source_suffix(project)
    url, user, pat, cfg = _jira_conn()
    try:
        boards = await _list_boards(url, user, pat, bare, cfg)
    except Exception as e:
        raise HTTPException(502, str(e))
    return [
        {
            "id": str(b.id),
            "name": b.name,
            "team_id": str(b.id),
            "team_name": b.board_type,
            "label": f"{b.name} ({b.board_type})",
        }
        for b in boards
    ]


class WorkItemsRequest(BaseModel):
    project: str
    board_id: str
    board_name: str
    team_id: str = ""
    team_name: str = ""


def _issue_row(iss) -> dict[str, Any]:
    """Map a JiraIssue to the same row shape the ADO board grid consumes."""
    return {
        "wi_id": iss.key,          # JIRA uses string keys (e.g. PROJ-123)
        "title": iss.summary,
        "wi_type": iss.issue_type,
        "state": iss.status,
        "board_column": iss.status,
        "board_lane": "",
        "assigned_to": iss.assignee,
        "tags": list(iss.labels),
        "iteration_path": iss.sprint,
        "iteration_leaf": iss.sprint,
        "area_path": "",
        "linked_test_case_count": getattr(iss, "test_case_count", 0),
    }


@router.post("/workitems")
@trace
async def list_work_items(req: WorkItemsRequest) -> dict[str, Any]:
    """Fetch JIRA issues for a project, grouped by status (lane)."""
    from core.source_types import strip_source_suffix
    from jira.boards import search_issues

    bare, _st = strip_source_suffix(req.project)
    url, user, pat, cfg = _jira_conn()
    jql = f'project = "{bare}" ORDER BY status ASC, key ASC'
    try:
        issues = await search_issues(url, user, pat, jql, cfg, max_results=300)
    except Exception as e:
        raise HTTPException(502, str(e))

    # Group by status, preserving first-seen order for stable columns.
    columns: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for iss in issues:
        col = iss.status or "No Status"
        if col not in grouped:
            grouped[col] = []
            columns.append(col)
        grouped[col].append(_issue_row(iss))

    groups = [{"column": c, "items": grouped[c]} for c in columns]
    return {"columns": columns, "groups": groups, "total": len(issues)}


@router.get("/workitem/{project}/{issue_key}")
@trace
async def work_item_detail(project: str, issue_key: str) -> dict[str, Any]:
    """Fetch full detail for a single JIRA issue, shaped like an ADO WI."""
    from core.source_types import strip_source_suffix
    from jira.boards import get_issue_detail

    _bare, _st = strip_source_suffix(project)
    url, user, pat, cfg = _jira_conn()
    try:
        raw = await get_issue_detail(url, user, pat, issue_key, cfg)
    except Exception as e:
        raise HTTPException(502, str(e))
    if not raw:
        raise HTTPException(404, f"Issue {issue_key} not found")

    fields = raw.get("fields") or {}
    rendered = raw.get("renderedFields") or {}
    issue_type = (fields.get("issuetype") or {}).get("name", "")
    status = (fields.get("status") or {}).get("name", "")
    assignee = (fields.get("assignee") or {}).get("displayName", "")
    desc_text = str(fields.get("description") or "")
    desc_html = str(rendered.get("description") or "")

    # JIRA comments live under fields.comment.comments
    comment_block = (fields.get("comment") or {}).get("comments", []) or []
    comments = [
        (
            str(c.get("created", "")),
            str((c.get("author") or {}).get("displayName", "")),
            str(c.get("body", "")),
        )
        for c in comment_block
    ]

    return {
        "wi_id": issue_key,
        "title": str(fields.get("summary", "")),
        "wi_type": issue_type,
        "state": status,
        "board_column": status,
        "area_path": "",
        "iteration_path": "",
        "assigned_to": assignee,
        "tags": list(fields.get("labels") or []),
        "description_text": desc_text,
        "acceptance_text": "",
        "description_html": desc_html,
        "acceptance_html": "",
        "comments": [
            {"when": when, "author": author, "text": text}
            for when, author, text in comments
        ],
        "comments_html": [],
        "attachments": [
            {
                "name": str(a.get("filename", "")),
                "url": str(a.get("content", "")),
                "size": int(a.get("size", 0) or 0),
                "comment": "",
            }
            for a in (fields.get("attachment") or [])
        ],
        "hyperlinks": [],
        "related": [],
    }
