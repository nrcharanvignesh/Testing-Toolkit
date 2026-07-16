"""Tooling endpoints — PDF packaging of work items.

Mirrors the desktop "Package PDFs" action, which runs the extract -> convert ->
merge pipeline (``core.orchestrator.run_pipeline``) and writes one combined PDF
packet per work item into the project's outputs/packets folder. Because a run
can take a while (it downloads every attachment and converts Office/Visio files
to PDF) it is exposed as a background job the browser polls via /jobs.

    POST /tools/package   start a packaging run   -> {job_id}

Poll progress + logs with GET /jobs/{job_id}; the final result carries the
output directory and the per-item rows. Use /artifacts to download the packets.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.jobs import JOBS, Job
from core.trace import trace

router = APIRouter()


def _require_pat_org() -> tuple[str, str]:
    from core.settings_store import get_setting, load_pat_value, KEY_ORG

    pat = load_pat_value()
    org = get_setting(KEY_ORG)
    if not pat or not org:
        raise HTTPException(400, "PAT or organization not configured")
    return pat, org


class PackageRequest(BaseModel):
    project: str
    wi_ids: list[int]
    paper_size: str = "A4"


async def _run_package(
    job: Job, req: PackageRequest, pat: str, org: str
) -> None:
    from core.app_config import OUTPUTS_DIR
    from core.app_logging import stream_agent_logs
    from core.orchestrator import run_pipeline
    from core.project_store import _safe_name
    from core.settings_store import build_runtime_config

    _log_bridge = stream_agent_logs(job.log)
    _log_bridge.__enter__()
    try:
        out_dir = OUTPUTS_DIR / _safe_name(req.project) / "packets"
        out_dir.mkdir(parents=True, exist_ok=True)
        work_dir = (out_dir / "_work").resolve()
        work_dir.mkdir(parents=True, exist_ok=True)

        cfg = build_runtime_config()
        cfg.project = req.project
        cfg.pat = pat
        cfg.organization = org
        cfg.work_item_ids = list(req.wi_ids)
        cfg.output_dir = out_dir
        cfg.work_dir = work_dir
        if hasattr(cfg, "paper_size"):
            cfg.paper_size = req.paper_size

        job.log(
            f"[INFO] Packaging {len(req.wi_ids)} work item(s) into {out_dir}"
        )
        job.set_progress("package", 0, len(req.wi_ids))

        result = await run_pipeline(
            cfg,
            on_progress=lambda stage, c, t: job.set_progress(stage, c, t),
        )

        rows = list(getattr(result, "package_rows", []) or [])
        job.finish({
            "output_dir": str(out_dir),
            "n_package_ok": getattr(result, "n_package_ok", 0),
            "n_extract_ok": getattr(result, "n_extract_ok", 0),
            "manifest_path": (
                str(result.manifest_path)
                if getattr(result, "manifest_path", None)
                else ""
            ),
            "rows": rows,
        })
        job.log(
            f"[SUCCESS] Packaged {getattr(result, 'n_package_ok', 0)} "
            f"work item(s)."
        )
    except Exception as e:  # noqa: BLE001
        job.fail(f"{type(e).__name__}: {e}")
        job.log(f"[ERROR] {job.error}")
    finally:
        _log_bridge.__exit__()


@router.get("/log")
@trace
async def recent_log(max_bytes: int = 8_000_000) -> dict[str, Any]:
    """Return the tail of the rotating log file plus its on-disk path.

    Mirrors the desktop Help -> 'View recent log...' dialog. Defaults to a
    large (~8 MB) tail so the full recent history is available for a bug
    report, not just a small window.
    """
    from core.app_logging import init_logging, log_path, log_dir, tail_log

    # Safety net: if the process was launched in a way that skipped startup
    # logging init, configure it now (idempotent) so the dialog points at a
    # real file instead of "(no log file configured)".
    try:
        if log_path() is None:
            init_logging()
    except Exception:
        pass

    try:
        text = tail_log(max_bytes)
    except Exception as e:  # noqa: BLE001
        text = f"(could not read log: {e!r})"
    p = log_path()
    d = log_dir()
    return {
        "text": text,
        "path": str(p) if p else "",
        "dir": str(d) if d else "",
    }


@router.post("/package")
@trace
async def package_pdfs(req: PackageRequest) -> dict[str, str]:
    if not req.wi_ids:
        raise HTTPException(400, "No work items selected")
    pat, org = _require_pat_org()
    job = JOBS.create("package")
    job.log("[INFO] Starting PDF packaging...")
    asyncio.create_task(_run_package(job, req, pat, org))
    return {"job_id": job.id}
