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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from core.app_config import PROJECTS_DIR
from ado.testcase_creator import SYSTEM_PROMPT as DEFAULT_SYSTEM_PROMPT
from kb.store import KbIndex, load_or_build_index
from testgen.tc_types import default_prompt as _default_prompt_for_type
from testgen.tc_types import is_valid as _is_valid_tc_type

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
        """Cached deep-project-understanding summary extracted from the KB
        (see kb.context_summary). Lives under the KB dir alongside the index."""
        return self.kb_dir / "context_summary.json"

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
    except Exception:
        pass
    return None


def _default_prompt(tc_type: str | None) -> str:
    if tc_type and _is_valid_tc_type(tc_type):
        return _default_prompt_for_type(tc_type)
    # Prefer the bundled prompt.md as the generic default when available.
    prompt_md = _load_prompt_md()
    if prompt_md:
        return prompt_md
    return DEFAULT_SYSTEM_PROMPT


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
                DEFAULT_SYSTEM_PROMPT, encoding="utf-8"
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


def list_kb_files(full_name: str) -> list[Path]:
    p = ProjectPaths.for_name(full_name)
    if not p.kb_dir.exists():
        return []
    return [
        f for f in sorted(p.kb_dir.rglob("*"), key=lambda x: str(x).lower())
        if f.is_file() and not f.name.startswith(".")
    ]


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
                                 enable_dense=enable_dense, force=force)
    except Exception as e:  # noqa: BLE001 - hybrid build never blocks
        if on_log is not None:
            try:
                on_log(f"[WARN] Hybrid index build skipped: {e!r}")
            except Exception:
                pass
    # Extract a deep project-understanding summary from the KB when an LLM is
    # available and the KB changed. Injected into generation via
    # rlm.generate_test_cases_rlm(project_full=...). Best-effort: never blocks
    # indexing.
    if llm_client and getattr(index, "chunks", None):
        try:
            _maybe_extract_context(p, index, llm_client, llm_model, on_log)
        except Exception as exc:  # noqa: BLE001
            if on_log:
                try:
                    on_log(f"[WARN] Context extraction skipped: {exc!r}")
                except Exception:
                    pass
    elif not getattr(index, "chunks", None):
        # KB is now empty (every document was deleted). Remove any stale
        # context summary so we don't keep describing deleted content -- the
        # project context must stay in sync with CRUD of KB files.
        try:
            if p.context_summary_path.exists():
                p.context_summary_path.unlink()
                if on_log:
                    on_log("[INFO] KB is empty; cleared project context summary")
        except OSError:
            pass
    return index


def _maybe_extract_context(
    p: "ProjectPaths", index: "Any",
    llm_client: "Any", llm_model: str,
    on_log: "Any | None" = None,
) -> None:
    """Run project context extraction if the KB fingerprint changed. Mirrors
    the desktop hook so the web app produces the same context_summary.json."""
    import asyncio
    import hashlib

    from kb.context_summary import extract_project_context_async, save_context_summary

    h = hashlib.sha256()
    for c in index.chunks:
        h.update((getattr(c, "text", "") or "").encode("utf-8", errors="replace"))
    fp = h.hexdigest()[:16]

    existing_fp = context_summary_fingerprint(p.full_name)
    if fp == existing_fp and existing_fp:
        return  # KB unchanged, skip

    # Prefer the balanced (MEDIUM) tier for structured extraction unless an
    # explicit model was passed in.
    model = llm_model
    if not model:
        try:
            from core.model_router import Task, route
            model = route(Task.MAP_EXTRACT)
        except Exception:  # noqa: BLE001
            model = ""

    ctx = asyncio.run(extract_project_context_async(
        kb_index=index, client=llm_client, model=model,
        kb_fingerprint=fp, on_log=on_log,
    ))
    if not ctx.is_empty():
        save_context_summary(p.context_summary_path, ctx)
        if on_log:
            on_log("[INFO] Project context summary saved")


