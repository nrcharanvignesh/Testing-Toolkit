"""
project_store.py
Per-project workspace under ~/TestingToolkit/projects/<safe_name>/.

Each ADO project gets its own:
    system_prompt.txt   the strict TC-generation system prompt (editable;
                        defaults to the canonical one shipped in
                        ado_testcase_creator.SYSTEM_PROMPT)
    kb/                 drop requirement / spec documents here; these are
                        what the Recursive Language Model navigates
    kb_index.json       cached deterministic chunk index (see kb_store)
    generated/          per-run JSON payloads + review xlsx files

This replaces the old "build an AI Project per client and upload PDFs
to its Knowledge Base" step: the KB now lives locally and is read at
generation time, so no embedding model and no external project setup are
required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

_log = logging.getLogger(__name__)

from core.app_config import PROJECTS_DIR
from kb.store import KbIndex, load_or_build_index
from testgen.tc_types import default_prompt as _default_prompt_for_type
from testgen.tc_types import is_valid as _is_valid_tc_type


def _get_default_system_prompt() -> str:
    # ponytail: lazy import breaks core->ado package cycle; inline if perf matters
    from ado.testcase_creator import SYSTEM_PROMPT
    return SYSTEM_PROMPT

_BAD_CHARS: Final[str] = '<>:"/\\|?*'


def _safe_name(full_name: str) -> str:
    cleaned = "".join("_" if c in _BAD_CHARS or ord(c) < 32 else c
                      for c in full_name).strip(". ")
    return cleaned[:120] or "project"


@dataclass(slots=True)
class ProjectPaths:
    full_name: str
    root: Path
    kb_dir: Path
    index_path: Path
    system_prompt_path: Path
    generated_dir: Path
    templates_dir: Path
    cache_dir: Path
    hybrid_dir: Path

    @classmethod
    def for_name(cls, full_name: str) -> "ProjectPaths":
        root = PROJECTS_DIR / _safe_name(full_name)
        generated = root / "generated"
        return cls(
            full_name=full_name,
            root=root,
            kb_dir=root / "kb",
            index_path=root / "kb_index.json",
            system_prompt_path=root / "system_prompt.txt",
            generated_dir=generated,
            templates_dir=root / "templates",
            cache_dir=generated / ".cache",
            hybrid_dir=root / "hybrid_index",
        )

    @property
    def context_summary_path(self) -> Path:
        """Cached aggregate project context."""
        return self.kb_dir / "context_summary.json"

    @property
    def context_maps_dir(self) -> Path:
        """Atomic per-document context-map checkpoints."""
        return self.kb_dir / ".context_maps"

    def prompt_path(self, tc_type: str | None) -> Path:
        """Per-phase prompt file when tc_type is given, else the legacy
        generic prompt file."""
        if tc_type and _is_valid_tc_type(tc_type):
            return self.root / f"system_prompt_{tc_type}.txt"
        return self.system_prompt_path

    def template_spec_path(self, tc_type: str) -> Path:
        return self.templates_dir / f"template_{tc_type}.spec.json"

    def find_template(self, tc_type: str) -> Path | None:
        """Locate the stored template workbook for a phase (suffix is
        preserved on upload, so glob for any extension)."""
        if not self.templates_dir.exists():
            return None
        matches = sorted(self.templates_dir.glob(f"template_{tc_type}.*"))
        for m in matches:
            if m.suffix.lower() != ".json" and m.is_file():
                return m
        return None


def _load_prompt_md() -> str | None:
    """Load the bundled prompt.md if present alongside the package. Returns the
    content string, or None if missing/empty."""
    try:
        prompt_path = Path(__file__).resolve().parent.parent / "prompt.md"
        if prompt_path.is_file():
            text = prompt_path.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
            if text:
                return text
    except Exception as e:
        _log.debug("_load_prompt_md read failed: %s", e)
    return None


def _default_prompt(tc_type: str | None) -> str:
    if tc_type and _is_valid_tc_type(tc_type):
        return _default_prompt_for_type(tc_type)
    # Prefer the bundled prompt.md as the generic default when available.
    prompt_md = _load_prompt_md()
    if prompt_md:
        return prompt_md
    return _get_default_system_prompt()


def ensure_project(full_name: str) -> ProjectPaths:
    """Create the project folder skeleton and seed the default prompts on
    first use. Seeds the legacy generic prompt plus one prompt per phase
    (Implementation / SIT / UAT). Idempotent."""
    from testgen.tc_types import TC_TYPES

    p = ProjectPaths.for_name(full_name)
    for d in (p.root, p.kb_dir, p.generated_dir, p.templates_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    if not p.system_prompt_path.exists():
        try:
            p.system_prompt_path.write_text(
                _get_default_system_prompt(), encoding="utf-8"
            )
        except OSError:
            pass
    for tc_type in TC_TYPES:
        path = p.prompt_path(tc_type)
        if not path.exists():
            try:
                path.write_text(_default_prompt(tc_type), encoding="utf-8")
            except OSError:
                pass
    return p


def read_system_prompt(full_name: str, tc_type: str | None = None) -> str:
    """Read the editable system prompt. With tc_type, returns that phase's
    prompt (seeded from the phase default if absent); without, the legacy
    generic prompt."""
    p = ProjectPaths.for_name(full_name)
    path = p.prompt_path(tc_type)
    try:
        if path.exists():
            txt = path.read_text(encoding="utf-8", errors="replace").strip()
            if txt:
                return txt
    except OSError:
        pass
    return _default_prompt(tc_type)


def write_system_prompt(
    full_name: str, text: str, tc_type: str | None = None
) -> bool:
    p = ensure_project(full_name)
    try:
        p.prompt_path(tc_type).write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


def reset_system_prompt(full_name: str, tc_type: str | None = None) -> bool:
    return write_system_prompt(full_name, _default_prompt(tc_type), tc_type)


def get_index(full_name: str) -> KbIndex:
    """Load (or rebuild if stale) the project's KB chunk index."""
    p = ensure_project(full_name)
    return load_or_build_index(p.kb_dir, p.index_path)


