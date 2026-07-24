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
_MAX_CONCURRENCY: Final[int] = 4
_MAX_WINDOW_CONCURRENCY: Final[int] = 5
_MAX_DOC_RETRIES: Final[int] = 5
_DOC_TIMEOUT_SEC: Final[float] = 120.0  # per-document base cap (scaled by windows)
_POST_PASS_ROUNDS: Final[int] = 5
_RETRY_WINDOW_CONCURRENCY: Final[int] = 2
# Documents exceeding this character count are routed to the frontier model
# instead of the medium tier, because they produce more windows whose JSON
# merge complexity exceeds Sonnet's reliable extraction depth.
_LARGE_DOC_CHARS: Final[int] = 100_000
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
    "You are a Principal QA architect extracting testable domain knowledge from "
    "project documentation. Return valid JSON only with exactly the requested 16 "
    "category keys. Each value is a list of objects with 'name' and 'description'.\n\n"
    "QUALITY RULES (follow strictly):\n"
    "- Extract ONLY concrete, testable, verifiable facts directly stated in the text.\n"
    "- DO NOT paraphrase vaguely. Preserve exact field names, limits, states, URLs, "
    "version numbers, thresholds, formulas.\n"
    "- DO NOT infer or speculate. If the document says 'may' or 'could', skip it.\n"
    "- DO NOT extract document metadata (author, revision date, filename).\n"
    "- DO NOT extract generic software truisms ('the system should be reliable').\n"
    "- Each item.name must be a specific noun or noun phrase (not a sentence).\n"
    "- Each item.description must state a testable fact in one concise sentence.\n"
    "- Omit categories with no evidence -- empty list [] is correct.\n\n"
    "CATEGORIES:\n"
    "- actors: named user roles, personas, system actors with stated permissions\n"
    "- entities: business objects with known fields, constraints, cardinality\n"
    "- data_models: stated relationships, foreign keys, enums, inheritance\n"
    "- workflows: explicit step sequences, approval chains, branching, error paths\n"
    "- system_architecture: named services, databases, deployment topology\n"
    "- integrations: API endpoints, protocols, auth, rate limits, SLAs\n"
    "- business_rules: validation rules, formulas, thresholds, conditional logic\n"
    "- screens: named pages/dialogs/forms with stated fields and behavior\n"
    "- inputs_outputs: fields with type/format/min/max/required, reports, exports\n"
    "- state_machines: named states, valid transitions, guards, triggers\n"
    "- test_data_needs: specific configs, boundary values, prerequisite states\n"
    "- edge_cases: stated boundary conditions, concurrency, null handling\n"
    "- non_functional: explicit targets (latency, SLA, RPO, compliance)\n"
    "- dependencies: named upstream/downstream services, version constraints\n"
    "- security_constraints: stated auth methods, encryption, audit, PII rules\n"
    "- glossary: domain terms with precise definitions from the document"
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
    # Lazy-built lookup indexes for O(1) retrieval (not serialized).
    _idx_name: dict[str, list[tuple[str, ContextItem]]] = field(
        default_factory=dict, init=False, repr=False, compare=False,
    )
    _idx_source: dict[str, list[tuple[str, ContextItem]]] = field(
        default_factory=dict, init=False, repr=False, compare=False,
    )
    _idx_category: dict[str, dict[str, ContextItem]] = field(
        default_factory=dict, init=False, repr=False, compare=False,
    )

    def _ensure_indexes(self) -> None:
        if self._idx_name:
            return
        for category in CATEGORIES:
            cat_map: dict[str, ContextItem] = {}
            for item in getattr(self, category):
                key = _normalize_name(item.name)
                if not key:
                    continue
                cat_map[key] = item
                self._idx_name.setdefault(key, []).append((category, item))
                for src in item.sources:
                    self._idx_source.setdefault(src, []).append((category, item))
            if cat_map:
                self._idx_category[category] = cat_map

    def lookup_by_name(self, name: str) -> list[tuple[str, ContextItem]]:
        """O(1) lookup: returns [(category, item), ...] for items matching name."""
        self._ensure_indexes()
        return self._idx_name.get(_normalize_name(name), [])

    def lookup_by_source(self, source: str) -> list[tuple[str, ContextItem]]:
        """O(1) lookup: returns [(category, item), ...] contributed by source doc."""
        self._ensure_indexes()
        return self._idx_source.get(source, [])

    def lookup_in_category(self, category: str, name: str) -> ContextItem | None:
        """O(1) lookup within a specific category."""
        self._ensure_indexes()
        cat_map = self._idx_category.get(category)
        if cat_map is None:
            return None
        return cat_map.get(_normalize_name(name))

    def all_names(self) -> frozenset[str]:
        """All normalized item names across all categories."""
        self._ensure_indexes()
        return frozenset(self._idx_name.keys())

    def sources_index(self) -> dict[str, list[tuple[str, ContextItem]]]:
        """Full source->items map for batch operations."""
        self._ensure_indexes()
        return self._idx_source

    def is_empty(self) -> bool:
        if self.override_summary.strip():
            return False
        return not any(getattr(self, category) for category in CATEGORIES)

    def to_prompt_section_for_sources(self, sources: list[str]) -> str:
        """Targeted prompt section: only items contributed by the given sources."""
        if not self.enabled or not sources:
            return self.to_prompt_section()
        if self.override_summary.strip():
            return self.override_summary.strip()
        self._ensure_indexes()
        relevant: dict[str, list[ContextItem]] = {}
        for src in sources:
            for category, item in self._idx_source.get(src, []):
                relevant.setdefault(category, []).append(item)
        if not relevant:
            return self.to_prompt_section()
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
        parts = ["PROJECT CONTEXT (targeted)", "=" * 40]
        for category in CATEGORIES:
            items = relevant.get(category)
            if not items:
                continue
            parts.append(f"\n{titles[category]}:")
            seen: set[str] = set()
            for item in items:
                key = _normalize_name(item.name)
                if key in seen:
                    continue
                seen.add(key)
                parts.append(f"  - {item.name}: {item.description}")
        parts.append("")
        return "\n".join(parts)

    def to_prompt_section_selective(self, work_item_text: str, max_items: int = 80) -> str:
        """RAG-style selective injection: only categories/items relevant to the WI."""
        if not self.enabled:
            return ""
        if self.override_summary.strip():
            return self.override_summary.strip()
        if self.is_empty() or not work_item_text.strip():
            return self.to_prompt_section()
        wi_lower = work_item_text.lower()
        wi_tokens = frozenset(re.sub(r"[^a-z0-9]+", " ", wi_lower).split())
        titles = {
            "actors": "ACTORS / ROLES",
            "entities": "BUSINESS ENTITIES",
            "data_models": "DATA MODELS",
            "workflows": "WORKFLOWS",
            "system_architecture": "SYSTEM ARCHITECTURE",
            "integrations": "INTEGRATIONS / APIs",
            "business_rules": "BUSINESS RULES",
            "screens": "SCREENS / UI",
            "inputs_outputs": "INPUTS / OUTPUTS",
            "state_machines": "STATE MACHINES",
            "test_data_needs": "TEST DATA NEEDS",
            "edge_cases": "EDGE CASES",
            "non_functional": "NON-FUNCTIONAL REQUIREMENTS",
            "dependencies": "DEPENDENCIES",
            "security_constraints": "SECURITY CONSTRAINTS",
            "glossary": "GLOSSARY",
        }
        selected: list[tuple[str, ContextItem, float]] = []
        for category in CATEGORIES:
            items = getattr(self, category)
            if not items:
                continue
            for item in items:
                name_lower = item.name.lower()
                desc_lower = item.description.lower()
                name_tokens = frozenset(re.sub(r"[^a-z0-9]+", " ", name_lower).split())
                overlap = len(wi_tokens & name_tokens)
                score = 0.0
                if overlap >= 2:
                    score = float(overlap) * 3.0
                elif overlap == 1 and len(name_tokens) <= 3:
                    score = 2.0
                if name_lower in wi_lower:
                    score += 5.0
                desc_tokens = frozenset(re.sub(r"[^a-z0-9]+", " ", desc_lower).split())
                desc_overlap = len(wi_tokens & desc_tokens)
                if desc_overlap >= 3:
                    score += float(desc_overlap) * 0.5
                if score > 0:
                    selected.append((category, item, score))
        if not selected:
            # No keyword matches -- return empty rather than full dump
            return ""
        selected.sort(key=lambda t: t[2], reverse=True)
        selected = selected[:max_items]
        grouped: dict[str, list[ContextItem]] = {}
        for category, item, _score in selected:
            grouped.setdefault(category, []).append(item)
        parts = ["PROJECT CONTEXT (selective -- relevant to this work item)", "=" * 40]
        for category in CATEGORIES:
            items = grouped.get(category)
            if not items:
                continue
            parts.append(f"\n{titles[category]}:")
            for item in items:
                parts.append(f"  - {item.name}: {item.description}")
        parts.append("")
        return "\n".join(parts)

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
        d = asdict(self)
        d.pop("_idx_name", None)
        d.pop("_idx_source", None)
        d.pop("_idx_category", None)
        return d

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
        except Exception as exc:  # noqa: BLE001 - LLM timeout/rate-limit
            last_error = exc
            if attempt < 3:
                await asyncio.sleep(2.0 * attempt)
    raise RuntimeError(f"window extraction failed after 3 attempts: {last_error}")


