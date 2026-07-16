"""
agent/routes/sources.py
Unified work-item source facade over ADO + JIRA.

The frontend keys everything by a project name string. When BOTH sources are
configured, project names carry a source suffix (" - ADO" / " - JIRA") so the
same board grid can show projects from either backend; the facade strips the
suffix and dispatches to the matching source route handlers. When only one
source is configured, names are returned unsuffixed (backward compatible with
the original single-ADO behavior).

Kept thin (YAGNI): it delegates to the existing ado.* / jira.* route handlers
rather than duplicating their HTTP logic.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.source_types import SourceType, append_source_suffix, strip_source_suffix
from core.trace import trace

router = APIRouter()


def _both_configured() -> bool:
    from core.settings_store import is_configured, is_jira_configured

    return is_configured() and is_jira_configured()


def _source_of(project: str) -> SourceType:
    """Resolve a (possibly suffixed) project name to its source backend."""
    from core.source_resolver import resolve_source

    return resolve_source(project)


class VerifyAllResponse(BaseModel):
    ok: bool                 # True if ANY configured source verifies
    detail: str = ""
    ado_configured: bool = False
    jira_configured: bool = False
    ado: dict[str, Any] = {}
    jira: dict[str, Any] = {}


@router.get("/verify", response_model=VerifyAllResponse)
@trace
async def verify_all() -> VerifyAllResponse:
    """Verify every configured source. Reports per-source status so the UI can
    show which backends are reachable."""
    from core.settings_store import is_configured, is_jira_configured

    ado_cfg = is_configured()
    jira_cfg = is_jira_configured()

    ado_res: dict[str, Any] = {"ok": False, "detail": "not configured"}
    jira_res: dict[str, Any] = {"ok": False, "detail": "not configured"}

    if ado_cfg:
        from agent.routes.ado import verify_pat as _verify_ado

        r = await _verify_ado()
        ado_res = {"ok": r.ok, "detail": r.detail}
    if jira_cfg:
        from agent.routes.jira import verify_jira as _verify_jira

        r = await _verify_jira()
        jira_res = {"ok": r.ok, "detail": r.detail}

    any_ok = bool(ado_res["ok"] or jira_res["ok"])
    if not ado_cfg and not jira_cfg:
        detail = "No work-item source is configured."
    elif any_ok:
        detail = "Connected."
    else:
        detail = ado_res["detail"] or jira_res["detail"] or "Verification failed."

    return VerifyAllResponse(
        ok=any_ok,
        detail=detail,
        ado_configured=ado_cfg,
        jira_configured=jira_cfg,
        ado=ado_res,
        jira=jira_res,
    )


@router.get("/projects")
@trace
async def list_projects() -> list[str]:
    """Merged project list. Suffixed by source only when both are configured."""
    from core.settings_store import is_configured, is_jira_configured

    ado_cfg = is_configured()
    jira_cfg = is_jira_configured()
    both = ado_cfg and jira_cfg

    out: list[str] = []

    if ado_cfg:
        from agent.routes.ado import list_projects as _ado_projects

        try:
            names = await _ado_projects()
        except HTTPException:
            names = []
        for n in names:
            out.append(append_source_suffix(n, SourceType.ADO) if both else n)

    if jira_cfg:
        from agent.routes.jira import list_projects as _jira_projects

        try:
            names = await _jira_projects()
        except HTTPException:
            names = []
        for n in names:
            out.append(append_source_suffix(n, SourceType.JIRA) if both else n)

    return out


@router.get("/boards/{project}")
@trace
async def list_boards(project: str) -> list[dict[str, Any]]:
    """Dispatch board listing to the resolved source."""
    bare, _ = strip_source_suffix(project)
    if _source_of(project) is SourceType.JIRA:
        from agent.routes.jira import list_boards as _jira_boards

        return await _jira_boards(bare)
    from agent.routes.ado import list_boards as _ado_boards

    return await _ado_boards(bare)


class WorkItemsRequest(BaseModel):
    project: str
    board_id: str
    board_name: str
    team_id: str = ""
    team_name: str = ""


@router.post("/workitems")
@trace
async def list_work_items(req: WorkItemsRequest) -> dict[str, Any]:
    """Dispatch board-view loading to the resolved source."""
    bare, _ = strip_source_suffix(req.project)
    if _source_of(req.project) is SourceType.JIRA:
        from agent.routes.jira import (
            WorkItemsRequest as JReq,
            list_work_items as _jira_items,
        )

        return await _jira_items(
            JReq(
                project=bare,
                board_id=req.board_id,
                board_name=req.board_name,
                team_id=req.team_id,
                team_name=req.team_name,
            )
        )
    from agent.routes.ado import (
        WorkItemsRequest as AReq,
        list_work_items as _ado_items,
    )

    return await _ado_items(
        AReq(
            project=bare,
            board_id=req.board_id,
            board_name=req.board_name,
            team_id=req.team_id,
            team_name=req.team_name,
        )
    )


@router.post("/workitems/stream")
@trace
async def list_work_items_stream(req: WorkItemsRequest):
    """Progressive board load (SSE). ADO-only: JIRA has no streaming loader, so
    JIRA projects get a 400 and the client falls back to POST /workitems."""
    bare, _ = strip_source_suffix(req.project)
    if _source_of(req.project) is SourceType.JIRA:
        raise HTTPException(
            400, "Streaming board load is only supported for ADO"
        )
    from agent.routes.ado import (
        WorkItemsRequest as AReq,
        list_work_items_stream as _ado_stream,
    )

    return await _ado_stream(
        AReq(
            project=bare,
            board_id=req.board_id,
            board_name=req.board_name,
            team_id=req.team_id,
            team_name=req.team_name,
        )
    )


@router.get("/workitem/{project}/{wi_id}")
@trace
async def work_item_detail(project: str, wi_id: str) -> dict[str, Any]:
    """Dispatch single work-item detail to the resolved source. ADO ids are
    integers; JIRA keys are strings (e.g. PROJ-123)."""
    bare, _ = strip_source_suffix(project)
    if _source_of(project) is SourceType.JIRA:
        from agent.routes.jira import work_item_detail as _jira_detail

        return await _jira_detail(bare, wi_id)
    from agent.routes.ado import work_item_detail as _ado_detail

    try:
        wid_int = int(wi_id)
    except ValueError:
        raise HTTPException(400, f"Invalid ADO work item id: {wi_id}")
    return await _ado_detail(bare, wid_int)


class TagRequest(BaseModel):
    project: str
    wi_id: str
    tag: str


@router.post("/tag")
@trace
async def tag_work_item(req: TagRequest) -> dict[str, Any]:
    """Tag a work item. ADO-only (JIRA labeling not wired). JIRA projects
    return 400 so the UI can hide the action for that source."""
    bare, _ = strip_source_suffix(req.project)
    if _source_of(req.project) is SourceType.JIRA:
        raise HTTPException(400, "Tagging is only supported for ADO work items")
    from agent.routes.ado import TagRequest as AReq, tag_work_item_route

    try:
        wid_int = int(req.wi_id)
    except ValueError:
        raise HTTPException(400, f"Invalid ADO work item id: {req.wi_id}")
    return await tag_work_item_route(
        AReq(project=bare, wi_id=wid_int, tag=req.tag)
    )