def index_project_resumable(
    full_name: str,
    on_progress: "Any | None" = None,
    on_log: "Any | None" = None,
    should_stop: "Any | None" = None,
    enable_dense: bool = True,
    on_sub_progress: "Any | None" = None,
    llm_client: "Any | None" = None,
    llm_model: str = "",
    force: bool = False,
    build_context: bool = True,
) -> KbIndex:
    """Build/refresh this project's KB index incrementally and resumably
    (see kb_indexer), then build the hybrid retrieval index (BM25 always;
    local dense vectors added when enable_dense is True AND a local
    embedding backend is available) from the same chunks. Safe to call at
    startup and whenever KB files change; a no-op when nothing has changed.

    enable_dense defaults to True. It is safe: if no local embedding backend
    is installed, dense degrades automatically to lexical (BM25) - which
    needs no model or network - and never blocks. Turn it off only if a
    restricted network makes a first-run model download hang.

    llm_client + llm_model: when provided, contextual retrieval (LLM-generated
    situating prefixes) is applied to each chunk at
    index time. This improves retrieval accuracy by 49-67%. The fast model
    (e.g. Haiku) is recommended for cost efficiency.

    on_sub_progress(phase, current, total) is forwarded to multimedia
    extractors for within-file granular progress (e.g. transcription seconds)."""
    from kb.indexer import build_index_resumable

    p = ensure_project(full_name)
    index = build_index_resumable(
        p.kb_dir, p.index_path, on_progress=on_progress, on_log=on_log,
        should_stop=should_stop, on_sub_progress=on_sub_progress,
        llm_client=llm_client, llm_model=llm_model, force=force,
    )
    if should_stop is not None and should_stop():
        return index
    try:
        _build_hybrid_from_index(p, index, on_log=on_log,
                                 should_stop=should_stop,
                                 enable_dense=enable_dense, force=force,
                                 on_progress=on_progress)
    except Exception as e:  # noqa: BLE001 - hybrid build never blocks
        if on_log is not None:
            try:
                on_log(f"[WARN] Hybrid index build skipped: {e!r}")
            except Exception as cb_e:
                _log.debug("on_log callback failed (hybrid build warn): %s", cb_e)
    # Extract a deep project-understanding summary from the KB when an LLM is
    # available and the KB changed. Injected into generation via
    # rlm.generate_test_cases_rlm(project_full=...). Best-effort: never blocks
    # indexing.
    if build_context and llm_client and getattr(index, "chunks", None):
        try:
            _maybe_extract_context(
                p, index, llm_client, llm_model, on_log,
                on_sub_progress=on_sub_progress, force=force,
            )
        except Exception as exc:  # noqa: BLE001
            if on_log:
                try:
                    on_log(f"[WARN] Context extraction skipped: {exc!r}")
                except Exception as cb_e:
                    _log.debug("on_log callback failed (context extraction warn): %s", cb_e)
    elif not getattr(index, "chunks", None):
        # KB is now empty (every document was deleted). Remove any stale
        # context summary so we don't keep describing deleted content -- the
        # project context must stay in sync with CRUD of KB files.
        try:
            p.context_summary_path.unlink(missing_ok=True)
            if p.context_maps_dir.exists():
                for context_map in p.context_maps_dir.glob("*.json"):
                    context_map.unlink(missing_ok=True)
                p.context_maps_dir.rmdir()
            if on_log:
                on_log("[INFO] KB is empty; cleared project context summary and maps")
        except OSError:
            pass
    return index


