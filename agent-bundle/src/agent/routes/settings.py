"""Settings endpoints — read/write local settings and secrets."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class SettingsResponse(BaseModel):
    configured: bool
    has_api_key: bool
    organization: str
    model: str
    fast_model: str
    fallback_model: str
    base_url: str
    project_prefix: str


class SaveSettingsRequest(BaseModel):
    organization: str | None = None
    base_url: str | None = None
    model: str | None = None
    fast_model: str | None = None
    fallback_model: str | None = None
    project_prefix: str | None = None
    api_key: str | None = None
    pat: str | None = None


@router.get("", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    from core.settings_store import (
        get_setting,
        has_api_key,
        is_configured,
        KEY_BASE_URL,
        KEY_FALLBACK_MODEL,
        KEY_FAST_MODEL,
        KEY_MODEL,
        KEY_ORG,
        KEY_PREFIX,
    )
    return SettingsResponse(
        configured=is_configured(),
        has_api_key=has_api_key(),
        organization=get_setting(KEY_ORG),
        model=get_setting(KEY_MODEL),
        fast_model=get_setting(KEY_FAST_MODEL),
        fallback_model=get_setting(KEY_FALLBACK_MODEL),
        base_url=get_setting(KEY_BASE_URL),
        project_prefix=get_setting(KEY_PREFIX),
    )


@router.post("")
async def save_settings(req: SaveSettingsRequest) -> dict:
    from core.settings_store import (
        save_api_key,
        save_pat_value,
        save_settings as save_plain,
        KEY_BASE_URL,
        KEY_FALLBACK_MODEL,
        KEY_FAST_MODEL,
        KEY_MODEL,
        KEY_ORG,
        KEY_PREFIX,
    )

    plain: dict[str, str] = {}
    if req.organization is not None:
        plain[KEY_ORG] = req.organization
    if req.base_url is not None:
        plain[KEY_BASE_URL] = req.base_url
    if req.model is not None:
        plain[KEY_MODEL] = req.model
    if req.fast_model is not None:
        plain[KEY_FAST_MODEL] = req.fast_model
    if req.fallback_model is not None:
        plain[KEY_FALLBACK_MODEL] = req.fallback_model
    if req.project_prefix is not None:
        plain[KEY_PREFIX] = req.project_prefix

    if plain:
        save_plain(plain)

    if req.api_key:
        if not save_api_key(req.api_key):
            raise HTTPException(500, "Failed to save API key")

    if req.pat:
        if not save_pat_value(req.pat):
            raise HTTPException(500, "Failed to save PAT")

    return {"ok": True}
