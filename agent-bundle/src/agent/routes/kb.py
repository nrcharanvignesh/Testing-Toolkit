"""KB endpoints - indexing, retrieval, embedding, reranking."""

from __future__ import annotations

import logging
import asyncio
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.app_config import PROJECTS_DIR
from core.trace import trace
from agent.jobs import Job, spawn_job_task

router = APIRouter()
_log = logging.getLogger(__name__)

_MAX_KB_UPLOAD_BYTES = 250 * 1024 * 1024
_MAX_TEMPLATE_UPLOAD_BYTES = 25 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _copy_upload_limited(file_obj: Any, destination: Path, limit_bytes: int) -> int:
    """Stream an upload to a temporary file while enforcing a hard byte ceiling."""
    written = 0
    with destination.open("wb") as output:
        while True:
            chunk = file_obj.read(_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > limit_bytes:
                raise ValueError(f"Upload exceeds the {limit_bytes // (1024 * 1024)} MB limit")
            output.write(chunk)
    return written


class RetrieveRequest(BaseModel):
    project: str
    query: str
    top_k: int = 32


class RetrieveResponse(BaseModel):
    chunks: list[dict[str, Any]]


@router.post("/retrieve", response_model=RetrieveResponse)
@trace
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    """Run hybrid BM25 + dense + reranker search on the project KB."""
    from kb.retrieval import HybridRetriever

    project_dir = PROJECTS_DIR / req.project
    if not project_dir.exists():
        raise HTTPException(404, f"Project '{req.project}' not found locally")

    retriever = HybridRetriever(project_dir)
    if not retriever.is_available():
        raise HTTPException(
            409, "KB index not built yet. Upload documents and trigger indexing first."
        )

    results = await asyncio.to_thread(retriever.retrieve, req.query, req.top_k)
    return RetrieveResponse(
        chunks=[
            {
                "chunk_id": r.chunk_id,
                "doc": r.doc,
                "title": r.title,
                "text": r.text,
                "score": r.score,
            }
            for r in results
        ]
    )


class EmbedRequest(BaseModel):
    texts: list[str]


@router.post("/embed")
@trace
async def embed(req: EmbedRequest) -> dict:
    """Embed texts using the local ONNX model."""
    from agent.model_loader import get_cached_embedder

    embedder = get_cached_embedder()
    if embedder is None:
        raise HTTPException(503, "Embedding model not available")

    vectors = await asyncio.to_thread(embedder.embed, req.texts)
    return {"vectors": [v.tolist() for v in vectors]}


class RerankRequest(BaseModel):
    query: str
    candidates: list[str]
    top_k: int = 32


@router.post("/rerank")
@trace
async def rerank(req: RerankRequest) -> dict:
    """Rerank candidates using the local cross-encoder model."""
    from agent.model_loader import get_cached_reranker

    reranker = get_cached_reranker()
    if reranker is None:
        raise HTTPException(503, "Reranker model not available")

    ranked = await asyncio.to_thread(
        reranker.rerank, req.query, req.candidates, req.top_k
    )
    return {"ranked": ranked}


class IndexRequest(BaseModel):
    project: str
    # When True, ignore the "index is current" shortcut and rebuild the whole
    # KB from scratch (full re-extraction + BM25 + dense). Used by the explicit
    # "Rebuild KB index" button and the post-reinstall reindex-all flow.
    force: bool = False


def _job_callbacks(job: "Job") -> tuple["Any", "Any", "Any"]:
    """Standard (on_log, on_sub_progress, should_stop) adapters for a Job."""
    def _on_log(msg: str) -> None:
        if msg:
            job.log(msg)

    def _on_sub_progress(phase: str, current: int, total: int) -> None:
        job.set_progress(phase.lower().replace(" ", "-"), int(current), int(total))

    def _should_stop() -> bool:
        return job.stopped

    return _on_log, _on_sub_progress, _should_stop


def _fmt_duration(secs: float) -> str:
    s = int(max(0.0, secs))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


def _run_kb_index(job: "Job", project: str, force: bool = False) -> None:
    """Worker body: build/refresh the resumable KB index while streaming
    per-file progress and log lines into the Job."""
    import time as _time
    import core.project_store as ps

    start = _time.monotonic()
    _on_log, _on_sub_progress, _should_stop = _job_callbacks(job)

    def _on_progress(done: int, total: int, _elapsed: float, name: str = "") -> None:
        job.set_progress(name or "indexing", int(done), int(total))
        if total > 0 and 0 < done < total:
            elapsed = _time.monotonic() - start
            pct = int(round(100.0 * done / max(total, 1)))
            if done > 0:
                remaining = elapsed / done * (total - done)
                timing = f"{_fmt_duration(elapsed)} / {_fmt_duration(remaining)} - {pct}%"
            else:
                timing = f"{_fmt_duration(elapsed)} / -- - {pct}%"
            label = f" ({name})" if name else ""
            job.log(f"[INFO] KB indexing {done}/{total}{label} | {timing}")

    # Build an LLM client for contextual retrieval (situating prefixes), using
    # the fast model for cost efficiency. Degrades gracefully if unavailable.
    ctx_client = None
    ctx_model = ""
    try:
        from core.model_router import Task, route
        from core.settings_store import build_llm_client
        ctx_client = build_llm_client()
        ctx_model = route(Task.CONTEXTUALIZE_CHUNK)
    except Exception as e:
        _log.debug("build_llm_client failed: %s", e)
        ctx_client = None
        ctx_model = ""

    # Stream the agent's full internal DEBUG logging (extraction, embedding,
    # chunking, HTTP retries) into the job log so KB indexing is verbose.
    from core.app_logging import stream_agent_logs

    _log_bridge = stream_agent_logs(_on_log)
    _log_bridge.__enter__()
    try:
        if force:
            job.log("[INFO] Full rebuild requested; ignoring cached index.")
        context_job_id = None
        if ctx_client is not None:
            context_job_id = _start_context_job(
                project, force=force, client=ctx_client, model=ctx_model,
            )
            job.log(
                f"[INFO] Project context pipeline started alongside indexing "
                f"as job {context_job_id}."
            )
        job.log(f"[INFO] KB indexing started for '{project}'.")
        result = ps.index_project_resumable(
            project,
            on_progress=_on_progress,
            on_log=_on_log,
            should_stop=_should_stop,
            enable_dense=True,
            llm_client=ctx_client,
            llm_model=ctx_model,
            on_sub_progress=_on_sub_progress,
            force=force,
            build_context=False,
        )
        docs = int(getattr(result, "n_docs", 0) or 0)
        chunks = len(getattr(result, "chunks", []) or [])
        # Report dense status from the actual built hybrid manifest rather than
        # an attribute the index object does not carry.
        has_dense = False
        try:
            from kb.retrieval import hybrid_has_dense

            has_dense = bool(hybrid_has_dense(ps.ensure_project(project).hybrid_dir))
        except Exception as e:
            _log.debug("hybrid_has_dense check failed: %s", e)
            has_dense = False
        job.finish({
            "n_documents": docs,
            "n_chunks": chunks,
            "has_dense": has_dense,
            "context_job_id": context_job_id,
        })
        if chunks > 0:
            job.log(
                f"[SUCCESS] KB indexing finished: {docs} doc(s), "
                f"{chunks} chunk(s); ready for generation."
            )
        else:
            job.log("[INFO] KB indexing finished: no indexable content.")
    except Exception as e:  # noqa: BLE001
        job.fail(f"{type(e).__name__}: {e}")
        job.log(f"[ERROR] KB indexing did not finish: {job.error}")
    finally:
        _log_bridge.__exit__()


def _run_context_job(
    job: "Job", project: str, force: bool = False,
    client: Any | None = None, model: str = "",
) -> None:
    """Map project context independently so retrieval is never blocked."""
    import core.project_store as ps

    _log, _, _ = _job_callbacks(job)

    def _progress(phase: str, current: int, total: int) -> None:
        job.set_progress(phase, int(current), int(total))
        if total > 0:
            job.log(
                f"[INFO] Project context {phase}: {current}/{total} "
                f"({int(100 * current / total)}%)"
            )

    try:
        if client is None:
            from core.model_router import Task, route
            from core.settings_store import build_llm_client
            client = build_llm_client()
            model = route(Task.CONTEXTUALIZE_CHUNK)
        import time
        from agent.jobs import JOBS

        job.log(f"[INFO] Project context mapping started for '{project}'.")
        # Map the last complete index immediately while extraction works on file
        # deltas. Then wait for indexing to atomically publish its final index and
        # run once more; unchanged document maps are cache hits.
        try:
            initial = ps.get_index(project)
        except Exception as e:
            _log.debug("get_index failed: %s", e)
            initial = None
        if initial is not None and getattr(initial, "chunks", None):
            ps.extract_project_context(
                project, initial, client, model,
                on_log=_log, on_progress=_progress, force=False,
            )

        # Wait for the index job to finish, capped at 30 minutes.
        # Signal the waiting phase so the UI does not show stale progress.
        job.set_progress("waiting-for-index", 0, 0)
        job.log("[INFO] Waiting for KB indexing to complete before final context pass...")
        _wait_deadline = time.time() + 30 * 60
        while JOBS.find_active("kb_index", project) is not None:
            if job.stopped:
                job.fail("Project context mapping stopped")
                return
            if time.time() > _wait_deadline:
                job.log("[WARN] Timed out waiting for KB index (30 min cap).")
                break
            time.sleep(0.25)

        final_index = ps.get_index(project)
        if not getattr(final_index, "chunks", None):
            job.finish({
                "mapped_documents": 0,
                "total_documents": 0,
                "status": "unavailable",
            })
            job.log("[INFO] Project context skipped: KB has no indexable content.")
            return
        if job.stopped:
            job.fail("Project context mapping stopped")
            return
        ps.extract_project_context(
            project, final_index, client, model,
            on_log=_log, on_progress=_progress, force=force,
        )
        if job.stopped:
            job.fail("Project context mapping stopped after extraction")
            return
        context = ps.read_context_summary(project)
        if context is None:
            raise RuntimeError("project context summary is unavailable")
        job.finish({
            "mapped_documents": context.mapped_documents,
            "total_documents": context.total_documents,
            "failed_documents": context.failed_documents,
            "status": context.status,
        })
        level = "WARN" if context.status == "partial" else "SUCCESS"
        job.log(
            f"[{level}] Project context {context.status}: "
            f"{context.mapped_documents}/{context.total_documents} document(s), "
            f"{len(context.failed_documents)} unavailable."
        )
    except Exception as exc:  # noqa: BLE001
        job.fail(f"{type(exc).__name__}: {exc}")
        job.log(f"[ERROR] Project context did not finish: {job.error}")


# ---------------------------------------------------------------------------
# Selective context update: debounced per-file context refresh
# ---------------------------------------------------------------------------

_CONTEXT_DEBOUNCE: dict[str, "threading.Timer"] = {}
_CONTEXT_DEBOUNCE_SECS = 5.0  # batch CRUD within 5s window


def _schedule_selective_context(project: str, filename: str) -> None:
    """Debounced trigger: schedules a context job 5s after the last file change.

    Multiple rapid uploads batch into one context run (the incremental cache
    ensures only changed files are re-mapped).
    """
    import threading

    key = project
    existing = _CONTEXT_DEBOUNCE.get(key)
    if existing is not None:
        existing.cancel()

    def _fire() -> None:
        _CONTEXT_DEBOUNCE.pop(key, None)
        _log.info("[INFO] Auto-context triggered for %s (file: %s)", project, filename)
        _start_context_job(project, force=False)

    timer = threading.Timer(_CONTEXT_DEBOUNCE_SECS, _fire)
    timer.daemon = True
    _CONTEXT_DEBOUNCE[key] = timer
    timer.start()


def _invalidate_context_map(project: str, filename: str) -> None:
    """Remove cached context maps for a deleted file.

    The context map signature is sha256(schema_version + source + text), so we
    cannot reconstruct it without the file content. Instead, load each cached map
    and remove those whose source matches the deleted filename.
    """
    import json as _json
    import core.project_store as ps

    try:
        p = ps.ensure_project(project)
        maps_dir = p.context_maps_dir
        if not maps_dir.exists():
            return
        for cached in maps_dir.glob("*.json"):
            try:
                data = _json.loads(cached.read_text(encoding="utf-8"))
                sources = data.get("sources") or []
                if filename in sources or any(filename in s for s in sources):
                    cached.unlink(missing_ok=True)
            except (OSError, _json.JSONDecodeError, TypeError):
                pass
    except Exception:
        pass


def _start_context_job(
    project: str, force: bool = False,
    client: Any | None = None, model: str = "",
) -> str:
    """Return a deduplicated context job id and run it in the background."""
    from agent.jobs import JOBS

    existing = JOBS.find_active("kb_context", project)
    if existing is not None and not force:
        return existing.id
    context_job = JOBS.create(
        "kb_context", project=project, resumable=True,
        recovery={"project": project, "force": force},
    )
    context_job.log("[INFO] Starting project context mapping...")
    # This helper is called from the index worker thread as well as async route
    # handlers, so it cannot depend on a running asyncio event loop.
    import threading

    threading.Thread(
        target=_run_context_job,
        args=(context_job, project, force, client, model),
        name=f"kb-context-{context_job.id}",
        daemon=True,
    ).start()
    return context_job.id


@router.post("/index")
@trace
async def index_project(req: IndexRequest) -> dict:
    """Start a background KB indexing run and return its job id. Poll
    /jobs/{job_id} for live per-file progress and logs, exactly like the
    desktop worker + footer. Mirrors MainWindow._kick_kb_index.

    The run is a detached asyncio task, so it KEEPS GOING after the browser is
    closed. If an index for this project is already running we return that same
    job id instead of starting a duplicate, so a reopened browser (or a second
    auto-index trigger) simply reattaches to the in-flight run."""
    from agent.jobs import JOBS

    project_dir = PROJECTS_DIR / req.project
    kb_dir = project_dir / "kb"
    if not kb_dir.exists():
        raise HTTPException(404, f"No kb/ folder found for project '{req.project}'")

    # Dedupe: reuse an in-flight index for this project (unless a forced full
    # rebuild was explicitly requested).
    existing = JOBS.find_active("kb_index", req.project)
    if existing is not None and not req.force:
        return {"job_id": existing.id, "reused": True}

    job = JOBS.create(
        "kb_index", project=req.project, resumable=True,
        recovery={"project": req.project, "force": req.force},
    )
    job.log("[INFO] Starting KB indexing...")
    spawn_job_task(
        asyncio.to_thread(_run_kb_index, job, req.project, req.force), job
    )
    return {"job_id": job.id}


def recover_interrupted_kb_jobs() -> int:
    """Resume safe project-scoped KB work after an agent restart."""
    from agent.jobs import JOBS

    resumed = 0
    for job in JOBS.recovering():
        project = str(job.recovery.get("project") or job.project).strip()
        force = bool(job.recovery.get("force", False))
        if not project:
            job.fail("Recovery metadata is missing the project name.")
            continue
        JOBS.mark_running(job)
        job.log("[INFO] Resuming project preparation from persisted checkpoints.")
        if job.kind == "kb_index":
            threading.Thread(
                target=_run_kb_index,
                args=(job, project, force),
                name=f"kb-index-recovery-{job.id}",
                daemon=True,
            ).start()
            resumed += 1
        elif job.kind == "kb_context":
            threading.Thread(
                target=_run_context_job,
                args=(job, project, force, None, ""),
                name=f"kb-context-recovery-{job.id}",
                daemon=True,
            ).start()
            resumed += 1
        else:
            job.fail(f"Unsupported recovery job kind: {job.kind}")
    return resumed


@router.get("/index/active/{project}")
@trace
async def active_index_job(project: str) -> dict:
    """Return the id of an in-flight KB index job for ``project`` (if any) so a
    reopened browser can reattach to indexing that is still running in the agent
    after the web app was closed. ``job_id`` is null when nothing is running."""
    from agent.jobs import JOBS

    job = JOBS.find_active("kb_index", project)
    return {"job_id": job.id if job else None}


@router.post("/upload/{project}")
@trace
async def upload_document(
    project: str,
    file: UploadFile = File(...),
) -> dict:
    """Upload a document to the project's kb/ folder.

    The body is streamed into a size-limited temporary file on a worker thread,
    then atomically replaces the destination. That keeps memory flat, prevents
    partial overwrites, and never blocks the event loop. No indexing happens
    here - that is deferred to the explicit rebuild/auto-index on dialog close."""
    import tempfile

    project_dir = PROJECTS_DIR / project
    kb_dir = project_dir / "kb"
    kb_dir.mkdir(parents=True, exist_ok=True)

    # Basename-only so a crafted filename cannot escape kb/ via path traversal.
    safe_name = Path(file.filename or "document").name
    if not safe_name:
        raise HTTPException(400, "Invalid file name")
    dest = kb_dir / safe_name
    temp_path: Path | None = None

    def _save() -> int:
        nonlocal temp_path
        import os

        fd, raw_path = tempfile.mkstemp(prefix=f".{safe_name}.", suffix=".upload", dir=kb_dir)
        os.close(fd)
        temp_path = Path(raw_path)
        size = _copy_upload_limited(file.file, temp_path, _MAX_KB_UPLOAD_BYTES)
        temp_path.replace(dest)
        temp_path = None
        return size

    try:
        size = await asyncio.to_thread(_save)
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    # Trigger selective context update for just this file (non-blocking)
    _schedule_selective_context(project, safe_name)
    return {"ok": True, "path": str(dest), "size": size}


@router.get("/status/{project}")
@trace
async def kb_status(project: str) -> dict:
    """Return KB index status for a project."""
    project_dir = PROJECTS_DIR / project
    kb_dir = project_dir / "kb"
    index_file = project_dir / "kb_index.json"

    docs: list[str] = []
    if kb_dir.exists():
        docs = [f.name for f in kb_dir.iterdir() if f.is_file()]

    n_chunks = 0
    n_documents = 0
    indexed = index_file.exists()
    if indexed:
        try:
            import core.project_store as ps
            idx = ps.get_index(project)
            chunks = getattr(idx, "chunks", None) or []
            n_chunks = len(chunks)
            n_documents = len({getattr(c, "source", "") for c in chunks} or docs)
        except Exception as e:
            _log.debug("kb status index read failed: %s", e)
            n_documents = len(docs)

    return {
        "project": project,
        "documents": docs,
        "indexed": indexed,
        "n_chunks": n_chunks,
        "n_documents": n_documents,
    }


@router.delete("/document/{project}")
@trace
async def delete_document(project: str, name: str) -> dict:
    """Delete a single document from the project's kb/ folder and invalidate
    the stored index so the next rebuild reflects the change. Mirrors the
    desktop project KB dialog's "Remove selected" action."""
    project_dir = PROJECTS_DIR / project
    kb_dir = (project_dir / "kb").resolve()
    target = (kb_dir / name).resolve()
    # Path-traversal guard: the resolved target must live inside kb/.
    if target.parent != kb_dir:
        raise HTTPException(400, "Invalid document name")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"Document not found: {name}")
    target.unlink()

    # Keep the last complete index until the incremental follow-up succeeds.
    # The next index pass compares source name + SHA, removes this document's
    # chunks/maps, and preserves every unchanged document. This avoids a window
    # where a failed rebuild destroys the last usable KB.
    # Invalidate context map for the deleted file so next context run skips it
    _invalidate_context_map(project, name)
    return {"ok": True, "changed": name}