def extract_project_context(
    full_name: str, index: "Any", llm_client: "Any", llm_model: str,
    on_log: "Any | None" = None, on_progress: "Any | None" = None,
    force: bool = False,
) -> None:
    """Build project context for an already-usable index."""
    _maybe_extract_context(
        ensure_project(full_name), index, llm_client, llm_model,
        on_log=on_log, on_sub_progress=on_progress, force=force,
    )


def _maybe_extract_context(
    p: "ProjectPaths", index: "Any", llm_client: "Any", llm_model: str,
    on_log: "Any | None" = None, on_sub_progress: "Any | None" = None,
    force: bool = False,
) -> None:
    """Map changed documents, checkpoint them, then merge the aggregate."""
    import asyncio
    import hashlib

    from kb.context_summary import (
        build_context_incremental_async,
        load_context_summary,
        save_context_summary,
    )

    h = hashlib.sha256()
    for chunk in index.chunks:
        h.update((getattr(chunk, "text", "") or "").encode("utf-8", errors="replace"))
    fingerprint = h.hexdigest()[:16]
    if not force and fingerprint == context_summary_fingerprint(p.full_name):
        existing = load_context_summary(p.context_summary_path)
        if existing is None or existing.status != "partial":
            return
    model = llm_model
    if not model:
        from core.model_router import Task, route
        model = route(Task.MAP_EXTRACT)

    # Resolve the frontier model for oversized-document escalation
    large_model = ""
    try:
        from core.app_config import MODEL_LARGE
        if MODEL_LARGE and MODEL_LARGE != model:
            large_model = MODEL_LARGE
    except Exception:
        pass

    previous = load_context_summary(p.context_summary_path)
    context = asyncio.run(build_context_incremental_async(
        kb_index=index, client=llm_client, model=model,
        maps_dir=p.context_maps_dir, kb_fingerprint=fingerprint,
        on_log=on_log, on_progress=on_sub_progress, force=force,
        large_model=large_model,
    ))
    if previous is not None:
        context.enabled = previous.enabled
    # A partial aggregate is useful and must not fail the KB lifecycle. Preserve
    # the previous complete summary only when this run mapped nothing at all;
    # successful per-document maps remain checkpointed for the next retry.
    if context.mapped_documents == 0 and previous is not None and not previous.is_empty():
        if on_log:
            on_log(
                "[WARN] Context mapped 0 documents; previous summary preserved "
                "and indexing remains usable"
            )
        return
    if not context.is_empty() and save_context_summary(p.context_summary_path, context):
        if on_log:
            level = "WARN" if context.status == "partial" else "INFO"
            on_log(
                f"[{level}] Project context {context.status} summary saved "
                f"atomically ({context.mapped_documents}/"
                f"{context.total_documents} documents)"
            )


