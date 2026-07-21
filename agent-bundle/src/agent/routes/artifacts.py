"""Artifacts endpoints — browse + download generated outputs.

Backs the web "Outputs" tab (the desktop artifacts browser). It enumerates the
two kinds of generated files the toolkit produces for a project:

  * "testcases" — reviewer Excel workbooks in projects/<p>/generated/
  * "packets"   — combined PDF packets in outputs/<p>/packets/
  * "exports"   — Excel exports from the web UI saved for offline access

    GET  /artifacts/{project}        list artifacts (newest first)
    GET  /artifacts/download?path=   stream a single artifact
    POST /artifacts/upload           save a file to the exports folder

Downloads are constrained to the toolkit workspace so a crafted ?path= cannot
escape and read arbitrary files off the user's machine.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse

from core.trace import trace

router = APIRouter()


def _workspace_root() -> Path:
    from core.app_config import WORKSPACE

    return Path(WORKSPACE).resolve()


def _exports_root() -> Path:
    from core.app_config import EXPORTS_DIR

    return Path(EXPORTS_DIR).resolve()


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
    exports = _exports_root()
    # Constrain to workspace or exports dir (path traversal guard).
    in_workspace = root in target.parents or target == root
    in_exports = exports in target.parents or target == exports
    if not (in_workspace or in_exports):
        raise HTTPException(403, "Path outside workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Artifact not found")
    media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media, filename=target.name)


@router.delete("/delete")
@trace
async def delete_artifact(path: str = Query(...)) -> dict[str, Any]:
    """Delete a single generated artifact (desktop 'Delete' button).

    Constrained to the toolkit workspace or exports dir so a crafted ?path=
    cannot delete arbitrary files off the user's machine.
    """
    target = Path(path).resolve()
    root = _workspace_root()
    exports = _exports_root()
    in_workspace = root in target.parents or target == root
    in_exports = exports in target.parents or target == exports
    if not (in_workspace or in_exports):
        raise HTTPException(403, "Path outside workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Artifact not found")
    target.unlink()
    return {"ok": True, "deleted": str(target)}


@router.post("/upload")
@trace
async def upload_artifact(
    file: UploadFile = File(...),
    project: str = Form(""),
) -> dict[str, Any]:
    """Save an uploaded file (Excel export) to Downloads/Testing_Toolkit/."""
    from core.app_config import EXPORTS_DIR

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    filename = file.filename or "export.xlsx"
    safe_filename = Path(filename).name.replace("/", "_").replace("\\", "_")
    dest = EXPORTS_DIR / safe_filename

    content = await file.read()
    dest.write_bytes(content)
    return {"ok": True, "path": str(dest), "size": len(content)}


_ALLOWED_EXTENSIONS: set[str] = {".xlsx", ".pdf", ".mkv"}


@router.get("/{project}")
@trace
async def list_artifacts(project: str) -> list[dict[str, Any]]:
    from core.app_config import EXPORTS_DIR, OUTPUTS_DIR

    out: list[dict[str, Any]] = []

    # User-facing outputs under EXPORTS_DIR (~/Downloads/Testing_Toolkit)
    if EXPORTS_DIR.exists():
        for f in EXPORTS_DIR.rglob("*"):
            if not f.is_file() or f.name.startswith("."):
                continue
            if f.suffix.lower() not in _ALLOWED_EXTENSIONS:
                continue
            kind = (
                "recording" if f.suffix.lower() == ".mkv"
                else "report" if f.suffix.lower() == ".xlsx"
                else "e2e_report" if "e2e_report" in f.name.lower()
                else "packets"
            )
            out.append(_describe(f, kind))

    # Packaged PDFs under OUTPUTS_DIR (workspace/outputs/<project>/packets)
    from core.project_store import _safe_name
    safe_project = _safe_name(project)
    packets_dir = OUTPUTS_DIR / safe_project / "packets"
    if packets_dir.exists():
        for f in packets_dir.rglob("*"):
            if not f.is_file() or f.name.startswith("."):
                continue
            if f.suffix.lower() not in _ALLOWED_EXTENSIONS:
                continue
            out.append(_describe(f, "packets"))

    out.sort(key=lambda r: r["modified"], reverse=True)
    return out
