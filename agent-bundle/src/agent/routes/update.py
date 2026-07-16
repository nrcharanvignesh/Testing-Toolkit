"""Detection-only agent update endpoint.

The running agent may compare its version with the published manifest, but it
never downloads, applies, or restarts itself. A newer version must be installed
through the normal installer so replacement and rollback behavior stay safe.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent import updater
from core.trace import trace

router = APIRouter()


@router.get("/status")
@trace
async def update_status() -> dict:
    """Return current vs. available version without mutating the installation."""
    return updater.check_for_update()
