"""ADO proxy endpoints — uses locally-stored PAT to call Azure DevOps."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


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
    from core.settings_store import get_setting, load_pat_value, KEY_ORG
    from core.runtime_config import RuntimeConfig
    from ado.api import list_projects as _list_projects

    pat = load_pat_value()
    org = get_setting(KEY_ORG)
    if not pat or not org:
        raise HTTPException(400, "PAT or organization not configured")

    cfg = RuntimeConfig.from_env_defaults()
    cfg.pat = pat
    cfg.organization = org
    try:
        return await _list_projects(pat, org, cfg)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.get("/boards/{project}")
async def list_boards(project: str) -> list[dict[str, Any]]:
    """List boards/teams for a given project."""
    from core.settings_store import get_setting, load_pat_value, KEY_ORG
    from core.runtime_config import RuntimeConfig
    from ado.boards import list_boards_for_project

    pat = load_pat_value()
    org = get_setting(KEY_ORG)
    if not pat or not org:
        raise HTTPException(400, "PAT or organization not configured")

    cfg = RuntimeConfig.from_env_defaults()
    cfg.pat = pat
    cfg.organization = org
    cfg.project = project
    try:
        return await list_boards_for_project(cfg)
    except Exception as e:
        raise HTTPException(502, str(e))


class WorkItemsRequest(BaseModel):
    project: str
    team: str
    board: str


@router.post("/workitems")
async def list_work_items(req: WorkItemsRequest) -> list[dict[str, Any]]:
    """Fetch work items for a specific board."""
    from core.settings_store import get_setting, load_pat_value, KEY_ORG
    from core.runtime_config import RuntimeConfig
    from ado.boards import fetch_board_work_items

    pat = load_pat_value()
    org = get_setting(KEY_ORG)
    if not pat or not org:
        raise HTTPException(400, "PAT or organization not configured")

    cfg = RuntimeConfig.from_env_defaults()
    cfg.pat = pat
    cfg.organization = org
    cfg.project = req.project
    try:
        return await fetch_board_work_items(cfg, req.team, req.board)
    except Exception as e:
        raise HTTPException(502, str(e))
