"""Internal LLM health endpoint using centrally managed configuration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from core.trace import trace

router = APIRouter()


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
@trace
async def complete(req: CompleteRequest) -> CompleteResponse:
    """Run a bounded internal completion for connectivity diagnostics."""
    from core.model_router import Task, route
    from core.settings_store import build_llm_client

    client = build_llm_client()
    if client is None:
        raise HTTPException(
            503,
            "The centrally managed AI service is not configured. "
            "Contact the Testing Toolkit administrator.",
        )
    model = route(Task.CHAT_STREAMING)
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