def _build_hybrid_from_index(
    p: ProjectPaths, index: KbIndex, on_log: "Any | None" = None,
    should_stop: "Any | None" = None, enable_dense: bool = True,
    force: bool = False,
) -> None:
    """Convert KB chunks to the hybrid index. Lexical (BM25) is always built.
    A local dense embedder is constructed ONLY when enable_dense is True (it
    may download a model on first use)."""
    from kb.retrieval import build_hybrid_index, densify_chunks

    coarse = [
        {"chunk_id": c.chunk_id, "doc": c.doc, "title": c.title,
         "text": c.contextualized_text}
        for c in index.chunks
    ]
    if not coarse:
        return
    # Densify: split the coarse RLM chunks into fine retrieval windows so
    # lexical/dense retrieval is precise.
    chunks = densify_chunks(coarse)
    # Dense indexing is ENFORCED by default (TT_ENFORCE_DENSE): we must build
    # dense vectors with the bundled local model and must NOT silently fall back
    # to lexical-only. Enforcement overrides any caller request to disable dense.
    from kb.embeddings import dense_enforced

    enforced = dense_enforced()
    if enforced:
        enable_dense = True
    # Whether dense vectors can actually be added now (cheap import check;
    # does not load the model). If dense is wanted and achievable but the
    # existing index is lexical-only, the currency check below returns False
    # and we rebuild with vectors. When enforced we always want dense.
    want_dense = False
    if enable_dense:
        if enforced:
            want_dense = True
        else:
            try:
                from kb.embeddings import embedding_backend_available

                want_dense = bool(embedding_backend_available())
            except Exception:
                want_dense = False
    # Skip rebuilding when the hybrid index already reflects this chunk set
    # (cheap manifest-only check); avoids redundant CPU on every reselect.
    # A forced rebuild bypasses this shortcut entirely.
    try:
        from kb.retrieval import hybrid_index_is_current

        # Configured embedding identity (cheap constants, no model load) so a
        # model/dim upgrade forces exactly one rebuild.
        want_model = ""
        want_dim = 0
        if want_dense:
            try:
                from core.app_config import EMBED_DIM, EMBED_MODEL

                # The API embedder names itself "api:<model>" (see
                # kb.embeddings._APIEmbedder.name); match that so the manifest
                # comparison in hybrid_index_is_current is consistent.
                want_model = f"api:{EMBED_MODEL}"
                want_dim = int(EMBED_DIM)
            except Exception:
                want_model, want_dim = "", 0

        if not force and hybrid_index_is_current(p.hybrid_dir, len(chunks),
                                                  index.built_at,
                                                  want_dense=want_dense,
                                                  want_model=want_model,
                                                  want_dim=want_dim):
            if on_log is not None:
                try:
                    on_log("[INFO] Hybrid index already current; reused.")
                except Exception:
                    pass
            return
    except Exception:
        pass
    embedder = None
    if enable_dense and enforced:
        # ENFORCED path: verify the dense embedder API backend strictly. Any
        # failure raises and is surfaced as a visible index-job error instead
        # of a silent downgrade to lexical-only. Reranking is a retrieval-time
        # gateway API call (kb.reranker.native_rerank), so nothing to verify
        # here.
        from kb.embeddings import get_text_embedder_strict

        embedder = get_text_embedder_strict()
        if on_log is not None:
            try:
                backend = getattr(embedder, "name", "local")
                on_log(f"[SUCCESS] Dense indexing enforced: embedder "
                       f"({backend}) ready; adding vectors alongside BM25.")
            except Exception:
                pass
    elif enable_dense:
        try:
            from kb.embeddings import get_text_embedder

            embedder = get_text_embedder()
        except Exception:
            embedder = None
        if on_log is not None:
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
                        # Importable but construction failed (e.g. blocked
                        # first-run model download).
                        be = last_build_error() or "model could not be built"
                        on_log("[WARN] Dense backend is installed but could "
                               f"not be initialized: {be}. Using lexical "
                               "retrieval. (A blocked model download is the "
                               "usual cause; pre-bundle the model files.)")
                    else:
                        on_log(f"[INFO] Dense inactive: {reason} Using "
                               "lexical (BM25) retrieval.")
            except Exception:
                pass
    ok = build_hybrid_index(
        p.hybrid_dir, chunks, embedder=embedder, on_log=on_log,
        should_stop=should_stop, enforce_dense=(enable_dense and enforced),
    )
    # When enforced, confirm dense vectors actually landed in the manifest;
    # otherwise raise so the failure is loud rather than a lexical-only index.
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


def kb_dedup_summary(full_name: str) -> "tuple[int, int]":
    """(total_files_in_kb_folder, pdf_twins_skipped). Lets the UI explain why
    the indexed document count is lower than the uploaded file count."""
    from kb.store import _raw_scan, dedup_twins

    p = ProjectPaths.for_name(full_name)
    raw = _raw_scan(p.kb_dir)
    _kept, dropped = dedup_twins(raw)
    return len(raw), len(dropped)


def kb_file_count(full_name: str) -> int:
    """Cheap count of KB source files for a project (lists the kb dir; does
    NOT parse the index). Used to decide the 'no files' footer state."""
    from kb.store import _scan_sources

    try:
        p = ProjectPaths.for_name(full_name)
        return len(_scan_sources(p.kb_dir))
    except Exception:
        return 0


def project_index_status(full_name: str) -> "Any":
    """Cheap status (is_current / n_files / has_partial) for deciding
    whether to kick off indexing."""
    from kb.indexer import index_status

    p = ProjectPaths.for_name(full_name)
    return index_status(p.kb_dir, p.index_path)


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


def context_summary_fingerprint(full_name: str) -> str:
    """Return the KB fingerprint stored in the context summary, or empty
    string if no summary exists. Used to check staleness."""
    ctx = read_context_summary(full_name)
    if ctx is None:
        return ""
    return getattr(ctx, "kb_fingerprint", "")
