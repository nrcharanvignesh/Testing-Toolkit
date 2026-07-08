"""Integration test for the chat SSE route: proves text streams in real time
(the batch-1/2 regression) rather than buffering the whole turn."""
from __future__ import annotations

import asyncio
import json
import time
import types

import pytest


@pytest.mark.asyncio
async def test_chat_streams_incrementally(monkeypatch):
    import agent.routes.chat as c
    import core.settings_store as ss
    import core.anthropic_client as ac
    import core.guardrails as gr

    class FakeResult:
        has_tool_use = False
        stop_reason = "end_turn"
        content: list = []
        tool_calls: list = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def stream_message_with_tools_async(
            self, model, messages, system, tools, on_text_delta
        ):
            for ch in ["Hel", "lo ", "wor", "ld"]:
                await asyncio.sleep(0.02)
                on_text_delta(ch)
            return FakeResult()

    monkeypatch.setattr(ac, "AnthropicClient", FakeClient)
    monkeypatch.setattr(ss, "load_api_key", lambda: "k")
    monkeypatch.setattr(ss, "get_setting", lambda k: "m")
    monkeypatch.setattr(
        ss, "build_runtime_config",
        lambda: types.SimpleNamespace(build_ssl=lambda: None))
    monkeypatch.setattr(c, "_base_system_prompt", lambda p: "SYS")
    monkeypatch.setattr(gr, "check_input_guardrail", lambda t: None)

    req = c.ChatRequest(
        messages=[c.ChatMessage(role="user", content="hi")],
        project="P", use_kb=False, use_tools=False)

    resp = await c.chat_stream(req)
    texts: list[str] = []
    arrivals: list[float] = []
    t0 = time.monotonic()
    async for chunk in resp.body_iterator:
        for line in chunk.split("\n"):
            if line.startswith("data: "):
                evt = json.loads(line[6:])
                if evt.get("type") == "text":
                    texts.append(evt["text"])
                    arrivals.append(time.monotonic() - t0)

    assert texts == ["Hel", "lo ", "wor", "ld"]
    # streaming: chunks must arrive spread over time, not all at the end
    assert arrivals[-1] - arrivals[0] >= 0.04, (
        f"not streaming, arrivals={arrivals}")
