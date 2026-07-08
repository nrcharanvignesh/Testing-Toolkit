"""
chat.py
Custom Generate agentic chat endpoint. Streams an LLM response over
Server-Sent Events, running a tool_use loop (ADO CRUD) between rounds.

Endpoint:
    POST /chat/stream  -> text/event-stream

SSE event protocol (one JSON object per `data:` line):
    {"type": "text",  "text": "<delta>"}      incremental assistant text
    {"type": "tool",  "name": "...", "phase": "start"|"done"}
    {"type": "error", "message": "..."}
    {"type": "done",  "stop_reason": "..."}

Optional KB grounding: when `use_kb` is set and the project has an index,
the top retrieved chunks are prepended to the system prompt so answers are
grounded in the project's knowledge base.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()

_MAX_TOOL_ROUNDS: int = 10
_KB_TOP_K: int = 6


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatImage(BaseModel):
    """A pasted/attached image for a multi-modal user turn (desktop parity:
    chat_dialog.py pending images). `data_b64` is the raw base64 (no data-URL
    prefix); `media_type` is e.g. "image/png" / "image/jpeg"."""

    media_type: str = "image/png"
    data_b64: str


class ChatRequest(BaseModel):
    project: str
    messages: list[ChatMessage]
    use_kb: bool = True
    use_tools: bool = True
    model: str | None = None
    # Extra reference text (extracted from user attachments) folded into the
    # latest user turn by the caller; kept separate so we can log its size.
    attachment_text: str = ""
    # Images attached to the latest user turn (multi-modal). Sent to the LLM as
    # Anthropic image content blocks folded into the last user message.
    images: list[ChatImage] = []


def _base_system_prompt(project: str) -> str:
    return (
        "You are the Custom Generate assistant for the Testing Toolkit. "
        f"You are helping with the Azure DevOps project '{project}'. "
        "You can search and read work items, add comments, update fields, "
        "and create work items using the provided tools. Be concise and "
        "practical. When you use a tool, briefly explain what you did."
    )


def _kb_context(project: str, query: str) -> str:
    """Retrieve the top KB chunks for the query. Best-effort: returns an
    empty string if the project has no index or retrieval fails."""
    try:
        from core.app_config import PROJECTS_DIR
        from kb.retrieval import HybridRetriever

        project_dir = PROJECTS_DIR / project
        if not project_dir.exists():
            return ""
        retriever = HybridRetriever(project_dir)
        if not retriever.is_available():
            return ""
        hits = retriever.retrieve(query, _KB_TOP_K)
        blocks: list[str] = []
        for h in hits or []:
            src = getattr(h, "title", "") or getattr(h, "doc", "")
            text = getattr(h, "text", "") or ""
            if text.strip():
                blocks.append(f"[{src}]\n{text.strip()}")
        if not blocks:
            return ""
        return (
            "\n\n=== PROJECT KNOWLEDGE BASE (use to ground your answer) ===\n"
            + "\n\n---\n\n".join(blocks)
            + "\n=== END KNOWLEDGE BASE ===\n"
        )
    except Exception:
        return ""


def _build_tool_context(project: str):
    """Construct a ToolContext with ADO credentials, or None when ADO is
    not configured (chat still works, just without tools)."""
    try:
        from core.settings_store import (
            get_setting,
            load_pat_value,
            KEY_ORG,
            build_runtime_config,
        )
        from core.chat_tools import ToolContext

        pat = load_pat_value()
        org = get_setting(KEY_ORG)
        if not pat or not org:
            return None
        cfg = build_runtime_config()
        cfg.pat = pat
        cfg.organization = org
        cfg.project = project
        return ToolContext(ado_org=org, ado_project=project, ado_cfg=cfg)
    except Exception:
        return None


@router.post("/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Stream an agentic chat completion as Server-Sent Events."""
    from core.settings_store import (
        get_setting,
        load_api_key,
        KEY_BASE_URL,
        KEY_MODEL,
        build_runtime_config,
    )
    from core.anthropic_client import AnthropicClient
    from core.chat_tools import execute_tool, get_tool_definitions
    from core.guardrails import check_input_guardrail

    api_key = load_api_key()
    if not api_key:
        raise HTTPException(400, "No API key configured")
    if not req.messages:
        raise HTTPException(400, "No messages provided")

    base_url = get_setting(KEY_BASE_URL)
    model = req.model or get_setting(KEY_MODEL)
    cfg = build_runtime_config()
    client = AnthropicClient(
        api_key=api_key, base_url=base_url, ssl_verify=cfg.build_ssl()
    )

    # Input guardrail on the latest user turn (cheap, deterministic).
    last_user = next(
        (m.content for m in reversed(req.messages) if m.role == "user"), ""
    )
    refusal = check_input_guardrail(last_user)

    # System prompt = base + optional KB grounding.
    system = _base_system_prompt(req.project)
    if req.use_kb:
        system += _kb_context(req.project, last_user)

    tool_ctx = _build_tool_context(req.project) if req.use_tools else None
    tools = get_tool_definitions(tool_ctx) if tool_ctx else []

    # API message list. Attachment text is folded into the last user turn.
    api_messages: list[dict[str, Any]] = [
        {"role": m.role, "content": m.content} for m in req.messages
    ]
    if req.attachment_text.strip() and api_messages:
        for i in range(len(api_messages) - 1, -1, -1):
            if api_messages[i]["role"] == "user":
                api_messages[i]["content"] = (
                    str(api_messages[i]["content"])
                    + "\n\n=== ATTACHED REFERENCE FILES ===\n"
                    + req.attachment_text.strip()
                )
                break

    # Fold attached images into the last user turn as Anthropic image content
    # blocks (desktop chat parity). Text stays as a leading text block so the
    # model sees the prompt alongside the screenshots.
    if req.images and api_messages:
        for i in range(len(api_messages) - 1, -1, -1):
            if api_messages[i]["role"] == "user":
                text_part = str(api_messages[i]["content"] or "")
                blocks: list[dict[str, Any]] = []
                if text_part.strip():
                    blocks.append({"type": "text", "text": text_part})
                for img in req.images:
                    data = (img.data_b64 or "").strip()
                    if not data:
                        continue
                    # Tolerate a data-URL prefix if the client sent one.
                    if "," in data and data.lower().startswith("data:"):
                        data = data.split(",", 1)[1]
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img.media_type or "image/png",
                                "data": data,
                            },
                        }
                    )
                if blocks:
                    api_messages[i]["content"] = blocks
                break

    async def _sse(obj: dict[str, Any]) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=True)}\n\n"

    async def _gen():
        # Short-circuit off-topic requests with the canned refusal.
        if refusal:
            yield await _sse({"type": "text", "text": refusal})
            yield await _sse({"type": "done", "stop_reason": "guardrail"})
            return

        try:
            loop = asyncio.get_running_loop()
            _DONE = object()

            for _round in range(_MAX_TOOL_ROUNDS):
                # Bridge the per-chunk callback to this async generator via a
                # queue so text streams to the client in real time instead of
                # buffering until the whole round completes. call_soon_threadsafe
                # keeps it correct whether the client streams the response on
                # the loop or in a worker thread.
                queue: asyncio.Queue[Any] = asyncio.Queue()

                def _on_delta(chunk: str) -> None:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)

                async def _run():
                    try:
                        return await client.stream_message_with_tools_async(
                            model=model,
                            messages=api_messages,
                            system=system,
                            tools=tools or None,
                            on_text_delta=_on_delta,
                        )
                    finally:
                        loop.call_soon_threadsafe(queue.put_nowait, _DONE)

                task = asyncio.ensure_future(_run())
                # Forward text chunks as they arrive.
                while True:
                    item = await queue.get()
                    if item is _DONE:
                        break
                    yield await _sse({"type": "text", "text": item})
                result = await task  # propagate result / re-raise errors

                if not result.has_tool_use:
                    yield await _sse(
                        {"type": "done", "stop_reason": result.stop_reason}
                    )
                    return

                # Append the assistant turn (with tool_use blocks) verbatim.
                api_messages.append(
                    {"role": "assistant", "content": result.content}
                )
                # Execute each tool call, collect tool_result blocks.
                tool_results: list[dict[str, Any]] = []
                for call in result.tool_calls:
                    name = call.get("name", "")
                    yield await _sse(
                        {"type": "tool", "name": name, "phase": "start"}
                    )
                    try:
                        out = execute_tool(
                            name, call.get("input", {}) or {}, tool_ctx
                        )
                    except Exception as e:  # noqa: BLE001
                        out = json.dumps({"error": str(e)})
                    yield await _sse(
                        {"type": "tool", "name": name, "phase": "done"}
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.get("id", ""),
                            "content": out,
                        }
                    )
                api_messages.append(
                    {"role": "user", "content": tool_results}
                )

            # Ran out of tool rounds.
            yield await _sse(
                {"type": "done", "stop_reason": "max_tool_rounds"}
            )
        except Exception as e:  # noqa: BLE001
            yield await _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