class _DocRetryExhausted(Exception):
    """Sentinel: all retry attempts for a single document failed."""
    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(str(cause))


async def _process_doc_retries(
    signature: str, source: str, text: str,
    semaphore: asyncio.Semaphore, client: Any, model: str,
    maps_dir: Path, completed: dict[str, "ProjectContext"],
    log: LogFn,
    window_concurrency: int = _MAX_WINDOW_CONCURRENCY,
) -> None:
    """Retry loop for a single document, separated so wait_for can wrap it."""
    for attempt in range(1, _MAX_DOC_RETRIES + 1):
        try:
            async with semaphore:
                window_sem = asyncio.Semaphore(window_concurrency)

                async def _throttled(w: str) -> "ProjectContext":
                    async with window_sem:
                        return await _extract_window(client, model, source, w)

                window_maps: list["ProjectContext"] = list(await asyncio.gather(
                    *[_throttled(w) for w in _windows(text)]
                ))
            mapped = merge_context_maps(window_maps, signature)
            mapped.mapped_documents = 1
            mapped.total_documents = 1
            _atomic_json(maps_dir / f"{signature}.json", mapped.to_dict())
            completed[signature] = mapped
            return
        except Exception as exc:  # noqa: BLE001
            if attempt < _MAX_DOC_RETRIES:
                delay = min(float(2 ** attempt), 60.0)
                log(
                    f"[WARN] Context map retry {attempt}/{_MAX_DOC_RETRIES} "
                    f"for {source} in {delay:.0f}s: "
                    f"{type(exc).__name__}: {exc}"
                )
                await asyncio.sleep(delay)
            else:
                raise _DocRetryExhausted(exc) from exc


