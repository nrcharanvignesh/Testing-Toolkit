"""
rlm.py
Recursive Language Model retrieval + test-case generation.

This replaces a local RAG / embedding index. Instead of embedding chunks
and doing vector search, the language model itself navigates the
knowledge base. The context is treated as an environment the model
explores:

    1. NAVIGATE (root, fast model)
       The model is shown a compact MAP of the KB (chunk ids + titles +
       sizes) plus a summary of the selected work items, and returns the
       ids of the chunks worth opening. If the map itself is too large to
       fit, the index is partitioned and each partition is navigated
       recursively, then the selected ids are unioned.

    2. MAP (fast model, parallel, order-stable)
       Each selected chunk is opened and the model extracts only the
       passages relevant to the work items (acceptance criteria, field
       rules, error behaviors, supported browsers, etc.). Irrelevant
       chunks return NONE and are dropped. This compresses the KB to a
       small focused context.

    3. REDUCE / GENERATE (root, primary model)
       The project's strict system prompt + the work-item dump + the
       focused excerpts are sent to the primary model, which emits the
       fenced JSON test-case payload.

Small KBs skip navigate/map entirely (the whole KB is passed directly).
Projects with no KB generate from the work items alone.

All model calls go through anthropic_client.AnthropicClient, so they
inherit the application's TLS handling. temperature is pinned to 0.0 for
the most repeatable output the API permits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Final

from core.anthropic_client import AnthropicClient, AnthropicError
from core.model_router import Task, route
from ado.testcase_creator import normalize_payload, validate_payload
from core.app_config import (
    RLM_DECOMPOSE_MAX_TOKENS,
    RLM_DIRECT_CONTEXT_TOKENS,
    RLM_GENERATE_MAX_TOKENS,
    RLM_MAP_CHUNK_TOKENS,
    RLM_MAP_MAX_TOKENS,
    RLM_NAVIGATE_MAX_TOKENS,
    RLM_VERIFY_MAX_TOKENS,
)
from testgen.gen_cache import GenCache, context_key, generation_key, kb_fingerprint
from kb.store import KbChunk, KbIndex, approx_tokens

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int], None]
_logger = logging.getLogger(__name__)

_MAP_CONCURRENCY: Final[int] = 12
_GEN_CONCURRENCY: Final[int] = 6
_NAV_CONCURRENCY: Final[int] = 6

# Stable tag for the retrieval-context cache key: any change to the token
# budgets that shape navigate/map output must invalidate cached context.
_BUDGET_TAG: Final[str] = (
    f"d{RLM_DIRECT_CONTEXT_TOKENS}-n{RLM_NAVIGATE_MAX_TOKENS}-"
    f"m{RLM_MAP_MAX_TOKENS}-c{RLM_MAP_CHUNK_TOKENS}"
)

# Hybrid retrieval (when a prebuilt index is available): how many chunks to
# fuse/rerank and return for assembly into the generation context.
_HYBRID_TOP_K: Final[int] = 32
_HYBRID_CANDIDATES: Final[int] = 96
_CHARS_PER_TOKEN: Final[float] = 3.5
_DEFAULT_MAX_REPAIR: Final[int] = 2
# Chunk-id token, e.g. d003c0007. Used to parse navigator output robustly.
_CHUNK_ID_RE: Final[re.Pattern[str]] = re.compile(r"d\d{3}c\d{4}")
_JSON_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE
)


class StopRequested(RuntimeError):
    """Raised internally when the user cancels a generation run."""


# ---------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------
_NAVIGATE_SYSTEM: Final[str] = (
    "You are a retrieval router for a QA test-case generator. You are "
    "given a MAP of knowledge-base sections (each line is "
    "'[chunk_id] (~tokens) title') and a summary of the work items that "
    "need test cases. Return ONLY the chunk_ids whose content is likely "
    "needed to write accurate test cases for those work items - "
    "requirements, field validations, error behaviors, business rules, "
    "supported browsers/platforms, and acceptance criteria. Be "
    "selective; do not return everything. Output ONLY a JSON array of "
    "chunk_id strings, for example [\"d000c0001\",\"d002c0005\"]. No "
    "prose, no code fence."
)

_MAP_SYSTEM: Final[str] = (
    "You extract source facts for QA test-case authoring. You are given "
    "one knowledge-base section and the work items under test. Quote or "
    "tightly paraphrase ONLY the parts of the section that are relevant "
    "to testing those work items: explicit requirements, field "
    "validation rules and boundaries, error/exception behaviors, "
    "enumerated states, supported browsers/devices, and acceptance "
    "criteria. Preserve exact field names, error codes, thresholds, and "
    "labels. Omit everything irrelevant. If nothing in the section is "
    "relevant, reply with exactly NONE. Output plain text only."
)

_DECOMPOSE_SYSTEM: Final[str] = (
    "You decompose work items into atomic testable requirements. For each "
    "work item, list every discrete testable behavior: individual acceptance "
    "criteria, field validation rules with boundaries, state transitions, "
    "error conditions, platform constraints, and UI elements. For each "
    "requirement, note the screen/page/dialog where the behavior occurs "
    "(e.g. 'On the Create Assessment Page: title field accepts max 100 "
    "characters'). Format as a numbered list. Be exhaustive but do not "
    "invent requirements not stated in the inputs. Output plain text only."
)

_VERIFY_SYSTEM: Final[str] = (
    "You are a QA coverage analyst. You are given a set of requirements "
    "(work items + knowledge base context) and the test cases already "
    "generated for them. Your job is to identify GAPS: acceptance criteria, "
    "field validations, error behaviors, or boundary conditions that are NOT "
    "adequately covered by any existing test case. For each gap, generate "
    "the missing test case(s) following the exact same JSON schema. "
    "CRITICAL: Every test step action MUST begin with the screen/page "
    "context where the action takes place - format: 'On [Screen/Page "
    "Name], [action]' or 'Navigate to [Screen/Page Name]' for navigation "
    "steps. Never write a step without indicating which screen, page, or "
    "dialog the user is currently on. "
    "Output ONLY the additional test cases as a JSON object in the same "
    "schema (schema_version 1, stories array). If coverage is complete, "
    "output exactly: {\"schema_version\": 1, \"stories\": []}"
)


# ---------------------------------------------------------------------
# Result / trace
# ---------------------------------------------------------------------
@dataclass(slots=True)
class RlmTrace:
    mode: str = "no_kb"                 # no_kb | direct | recursive
    kb_docs: int = 0
    kb_chunks: int = 0
    kb_tokens: int = 0
    selected_chunk_ids: list[str] = field(default_factory=list)
    navigate_calls: int = 0
    map_calls: int = 0
    map_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class RlmResult:
    ok: bool
    payload: dict[str, Any] | None
    raw_text: str
    parse_error: str
    trace: RlmTrace


# ---------------------------------------------------------------------
# Work-item dump formatting
# ---------------------------------------------------------------------
def build_work_item_dump(items: list[Any]) -> str:
    """Format normalized work-item records (ado_boards.WorkItemDetail or
    any object/dict exposing the same fields) into the 'ADO Dump' text
    the generator expects. Sorted by work item id for determinism."""
    def _g(it: Any, name: str, default: Any = "") -> Any:
        if isinstance(it, dict):
            return it.get(name, default)
        return getattr(it, name, default)

    ordered = sorted(items, key=lambda it: int(_g(it, "wi_id", 0) or 0))
    blocks: list[str] = []
    for it in ordered:
        wi_id = int(_g(it, "wi_id", 0) or 0)
        title = str(_g(it, "title", "")).strip()
        wtype = str(_g(it, "wi_type", "")).strip()
        state = str(_g(it, "state", "")).strip()
        area = str(_g(it, "area_path", "")).strip()
        iteration = str(_g(it, "iteration_path", "")).strip()
        tags = _g(it, "tags", []) or []
        desc = str(_g(it, "description_text", "")).strip()
        ac = str(_g(it, "acceptance_text", "")).strip()
        comments = _g(it, "comments", []) or []

        lines: list[str] = []
        lines.append("=" * 72)
        lines.append(f"WORK ITEM #{wi_id} [{wtype}] - {title}")
        meta = f"State: {state or 'n/a'}"
        if area:
            meta += f" | Area: {area}"
        if iteration:
            meta += f" | Iteration: {iteration}"
        lines.append(meta)
        if tags:
            lines.append("Tags: " + ", ".join(str(t) for t in tags))
        lines.append("")
        lines.append("DESCRIPTION:")
        lines.append(desc or "(none)")
        lines.append("")
        lines.append("ACCEPTANCE CRITERIA:")
        lines.append(ac or "(none)")
        if comments:
            lines.append("")
            lines.append("COMMENTS:")
            for c in comments:
                if isinstance(c, (list, tuple)) and len(c) >= 3:
                    when, author, text = c[0], c[1], c[2]
                elif isinstance(c, dict):
                    when = c.get("when", "")
                    author = c.get("author", "")
                    text = c.get("text", "")
                else:
                    when, author, text = "", "", str(c)
                lines.append(f"  - [{when} | {author}] {text}".rstrip())
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks).strip()


def _work_item_summary(dump: str, max_chars: int = 6000) -> str:
    """A compact summary of the work items for the navigate prompt."""
    if len(dump) <= max_chars:
        return dump
    return dump[:max_chars] + "\n...(truncated for routing)..."


# ---------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------
def _assemble_full_kb(index: KbIndex) -> str:
    parts: list[str] = []
    last_doc = ""
    for c in index.chunks:
        if c.doc != last_doc:
            parts.append(f"\n##### SOURCE: {c.doc} #####")
            last_doc = c.doc
        header = f"[{c.chunk_id}] {c.title}".strip()
        parts.append(f"--- {header} ---\n{c.text}")
    return "\n\n".join(parts).strip()


def _assemble_excerpts(excerpts: list[tuple[KbChunk, str]]) -> str:
    # Sort by extraction length descending: longer extractions carry more
    # relevant detail and LLMs attend more strongly to earlier context.
    ranked = sorted(excerpts, key=lambda t: len(t[1]), reverse=True)
    parts: list[str] = []
    for chunk, text in ranked:
        parts.append(
            f"--- From {chunk.doc} :: {chunk.title} ---\n{text.strip()}"
        )
    return "\n\n".join(parts).strip()


def _build_generation_user(
    work_item_dump: str, kb_context: str,
    project_context: str = "",
) -> str:
    sections: list[str] = []
    # Inject project context summary (domain understanding) first
    if project_context.strip():
        sections.append(project_context.strip())
    if kb_context.strip():
        sections.append(
            "REQUIREMENTS CONTEXT (extracted from the project knowledge "
            "base; treat as the source of truth for application "
            "behavior):\n" + kb_context
        )
    else:
        sections.append(
            "REQUIREMENTS CONTEXT: (no project knowledge base supplied; "
            "rely only on the work item content below and do not invent "
            "requirements)"
        )
    sections.append(
        "ADO DUMP (the user stories / work items to cover):\n"
        + work_item_dump
    )
    return "\n\n".join(sections)


def _build_repair_user(previous_output: str, errors: str) -> str:
    """Prompt that asks the model to fix its own rejected output. The
    project system prompt (with the schema rules) is reused as the system
    message, so this only needs the problems plus the prior attempt."""
    return (
        "Your previous response was REJECTED by the validator. Return the "
        "corrected and COMPLETE test-case JSON only, in a single ```json "
        "code fence, following the exact schema from the system prompt. Do "
        "not add commentary, apologies, or explanations.\n\n"
        "PROBLEMS TO FIX (resolve every one):\n" + errors + "\n\n"
        "YOUR PREVIOUS RESPONSE (fix it; keep the valid parts):\n"
        + previous_output
    )


async def _decompose_requirements(
    client: AnthropicClient,
    fast_model: str,
    work_item_dump: str,
    kb_context: str,
    on_log: LogFn | None,
    trace: RlmTrace,
    stop_event: threading.Event | None,
) -> str:
    """Decompose work items into atomic testable requirements using the fast
    model. Returns a structured numbered list the generation model uses as an
    explicit coverage checklist."""
    if stop_event is not None and stop_event.is_set():
        raise StopRequested()
    user_parts: list[str] = []
    if kb_context.strip():
        user_parts.append(
            "REQUIREMENTS CONTEXT:\n" + kb_context[:20000]
        )
    user_parts.append("WORK ITEMS:\n" + work_item_dump)
    user_parts.append(
        "\nDecompose into a numbered list of atomic testable requirements."
    )
    res = await client.complete_async(
        model=fast_model, system=_DECOMPOSE_SYSTEM,
        user="\n\n".join(user_parts),
        max_tokens=RLM_DECOMPOSE_MAX_TOKENS, temperature=0.0,
    )
    trace.input_tokens += res.usage.input_tokens
    trace.output_tokens += res.usage.output_tokens
    text = (res.text or "").strip()
    if text:
        _log(on_log, f"[INFO] Decomposed requirements into checklist "
                     f"({len(text.splitlines())} items).")
    return text


async def _verify_and_fill(
    client: AnthropicClient,
    primary_model: str,
    system_prompt: str,
    work_item_dump: str,
    kb_context: str,
    initial_payload: dict[str, Any],
    on_log: LogFn | None,
    stop_event: threading.Event | None,
    trace: RlmTrace,
) -> dict[str, Any] | None:
    """Post-generation coverage verification: identify gaps and produce
    additional test cases. Returns a delta payload (same schema) or None."""
    if stop_event is not None and stop_event.is_set():
        raise StopRequested()
    _log(on_log, "[INFO] Running coverage verification + gap-fill pass...")
    existing_json = json.dumps(initial_payload, indent=None)
    if len(existing_json) > 60000:
        # Truncate at a structural boundary (end of last complete test_case).
        cut = existing_json.rfind("},", 0, 60000)
        if cut > 0:
            existing_json = existing_json[:cut + 1] + "]}}]}"
        else:
            existing_json = existing_json[:60000]
    user_parts: list[str] = []
    if kb_context.strip():
        user_parts.append(
            "REQUIREMENTS CONTEXT:\n" + kb_context[:40000]
        )
    user_parts.append("WORK ITEMS:\n" + work_item_dump)
    user_parts.append(
        "EXISTING TEST CASES (already generated):\n```json\n"
        + existing_json + "\n```"
    )
    user_parts.append(
        "\nIdentify any acceptance criteria, field validations, error "
        "behaviors, or boundary conditions NOT covered. Generate the "
        "missing test cases in the same JSON schema."
    )
    user_msg = "\n\n".join(user_parts)
    delta = None
    for _repair_attempt in range(2):
        res = await client.complete_async(
            model=primary_model, system=_VERIFY_SYSTEM,
            user=user_msg,
            max_tokens=RLM_VERIFY_MAX_TOKENS, temperature=0.0,
        )
        trace.input_tokens += res.usage.input_tokens
        trace.output_tokens += res.usage.output_tokens
        delta, _err = parse_payload(res.text)
        if delta is not None:
            break
        _log(on_log, f"[WARN] Verify pass returned unparseable output "
                     f"(attempt {_repair_attempt + 1}/2); "
                     f"{'retrying...' if _repair_attempt == 0 else 'skipping.'}")
    if delta is None:
        return None
    delta_stories = delta.get("stories") or []
    n_new = sum(len(s.get("test_cases") or []) for s in delta_stories)
    if n_new == 0:
        _log(on_log, "[SUCCESS] Coverage verification found no gaps - "
                     "all requirements covered.")
        return None
    _log(on_log, f"[SUCCESS] Coverage verification found {n_new} additional "
                 f"test case(s) for uncovered requirements.")
    return delta


async def _generate_one(
    client: AnthropicClient,
    primary_model: str,
    system_prompt: str,
    work_item_dump: str,
    kb_context: str,
    on_log: LogFn | None,
    stop_event: threading.Event | None,
    trace: RlmTrace,
    max_repair: int,
    label: str = "",
    cache: GenCache | None = None,
    decomposed_reqs: str = "",
    project_context: str = "",
) -> RlmResult:
    """Generate the payload for one dump, then self-correct: if parsing or
    schema validation fails, the validator's errors are sent back to the
    model to fix, up to max_repair times. Autonomous, bounded, cancelable.

    A validated payload is cached keyed by the exact inputs (system prompt,
    item dump, resolved KB context, model); a cache hit returns instantly
    without an API call. Cached payloads are re-validated before reuse so a
    stale or corrupt entry can never produce an invalid result."""
    tag = f"{label}: " if label else ""
    gen_key = ""
    extra_tag = ""
    if decomposed_reqs:
        import hashlib as _hl
        extra_tag = f"d{_hl.md5(decomposed_reqs.encode()).hexdigest()[:8]}"
    if cache is not None and cache.enabled:
        gen_key = generation_key(
            system_prompt, work_item_dump, kb_context, primary_model,
            extra_tag=extra_tag,
        )
        cached = cache.get(gen_key)
        if isinstance(cached, dict) and validate_payload(cached).ok:
            _log(on_log, f"[INFO] {tag}reusing cached result (no API call).")
            return RlmResult(ok=True, payload=cached, raw_text="",
                             parse_error="", trace=trace)
    user = _build_generation_user(work_item_dump, kb_context, project_context)
    if decomposed_reqs:
        user = (
            user + "\n\nDECOMPOSED REQUIREMENTS (each must be covered by "
            "at least one test case):\n" + decomposed_reqs
        )
    payload: dict[str, Any] | None = None
    text = ""
    err = ""
    for attempt in range(max_repair + 1):
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()
        _tc = time.perf_counter()
        _log(on_log, f"[INFO] {tag}Calling LLM (model={primary_model})...")
        gen = await client.complete_async(
            model=primary_model, system=system_prompt, user=user,
            max_tokens=RLM_GENERATE_MAX_TOKENS, temperature=0.0,
        )
        _log(on_log, f"[INFO] {tag}LLM responded in "
                     f"{time.perf_counter() - _tc:.1f}s "
                     f"(in={gen.usage.input_tokens}, "
                     f"out={gen.usage.output_tokens} tokens).")
        trace.input_tokens += gen.usage.input_tokens
        trace.output_tokens += gen.usage.output_tokens
        text = gen.text
        if gen.stop_reason == "max_tokens":
            _log(on_log,
                 f"[WARN] {tag}hit the output token limit; JSON may be "
                 f"truncated.")
        payload, perr = parse_payload(text)
        if payload is not None:
            # Coerce near-miss categories/priorities to valid values so a
            # stray label (e.g. "Boundary") does not force a repair round.
            normalize_payload(payload)
            rep = validate_payload(payload)
            if rep.ok:
                if attempt:
                    _log(on_log, f"[SUCCESS] {tag}fixed after {attempt} "
                                 f"repair attempt(s).")
                if gen_key and cache is not None:
                    cache.set(gen_key, payload)
                return RlmResult(ok=True, payload=payload, raw_text=text,
                                 parse_error="", trace=trace)
            err = "; ".join(rep.errors[:8]) or "schema validation failed"
        else:
            err = perr
        if attempt < max_repair:
            _log(on_log,
                 f"[WARN] {tag}output invalid ({err}); asking AI to fix "
                 f"(attempt {attempt + 2}/{max_repair + 1})...")
            user = _build_repair_user(text, err)
    _log(on_log, f"[ERROR] {tag}still invalid after {max_repair} repair "
                 f"attempt(s): {err}")
    return RlmResult(ok=False, payload=payload, raw_text=text,
                     parse_error=err, trace=trace)


async def _build_kb_context(
    client: AnthropicClient,
    fast_model: str,
    kb_index: KbIndex,
    work_item_dump: str,
    on_log: LogFn | None,
    trace: RlmTrace,
    stop_event: threading.Event | None,
    cache: GenCache | None = None,
    retriever: Any | None = None,
) -> str:
    """Resolve the requirements context for generation.

    Fast path: if a hybrid retriever (BM25 + optional local dense vectors) is
    available and the KB is large, retrieve the most relevant chunks for the
    work-item dump and assemble them - no LLM round-trips for retrieval. This
    replaces the slow navigate->map traversal while keeping (or improving)
    quality. Falls back to the cached navigate->map path when no retriever is
    available or it returns nothing; small KBs still pass through directly."""
    big_kb = kb_index.total_tokens > RLM_DIRECT_CONTEXT_TOKENS
    if (retriever is not None and big_kb and kb_index.chunks):
        try:
            available = retriever.is_available()
        except Exception as e:
            _logger.debug("retriever.is_available failed: %s", e)
            available = False
        if available:
            try:
                from kb.retrieval import (
                    assemble_context, _decompose_query_heuristic,
                )

                sub_queries = _decompose_query_heuristic(work_item_dump)
                if len(sub_queries) > 1 and hasattr(retriever,
                                                    "multi_query_retrieve"):
                    chunks = retriever.multi_query_retrieve(
                        sub_queries, top_k=_HYBRID_TOP_K,
                        candidate_k=_HYBRID_CANDIDATES,
                    )
                else:
                    chunks = retriever.retrieve(
                        work_item_dump, top_k=_HYBRID_TOP_K,
                        candidate_k=_HYBRID_CANDIDATES,
                    )
                if chunks:
                    budget = int(RLM_DIRECT_CONTEXT_TOKENS * _CHARS_PER_TOKEN)
                    ctx = assemble_context(chunks, budget)
                    if ctx.strip():
                        trace.mode = "hybrid"
                        caps = {}
                        try:
                            caps = retriever.capabilities()
                        except Exception as e:
                            _logger.debug("retriever.capabilities failed: %s", e)
                            caps = {}
                        dense = "dense+lexical" if caps.get("dense") \
                            else "lexical"
                        _log(on_log,
                             f"[INFO] Hybrid retrieval ({dense}) selected "
                             f"{len(chunks)} chunk(s); skipping navigate/map.")
                        return ctx
            except Exception as e:  # noqa: BLE001 - fall back on any error
                _log(on_log,
                     f"[WARN] Hybrid retrieval failed ({e!r}); falling back "
                     f"to navigate/map.")
    if cache is not None and cache.enabled and kb_index.chunks:
        key = context_key(
            kb_fingerprint(kb_index), work_item_dump, fast_model, _BUDGET_TAG
        )
        hit = cache.get(key)
        if isinstance(hit, str):
            trace.mode = "cached"
            _log(on_log,
                 "[INFO] Reusing cached retrieval context (KB unchanged); "
                 "skipping navigate/map.")
            return hit
        ctx = await _resolve_kb_context(
            client, fast_model, kb_index, work_item_dump, on_log, trace,
            stop_event,
        )
        cache.set(key, ctx)
        return ctx
    return await _resolve_kb_context(
        client, fast_model, kb_index, work_item_dump, on_log, trace,
        stop_event,
    )


async def _resolve_kb_context(
    client: AnthropicClient,
    fast_model: str,
    kb_index: KbIndex,
    work_item_dump: str,
    on_log: LogFn | None,
    trace: RlmTrace,
    stop_event: threading.Event | None,
) -> str:
    """Resolve the requirements context once (shared across all work item
    generations): direct pass-through for small KBs, navigate->map for
    large ones, empty when there is no KB."""
    if not kb_index.chunks:
        trace.mode = "no_kb"
        _log(on_log,
             "[WARN] No KB documents for this project; generating from "
             "work items only.")
        return ""
    if kb_index.total_tokens <= RLM_DIRECT_CONTEXT_TOKENS:
        trace.mode = "direct"
        _log(on_log,
             f"[INFO] KB fits in context ({kb_index.total_tokens} tok, "
             f"{kb_index.n_docs} docs); passing it directly.")
        return _assemble_full_kb(kb_index)
    trace.mode = "recursive"
    _log(on_log,
         f"[INFO] KB is large ({kb_index.total_tokens} tok, "
         f"{len(kb_index.chunks)} chunks); using recursive retrieval.")
    wi_summary = _work_item_summary(work_item_dump)
    selected = await _navigate(
        client, fast_model, kb_index, wi_summary, on_log, trace
    )
    trace.selected_chunk_ids = selected
    _log(on_log,
         f"[INFO] Navigator selected {len(selected)} of "
         f"{len(kb_index.chunks)} chunks.")
    if not selected:
        _log(on_log,
             "[WARN] Navigator selected no chunks; generating from work "
             "items only.")
        return ""
    # Map uses medium tier (more complex extraction than navigate)
    map_model = route(Task.MAP_EXTRACT)
    excerpts = await _map_extract(
        client, map_model, kb_index, selected, work_item_dump,
        on_log, trace, stop_event,
    )
    _log(on_log,
         f"[INFO] Extracted relevant context from {trace.map_hits} of "
         f"{len(selected)} chunks.")
    return _assemble_excerpts(excerpts)


# ---------------------------------------------------------------------
# Navigate
# ---------------------------------------------------------------------
def _partition_chunks(
    chunks: list[KbChunk], budget_chars: int
) -> list[list[KbChunk]]:
    groups: list[list[KbChunk]] = []
    cur: list[KbChunk] = []
    cur_len = 0
    for c in chunks:
        line_len = len(c.chunk_id) + len(c.title) + 40
        if cur and cur_len + line_len > budget_chars:
            groups.append(cur)
            cur = [c]
            cur_len = line_len
        else:
            cur.append(c)
            cur_len += line_len
    if cur:
        groups.append(cur)
    return groups


def _listing_for(chunks: list[KbChunk]) -> str:
    lines: list[str] = []
    last_doc = ""
    for c in chunks:
        if c.doc != last_doc:
            lines.append(f"== {c.doc} ==")
            last_doc = c.doc
        lines.append(f"  [{c.chunk_id}] (~{approx_tokens(c.text)} tok) {c.title}")
    return "\n".join(lines)


def _parse_chunk_ids(text: str, valid: set[str]) -> list[str]:
    """Pull chunk ids out of the navigator response, tolerant of stray
    prose or code fences. Order preserved, de-duplicated, validated."""
    found = _CHUNK_ID_RE.findall(text or "")
    seen: set[str] = set()
    out: list[str] = []
    for cid in found:
        if cid in valid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


async def _navigate_one(
    client: AnthropicClient,
    fast_model: str,
    listing: str,
    wi_summary: str,
    valid: set[str],
    trace: RlmTrace,
) -> list[str]:
    user = (
        "WORK ITEMS SUMMARY:\n" + wi_summary
        + "\n\nKNOWLEDGE BASE MAP:\n" + listing
        + "\n\nReturn the JSON array of relevant chunk_ids."
    )
    res = await client.complete_async(
        model=fast_model, system=_NAVIGATE_SYSTEM, user=user,
        max_tokens=RLM_NAVIGATE_MAX_TOKENS, temperature=0.0,
    )
    trace.navigate_calls += 1
    trace.input_tokens += res.usage.input_tokens
    trace.output_tokens += res.usage.output_tokens
    return _parse_chunk_ids(res.text, valid)


async def _navigate(
    client: AnthropicClient,
    fast_model: str,
    index: KbIndex,
    wi_summary: str,
    on_log: LogFn | None,
    trace: RlmTrace,
) -> list[str]:
    valid = {c.chunk_id for c in index.chunks}
    listing = index.map_listing()
    budget_chars = (RLM_DIRECT_CONTEXT_TOKENS * _CHARS_PER_TOKEN) // 2
    selected: list[str] = []
    if len(listing) <= budget_chars:
        selected = await _navigate_one(
            client, fast_model, listing, wi_summary, valid, trace
        )
    else:
        groups = _partition_chunks(index.chunks, budget_chars)
        _log(on_log,
             f"[INFO] KB map is large; navigating {len(groups)} partitions")
        sem = asyncio.Semaphore(_NAV_CONCURRENCY)

        async def _nav_group(g: list[KbChunk]) -> list[str]:
            async with sem:
                return await _navigate_one(
                    client, fast_model, _listing_for(g), wi_summary, valid,
                    trace,
                )

        results = await asyncio.gather(*[_nav_group(g) for g in groups])
        for ids in results:
            selected.extend(ids)
    # De-dupe preserving order, then re-order by index order for stability.
    chosen = set(selected)
    return [c.chunk_id for c in index.chunks if c.chunk_id in chosen]


# ---------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------
async def _map_extract(
    client: AnthropicClient,
    fast_model: str,
    index: KbIndex,
    selected_ids: list[str],
    work_item_dump: str,
    on_log: LogFn | None,
    trace: RlmTrace,
    stop_event: threading.Event | None,
) -> list[tuple[KbChunk, str]]:
    chunks = [index.by_id(cid) for cid in selected_ids]
    chunks = [c for c in chunks if c is not None]
    if not chunks:
        return []
    sem = asyncio.Semaphore(_MAP_CONCURRENCY)
    wi_for_map = _work_item_summary(work_item_dump, max_chars=4000)

    async def _one(order: int, chunk: KbChunk) -> tuple[int, KbChunk, str]:
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()
        async with sem:
            user = (
                "WORK ITEMS:\n" + wi_for_map
                + f"\n\nSECTION [{chunk.chunk_id}] from {chunk.doc} "
                f"({chunk.title}):\n" + chunk.text
            )
            res = await client.complete_async(
                model=fast_model, system=_MAP_SYSTEM, user=user,
                max_tokens=RLM_MAP_MAX_TOKENS, temperature=0.0,
            )
            trace.map_calls += 1
            trace.input_tokens += res.usage.input_tokens
            trace.output_tokens += res.usage.output_tokens
            return order, chunk, res.text.strip()

    tasks = [_one(i, c) for i, c in enumerate(chunks)]
    gathered = await asyncio.gather(*tasks)
    gathered.sort(key=lambda t: t[0])      # stable, completion-order-independent
    excerpts: list[tuple[KbChunk, str]] = []
    for _order, chunk, text in gathered:
        if text and text.strip().upper() != "NONE":
            excerpts.append((chunk, text))
    trace.map_hits = len(excerpts)
    return excerpts


# ---------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------
def parse_payload(text: str) -> tuple[dict[str, Any] | None, str]:
    """Extract and parse the JSON payload from a model response. Tolerant
    of a leading/trailing code fence or stray prose. Returns
    (payload, error_message)."""
    raw = (text or "").strip()
    if not raw:
        return None, "Model returned an empty response."

    candidates: list[str] = []
    m = _JSON_FENCE_RE.search(raw)
    if m:
        candidates.append(m.group(1).strip())
    # Outermost balanced object as a fallback.
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw[start:end + 1])
    candidates.append(raw)

    last_err = ""
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data, ""
            last_err = "Top-level JSON is not an object."
        except json.JSONDecodeError as e:
            last_err = f"JSON parse error: {e}"
    return None, last_err or "Could not locate a JSON object in the response."


# ---------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------
def _log(on_log: LogFn | None, msg: str) -> None:
    if on_log:
        try:
            on_log(msg)
        except Exception as e:
            _logger.debug("on_log callback failed: %s", e)


async def generate_test_cases_rlm_async(
    client: AnthropicClient,
    primary_model: str,
    fast_model: str,
    system_prompt: str,
    kb_index: KbIndex,
    work_item_dump: str,
    per_item_dumps: list[str] | None = None,
    on_log: LogFn | None = None,
    on_progress: ProgressFn | None = None,
    stop_event: threading.Event | None = None,
    max_repair: int = _DEFAULT_MAX_REPAIR,
    cache: GenCache | None = None,
    retriever: Any | None = None,
    enable_decompose: bool = True,
    enable_verify: bool = True,
    project_full: str = "",
) -> RlmResult:
    """Run navigate -> map -> decompose -> generate -> verify.

    The requirements context is resolved once, then test cases are generated
    PER WORK ITEM IN PARALLEL (each a smaller, faster call) and merged.
    After merging, a coverage verification pass identifies and fills gaps.
    Each item's output is validated and, if invalid, automatically sent back
    to the model to fix (bounded by max_repair). Returns the merged payload."""
    fast_model = (fast_model or "").strip() or primary_model
    # Navigate model from router (medium tier - quality gatekeeper)
    nav_model = route(Task.NAVIGATE_CHUNKS)
    trace = RlmTrace(
        kb_docs=kb_index.n_docs,
        kb_chunks=len(kb_index.chunks),
        kb_tokens=kb_index.total_tokens,
    )
    if stop_event is not None and stop_event.is_set():
        raise StopRequested()

    _t0 = time.perf_counter()
    _log(on_log, "[INFO] Retrieving relevant KB context...")
    kb_context = await _build_kb_context(
        client, nav_model, kb_index, work_item_dump, on_log, trace,
        stop_event, cache=cache, retriever=retriever,
    )
    _log(on_log, f"[INFO] KB context resolved ({time.perf_counter() - _t0:.1f}s, "
                 f"{len(kb_context)} chars).")
    if stop_event is not None and stop_event.is_set():
        raise StopRequested()

    # Requirement decomposition: enumerate atomic testable requirements.
    decomposed = ""
    decompose_model = route(Task.DECOMPOSE_REQUIREMENTS)
    if enable_decompose:
        _t1 = time.perf_counter()
        _log(on_log, "[INFO] Decomposing requirements into atomic checklist...")
        decomposed = await _decompose_requirements(
            client, decompose_model, work_item_dump, kb_context, on_log, trace,
            stop_event,
        )
        n_reqs = decomposed.count("\n") + 1 if decomposed.strip() else 0
        _log(on_log, f"[INFO] Decomposition complete ({n_reqs} requirements, "
                     f"{time.perf_counter() - _t1:.1f}s).")

    # Load project context summary (deep domain understanding)
    _proj_ctx = ""
    if project_full:
        try:
            from core.project_store import read_context_summary
            ctx_obj = read_context_summary(project_full)
            if ctx_obj is not None:
                _proj_ctx = ctx_obj.to_prompt_section()
                if _proj_ctx:
                    _log(on_log, "[INFO] Project context summary injected into prompt")
        except Exception as e:
            _logger.debug("read_context_summary failed: %s", e)  # graceful: context summary is optional

    dumps = [d for d in (per_item_dumps or []) if d and d.strip()]
    if not dumps:
        dumps = [work_item_dump]

    if len(dumps) == 1:
        _log(on_log,
             f"[INFO] Generating test cases with model {primary_model}...")
        if on_progress is not None:
            on_progress(0, 1)
        res = await _generate_one(
            client, primary_model, system_prompt, dumps[0], kb_context,
            on_log, stop_event, trace, max_repair, cache=cache,
            decomposed_reqs=decomposed, project_context=_proj_ctx,
        )
        if on_progress is not None:
            on_progress(1, 1)
    else:
        _log(on_log,
             f"[INFO] Generating {len(dumps)} work items in parallel "
             f"(model {primary_model}, up to {_GEN_CONCURRENCY} at a time)...")
        sem = asyncio.Semaphore(_GEN_CONCURRENCY)
        _completed_count = 0

        async def _gen(i: int, dump: str) -> RlmResult:
            nonlocal _completed_count
            async with sem:
                if stop_event is not None and stop_event.is_set():
                    raise StopRequested()
                r = await _generate_one(
                    client, primary_model, system_prompt, dump, kb_context,
                    on_log, stop_event, trace, max_repair,
                    label=f"item {i + 1}/{len(dumps)}", cache=cache,
                    decomposed_reqs=decomposed, project_context=_proj_ctx,
                )
                _completed_count += 1
                if on_progress is not None:
                    on_progress(_completed_count, len(dumps))
                return r

        if on_progress is not None:
            on_progress(0, len(dumps))
        results = await asyncio.gather(
            *[_gen(i, d) for i, d in enumerate(dumps)]
        )
        merged_stories: list[Any] = []
        failed = 0
        for r in results:
            stories = (r.payload or {}).get("stories") or []
            if stories:
                merged_stories.extend(stories)
            if not r.ok:
                failed += 1
        if failed:
            _log(on_log,
                 f"[WARN] {failed} of {len(dumps)} work item(s) did not "
                 f"produce valid JSON and were skipped.")
        merged_payload: dict[str, Any] | None = (
            {"schema_version": 1, "stories": merged_stories}
            if merged_stories else None
        )
        res = RlmResult(
            ok=merged_payload is not None, payload=merged_payload,
            raw_text="", parse_error="" if merged_payload else
            "No work item produced valid JSON.", trace=trace,
        )

    # Coverage verification + gap-fill. Skip for single WI (decomposition
    # already covers requirements exhaustively; verify adds ~35s overhead).
    if enable_verify and len(dumps) > 1 and res.ok and res.payload is not None:
        _log(on_log, "[INFO] Running coverage verification + gap-fill pass...")
        delta = await _verify_and_fill(
            client, primary_model, system_prompt, work_item_dump,
            kb_context, res.payload, on_log, stop_event, trace,
        )
        if delta is not None:
            normalize_payload(delta)
            vr = validate_payload(delta)
            if not vr.ok:
                _log(on_log, f"[WARN] Verify delta failed validation: "
                     f"{vr.errors}; skipping gap-fill merge.")
                delta = None
        if delta is not None:
            for story in (delta.get("stories") or []):
                parent_id = story.get("parent_work_item_id")
                if parent_id is not None:
                    existing = next(
                        (s for s in res.payload["stories"]
                         if s.get("parent_work_item_id") == parent_id), None
                    )
                else:
                    existing = None
                if existing is not None:
                    existing.setdefault("test_cases", []).extend(
                        story.get("test_cases") or []
                    )
                else:
                    res.payload["stories"].append(story)

    n_tcs = sum(
        len(s.get("test_cases") or [])
        for s in (res.payload or {}).get("stories") or []
    )
    _elapsed = time.perf_counter() - _t0
    if res.ok:
        _log(on_log,
             f"[SUCCESS] Produced {n_tcs} test case(s) in {_elapsed:.1f}s "
             f"(in {trace.input_tokens} / out {trace.output_tokens} tokens).")
    else:
        _log(on_log, f"[ERROR] {res.parse_error} ({_elapsed:.1f}s elapsed)")
    if cache is not None and cache.enabled:
        _log(on_log, f"[INFO] {cache.stats()}.")
    return res