@router.post("/clear/{project}")
@trace
async def clear_kb(project: str, keep_documents: bool = False) -> dict:
    """Force-clear a project's knowledge base at ANY point, regardless of
    progress. Requests a stop on any in-flight KB indexing and project-context
    jobs, then wipes the index, dense vector store, context summary, and context
    maps. By default the uploaded source documents are removed too; pass
    ``keep_documents=true`` to keep the files and only drop the derived index.

    This is a hard terminate + wipe (a "force kill"), not a graceful rebuild."""
    from agent.jobs import JOBS
    import core.project_store as ps

    stopped: list[str] = []
    for kind in ("kb_index", "kb_context"):
        job = JOBS.find_active(kind, project)
        if job is not None:
            job.request_stop()
            job.log("[WARN] KB force-clear requested — stopping this job.")
            stopped.append(job.id)

    # Wait for stopped jobs to reach a terminal state so they cannot re-write
    # files after the wipe below. Timeout after 5s to avoid hanging forever.
    import time as _time
    deadline = _time.monotonic() + 5.0
    for jid in stopped:
        while _time.monotonic() < deadline:
            j = JOBS.get(jid)
            if j is None or j.state in ("done", "error"):
                break
            await asyncio.sleep(0.1)

    result = await asyncio.to_thread(ps.clear_kb, project, keep_documents=keep_documents)

    # Double-wipe: if a context job was mid-LLM-call and wrote its summary back
    # between request_stop and the wipe above, clear it again now.
    if stopped:
        await asyncio.to_thread(ps.clear_context_summary, project)

    result["stopped_jobs"] = stopped
    return result


