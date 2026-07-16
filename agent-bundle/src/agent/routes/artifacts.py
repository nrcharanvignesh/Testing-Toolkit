"""Artifacts endpoints — browse + download generated outputs.

Backs the web "Outputs" tab (the desktop artifacts browser). It enumerates the
two kinds of generated files the toolkit produces for a project:

  * "testcases" — reviewer Excel workbooks in projects/<p>/generated/
  * "packets"   — combined PDF packets in outputs/<p>/packets/

    GET /artifacts/{project}        list artifacts (newest first)
    GET /artifacts/download?path=   stream a single artifact

Downloads are constrained to the toolkit workspace so a crafted ?path= cannot
escape and read arbitrary files off the user's machine.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from core.trace import trace

router = APIRouter()


def _workspace_root() -> Path:
    from core.app_config import WORKSPACE

    return Path(WORKSPACE).resolve()


def _describe(path: Path, kind: str) -> dict[str, Any]:
    st = path.stat()
    return {
        "name": path.name,
        "path": str(path.resolve()),
        "kind": kind,
        "size": st.st_size,
        "modified": st.st_mtime,
    }


# NOTE: the static "/download" route MUST be declared before the dynamic
# "/{project}" route, otherwise FastAPI matches GET /artifacts/download as
# list_artifacts(project="download").
@router.get("/download")
@trace
async def download_artifact(path: str = Query(...)) -> FileResponse:
    target = Path(path).resolve()
    root = _workspace_root()
    # Constrain to the workspace so ?path= cannot escape (path traversal).
    if root not in target.parents and target != root:
        raise HTTPException(403, "Path outside workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Artifact not found")
    media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media, filename=target.name)


@router.delete("/delete")
@trace
async def delete_artifact(path: str = Query(...)) -> dict[str, Any]:
    """Delete a single generated artifact (desktop 'Delete' button).

    Constrained to the toolkit workspace so a crafted ?path= cannot delete
    arbitrary files off the user's machine.
    """
    target = Path(path).resolve()
    root = _workspace_root()
    if root not in target.parents and target != root:
        raise HTTPException(403, "Path outside workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Artifact not found")
    target.unlink()
    return {"ok": True, "deleted": str(target)}


@router.get("/{project}")
@trace
async def list_artifacts(project: str) -> list[dict[str, Any]]:
    from core.app_config import OUTPUTS_DIR
    from core.project_store import ProjectPaths, _safe_name

    out: list[dict[str, Any]] = []

    paths = ProjectPaths.for_name(project)
    gen = paths.generated_dir
    if gen.exists():
        for f in gen.iterdir():
            if f.is_file() and not f.name.startswith("."):
                out.append(_describe(f, "testcases"))

    packets = OUTPUTS_DIR / _safe_name(project) / "packets"
    if packets.exists():
        for f in packets.rglob("*.pdf"):
            if f.is_file() and not f.name.startswith("_"):
                out.append(_describe(f, "packets"))

    out.sort(key=lambda r: r["modified"], reverse=True)
    return out
