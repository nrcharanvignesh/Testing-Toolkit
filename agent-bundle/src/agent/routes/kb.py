"""KB endpoints — indexing, retrieval, embedding, reranking."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.app_config import PROJECTS_DIR
from agent.jobs import Job

router = APIRouter()


class RetrieveRequest(BaseModel):
    project: str
    query: str
    top_k: int = 32


class RetrieveResponse(BaseModel):
    chunks: list[dict[str, Any]]


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    """Run hybrid BM25 + dense + reranker search on the project KB."""
    from kb.retrieval import HybridRetriever

    project_dir = PROJECTS_DIR / req.project
    if not project_dir.exists():
        raise HTTPException(404, f"Project '{req.project}' not found locally")

    retriever = HybridRetriever(project_dir)
    if not retriever.is_ready():
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


def _run_kb_index(job: "Job", project: str, force: bool = False) -> None:
    """Worker body mirroring MainWindow._kick_kb_index in the desktop app:
    build/refresh the resumable KB index while streaming per-file progress and
    log lines into the Job so the browser can render the same
    'KB indexing 3/10 | 12s / 30s - 30%' status the desktop footer shows."""
    import time as _time
    import core.project_store as ps

    start = _time.monotonic()

    def _fmt_duration(secs: float) -> str:
        s = int(max(0.0, secs))
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        return f"{m}m {s:02d}s"

    def _on_progress(done: int, total: int, _elapsed: float, name: str = "") -> None:
        # Carry the current filename in the stage slot for per-file display.
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

    def _on_log(msg: str) -> None:
        if msg:
            job.log(msg)

    def _should_stop() -> bool:
        return job.stopped

    # Build an LLM client for contextual retrieval (situating prefixes), using
    # the fast model for cost efficiency. Degrades gracefully if unavailable.
    ctx_client = None
    ctx_model = ""
    try:
        from core.settings_store import build_anthropic_client, model_pair
        ctx_client = build_anthropic_client()
        _, ctx_model = model_pair()
    except Exception:
        ctx_client = None
        ctx_model = ""

    try:
        if force:
            job.log("[INFO] Full rebuild requested; ignoring cached index.")
        job.log(f"[INFO] KB indexing started for '{project}'.")
        result = ps.index_project_resumable(
            project,
            on_progress=_on_progress,
            on_log=_on_log,
            should_stop=_should_stop,
            enable_dense=True,
            llm_client=ctx_client,
            llm_model=ctx_model,
            force=force,
        )
        docs = int(getattr(result, "n_docs", 0) or 0)
        chunks = len(getattr(result, "chunks", []) or [])
        job.finish({
            "n_documents": docs,
            "n_chunks": chunks,
            "has_dense": bool(getattr(result, "has_dense", False)),
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


@router.post("/index")
async def index_project(req: IndexRequest) -> dict:
    """Start a background KB indexing run and return its job id. Poll
    /jobs/{job_id} for live per-file progress and logs, exactly like the
    desktop worker + footer. Mirrors MainWindow._kick_kb_index."""
    from agent.jobs import JOBS, Job

    project_dir = PROJECTS_DIR / req.project
    kb_dir = project_dir / "kb"
    if not kb_dir.exists():
        raise HTTPException(404, f"No kb/ folder found for project '{req.project}'")

    job = JOBS.create("kb_index")
    job.log("[INFO] Starting KB indexing...")
    asyncio.create_task(
        asyncio.to_thread(_run_kb_index, job, req.project, req.force)
    )
    return {"job_id": job.id}


@router.post("/upload/{project}")
async def upload_document(
    project: str,
    file: UploadFile = File(...),
) -> dict:
    """Upload a document to the project's kb/ folder."""
    project_dir = PROJECTS_DIR / project
    kb_dir = project_dir / "kb"
    kb_dir.mkdir(parents=True, exist_ok=True)

    dest = kb_dir / file.filename
    content = await file.read()
    dest.write_bytes(content)
    return {"ok": True, "path": str(dest), "size": len(content)}


@router.get("/status/{project}")
async def kb_status(project: str) -> dict:
    """Return KB index status for a project."""
    project_dir = PROJECTS_DIR / project
    kb_dir = project_dir / "kb"
    index_file = project_dir / "kb_index.json"

    docs: list[str] = []
    if kb_dir.exists():
        docs = [f.name for f in kb_dir.iterdir() if f.is_file()]

    return {
        "project": project,
        "documents": docs,
        "indexed": index_file.exists(),
    }


@router.delete("/document/{project}")
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
    # Drop the index so status shows "not indexed" until a rebuild runs.
    index_file = project_dir / "kb_index.json"
    try:
        if index_file.exists():
            index_file.unlink()
    except OSError:
        pass
    return {"ok": True}


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
        except Exception:  # noqa: BLE001
            describe = "spec unavailable"
    return {"has": True, "name": Path(str(tpl)).name, "describe": describe}


@router.get("/template/{project}/{phase}")
async def template_status(project: str, phase: str) -> dict:
    """Return the stored template status for a phase (implementation/sit/uat)."""
    return _template_payload(project, phase)


@router.post("/template/{project}/{phase}")
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
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        llm_mapping = None
        llm_header_row = None
        try:
            from core.settings_store import build_llm_client, model_pair
            from testgen.template_analyzer import analyze_template_with_llm

            client = build_llm_client()
            if client is not None:
                primary, _fast = model_pair()
                llm_header_row, llm_mapping = analyze_template_with_llm(
                    client, primary, str(tmp_path)
                )
        except Exception:  # noqa: BLE001 — LLM analysis is best-effort.
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
