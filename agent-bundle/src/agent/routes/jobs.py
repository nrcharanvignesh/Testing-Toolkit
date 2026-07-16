"""Shared job polling endpoints used by generation, push, and defect upload.

    GET  /jobs/{job_id}?log_offset=N   poll state + new log lines + progress
    POST /jobs/{job_id}/stop           request cancellation (cooperative)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.jobs import JOBS
from core.trace import trace

router = APIRouter()


@router.get("/{job_id}")
@trace
async def get_job(job_id: str, log_offset: int = 0) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job.snapshot(log_offset=log_offset)


@router.post("/{job_id}/stop")
@trace
async def stop_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    job.request_stop()
    return {"ok": True, "state": job.state}
