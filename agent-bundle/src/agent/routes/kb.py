"""KB endpoints — indexing, retrieval, embedding, reranking."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.app_config import PROJECTS_DIR

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


@router.post("/index")
async def index_project(req: IndexRequest) -> dict:
    """Trigger KB indexing for a project. Runs synchronously (can be long)."""
    from kb.indexer import build_index

    project_dir = PROJECTS_DIR / req.project
    kb_dir = project_dir / "kb"
    if not kb_dir.exists():
        raise HTTPException(404, f"No kb/ folder found for project '{req.project}'")

    result = await asyncio.to_thread(build_index, project_dir)
    return {
        "n_chunks": result.n_chunks,
        "n_documents": result.n_documents,
        "has_dense": result.has_dense,
    }


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
