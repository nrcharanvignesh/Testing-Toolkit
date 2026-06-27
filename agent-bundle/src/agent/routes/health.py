"""Health and version endpoints."""

from __future__ import annotations

import os
import platform

from fastapi import APIRouter

from agent.model_loader import models_loaded
from agent.version import AGENT_VERSION

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    try:
        user = os.getlogin()
    except OSError:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    return {
        "status": "ok",
        "version": AGENT_VERSION,
        "user": user,
        "machine": platform.node(),
        "models_loaded": models_loaded(),
    }


@router.get("/version")
async def version() -> dict:
    import sys
    return {
        "version": AGENT_VERSION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