# ---------------------------------------------------------------------
# Per-phase test-script templates (desktop project KB dialog parity)
# ---------------------------------------------------------------------
def _template_payload(project: str, phase: str) -> dict:
    import core.project_store as ps

    tpl, spec = ps.get_template(project, phase)
    if tpl is None:
        return {"has": False, "name": "", "describe": ""}
    describe = ""
    if spec is not None:
        try:
            describe = spec.describe()
        except Exception as e:  # noqa: BLE001
            _log.debug("spec.describe failed: %s", e)
            describe = "spec unavailable"
    return {"has": True, "name": Path(str(tpl)).name, "describe": describe}


@router.get("/template/{project}/{phase}")
@trace
async def template_status(project: str, phase: str) -> dict:
    """Return the stored template status for a phase (implementation/sit/uat)."""
    return _template_payload(project, phase)


@router.post("/template/{project}/{phase}")
@trace
async def upload_template(
    project: str,
    phase: str,
    file: UploadFile = File(...),
) -> dict:
    """Store an uploaded Excel test-script template for a phase, analyzing it
    once into a reusable spec. Best-effort LLM column analysis is attempted
    when an API key is configured, exactly like the desktop dialog; otherwise
    the heuristic analyzer is used."""
    import tempfile

    import core.project_store as ps

    suffix = Path(file.filename or "template.xlsx").suffix.lower() or ".xlsx"
    if suffix not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        raise HTTPException(400, "Template must be an Excel workbook")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            await asyncio.to_thread(
                _copy_upload_limited,
                file.file,
                tmp_path,
                _MAX_TEMPLATE_UPLOAD_BYTES,
            )
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from exc


        llm_mapping = None
        llm_header_row = None
        try:
            from core.model_router import Task, route
            from core.settings_store import build_llm_client
            from testgen.template_analyzer import analyze_template_with_llm

            client = build_llm_client()
            if client is not None:
                llm_header_row, llm_mapping = analyze_template_with_llm(
                    client, route(Task.TEMPLATE_ANALYSIS), str(tmp_path)
                )
        except Exception as e:  # noqa: BLE001 - LLM analysis is best-effort.
            _log.debug("analyze_template_with_llm failed: %s", e)
            llm_mapping = None
            llm_header_row = None

        try:
            ps.save_template(
                project, phase, tmp_path,
                llm_mapping=llm_mapping, llm_header_row=llm_header_row,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    return _template_payload(project, phase)


@router.delete("/template/{project}/{phase}")
@trace
async def delete_template(project: str, phase: str) -> dict:
    """Remove the stored template (and its spec) for a phase."""
    import core.project_store as ps

    paths = ps.ProjectPaths.for_name(project)
    existing = paths.find_template(phase)
    if existing is not None and existing.exists():
        try:
            existing.unlink()
        except OSError:
            pass
    spec_path = paths.template_spec_path(phase)
    try:
        if spec_path.exists():
            spec_path.unlink()
    except OSError:
        pass
    return {"ok": True}


@router.get("/template/{project}/{phase}/download")
@trace
async def download_template(project: str, phase: str):
    """Download the stored template workbook for a phase (web equivalent of
    the desktop dialog's "Open" button)."""
    from fastapi.responses import FileResponse

    import core.project_store as ps

    tpl, _spec = ps.get_template(project, phase)
    if tpl is None:
        raise HTTPException(404, "No template uploaded for this phase")
    path = Path(str(tpl))
    return FileResponse(
        str(path),
        filename=path.name,
        media_type="application/octet-stream",
    )


# ---------------------------------------------------------------------
# Project context summary (desktop project KB dialog "Project Context")
# ---------------------------------------------------------------------
def _context_payload(project: str) -> dict:
    """Shape the stored ProjectContext for the web dialog, mirroring the
    desktop _refresh_context_status/_view_context counts + prompt section."""
    import core.project_store as ps

    ctx = ps.read_context_summary(project)
    if ctx is None or ctx.is_empty():
        return {
            "has": False, "n_items": 0, "counts": {}, "summary": "",
            "status": "unavailable", "enabled": True,
            "mapped_documents": 0, "total_documents": 0,
            "failed_documents": [],
        }
    counts = {
        "actors": len(ctx.actors),
        "entities": len(ctx.entities),
        "workflows": len(ctx.workflows),
        "integrations": len(ctx.integrations),
        "business_rules": len(ctx.business_rules),
        "screens": len(ctx.screens),
        "test_data_needs": len(ctx.test_data_needs),
        "edge_cases": len(ctx.edge_cases),
        "non_functional": len(ctx.non_functional),
        "dependencies": len(ctx.dependencies),
        "glossary": len(ctx.glossary),
    }
    return {
        "has": True,
        "n_items": sum(counts.values()),
        "counts": counts,
        "summary": ctx.to_prompt_section(),
        "edited": bool(getattr(ctx, "override_summary", "").strip()),
        "status": ctx.status,
        "enabled": ctx.enabled,
        "mapped_documents": ctx.mapped_documents,
        "total_documents": ctx.total_documents,
        "failed_documents": ctx.failed_documents,
    }


@router.get("/context/active/{project}")
@trace
async def active_context_job(project: str) -> dict:
    """Return live context progress so the UI can track it independently."""
    from agent.jobs import JOBS

    job = JOBS.find_active("kb_context", project)
    if job is None:
        return {"job_id": None, "progress": None}
    snapshot = job.snapshot()
    return {"job_id": job.id, "progress": snapshot.get("progress")}


@router.get("/context/{project}")
@trace
async def get_context(project: str) -> dict:
    """Return the auto-extracted project context summary (actors, entities,
    workflows, screens, ...). Web equivalent of the desktop dialog's
    "View" button + status label."""
    return _context_payload(project)


class ContextSettingRequest(BaseModel):
    enabled: bool


@router.put("/context/{project}/setting")
@trace
async def set_context_setting(project: str, req: ContextSettingRequest) -> dict:
    """Enable or disable injecting the stored summary into generation."""
    import core.project_store as ps

    context = await asyncio.to_thread(ps.read_context_summary, project)
    if context is None or context.is_empty():
        raise HTTPException(409, "Project context has not been generated yet.")
    context.enabled = req.enabled
    written = await asyncio.to_thread(ps.write_context_summary, project, context)
    if not written:
        raise HTTPException(500, "Could not persist the project context setting.")
    return _context_payload(project)


class ContextEditRequest(BaseModel):
    summary: str


@router.put("/context/{project}")
@trace
async def edit_context(project: str, req: ContextEditRequest) -> dict:
    """Save a user-edited project context summary. The edited text is injected
    verbatim into generation (overriding the auto-rendered category sections).
    An empty summary clears the override and falls back to the extracted one."""
    import core.project_store as ps
    from kb.context_summary import ProjectContext

    context = await asyncio.to_thread(ps.read_context_summary, project)
    if context is None:
        context = ProjectContext()
    context.override_summary = req.summary.strip()
    written = await asyncio.to_thread(ps.write_context_summary, project, context)
    if not written:
        raise HTTPException(500, "Could not persist the edited project context.")
    return _context_payload(project)


@router.delete("/context/{project}")
@trace
async def clear_context(project: str) -> dict:
    """Delete the stored project context summary for this project."""
    import core.project_store as ps

    await asyncio.to_thread(ps.clear_context_summary, project)
    return _context_payload(project)


@router.post("/context/{project}/regenerate")
@trace
async def regenerate_context(project: str) -> dict:
    """Re-extract project context from the current KB index using the LLM and
    persist it. Mirrors the desktop dialog's "Regenerate" button."""
    import hashlib

    import core.project_store as ps
    from kb.context_summary import build_context_incremental_async

    # An LLM client is required - degrade with a clear 409 when unavailable
    # (matches the desktop "No LLM" warning) rather than silently no-op.
    try:
        from core.model_router import Task, route
        from core.settings_store import build_llm_client

        client = build_llm_client()
        primary = route(Task.MAP_EXTRACT)
    except Exception as e:  # noqa: BLE001
        _log.debug("build_llm_client (regenerate_context) failed: %s", e)
        client = None
        primary = ""
    if client is None:
        raise HTTPException(
            409,
            "No LLM client configured. Context extraction needs a working API key.",
        )

    index = await asyncio.to_thread(ps.get_index, project)
    chunks = list(getattr(index, "chunks", []) or [])
    if not chunks:
        raise HTTPException(
            409, "The knowledge base is empty. Add documents and index first."
        )

    # Fingerprint the current KB state (sha256 of chunk text) exactly like the
    # desktop _regen_context worker.
    h = hashlib.sha256()
    for c in chunks:
        h.update((getattr(c, "text", "") or "").encode("utf-8", errors="replace"))
    fingerprint = h.hexdigest()[:16]

    paths = ps.ensure_project(project)
    ctx = await build_context_incremental_async(
        kb_index=index,
        client=client,
        model=primary,
        maps_dir=paths.context_maps_dir,
        kb_fingerprint=fingerprint,
        force=True,
    )
    previous = await asyncio.to_thread(ps.read_context_summary, project)
    if ctx.mapped_documents == 0 and previous is not None and not previous.is_empty():
        return {
            **_context_payload(project),
            "status": "preserved",
            "failed_documents": ctx.failed_documents,
            "warning": (
                f"No documents mapped after retries; preserved the previous "
                f"summary. {len(ctx.failed_documents)} document(s) unavailable."
            ),
        }
    if not ctx.is_empty():
        await asyncio.to_thread(ps.write_context_summary, project, ctx)

    return {
        **_context_payload(project),
        "status": ctx.status,
        "failed_documents": ctx.failed_documents,
        "warning": (
            f"Context is partial: {ctx.mapped_documents}/{ctx.total_documents} "
            "documents mapped."
            if ctx.status == "partial"
            else ""
        ),
    }