def _resolve_dense_flags(enable_dense: bool) -> tuple[bool, bool, bool]:
    """Return (enable_dense, enforced, want_dense) after applying TT_ENFORCE_DENSE."""
    from kb.embeddings import dense_enforced

    enforced = dense_enforced()
    if enforced:
        enable_dense = True
    want_dense = False
    if enable_dense:
        if enforced:
            want_dense = True
        else:
            try:
                from kb.embeddings import embedding_backend_available
                want_dense = bool(embedding_backend_available())
            except Exception as e:
                _log.debug("embedding_backend_available check failed: %s", e)
    return enable_dense, enforced, want_dense


def _hybrid_is_current(
    p: ProjectPaths, n_chunks: int, built_at: "Any",
    want_dense: bool, force: bool,
) -> bool:
    """Cheap manifest check: skip rebuilding when the index already reflects
    the current chunk set."""
    if force:
        return False
    try:
        from kb.retrieval import hybrid_index_is_current

        want_model, want_dim = "", 0
        if want_dense:
            try:
                from core.app_config import EMBED_DIM, EMBED_MODEL
                want_model = f"api:{EMBED_MODEL}"
                want_dim = int(EMBED_DIM)
            except Exception as e:
                _log.debug("EMBED_DIM/EMBED_MODEL config read failed: %s", e)
        return hybrid_index_is_current(
            p.hybrid_dir, n_chunks, built_at,
            want_dense=want_dense, want_model=want_model, want_dim=want_dim,
        )
    except Exception as e:
        _log.debug("_hybrid_is_current check failed: %s", e)
        return False


def _resolve_embedder(
    enable_dense: bool, enforced: bool, on_log: "Any | None",
) -> "Any | None":
    """Construct the embedder (strict when enforced, best-effort otherwise)."""
    if enable_dense and enforced:
        from kb.embeddings import get_text_embedder_strict
        embedder = get_text_embedder_strict()
        if on_log:
            try:
                backend = getattr(embedder, "name", "local")
                on_log(f"[SUCCESS] Dense indexing enforced: embedder "
                       f"({backend}) ready; adding vectors alongside BM25.")
            except Exception as e:
                _log.debug("on_log callback failed (enforced embedder ready): %s", e)
        return embedder
    if enable_dense:
        try:
            from kb.embeddings import get_text_embedder
            embedder = get_text_embedder()
        except Exception as e:
            _log.debug("get_text_embedder failed: %s", e)
            embedder = None
        if on_log:
            try:
                if embedder is not None:
                    backend = getattr(embedder, "name", "local")
                    on_log(f"[SUCCESS] Dense embedding enabled ({backend}); "
                           f"adding vectors alongside BM25.")
                else:
                    from kb.embeddings import (
                        embedding_backend_status,
                        last_build_error,
                    )
                    avail, reason = embedding_backend_status()
                    if avail:
                        be = last_build_error() or "model could not be built"
                        on_log("[WARN] Dense backend is installed but could "
                               f"not be initialized: {be}. Using lexical "
                               "retrieval. (A blocked model download is the "
                               "usual cause; pre-bundle the model files.)")
                    else:
                        on_log(f"[INFO] Dense inactive: {reason} Using "
                               "lexical (BM25) retrieval.")
            except Exception as e:
                _log.debug("on_log callback failed (embedder status): %s", e)
        return embedder
    return None


