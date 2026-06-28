"""Update endpoints.

Surfaces the background self-updater to the UI so the user can see the current
version and trigger a patch on demand ("Check for updates" / Settings).
"""

from __future__ import annotations

from fastapi import APIRouter

from agent import updater

router = APIRouter()


@router.get("/status")
async def update_status() -> dict:
    """Current vs. available version. Non-destructive."""
    return updater.check_for_update()


@router.post("/apply")
async def update_apply() -> dict:
    """Download + apply the latest patch, then restart the agent if anything
    changed. Returns before the restart so the caller can poll for reconnect."""
    return updater.apply_update_now()
