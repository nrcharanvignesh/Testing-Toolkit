"""ADO proxy endpoints — uses locally-stored PAT to call Azure DevOps.

These handlers are thin wrappers over the async functions in ``ado.boards`` and
``ado.api``. They load the PAT + organization from the local settings store,
build a RuntimeConfig, and serialize the dataclass results to plain JSON for the
web frontend.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


def _cfg_or_400(project: str | None = None):
    """Build a RuntimeConfig from stored PAT/org or raise 400."""
    from core.settings_store import get_setting, load_pat_value, KEY_ORG
    from core.runtime_config import RuntimeConfig

    pat = load_pat_value()
    org = get_setting(KEY_ORG)
    if not pat or not org:
        raise HTTPException(400, "PAT or organization not configured")

    cfg = RuntimeConfig.from_env_defaults()
    cfg.pat = pat
    cfg.organization = org
    if project:
        cfg.project = project
    return cfg, org


class VerifyPatResponse(BaseModel):
    ok: bool
    detail: str = ""


@router.get("/verify")
async def verify_pat() -> VerifyPatResponse:
    """Verify the stored PAT works against the configured organization."""
    from core.settings_store import get_setting, load_pat_value, KEY_ORG
    from core.runtime_config import RuntimeConfig
    from ado.api import verify_pat_for_org

    pat = load_pat_value()
    org = get_setting(KEY_ORG)
    if not pat or not org:
        return VerifyPatResponse(ok=False, detail="PAT or organization not configured")

    cfg = RuntimeConfig.from_env_defaults()
    cfg.pat = pat
    cfg.organization = org
    ok, detail = await verify_pat_for_org(pat, org, cfg)
    return VerifyPatResponse(ok=ok, detail=detail)


@router.get("/projects")
async def list_projects() -> list[str]:
    """List ADO projects for the configured organization."""
    from ado.api import list_projects as _list_projects

    cfg, org = _cfg_or_400()
    try:
        return await _list_projects(cfg.pat, org, cfg)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.get("/boards/{project}")
async def list_boards(project: str) -> list[dict[str, Any]]:
    """List boards (across all teams) for a given project."""
    from ado.boards import list_boards_for_project_async

    cfg, org = _cfg_or_400(project)
    try:
        boards = await list_boards_for_project_async(org, project, cfg)
    except Exception as e:
        raise HTTPException(502, str(e))
    return [
        {
            "id": b.id,
            "name": b.name,
            "team_id": b.team_id,
            "team_name": b.team_name,
            "label": b.label,
        }
        for b in boards
    ]


class WorkItemsRequest(BaseModel):
    project: str
    board_id: str
    board_name: str
    team_id: str
    team_name: str


@router.post("/workitems")
async def list_work_items(req: WorkItemsRequest) -> dict[str, Any]:
    """Fetch work items for a specific board, grouped by board column (lane)."""
    from ado.boards import Board, load_board_view_async

    cfg, org = _cfg_or_400(req.project)
    board = Board(
        id=req.board_id,
        name=req.board_name,
        team_id=req.team_id,
        team_name=req.team_name,
    )
    try:
        view = await load_board_view_async(org, req.project, board, cfg)
    except Exception as e:
        raise HTTPException(502, str(e))

    def _row(r) -> dict[str, Any]:
        return {
            "wi_id": r.wi_id,
            "title": r.title,
            "wi_type": r.wi_type,
            "state": r.state,
            "board_column": r.board_column,
            "board_lane": r.board_lane,
            "assigned_to": r.assigned_to,
            "tags": list(r.tags),
            "iteration_path": r.iteration_path,
            "iteration_leaf": r.iteration_leaf,
            "area_path": r.area_path,
        }

    groups = [
        {"column": name, "items": [_row(r) for r in rows]}
        for name, rows in view.grouped()
    ]
    return {
        "columns": [c.name for c in view.columns],
        "groups": groups,
        "total": len(view.rows),
    }


@router.get("/workitem/{project}/{wi_id}")
async def work_item_detail(project: str, wi_id: int) -> dict[str, Any]:
    """Fetch full detail for a single work item."""
    from ado.boards import fetch_work_item_detail_async

    cfg, org = _cfg_or_400(project)
    try:
        d = await fetch_work_item_detail_async(org, project, wi_id, cfg)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {
        "wi_id": d.wi_id,
        "title": d.title,
        "wi_type": d.wi_type,
        "state": d.state,
        "board_column": d.board_column,
        "area_path": d.area_path,
        "iteration_path": d.iteration_path,
        "assigned_to": d.assigned_to,
        "tags": list(d.tags),
        "description_text": d.description_text,
        "acceptance_text": d.acceptance_text,
        "comments": [
            {"when": when, "author": author, "text": text}
            for when, author, text in d.comments
        ],
        "attachments": [
            {"name": a.name, "url": a.url, "size": a.size}
            for a in d.attachments
        ],
        "hyperlinks": [{"url": url, "comment": comment} for url, comment in d.hyperlinks],
        "related": [
            {"name": name, "wi_id": rid, "url": url} for name, rid, url in d.related
        ],
    }
