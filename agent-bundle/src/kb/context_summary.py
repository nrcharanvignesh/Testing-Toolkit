"""Incremental, resumable project-context map/merge pipeline."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Final

LogFn = Callable[[str], None]
SCHEMA_VERSION: Final[int] = 2
_MAX_WINDOW_CHARS: Final[int] = 36000
_WINDOW_OVERLAP_CHARS: Final[int] = 2000
_MAX_TOKENS: Final[int] = 4096
_MAX_CONCURRENCY: Final[int] = 3
CATEGORIES: Final[tuple[str, ...]] = (
    "actors", "entities", "workflows", "integrations", "business_rules",
    "screens", "test_data_needs", "edge_cases", "non_functional",
    "dependencies", "glossary",
)
_SYSTEM: Final[str] = (
    "You are a Principal QA architect. Extract only facts supported by the supplied "
    "single-document window. Return valid JSON only, with exactly the requested 11 "
    "category keys. Each value is a list of objects with name and description. Be "
    "specific and preserve exact limits, states, field names, and conditions."
)


@dataclass(slots=True)
class ContextItem:
    name: str
    description: str
    sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectContext:
    actors: list[ContextItem] = field(default_factory=list)
    entities: list[ContextItem] = field(default_factory=list)
    workflows: list[ContextItem] = field(default_factory=list)
    integrations: list[ContextItem] = field(default_factory=list)
    business_rules: list[ContextItem] = field(default_factory=list)
    screens: list[ContextItem] = field(default_factory=list)
    test_data_needs: list[ContextItem] = field(default_factory=list)
    edge_cases: list[ContextItem] = field(default_factory=list)
    non_functional: list[ContextItem] = field(default_factory=list)
    dependencies: list[ContextItem] = field(default_factory=list)
    glossary: list[ContextItem] = field(default_factory=list)
    extracted_at: float = 0.0
    kb_fingerprint: str = ""
    status: str = "complete"
    mapped_documents: int = 0
    total_documents: int = 0
    failed_documents: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(getattr(self, category) for category in CATEGORIES)

    def to_prompt_section(self) -> str:
        if self.is_empty():
            return ""
        titles = {
            "actors": "ACTORS/ROLES", "entities": "BUSINESS ENTITIES",
            "workflows": "WORKFLOWS", "integrations": "INTEGRATION POINTS",
            "business_rules": "BUSINESS RULES", "screens": "SCREENS/PAGES",
            "test_data_needs": "TEST DATA NEEDS", "edge_cases": "EDGE CASES / NEGATIVE PATHS",
            "non_functional": "NON-FUNCTIONAL REQUIREMENTS",
            "dependencies": "DEPENDENCIES / DATA FLOWS", "glossary": "GLOSSARY / TERMINOLOGY",
        }
        parts = ["PROJECT CONTEXT SUMMARY", "=" * 40]
        if self.status == "partial":
            parts.append(
                f"PARTIAL: {self.mapped_documents}/{self.total_documents} "
                "documents mapped. Treat this context as incomplete."
            )
        for category in CATEGORIES:
            items = getattr(self, category)
            if not items:
                continue
            parts.append(f"\n{titles[category]}:")
            for item in items:
                source = f" [Sources: {', '.join(item.sources)}]" if item.sources else ""
                parts.append(f"  - {item.name}: {item.description}{source}")
        parts.append("")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectContext":
        def items(value: Any) -> list[ContextItem]:
            if not isinstance(value, list):
                return []
            return [ContextItem(
                name=str(item.get("name", "")).strip(),
                description=str(item.get("description", "")).strip(),
                sources=[str(source) for source in item.get("sources", []) if str(source).strip()],
            ) for item in value if isinstance(item, dict) and str(item.get("name", "")).strip()]
        kwargs = {category: items(data.get(category)) for category in CATEGORIES}
        return cls(
            **kwargs, extracted_at=float(data.get("extracted_at", 0.0) or 0.0),
            kb_fingerprint=str(data.get("kb_fingerprint", "")),
            status=str(data.get("status", "complete")),
            mapped_documents=int(data.get("mapped_documents", 0) or 0),
            total_documents=int(data.get("total_documents", 0) or 0),
            failed_documents=[str(item) for item in data.get("failed_documents", [])],
        )


def _parse_llm_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError("Context response must be an object")
    return data


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
    os.replace(tmp, path)


def _document_groups(kb_index: Any) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for chunk in getattr(kb_index, "chunks", []) or []:
        source = str(getattr(chunk, "doc", "") or getattr(chunk, "source", "") or "unknown")
        title = str(getattr(chunk, "title", "") or "")
        context = str(getattr(chunk, "context", "") or "")
        text = str(getattr(chunk, "text", "") or "")
        grouped.setdefault(source, []).append(f"## {title}\n{context}\n{text}".strip())
    return {source: "\n\n".join(parts) for source, parts in grouped.items()}


def _windows(text: str) -> list[str]:
    if len(text) <= _MAX_WINDOW_CHARS:
        return [text]
    step = _MAX_WINDOW_CHARS - _WINDOW_OVERLAP_CHARS
    return [text[start:start + _MAX_WINDOW_CHARS] for start in range(0, len(text), step)]


def _signature(source: str, text: str) -> str:
    return hashlib.sha256(f"{SCHEMA_VERSION}\0{source}\0{text}".encode("utf-8", errors="replace")).hexdigest()


def _normalize_name(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def merge_context_maps(maps: list[ProjectContext], fingerprint: str) -> ProjectContext:
    merged: dict[str, dict[str, ContextItem]] = {category: {} for category in CATEGORIES}
    for context in maps:
        for category in CATEGORIES:
            for item in getattr(context, category):
                key = _normalize_name(item.name)
                if not key:
                    continue
                existing = merged[category].get(key)
                if existing is None:
                    merged[category][key] = ContextItem(item.name, item.description, sorted(set(item.sources)))
                    continue
                if item.description and item.description not in existing.description:
                    existing.description = f"{existing.description} {item.description}".strip()
                existing.sources = sorted(set(existing.sources + item.sources))
    values = {category: sorted(items.values(), key=lambda item: item.name.lower()) for category, items in merged.items()}
    return ProjectContext(**values, extracted_at=time.time(), kb_fingerprint=fingerprint)


async def _extract_window(client: Any, model: str, source: str, text: str) -> ProjectContext:
    prompt = (
        f"Source document: {source}\nReturn exactly these keys: {', '.join(CATEGORIES)}.\n"
        f"<document>\n{text}\n</document>"
    )
    result = await client.complete_async(
        model=model, system=_SYSTEM, user=prompt,
        max_tokens=_MAX_TOKENS, temperature=0.0,
    )
    context = ProjectContext.from_dict(_parse_llm_response(str(getattr(result, "text", "") or "")))
    for category in CATEGORIES:
        for item in getattr(context, category):
            item.sources = [source]
    return context


async def build_context_incremental_async(
    kb_index: Any, client: Any, model: str, maps_dir: Path,
    kb_fingerprint: str = "", on_log: LogFn | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
    force: bool = False,
) -> ProjectContext:
    log = on_log or (lambda _message: None)
    documents = _document_groups(kb_index)
    if not documents:
        return ProjectContext(kb_fingerprint=kb_fingerprint, status="unavailable")
    maps_dir.mkdir(parents=True, exist_ok=True)
    wanted = {_signature(source, text): (source, text) for source, text in documents.items()}
    for stale in maps_dir.glob("*.json"):
        if stale.stem not in wanted:
            stale.unlink(missing_ok=True)

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    completed: dict[str, ProjectContext] = {}
    failures: list[str] = []

    async def process(signature: str, source: str, text: str, position: int) -> None:
        path = maps_dir / f"{signature}.json"
        if not force:
            cached = load_context_summary(path)
            if cached is not None:
                completed[signature] = cached
                log(f"[DEBUG] Context map cache hit: {source}")
                if on_progress:
                    on_progress("context-map", position, len(wanted))
                return
        log(f"[INFO] Context map {position}/{len(wanted)}: {source}")
        try:
            async with semaphore:
                window_maps = [await _extract_window(client, model, source, window) for window in _windows(text)]
            mapped = merge_context_maps(window_maps, signature)
            mapped.mapped_documents = 1
            mapped.total_documents = 1
            _atomic_json(path, mapped.to_dict())
            completed[signature] = mapped
        except Exception as exc:  # noqa: BLE001
            failures.append(source)
            log(f"[WARN] Context map failed for {source}: {type(exc).__name__}: {exc}")
        if on_progress:
            on_progress("context-map", position, len(wanted))

    await asyncio.gather(*(
        process(signature, source, text, position)
        for position, (signature, (source, text)) in enumerate(wanted.items(), 1)
    ))
    aggregate = merge_context_maps(list(completed.values()), kb_fingerprint)
    aggregate.total_documents = len(wanted)
    aggregate.mapped_documents = len(completed)
    aggregate.failed_documents = sorted(failures)
    aggregate.status = "partial" if failures else "complete"
    if on_progress:
        on_progress("context-merge", len(completed), len(wanted))
    log(
        f"[{'WARN' if failures else 'SUCCESS'}] Context {aggregate.status}: "
        f"{aggregate.mapped_documents}/{aggregate.total_documents} document(s), "
        f"{sum(len(getattr(aggregate, category)) for category in CATEGORIES)} item(s)"
    )
    return aggregate


async def extract_project_context_async(
    kb_index: Any, client: Any, model: str, kb_fingerprint: str = "",
    on_log: LogFn | None = None,
) -> ProjectContext:
    """Backward-compatible in-memory map/merge entry point."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="tt_context_") as tmp:
        return await build_context_incremental_async(
            kb_index, client, model, Path(tmp), kb_fingerprint, on_log,
        )


def save_context_summary(path: Path, ctx: ProjectContext) -> bool:
    try:
        _atomic_json(path, ctx.to_dict())
        return True
    except OSError:
        return False


def load_context_summary(path: Path) -> ProjectContext | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ProjectContext.from_dict(data) if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