def _build_hybrid_from_index(
    p: ProjectPaths, index: KbIndex, on_log: "Any | None" = None,
    should_stop: "Any | None" = None, enable_dense: bool = True,
    force: bool = False, on_progress: "Any | None" = None,
) -> None:
    """Convert KB chunks to the hybrid index. Lexical (BM25) is always built.
    A local dense embedder is constructed ONLY when enable_dense is True."""
    from kb.retrieval import build_hybrid_index, densify_chunks

    coarse = [
        {"chunk_id": c.chunk_id, "doc": c.doc, "title": c.title,
         "text": c.contextualized_text, "source_path": c.source_path,
         "section_path": c.section_path,
         "document_role": c.document_role,
         "source_priority": c.source_priority}
        for c in index.chunks
    ]
    if not coarse:
        return
    chunks = densify_chunks(coarse, p.root)

    enable_dense, enforced, want_dense = _resolve_dense_flags(enable_dense)

    if _hybrid_is_current(p, len(chunks), index.built_at, want_dense, force):
        if on_log:
            try:
                on_log("[INFO] Hybrid index already current; reused.")
            except Exception as e:
                _log.debug("on_log callback failed (hybrid current): %s", e)
        return

    embedder = _resolve_embedder(enable_dense, enforced, on_log)

    # Bridge: on_progress from callers uses (done, total, elapsed, name="")
    # but build_hybrid_index emits (stage, current, total). Adapt here.
    def _hybrid_progress(stage: str, current: int, total: int) -> None:
        if on_progress is not None:
            try:
                on_progress(current, total, 0.0, stage)
            except Exception:
                pass

    ok = build_hybrid_index(
        p.hybrid_dir, chunks, embedder=embedder, on_log=on_log,
        should_stop=should_stop, enforce_dense=(enable_dense and enforced),
        on_progress=_hybrid_progress,
    )
    if not ok and not (should_stop and should_stop()):
        raise RuntimeError("Hybrid KB generation could not be published")
    if ok and enable_dense and enforced and not (should_stop and should_stop()):
        try:
            from kb.retrieval import hybrid_has_dense
            if not hybrid_has_dense(p.hybrid_dir):
                raise RuntimeError(
                    "Dense indexing is enforced but no dense vectors were "
                    "written. The bundled embedding model may be missing; "
                    "reinstall the agent (or set TT_ENFORCE_DENSE=0 to allow "
                    "lexical-only retrieval)."
                )
        except ImportError:
            pass


def open_project_retriever(full_name: str) -> "Any | None":
    """Open the project's hybrid retriever for fast local retrieval, or None
    if no hybrid index has been built yet (callers fall back to the RLM
    navigate/map path)."""
    from kb.retrieval import open_retriever

    p = ProjectPaths.for_name(full_name)
    return open_retriever(p.hybrid_dir)


def save_template(
    full_name: str, tc_type: str, src_xlsx: Path | str,
    llm_mapping: dict[str, int] | None = None,
    llm_header_row: int | None = None,
) -> tuple[Path, "object"]:
    """Store the uploaded client template for a phase (preserving its
    original extension) and analyze it once into a reusable spec. Returns
    (stored_template_path, TemplateSpec).

    When llm_mapping is provided (from LLM template analysis), it overrides
    the heuristic column detection. llm_header_row overrides header row
    detection when provided.

    Raises ValueError for an invalid phase. Any older stored template for
    the phase (with a different extension) is removed so find_template stays
    unambiguous."""
    import shutil

    from testgen.testcase_template import analyze_and_save

    if not _is_valid_tc_type(tc_type):
        raise ValueError(f"invalid tc_type: {tc_type!r}")
    p = ensure_project(full_name)
    src = Path(src_xlsx)
    suffix = src.suffix.lower() or ".xlsx"
    # Remove any previous template for this phase (any extension).
    existing = p.find_template(tc_type)
    if existing is not None and existing.exists():
        try:
            existing.unlink()
        except OSError:
            pass
    dest = p.templates_dir / f"template_{tc_type}{suffix}"
    shutil.copyfile(str(src), str(dest))
    spec = analyze_and_save(
        dest, p.template_spec_path(tc_type),
        llm_mapping=llm_mapping, llm_header_row=llm_header_row,
    )
    return dest, spec


