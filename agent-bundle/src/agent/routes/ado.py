"""ADO proxy endpoints — uses locally-stored PAT to call Azure DevOps.

These handlers are thin wrappers over the async functions in ``ado.boards`` and
``ado.api``. They load the PAT + organization from the local settings store,
build a RuntimeConfig, and serialize the dataclass results to plain JSON for the
web frontend.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from core.trace import trace

router = APIRouter()

# Hosts we are willing to proxy authenticated blobs from. ADO attachment and
# inline-image URLs always live on one of these.
_ALLOWED_BLOB_HOSTS = ("dev.azure.com", "visualstudio.com", "azure.com")


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
@trace
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
@trace
async def list_projects() -> list[str]:
    """List ADO projects for the configured organization."""
    from ado.api import list_projects as _list_projects

    cfg, org = _cfg_or_400()
    try:
        return await _list_projects(cfg.pat, org, cfg)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.get("/boards/{project}")
@trace
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
@trace
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
            "linked_test_case_count": getattr(r, "test_case_count", 0),
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


def _serialize_row(r) -> dict[str, Any]:
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
        "linked_test_case_count": getattr(r, "test_case_count", 0),
    }


@router.post("/workitems/stream")
@trace
async def list_work_items_stream(req: WorkItemsRequest) -> StreamingResponse:
    """Progressive board load over SSE. Emits `columns` first (skeleton), then
    incremental `batch` events as rows arrive, then a final `done` event. The
    frontend can render lanes immediately and fill them as data streams in;
    falls back cleanly to POST /workitems for older clients."""
    from ado.boards import Board, load_board_view_streaming

    cfg, org = _cfg_or_400(req.project)
    board = Board(
        id=req.board_id,
        name=req.board_name,
        team_id=req.team_id,
        team_name=req.team_name,
    )

    # Bridge the producer's synchronous on_batch callback (called from within
    # the running loop) to the SSE consumer via an unbounded queue. A sentinel
    # (None) signals completion or error.
    queue: asyncio.Queue = asyncio.Queue()

    def _on_batch(rows: list, sofar: int, total: int) -> None:
        # sofar == -1 signals "columns ready, rows incoming" (no rows yet). The
        # authoritative column list only exists on the returned view, so we emit
        # a lightweight `start` cue here and send columns in the `done` event.
        if sofar == -1:
            queue.put_nowait({"type": "start"})
            return
        queue.put_nowait(
            {
                "type": "batch",
                "items": [_serialize_row(r) for r in rows],
                "sofar": sofar,
                "total": total,
            }
        )

    async def _produce() -> None:
        try:
            view = await load_board_view_streaming(
                org, req.project, board, cfg, on_batch=_on_batch
            )
            queue.put_nowait(
                {
                    "type": "done",
                    "columns": [c.name for c in view.columns],
                    "total": len(view.rows),
                }
            )
        except Exception as e:  # noqa: BLE001
            queue.put_nowait({"type": "error", "message": str(e)})
        finally:
            queue.put_nowait(None)

    async def _gen():
        task = asyncio.create_task(_produce())
        try:
            while True:
                evt = await queue.get()
                if evt is None:
                    break
                yield f"data: {json.dumps(evt)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/workitem/{project}/{wi_id}")
@trace
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
        "description_html": d.description_html,
        "acceptance_html": d.acceptance_html,
        "comments": [
            {"when": when, "author": author, "text": text}
            for when, author, text in d.comments
        ],
        "comments_html": [
            {"when": when, "author": author, "html": html}
            for when, author, html in d.comments_html
        ],
        "attachments": [
            {"name": a.name, "url": a.url, "size": a.size, "comment": a.comment}
            for a in d.attachments
        ],
        "hyperlinks": [{"url": url, "comment": comment} for url, comment in d.hyperlinks],
        "related": [
            {"name": name, "wi_id": rid, "url": url} for name, rid, url in d.related
        ],
    }


class TagRequest(BaseModel):
    project: str
    wi_id: int
    tag: str


@router.post("/tag")
@trace
async def tag_work_item_route(req: TagRequest) -> dict[str, Any]:
    """Add a tag to an ADO work item (idempotent, case-insensitive dedupe)."""
    from ado.boards import tag_work_item

    tag = (req.tag or "").strip()
    if not tag:
        raise HTTPException(400, "tag must not be empty")

    cfg, org = _cfg_or_400(req.project)
    try:
        ok = await tag_work_item(
            org, req.project, req.wi_id, tag, cfg.pat,
            ssl_ctx=cfg.build_ssl(),
        )
    except Exception as e:
        raise HTTPException(502, str(e))
    if not ok:
        raise HTTPException(502, f"Failed to tag work item {req.wi_id}")
    return {"ok": True, "wi_id": req.wi_id, "tag": tag}


@router.get("/blob")
@trace
async def ado_blob(
    project: str = Query(...),
    url: str = Query(...),
    download: str | None = Query(None),
) -> Response:
    """Proxy an authenticated ADO blob (inline image or attachment) using the
    stored PAT, so the browser can display or download it. Only ADO-hosted URLs
    are allowed. When ``download`` is provided, the response is sent as an
    attachment with that filename."""
    from urllib.parse import urlparse
    from ado.boards import fetch_ado_blob_async

    host = (urlparse(url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _ALLOWED_BLOB_HOSTS):
        raise HTTPException(400, "Refusing to proxy a non-Azure-DevOps URL")

    cfg, org = _cfg_or_400(project)
    try:
        data, ctype = await fetch_ado_blob_async(org, project, url, cfg)
    except Exception as e:
        raise HTTPException(502, str(e))

    headers: dict[str, str] = {"Cache-Control": "private, max-age=3600"}
    if download:
        safe = download.replace('"', "").replace("\\", "")
        headers["Content-Disposition"] = f'attachment; filename="{safe}"'
    return Response(content=data, media_type=ctype, headers=headers)
