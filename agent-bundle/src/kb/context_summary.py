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
SCHEMA_VERSION: Final[int] = 3
_MAX_WINDOW_CHARS: Final[int] = 36000
_WINDOW_OVERLAP_CHARS: Final[int] = 2000
_MAX_TOKENS: Final[int] = 8192
_MAX_CONCURRENCY: Final[int] = 3
_MAX_WINDOW_CONCURRENCY: Final[int] = 3
CATEGORIES: Final[tuple[str, ...]] = (
    "actors",
    "entities",
    "data_models",
    "workflows",
    "system_architecture",
    "integrations",
    "business_rules",
    "screens",
    "inputs_outputs",
    "state_machines",
    "test_data_needs",
    "edge_cases",
    "non_functional",
    "dependencies",
    "security_constraints",
    "glossary",
)
_SYSTEM: Final[str] = (
    "You are a Principal QA architect and systems analyst performing exhaustive "
    "knowledge extraction. Extract EVERY fact supported by the supplied document "
    "window. Return valid JSON only, with exactly the requested 16 category keys. "
    "Each value is a list of objects with name and description.\n\n"
    "EXTRACTION DEPTH -- be maximally thorough:\n"
    "- actors: every user role, persona, system actor, API consumer, admin type, "
    "service account, external system identity\n"
    "- entities: every business object, domain entity, data record type with all "
    "known fields, constraints, cardinality, lifecycle states\n"
    "- data_models: ER relationships, class hierarchies, inheritance chains, "
    "composition/aggregation, foreign keys, indexes, enums, lookup tables\n"
    "- workflows: every process flow, sequence of steps, approval chains, "
    "branching logic, parallel paths, error/retry/timeout paths, happy + unhappy paths\n"
    "- system_architecture: technology stacks, deployment topology, services, "
    "containers, databases, queues, caches, CDNs, load balancers, regions, "
    "scaling policies, failover strategies\n"
    "- integrations: every API endpoint, webhook, event bus, file transfer, "
    "protocol (REST/gRPC/SOAP/GraphQL), auth mechanism, rate limits, SLAs\n"
    "- business_rules: every validation rule, calculation formula, threshold, "
    "conditional logic, priority ordering, conflict resolution, date/time rules\n"
    "- screens: every UI page, dialog, modal, panel, tab, form, table, chart, "
    "navigation flow, responsive breakpoints, accessibility requirements\n"
    "- inputs_outputs: every input field (type, format, min/max, required/optional, "
    "default), every output (reports, exports, notifications, emails, PDFs, logs)\n"
    "- state_machines: every entity state, valid transitions, guards/conditions, "
    "actions on entry/exit, event triggers, terminal states\n"
    "- test_data_needs: specific test data configurations, boundary values, "
    "prerequisite states, seed data, environment requirements\n"
    "- edge_cases: boundary conditions, off-by-one scenarios, concurrency conflicts, "
    "race conditions, null/empty handling, overflow, timezone/locale issues\n"
    "- non_functional: performance targets (latency, throughput, concurrency), "
    "availability (SLA, RPO, RTO), scalability limits, data retention, compliance "
    "(GDPR, HIPAA, SOC2), browser/device support matrix\n"
    "- dependencies: upstream/downstream services, data flows, shared libraries, "
    "third-party SDKs, version constraints, deprecation timelines\n"
    "- security_constraints: authentication methods, authorization models (RBAC/ABAC), "
    "encryption requirements, audit logging, PII handling, session management, "
    "CORS/CSP policies, vulnerability mitigations\n"
    "- glossary: domain-specific terminology, abbreviations, acronyms with "
    "precise definitions\n\n"
    "Be specific: preserve exact limits, states, field names, conditions, URLs, "
    "version numbers. Omit categories with no evidence rather than guessing."
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
    data_models: list[ContextItem] = field(default_factory=list)
    workflows: list[ContextItem] = field(default_factory=list)
    system_architecture: list[ContextItem] = field(default_factory=list)
    integrations: list[ContextItem] = field(default_factory=list)
    business_rules: list[ContextItem] = field(default_factory=list)
    screens: list[ContextItem] = field(default_factory=list)
    inputs_outputs: list[ContextItem] = field(default_factory=list)
    state_machines: list[ContextItem] = field(default_factory=list)
    test_data_needs: list[ContextItem] = field(default_factory=list)
    edge_cases: list[ContextItem] = field(default_factory=list)
    non_functional: list[ContextItem] = field(default_factory=list)
    dependencies: list[ContextItem] = field(default_factory=list)
    security_constraints: list[ContextItem] = field(default_factory=list)
    glossary: list[ContextItem] = field(default_factory=list)
    extracted_at: float = 0.0
    kb_fingerprint: str = ""
    status: str = "complete"
    enabled: bool = True
    mapped_documents: int = 0
    total_documents: int = 0
    failed_documents: list[str] = field(default_factory=list)
    # User-edited context text. When set, it is injected verbatim instead of the
    # auto-rendered category sections (set via the KB dialog "Edit" action).
    override_summary: str = ""

    def is_empty(self) -> bool:
        if self.override_summary.strip():
            return False
        return not any(getattr(self, category) for category in CATEGORIES)

    def to_prompt_section(self) -> str:
        if not self.enabled:
            return ""
        if self.override_summary.strip():
            return self.override_summary.strip()
        if self.is_empty():
            return ""
        titles = {
            "actors": "ACTORS / ROLES / PERSONAS",
            "entities": "BUSINESS ENTITIES / DOMAIN OBJECTS",
            "data_models": "DATA MODELS / ER RELATIONSHIPS / CLASS DIAGRAMS",
            "workflows": "WORKFLOWS / PROCESS FLOWS / SEQUENCE DIAGRAMS",
            "system_architecture": "SYSTEM ARCHITECTURE / STACKS / TOPOLOGY",
            "integrations": "INTEGRATION POINTS / APIs / PROTOCOLS",
            "business_rules": "BUSINESS RULES / VALIDATIONS / FORMULAS",
            "screens": "SCREENS / PAGES / UI COMPONENTS",
            "inputs_outputs": "INPUTS / OUTPUTS / REPORTS / NOTIFICATIONS",
            "state_machines": "STATE MACHINES / TRANSITIONS / LIFECYCLE",
            "test_data_needs": "TEST DATA NEEDS / BOUNDARY VALUES",
            "edge_cases": "EDGE CASES / NEGATIVE PATHS / RACE CONDITIONS",
            "non_functional": "NON-FUNCTIONAL REQUIREMENTS / SLAs / COMPLIANCE",
            "dependencies": "DEPENDENCIES / DATA FLOWS / SHARED LIBRARIES",
            "security_constraints": "SECURITY / AUTH / ENCRYPTION / AUDIT",
            "glossary": "GLOSSARY / TERMINOLOGY / ACRONYMS",
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
            enabled=bool(data.get("enabled", True)),
            mapped_documents=int(data.get("mapped_documents", 0) or 0),
            total_documents=int(data.get("total_documents", 0) or 0),
            failed_documents=[str(item) for item in data.get("failed_documents", [])],
            override_summary=str(data.get("override_summary", "")),
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


# Cap the accumulated description per item so a term that appears in 100+
# documents does not balloon into a wall of near-identical sentences.
_MAX_DESC_CHARS: Final[int] = 600


def _split_sentences(text: str) -> list[str]:
    """Split a description into trimmed sentence-like fragments for dedup."""
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _dedup_merge_description(existing: str, incoming: str) -> str:
    """Append only sentences from `incoming` whose normalized form is not
    already represented in `existing` (case/whitespace/punctuation-insensitive),
    capping the total length so repeated documents cannot inflate the summary."""
    if not incoming:
        return existing
    if not existing:
        kept = _split_sentences(incoming)
    else:
        seen = {_normalize_name(s) for s in _split_sentences(existing)}
        kept = _split_sentences(existing)
        for sentence in _split_sentences(incoming):
            norm = _normalize_name(sentence)
            if norm and norm not in seen:
                seen.add(norm)
                kept.append(sentence)
    out = " ".join(kept).strip()
    if len(out) > _MAX_DESC_CHARS:
        out = out[:_MAX_DESC_CHARS].rstrip() + "…"
    return out


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
                    merged[category][key] = ContextItem(
                        item.name,
                        _dedup_merge_description("", item.description),
                        sorted(set(item.sources)),
                    )
                    continue
                existing.description = _dedup_merge_description(
                    existing.description, item.description
                )
                existing.sources = sorted(set(existing.sources + item.sources))
    values = {category: sorted(items.values(), key=lambda item: item.name.lower()) for category, items in merged.items()}
    enabled = all(context.enabled for context in maps) if maps else True
    return ProjectContext(
        **values, extracted_at=time.time(), kb_fingerprint=fingerprint,
        enabled=enabled,
    )


async def _extract_window(client: Any, model: str, source: str, text: str) -> ProjectContext:
    prompt = (
        f"Source document: {source}\n"
        f"Return exactly these {len(CATEGORIES)} keys: {', '.join(CATEGORIES)}.\n"
        f"<document>\n{text}\n</document>"
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        retry_note = ""
        if attempt > 1:
            retry_note = (
                "\nYour previous response was invalid. Return one JSON object only, "
                "with no prose or markdown fences."
            )
        try:
            result = await client.complete_async(
                model=model, system=_SYSTEM, user=prompt + retry_note,
                max_tokens=_MAX_TOKENS, temperature=0.0,
            )
            raw = str(getattr(result, "text", "") or "")
            if not raw.strip():
                raise ValueError("empty context response")
            context = ProjectContext.from_dict(_parse_llm_response(raw))
            for category in CATEGORIES:
                for item in getattr(context, category):
                    item.sources = [source]
            return context
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt < 3:
                await asyncio.sleep(0.5 * attempt)
    raise RuntimeError(f"invalid context JSON after 3 attempts: {last_error}")


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
    progress_done = 0
    progress_lock = asyncio.Lock()

    async def report_progress(source: str) -> None:
        nonlocal progress_done
        async with progress_lock:
            progress_done += 1
            current = progress_done
        log(f"[INFO] Context mapped {current}/{len(wanted)}: {source}")
        if on_progress:
            on_progress("context-map", current, len(wanted))

    async def process(signature: str, source: str, text: str, position: int) -> None:
        path = maps_dir / f"{signature}.json"
        if not force:
            cached = load_context_summary(path)
            if cached is not None:
                completed[signature] = cached
                log(f"[DEBUG] Context map cache hit: {source}")
                await report_progress(source)
                return
        log(f"[INFO] Context map queued {position}/{len(wanted)}: {source}")
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                async with semaphore:
                    window_sem = asyncio.Semaphore(_MAX_WINDOW_CONCURRENCY)

                    async def _throttled(w: str) -> ProjectContext:
                        async with window_sem:
                            return await _extract_window(client, model, source, w)

                    window_maps: list[ProjectContext] = list(await asyncio.gather(
                        *[_throttled(w) for w in _windows(text)]
                    ))
                mapped = merge_context_maps(window_maps, signature)
                mapped.mapped_documents = 1
                mapped.total_documents = 1
                _atomic_json(path, mapped.to_dict())
                completed[signature] = mapped
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < 3:
                    delay = float(2 ** (attempt - 1))
                    log(
                        f"[WARN] Context map retry {attempt}/3 for {source} "
                        f"in {delay:.0f}s: {type(exc).__name__}: {exc}"
                    )
                    await asyncio.sleep(delay)
        if last_error is not None:
            failures.append(source)
            log(
                f"[WARN] Context map unavailable after 3 attempts for {source}: "
                f"{type(last_error).__name__}: {last_error}"
            )
        await report_progress(source)

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
