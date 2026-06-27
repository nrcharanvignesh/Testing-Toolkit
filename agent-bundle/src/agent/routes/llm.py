"""LLM proxy endpoints — calls Anthropic API using locally-stored key."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class CompleteRequest(BaseModel):
    model: str | None = None
    system: str = ""
    user: str
    max_tokens: int = 4096
    temperature: float = 0.0
    thinking_budget: int | None = None
    stop_sequences: list[str] | None = None


class CompleteResponse(BaseModel):
    text: str
    stop_reason: str
    input_tokens: int
    output_tokens: int


@router.post("/complete", response_model=CompleteResponse)
async def complete(req: CompleteRequest) -> CompleteResponse:
    """Single-shot LLM completion via the locally-stored API key."""
    from core.settings_store import get_setting, load_api_key, KEY_BASE_URL, KEY_MODEL
    from core.anthropic_client import AnthropicClient

    api_key = load_api_key()
    if not api_key:
        raise HTTPException(400, "No API key configured")

    base_url = get_setting(KEY_BASE_URL)
    model = req.model or get_setting(KEY_MODEL)

    client = AnthropicClient(api_key=api_key, base_url=base_url)
    try:
        result = await client.complete_async(
            model=model,
            system=req.system,
            user=req.user,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            thinking_budget=req.thinking_budget,
            stop_sequences=req.stop_sequences,
        )
    except Exception as e:
        raise HTTPException(502, f"LLM API error: {e!r}")

    return CompleteResponse(
        text=result.text,
        stop_reason=result.stop_reason,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )


@router.get("/models")
async def list_models() -> list[str]:
    """List available/working models from the configured API."""
    from core.settings_store import get_setting, load_api_key, KEY_BASE_URL
    from core.anthropic_client import AnthropicClient

    api_key = load_api_key()
    if not api_key:
        raise HTTPException(400, "No API key configured")

    base_url = get_setting(KEY_BASE_URL)
    client = AnthropicClient(api_key=api_key, base_url=base_url)
    try:
        models = await client.list_working_models_async()
    except Exception as e:
        raise HTTPException(502, f"Failed to list models: {e!r}")
    return models
