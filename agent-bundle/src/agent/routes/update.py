"""Update endpoints.

Surfaces the background self-updater to the UI so the user can see the current
version and trigger a patch on demand ("Check for updates" / Settings).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from agent import updater

router = APIRouter()


@router.get("/status")
async def update_status() -> dict:
    """Current vs. available version. Non-destructive."""
    return updater.check_for_update()


@router.post("/config")
async def update_config(payload: dict[str, Any] = Body(default={})) -> dict:
    """Enable/repair auto-update at runtime (no reinstall required).

    The web app forwards a read-only update token (which it can read because it
    is authenticated to the SSO-protected deployment) so a token-less install
    can begin self-updating immediately. Returns the refreshed update status.
    """
    return updater.configure(
        str(payload.get("token", "")),
        repo=str(payload.get("repo", "")),
        ref=str(payload.get("ref", "")),
        manifest_url=str(payload.get("manifest_url", "")),
    )


@router.post("/apply")
async def update_apply() -> dict:
    """Download + apply the latest patch, then restart the agent if anything
    changed. Returns before the restart so the caller can poll for reconnect."""
    return updater.apply_update_now()
