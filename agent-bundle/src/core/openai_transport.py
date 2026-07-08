"""
openai_transport.py
Translation layer between the app's internal Anthropic-Messages-shaped
data (messages, tools, content blocks) and the OpenAI /chat/completions
wire format exposed by the GenAI LiteLLM gateway.

The rest of the app only ever speaks the Anthropic content-block shape
(text / tool_use / tool_result). When the LLM client is configured for the
OpenAI provider format, it translates requests on the way out and responses
on the way back using the pure functions here, so no consumer code changes.

Anthropic shapes handled:
  message:      {"role": "user"|"assistant", "content": str | [block, ...]}
  text block:   {"type": "text", "text": "..."}
  tool_use:     {"type": "tool_use", "id": "...", "name": "...", "input": {}}
  tool_result:  {"type": "tool_result", "tool_use_id": "...", "content": "..."}
  tool def:     {"name": "...", "description": "...", "input_schema": {...}}

OpenAI shapes produced:
  system/user/assistant/tool messages with tool_calls / tool_call_id
  tool def:     {"type": "function", "function": {name, description, parameters}}

ASCII-only; fully type-hinted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def to_openai_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Translate Anthropic tool defs to OpenAI function-tool defs."""
    if not tools:
        return None
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name", "")
        if not name:
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": t.get("description", ""),
                    # Anthropic calls it input_schema; OpenAI calls it
                    # parameters. Both are JSON Schema objects.
                    "parameters": t.get("input_schema")
                    or {"type": "object", "properties": {}},
                },
            }
        )
    return out or None


def _text_from_blocks(blocks: list[Any]) -> str:
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(str(b.get("text", "")))
    return "".join(parts)


def to_openai_messages(
    system: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Translate a system prompt + Anthropic messages into an OpenAI
    /chat/completions messages array.

    Rules:
      - system (if non-empty) becomes the leading {role:"system"} message.
      - str content passes through unchanged.
      - assistant content with tool_use blocks -> assistant message with
        text content plus tool_calls[].
      - user content with tool_result blocks -> one {role:"tool"} message
        per result (OpenAI requires a separate tool message each).
    """
    out: list[dict[str, Any]] = []
    if system and system.strip():
        out.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            out.append({"role": role, "content": str(content)})
            continue

        # Content is a list of blocks. Split by block type.
        tool_results = [
            b for b in content
            if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
        tool_uses = [
            b for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]

        if tool_results:
            # Each tool_result becomes its own tool message.
            for tr in tool_results:
                raw = tr.get("content", "")
                text = raw if isinstance(raw, str) else json.dumps(raw)
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": text,
                    }
                )
            continue

        if role == "assistant" and tool_uses:
            tool_calls = [
                {
                    "id": tu.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tu.get("name", ""),
                        "arguments": json.dumps(tu.get("input", {})),
                    },
                }
                for tu in tool_uses
            ]
            entry: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
            text = _text_from_blocks(content)
            # OpenAI allows content=null when only tool_calls are present.
            entry["content"] = text or None
            out.append(entry)
            continue

        # Any other list content: flatten text blocks.
        out.append({"role": role, "content": _text_from_blocks(content)})

    return out


def openai_message_to_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate a non-streamed OpenAI assistant message into Anthropic
    content blocks (text and/or tool_use)."""
    blocks: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        blocks.append({"type": "text", "text": text})
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        args_raw = fn.get("arguments", "") or ""
        try:
            args = json.loads(args_raw) if args_raw else {}
        except (json.JSONDecodeError, TypeError):
            args = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "input": args,
            }
        )
    return blocks


@dataclass
class OpenAIStreamAccumulator:
    """Assembles Anthropic-style content blocks from a streamed OpenAI
    /chat/completions response.

    Feed each parsed SSE delta object (choices[0].delta) via `add_delta`.
    Text chunks are returned so the caller can forward them live. At the end,
    call `finalize()` to obtain the ordered content blocks (text first if any,
    then tool_use blocks) plus the stop_reason.
    """

    _text: str = ""
    # index -> {"id","name","arguments"}
    _tools: dict[int, dict[str, str]] = field(default_factory=dict)
    _finish_reason: str = ""

    def add_delta(self, delta: dict[str, Any], finish_reason: str = "") -> str:
        """Ingest one streamed delta. Returns the text chunk (may be "")."""
        if finish_reason:
            self._finish_reason = finish_reason
        chunk = ""
        content = delta.get("content")
        if isinstance(content, str) and content:
            self._text += content
            chunk = content
        for tc in delta.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index", 0) or 0
            slot = self._tools.setdefault(
                idx, {"id": "", "name": "", "arguments": ""}
            )
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function", {}) or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]
        return chunk

    def finalize(self) -> tuple[list[dict[str, Any]], str]:
        blocks: list[dict[str, Any]] = []
        if self._text:
            blocks.append({"type": "text", "text": self._text})
        for idx in sorted(self._tools):
            slot = self._tools[idx]
            if not slot.get("name"):
                continue
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": slot.get("id", ""),
                    "name": slot["name"],
                    "input": args,
                }
            )
        # Normalize OpenAI finish_reason "tool_calls" to Anthropic "tool_use".
        stop = self._finish_reason
        if stop == "tool_calls":
            stop = "tool_use"
        elif stop == "stop":
            stop = "end_turn"
        return blocks, stop
