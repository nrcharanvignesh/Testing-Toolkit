from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kb.context_summary import (
    ContextItem,
    ProjectContext,
    build_context_incremental_async,
    load_context_summary,
    merge_context_maps,
    save_context_summary,
)


def _index(documents: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(chunks=[
        SimpleNamespace(doc=name, source=name, title=name, context="", text=text)
        for name, text in documents.items()
    ])


def _response(source_name: str) -> str:
    payload = {
        "actors": [{"name": "Operator", "description": f"Uses {source_name}"}],
        "entities": [],
        "workflows": [{"name": source_name, "description": f"Flow from {source_name}"}],
        "integrations": [],
        "business_rules": [],
        "screens": [],
        "test_data_needs": [],
        "edge_cases": [],
        "non_functional": [],
        "dependencies": [],
        "glossary": [],
    }
    return json.dumps(payload)


class _Client:
    def __init__(self, failing_source: str = "") -> None:
        self.calls: list[str] = []
        self.failing_source = failing_source

    async def complete_async(self, **kwargs):
        prompt = str(kwargs["user"])
        source = prompt.split("Source document: ", 1)[1].splitlines()[0]
        self.calls.append(source)
        if source == self.failing_source:
            raise TimeoutError("gateway timeout")
        return SimpleNamespace(text=_response(source))


@pytest.mark.asyncio
async def test_incremental_context_maps_every_document_and_reuses_cache(tmp_path):
    client = _Client()
    maps_dir = tmp_path / "maps"
    first = await build_context_incremental_async(
        _index({"a.md": "A requirements", "b.md": "B requirements"}),
        client, "model", maps_dir, "fp-1",
    )
    assert first.status == "complete"
    assert first.mapped_documents == first.total_documents == 2
    assert set(client.calls) == {"a.md", "b.md"}
    operator = first.actors[0]
    assert operator.sources == ["a.md", "b.md"]

    client.calls.clear()
    second = await build_context_incremental_async(
        _index({"a.md": "A requirements", "b.md": "B requirements"}),
        client, "model", maps_dir, "fp-1",
    )
    assert second.status == "complete"
    assert client.calls == []


@pytest.mark.asyncio
async def test_incremental_context_rebuilds_changed_and_removes_deleted_maps(tmp_path):
    client = _Client()
    maps_dir = tmp_path / "maps"
    await build_context_incremental_async(
        _index({"a.md": "A1", "b.md": "B1"}), client, "model", maps_dir, "fp-1",
    )
    assert len(list(maps_dir.glob("*.json"))) == 2

    client.calls.clear()
    result = await build_context_incremental_async(
        _index({"a.md": "A2"}), client, "model", maps_dir, "fp-2",
    )
    assert client.calls == ["a.md"]
    assert result.total_documents == 1
    assert len(list(maps_dir.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_incremental_context_reports_partial_failure_and_resumes(tmp_path):
    maps_dir = tmp_path / "maps"
    client = _Client(failing_source="bad.md")
    result = await build_context_incremental_async(
        _index({"good.md": "Good", "bad.md": "Bad"}),
        client, "model", maps_dir, "fp",
    )
    assert result.status == "partial"
    assert result.mapped_documents == 1
    assert result.total_documents == 2
    assert result.failed_documents == ["bad.md"]
    assert {item.name for item in result.workflows} == {"good.md"}
    assert "PARTIAL: 1/2 documents mapped" in result.to_prompt_section()

    client.calls.clear()
    client.failing_source = ""
    complete = await build_context_incremental_async(
        _index({"good.md": "Good", "bad.md": "Bad"}),
        client, "model", maps_dir, "fp",
    )
    assert complete.status == "complete"
    assert complete.failed_documents == []
    assert client.calls == ["bad.md"]


@pytest.mark.asyncio
async def test_invalid_context_json_retries_then_succeeds(tmp_path):
    class FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_async(self, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                return SimpleNamespace(text="not json")
            return SimpleNamespace(text=_response("retry.md"))

    client = FlakyClient()
    result = await build_context_incremental_async(
        _index({"retry.md": "Retry requirements"}),
        client, "model", tmp_path / "maps", "fp-retry",
    )
    assert client.calls == 3
    assert result.status == "complete"
    assert result.mapped_documents == 1


@pytest.mark.asyncio
async def test_context_progress_is_monotonic_completion_count(tmp_path):
    progress: list[tuple[str, int, int]] = []
    result = await build_context_incremental_async(
        _index({"a.md": "A requirements", "b.md": "B requirements"}),
        _Client(), "model", tmp_path / "maps", "fp-progress",
        on_progress=lambda phase, current, total: progress.append(
            (phase, current, total)
        ),
    )
    mapped = [current for phase, current, _total in progress if phase == "context-map"]
    assert mapped == [1, 2]
    assert result.status == "complete"


def test_merge_preserves_provenance_and_legacy_summary_loads(tmp_path):
    merged = merge_context_maps([
        ProjectContext(actors=[ContextItem("Admin", "Creates users", ["a.md"])]),
        ProjectContext(actors=[ContextItem("admin", "Approves access", ["b.md"])]),
    ], "fp")
    assert len(merged.actors) == 1
    assert merged.actors[0].sources == ["a.md", "b.md"]
    assert "Creates users" in merged.actors[0].description
    assert "Approves access" in merged.actors[0].description

    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({
        "actors": [{"name": "User", "description": "Uses the system"}],
        "kb_fingerprint": "old",
    }), encoding="utf-8")
    loaded = load_context_summary(legacy)
    assert loaded is not None
    assert loaded.actors[0].sources == []
    assert loaded.status == "complete"

    target = tmp_path / "summary.json"
    merged.enabled = False
    assert save_context_summary(target, merged)
    reloaded = load_context_summary(target)
    assert reloaded is not None
    assert reloaded.enabled is False
    assert reloaded.to_prompt_section() == ""
