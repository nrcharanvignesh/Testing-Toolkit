"""Bulk-defect endpoints.

Parse defect documents (Word / Excel / PowerPoint / PDF) into structured
records, then create Bug work items in Azure DevOps.

    POST /defects/parse     multipart upload -> parsed defects (+ optional LLM)
    POST /defects/upload     create Bugs in ADO                  -> {job_id}
    POST /defects/excel     download a reviewer .xlsx of defects
    GET  /defects/job/{id}   poll (shared with /jobs)
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent.jobs import JOBS, Job

router = APIRouter()


def _require_pat_org() -> tuple[str, str]:
    from core.settings_store import get_setting, load_pat_value, KEY_ORG

    pat = load_pat_value()
    org = get_setting(KEY_ORG)
    if not pat or not org:
        raise HTTPException(400, "PAT or organization not configured")
    return pat, org


def _defect_to_dict(d: Any) -> dict[str, Any]:
    return {
        "parent_id": getattr(d, "parent_id", 0) or 0,
        "title": getattr(d, "title", "") or "",
        "description": getattr(d, "description", "") or "",
        "repro_steps": getattr(d, "repro_steps", "") or "",
        "severity": getattr(d, "severity", "") or "Medium",
        "expected_result": getattr(d, "expected_result", "") or "",
        "actual_result": getattr(d, "actual_result", "") or "",
        "images": [
            {
                "filename": img.filename,
                "data_b64": img.data_b64,
                "mime_type": img.mime_type,
            }
            for img in (getattr(d, "images", None) or [])
        ],
    }


# ---------------------------------------------------------------------
# Parse uploaded documents
# ---------------------------------------------------------------------
@router.post("/parse")
async def parse_documents(
    files: list[UploadFile] = File(...),
    use_llm: bool = Form(False),
) -> dict[str, Any]:
    """Extract defects from one or more uploaded documents. When programmatic
    parsing finds nothing and ``use_llm`` is set, falls back to the LLM."""
    from defects.parser import parse_defect_documents_async

    if not files:
        raise HTTPException(400, "No files uploaded")

    logs: list[str] = []

    def _log(msg: str) -> None:
        logs.append(msg)

    client = None
    model = ""
    if use_llm:
        from core.settings_store import build_llm_client, model_pair

        client = build_llm_client()
        if client is not None:
            primary, fast = model_pair()
            model = fast or primary

    tmpdir = Path(tempfile.mkdtemp(prefix="tt_defects_"))
    saved: list[Path] = []
    try:
        for f in files:
            dest = tmpdir / (f.filename or "upload.bin")
            dest.write_bytes(await f.read())
            saved.append(dest)

        defects = await parse_defect_documents_async(
            saved, on_log=_log, llm_client=client, llm_model=model,
        )
        return {
            "defects": [_defect_to_dict(d) for d in defects],
            "logs": logs,
            "n_defects": len(defects),
        }
    finally:
        # Best-effort cleanup of the temp upload dir.
        for p in saved:
            try:
                p.unlink()
            except OSError:
                pass
        try:
            tmpdir.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------
# Reviewed defect model (shared by upload + excel)
# ---------------------------------------------------------------------
class ReviewedDefectModel(BaseModel):
    parent_id: int = 0
    title: str = ""
    description: str = ""
    repro_steps: str = ""
    severity: str = "Medium"
    expected_result: str = ""
    actual_result: str = ""
    skip: bool = False


def _to_namespace(d: ReviewedDefectModel) -> SimpleNamespace:
    """ado_uploader reads defects via attribute access; build a lightweight
    object with the fields it needs (images are not used on upload)."""
    return SimpleNamespace(
        parent_id=int(d.parent_id or 0),
        title=d.title,
        description=d.description,
        repro_steps=d.repro_steps,
        severity=d.severity or "Medium",
        expected_result=d.expected_result,
        actual_result=d.actual_result,
        images=[],
    )


# ---------------------------------------------------------------------
# Upload Bugs to ADO
# ---------------------------------------------------------------------
class UploadRequest(BaseModel):
    project: str
    defects: list[ReviewedDefectModel]


async def _run_upload(
    job: Job, project: str, defects: list[SimpleNamespace], pat: str, org: str
) -> None:
    from core.settings_store import build_runtime_config
    from defects.ado_uploader import upload_defects_async

    try:
        cfg = build_runtime_config()
        cfg.project = project
        cfg.pat = pat
        cfg.organization = org
        result = await upload_defects_async(
            defects, org, project, pat, cfg,
            on_log=job.log,
            on_progress=lambda s, c, t: job.set_progress(s, c, t),
        )
        job.finish({
            "n_ok": result.n_ok,
            "n_failed": result.n_failed,
            "created": [
                {
                    "title": r.title,
                    "parent_id": r.parent_id,
                    "created_id": r.created_id,
                    "created_url": r.created_url,
                    "ok": r.ok,
                    "error": r.error,
                }
                for r in result.results
            ],
        })
    except Exception as e:  # noqa: BLE001
        job.fail(f"{type(e).__name__}: {e}")
        job.log(f"[ERROR] {job.error}")


@router.post("/upload")
async def upload_defects(req: UploadRequest) -> dict[str, str]:
    pat, org = _require_pat_org()
    kept = [_to_namespace(d) for d in req.defects if not d.skip and d.title.strip()]
    if not kept:
        raise HTTPException(400, "No defects to upload (all skipped or empty)")
    job = JOBS.create("defects_upload")
    job.log(f"[INFO] Uploading {len(kept)} defect(s) to Azure DevOps...")
    asyncio.create_task(_run_upload(job, req.project, kept, pat, org))
    return {"job_id": job.id}


# ---------------------------------------------------------------------
# Reviewer Excel download
# ---------------------------------------------------------------------
class ExcelRequest(BaseModel):
    defects: list[ReviewedDefectModel]


@router.post("/excel")
async def defects_excel(req: ExcelRequest) -> FileResponse:
    from datetime import datetime

    from defects.parser import ParsedDefect
    from defects.review_excel import defects_to_xlsx

    parsed = [
        ParsedDefect(
            parent_id=int(d.parent_id or 0),
            title=d.title,
            description=d.description,
            repro_steps=d.repro_steps,
            severity=d.severity or "Medium",
            expected_result=d.expected_result,
            actual_result=d.actual_result,
        )
        for d in req.defects
        if not d.skip
    ]
    if not parsed:
        raise HTTPException(400, "No defects to export")
    out = Path(tempfile.gettempdir()) / (
        f"defects_review_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )
    await asyncio.to_thread(defects_to_xlsx, parsed, out)
    return FileResponse(
        out,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        filename=out.name,
    )