def _timeout_for_doc(text: str, retry: bool = False) -> float:
    """Scale the per-document timeout by the number of windows it produces.
    A single-window doc gets the base 120s; large docs scale up to 900s.
    Retries double the base AND raise the cap to guarantee the retry
    timeout is always >= the initial effective timeout."""
    n_windows = max(1, len(_windows(text)))
    base = _DOC_TIMEOUT_SEC * max(1.0, n_windows * 0.6)
    if retry:
        base *= 2.0
    cap = 1200.0 if retry else 720.0
    return min(base, cap)


async def build_context_incremental_async(
    kb_index: Any, client: Any, model: str, maps_dir: Path,
    kb_fingerprint: str = "", on_log: LogFn | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
    force: bool = False,
    large_model: str = "",
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
        # Route oversized documents to the frontier model for better extraction
        effective_model = model
        if large_model and len(text) > _LARGE_DOC_CHARS:
            effective_model = large_model
            log(
                f"[INFO] Document '{source}' is {len(text):,} chars "
                f"(>{_LARGE_DOC_CHARS:,}); escalating to frontier model."
            )
        doc_timeout = _timeout_for_doc(text)
        # Add queuing headroom: docs waiting for the semaphore burn timeout
        # budget idly. Scale by position / concurrency to account for queue.
        queue_headroom = (position / _MAX_CONCURRENCY) * 30.0
        effective_timeout = doc_timeout + min(queue_headroom, 300.0)
        log(f"[INFO] Context map queued {position}/{len(wanted)}: {source} (timeout={effective_timeout:.0f}s)")
        last_error: Exception | None = None
        try:
            await asyncio.wait_for(
                _process_doc_retries(
                    signature, source, text, semaphore, client, effective_model,
                    maps_dir, completed, log,
                ),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            last_error = TimeoutError(
                f"document exceeded {effective_timeout:.0f}s hard cap"
            )
            log(
                f"[ERROR] Context map timed out for {source} "
                f"after {effective_timeout:.0f}s"
            )
        except _DocRetryExhausted as exc:
            last_error = exc.cause
            log(
                f"[ERROR] Context map failed after {_MAX_DOC_RETRIES} attempts "
                f"for {source}: {type(exc.cause).__name__}: {exc.cause}"
            )
        if last_error is not None:
            failures.append(source)
        await report_progress(source)

    await asyncio.gather(*(
        process(signature, source, text, position)
        for position, (signature, (source, text)) in enumerate(wanted.items(), 1)
    ))

    # Post-pass retry: exponential cooldown, serialize to avoid rate-limit
    # cascades, reduced window concurrency for gentler throughput.
    retry_sem = asyncio.Semaphore(_RETRY_WINDOW_CONCURRENCY)
    for retry_round in range(1, _POST_PASS_ROUNDS + 1):
        if not failures:
            break
        cooldown = min(15.0 * (2 ** (retry_round - 1)), 120.0)
        log(
            f"[INFO] Context retry round {retry_round}/{_POST_PASS_ROUNDS}: "
            f"{len(failures)} failed doc(s), cooldown {cooldown:.0f}s..."
        )
        if on_progress:
            on_progress("context-retry", len(completed), len(wanted))
        await asyncio.sleep(cooldown)
        retry_sigs = [
            sig for sig, (src, _) in wanted.items()
            if src in failures and sig not in completed
        ]
        retry_failures: list[str] = []
        for sig in retry_sigs:
            failures.remove(wanted[sig][0])

        # Serialize retries: process one doc at a time to stay within rate
        # limits. Each doc gets extended timeout and reduced concurrency.
        for sig in retry_sigs:
            src, txt = wanted[sig]
            retry_model = large_model or model
            retry_timeout = _timeout_for_doc(txt, retry=True)
            try:
                await asyncio.wait_for(
                    _process_doc_retries(
                        sig, src, txt, retry_sem, client, retry_model,
                        maps_dir, completed, log,
                        window_concurrency=_RETRY_WINDOW_CONCURRENCY,
                    ),
                    timeout=retry_timeout,
                )
                log(f"[SUCCESS] Context retry succeeded: {src}")
            except (asyncio.TimeoutError, _DocRetryExhausted) as exc:
                retry_failures.append(src)
                cause = exc.cause if isinstance(exc, _DocRetryExhausted) else exc
                log(f"[WARN] Context retry still failing: {src}: {cause}")
            if on_progress:
                on_progress("context-retry", len(completed), len(wanted))

        failures.extend(retry_failures)

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



def context_to_chunks(ctx: "ProjectContext") -> list[dict[str, str]]:
    """Convert project context items into chunk dicts for vectorization.

    Each ContextItem becomes one chunk with:
      chunk_id: ctx_{category}_{index:03d}
      text: "{category}: {name} - {description}"
      title: "{name}"
      section_path: "{category}"
    """
    chunks: list[dict[str, str]] = []
    for category in CATEGORIES:
        items = getattr(ctx, category, [])
        if not items:
            continue
        for idx, item in enumerate(items):
            if not item.name and not item.description:
                continue
            chunk_id = f"ctx_{category}_{idx:03d}"
            text = f"{category}: {item.name}"
            if item.description:
                text += f" - {item.description}"
            if item.sources:
                text += f" (sources: {', '.join(item.sources[:3])})"
            chunks.append({
                "chunk_id": chunk_id,
                "text": text,
                "title": item.name,
                "section_path": category,
            })
    return chunks


def context_fingerprint(ctx: "ProjectContext") -> str:
    """Compute fingerprint of context items for change detection."""
    parts: list[str] = []
    for chunk in context_to_chunks(ctx):
        parts.append(chunk["chunk_id"] + ":" + chunk["text"])
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


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