def get_template(
    full_name: str, tc_type: str
) -> tuple[Path | None, "object | None"]:
    """Return (template_path, TemplateSpec) for a phase, or (None, None) if
    no template was uploaded."""
    from testgen.testcase_template import load_spec

    if not _is_valid_tc_type(tc_type):
        return None, None
    p = ProjectPaths.for_name(full_name)
    tpl = p.find_template(tc_type)
    if tpl is None:
        return None, None
    spec = load_spec(p.template_spec_path(tc_type))
    return tpl, spec


def has_template(full_name: str, tc_type: str) -> bool:
    p = ProjectPaths.for_name(full_name)
    return _is_valid_tc_type(tc_type) and p.find_template(tc_type) is not None


# -----------------------------------------------------------------
# Context summary (deep project understanding from KB)
# -----------------------------------------------------------------

def read_context_summary(full_name: str) -> "Any | None":
    """Load the project's context summary if it exists."""
    from kb.context_summary import load_context_summary
    p = ProjectPaths.for_name(full_name)
    return load_context_summary(p.context_summary_path)


def write_context_summary(full_name: str, ctx: "Any") -> bool:
    """Persist a ProjectContext to disk."""
    from kb.context_summary import save_context_summary
    p = ensure_project(full_name)
    return save_context_summary(p.context_summary_path, ctx)


def clear_context_summary(full_name: str) -> bool:
    """Delete the project's stored context summary. Returns True if removed."""
    p = ProjectPaths.for_name(full_name)
    try:
        existed = p.context_summary_path.exists()
        p.context_summary_path.unlink(missing_ok=True)
        return existed
    except OSError:
        return False


def clear_kb(full_name: str, *, keep_documents: bool = False) -> dict:
    """Force-wipe a project's knowledge base regardless of any in-progress work.

    Removes the retrieval index (kb_index.json), the dense/hybrid vector store
    (hybrid_index/), the aggregate project-context summary, and the per-document
    context-map checkpoints (.context_maps). When ``keep_documents`` is False
    (the default) the uploaded source documents in kb/ are deleted too, leaving a
    clean empty knowledge base. Every removal is best-effort so a locked file can
    never leave the KB half-cleared — it deletes as much as it can and reports
    what was removed. This is a destructive "force clear" and does NOT rely on a
    graceful reindex.
    """
    import shutil

    p = ProjectPaths.for_name(full_name)
    removed: list[str] = []

    def _rm_file(path: Path, label: str) -> None:
        try:
            if path.exists():
                path.unlink(missing_ok=True)
                removed.append(label)
        except OSError:
            pass

    def _rm_tree(path: Path, label: str) -> None:
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                removed.append(label)
        except OSError:
            pass

    # 1) Retrieval index + dense vector store.
    _rm_file(p.index_path, "index")
    _rm_tree(p.hybrid_dir, "vectors")
    # 2) Project context summary + per-document map checkpoints.
    _rm_file(p.context_summary_path, "context-summary")
    _rm_tree(p.context_maps_dir, "context-maps")
    # 3) Uploaded source documents (unless the caller wants to keep them).
    if not keep_documents and p.kb_dir.exists():
        doc_count = 0
        for entry in list(p.kb_dir.iterdir()):
            try:
                if entry.is_file():
                    entry.unlink(missing_ok=True)
                    doc_count += 1
                elif entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                pass
        if doc_count:
            removed.append(f"{doc_count} document(s)")
    # Ensure an empty kb/ folder remains so future uploads/indexing work.
    try:
        p.kb_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    return {"cleared": removed, "kept_documents": keep_documents}


def context_summary_fingerprint(full_name: str) -> str:
    """Return the KB fingerprint stored in the context summary, or empty
    string if no summary exists. Used to check staleness."""
    ctx = read_context_summary(full_name)
    if ctx is None:
        return ""
    return getattr(ctx, "kb_fingerprint", "")
