"""
kb_retrieval.py
Hybrid local retrieval over a project knowledge base - the offline,
no-embedding-API core.

Pipeline (all local; the LLM API is used only by callers for synthesis):

    query
      |-- BM25 (lexical, always on; bm25.py)            -> ranked ids
      |-- dense cosine (optional; embeddings + vector_store) -> ranked ids
      +-- Reciprocal Rank Fusion (RRF)                   -> fused candidates
              |-- cross-encoder rerank (optional; reranker.py)
              +-- top-k RetrievedChunk

Degradation is graceful and automatic:
  * No embedding backend installed -> lexical-only (BM25) retrieval.
  * No reranker installed          -> fused (RRF) order is returned.
Both still return high-quality results; bundling fastembed later upgrades
the same index to dense + rerank with no code change.

On-disk index (one directory per project):
    chunks.jsonl       id, doc, title, text, context (one JSON object/line)
    bm25.json          serialized BM25Index
    vectors.npy        dense matrix (only if an embedder was available)
    vector_ids.json    row-id alignment for vectors.npy
    manifest.json      capabilities, model name, dim, counts, built_at

ASCII-only; fully type-hinted; logging via [INFO]/[WARN]/[SUCCESS].
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final

from kb.bm25 import BM25Index
from kb.vector_store import open_vector_store

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]

_RRF_K: Final[int] = 60
_DEFAULT_TOP_K: Final[int] = 32
_DEFAULT_CANDIDATES: Final[int] = 96
def _compute_embed_batch() -> int:
    try:
        from core.hardware import available_memory_mb
        avail = available_memory_mb()
        if avail > 8000:
            return 128
        if avail > 4000:
            return 64
    except Exception:
        pass
    return 32

_EMBED_BATCH: Final[int] = _compute_embed_batch()
# ponytail: 4 workers saturates the API without overloading RAM; scale up
# if the proxy supports higher concurrency per client.
_EMBED_WORKERS: Final[int] = 4
_SCHEMA: Final[int] = 2

_CHUNKS_FILE: Final[str] = "chunks.jsonl"
_CTX_CHUNKS_FILE: Final[str] = "ctx_chunks.jsonl"
_BM25_FILE: Final[str] = "bm25.json"
_MANIFEST_FILE: Final[str] = "manifest.json"
_CURRENT_FILE: Final[str] = "current.json"
_GENERATIONS_DIR: Final[str] = ".generations"


def _active_index_dir(index_dir: Path | str) -> Path:
    """Resolve the atomically published generation, with legacy fallback."""
    root = Path(index_dir)
    pointer = root / _CURRENT_FILE
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
        generation = str(data.get("generation", ""))
        candidate = root / _GENERATIONS_DIR / generation
        if generation and candidate.is_dir():
            return candidate
    except (OSError, ValueError, TypeError):
        pass
    return root


def _publish_generation(root: Path, generation: str) -> None:
    """Publish a complete immutable generation with one atomic pointer swap."""
    root.mkdir(parents=True, exist_ok=True)
    temp = root / f".{_CURRENT_FILE}.tmp-{os.getpid()}"
    temp.write_text(
        json.dumps({"schema": 1, "generation": generation}, ensure_ascii=True),
        encoding="utf-8",
    )
    os.replace(temp, root / _CURRENT_FILE)


def _log(on_log: LogFn | None, msg: str) -> None:
    if on_log is not None:
        try:
            on_log(msg)
        except Exception:
            pass


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    doc: str
    title: str
    text: str
    score: float = 0.0
    semantic_score: float = 0.0
    lexical_score: float = 0.0
    reranker_score: float = 0.0
    source_priority: float = 0.5
    section_path: str = ""
    document_role: str = "unknown"
    tier: str = "low"
    duplicate_cluster: str = ""


def rrf_fuse(
    rankings: list[list[str]], k: int = _RRF_K, top_n: int | None = None,
) -> list[str]:
    """Reciprocal Rank Fusion: merge several ranked id lists into one order.
    score(id) = sum over lists of 1 / (k + rank), rank 0-based. No score
    normalization needed; ties resolved by first appearance for stability."""
    score: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    seq = 0
    for ranking in rankings:
        for rank, rid in enumerate(ranking):
            score[rid] = score.get(rid, 0.0) + 1.0 / (k + rank)
            if rid not in first_seen:
                first_seen[rid] = seq
                seq += 1
    fused = sorted(score, key=lambda r: (-score[r], first_seen[r]))
    return fused[:top_n] if top_n is not None else fused


def _embed_texts(embedder: Any, texts: list[str]) -> Any:
    return embedder.embed(texts)


def _drop_stale_vectors(
    index_dir: Path, model_name: str, dim: int, on_log: LogFn | None,
) -> None:
    """Delete an existing on-disk vector store when it was built with a
    different embedding model or dimension than the current embedder. Without
    this, a model/dim upgrade would leave the old vectors in place (because the
    resume logic only embeds MISSING ids) and query vectors of the new
    dimension would silently mismatch. Best-effort; never raises."""
    import shutil

    p = index_dir / _MANIFEST_FILE
    if not p.exists():
        return
    try:
        from kb.kb_crypto import read_decrypted_text
        text = read_decrypted_text(p)
        if text is None:
            return
        man = json.loads(text)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return
    old_model = str(man.get("model", ""))
    old_dim = int(man.get("dim", 0) or 0)
    if not old_model and not old_dim:
        return  # lexical-only prior index; nothing to invalidate
    if old_model == str(model_name) and old_dim == int(dim):
        return  # same embedding identity; keep and resume
    _log(on_log, f"[INFO] Embedding model/dim changed "
                 f"({old_model}/{old_dim} -> {model_name}/{dim}); "
                 f"rebuilding dense vectors from scratch.")
    for name in ("vectors.npy", "vector_ids.json", _MANIFEST_FILE):
        try:
            (index_dir / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        shutil.rmtree(index_dir / "lance", ignore_errors=True)
    except OSError:
        pass


# ---------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------
ProgressFn = Callable[[str, int, int], None]


def build_hybrid_index(
    index_dir: Path | str,
    chunks: list[dict[str, Any]],
    embedder: Any | None = None,
    on_log: LogFn | None = None,
    should_stop: StopFn | None = None,
    enforce_dense: bool = False,
    on_progress: ProgressFn | None = None,
) -> bool:
    """Build (or refresh) the hybrid index from chunk dicts. Each chunk dict
    needs keys: chunk_id, doc, title, text, and optional context.

    BM25 is always built (fast). If an embedder is provided, dense vectors
    are built incrementally and saved in batches so a crash resumes the
    embedding step (the vector store already on disk is reused; only missing
    chunk ids are embedded). Returns True on success."""
    root_dir = Path(index_dir)
    root_dir.mkdir(parents=True, exist_ok=True)
    ids = [str(c.get("chunk_id", "")) for c in chunks]
    generation_hash = hashlib.sha256()
    for chunk_id, chunk in zip(ids, chunks):
        generation_hash.update(chunk_id.encode("utf-8", errors="replace"))
        generation_hash.update(str(chunk.get("text", "")).encode("utf-8", errors="replace"))
    generation = generation_hash.hexdigest()[:20]
    index_dir = root_dir / _GENERATIONS_DIR / generation
    index_dir.mkdir(parents=True, exist_ok=True)

    # Text used for BM25 and embedding includes the contextual prefix.
    def _ctx_text(c: dict[str, Any]) -> str:
        ctx = str(c.get("context", "") or "").strip()
        body = str(c.get("text", "") or "")
        return (ctx + "\n" + body).strip() if ctx else body

    texts = [_ctx_text(c) for c in chunks]

    # 1) chunks.jsonl (encrypted at rest)
    try:
        from kb.kb_crypto import write_encrypted_text
        chunks_text = "\n".join(
            json.dumps({
                "chunk_id": str(c.get("chunk_id", "")),
                "doc": str(c.get("doc", "")),
                "title": str(c.get("title", "")),
                "text": str(c.get("text", "")),
                "context": str(c.get("context", "") or ""),
                "source_path": str(c.get("source_path", c.get("doc", ""))),
                "section_path": str(c.get("section_path", "")),
                "document_role": str(c.get("document_role", "unknown")),
                "source_priority": float(c.get("source_priority", 0.5) or 0.5),
            }, ensure_ascii=True) for c in chunks
        )
        write_encrypted_text(index_dir / _CHUNKS_FILE, chunks_text)
    except OSError as e:
        _log(on_log, f"[ERROR] Could not write chunks: {e!r}")
        return False

    # 2) BM25 (always)
    bm = BM25Index.build(ids, texts)
    bm.save(index_dir / _BM25_FILE)
    _log(on_log, f"[INFO] BM25 index built over {len(ids)} chunk(s).")

    # 3) Dense vectors (optional, resumable)
    dim = 0
    model_name = ""
    if embedder is not None and ids:
        try:
            dim = int(getattr(embedder, "dim", 0))
            model_name = str(getattr(embedder, "name", "embedder"))
            # If a prior index used a different embedding model/dim, its stored
            # vectors are incompatible with the new query vectors. Drop the old
            # vector store + manifest so we re-embed every chunk from scratch
            # (otherwise `already == len(ids)` would skip re-embedding and leave
            # mismatched-dimension vectors on disk).
            _drop_stale_vectors(index_dir, model_name, dim, on_log)
            store = open_vector_store(index_dir, dim or 384)
            already = store.count()
            if already < len(ids):
                remaining = len(ids) - already
                _log(on_log, f"[INFO] Embedding {remaining} chunk(s) "
                             f"with {model_name} "
                             f"({_EMBED_WORKERS} workers)...")
                if on_progress is not None:
                    on_progress("embedding", already, len(ids))
                i = already
                while i < len(ids):
                    if should_stop is not None and should_stop():
                        store.save()
                        _log(on_log, "[WARN] Embedding paused; will resume.")
                        return True
                    # Prefetch multiple batches in parallel, write serially.
                    window_end = min(i + _EMBED_BATCH * _EMBED_WORKERS, len(ids))
                    batches = [
                        (ids[b:b + _EMBED_BATCH], texts[b:b + _EMBED_BATCH])
                        for b in range(i, window_end, _EMBED_BATCH)
                    ]
                    with ThreadPoolExecutor(
                        max_workers=_EMBED_WORKERS
                    ) as pool:
                        futures = {
                            pool.submit(_embed_texts, embedder, bt): bi
                            for bi, (_, bt) in enumerate(batches)
                        }
                        results: list[Any] = [None] * len(batches)
                        for fut in as_completed(futures):
                            results[futures[fut]] = fut.result()
                    # Write results in order (store is not thread-safe).
                    for bi, (b_ids, _) in enumerate(batches):
                        vecs = results[bi]
                        store.add(b_ids, vecs)
                        del vecs
                    store.save()
                    i = window_end
                    if on_progress is not None:
                        on_progress("embedding", min(i, len(ids)), len(ids))
                    gc.collect()
            dim = int(getattr(store, "dim", dim) or dim)
            _log(on_log, f"[SUCCESS] Dense vectors ready ({store.count()}).")
        except Exception as e:  # noqa: BLE001 - dense is optional
            if enforce_dense:
                # Enforced: do not degrade silently. Re-raise so the index job
                # fails visibly and the cause (e.g. missing bundled model) is
                # surfaced instead of producing a misleading lexical-only index.
                _log(on_log, f"[ERROR] Dense embedding failed and dense is "
                             f"enforced: {e!r}")
                raise
            _log(on_log, f"[WARN] Dense embedding failed ({e!r}); using "
                         f"lexical-only retrieval.")
            dim = 0
            model_name = ""

    # 4) manifest (encrypted at rest)
    try:
        from kb.kb_crypto import write_encrypted_text
        from kb.retrieval_config import load_retrieval_config

        config_fingerprint = load_retrieval_config(root_dir.parent).fingerprint()
        write_encrypted_text(index_dir / _MANIFEST_FILE, json.dumps({
            "schema": _SCHEMA,
            "config_fingerprint": config_fingerprint,
            "n_chunks": len(ids),
            "dense": bool(dim),
            "dim": int(dim),
            "model": model_name,
            "built_at": time.time(),
        }, ensure_ascii=True))
        _publish_generation(root_dir, generation)
        _log(on_log, f"[SUCCESS] Published KB generation {generation} atomically.")
    except OSError as exc:
        _log(on_log, f"[ERROR] Could not publish KB generation: {exc!r}")
        return False
    # ponytail: immutable generations are retained; add bounded GC only if disk
    # usage proves material, because deleting one can break an in-flight reader.
    return True


# ---------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------
class HybridRetriever:
    """Loads a built hybrid index and answers retrieve(query, k). Dense and
    rerank stages activate only if their backends are importable."""

    def __init__(self, index_dir: Path | str) -> None:
        self.root_dir = Path(index_dir)
        self.dir = _active_index_dir(self.root_dir)
        from kb.retrieval_config import load_retrieval_config

        self.config = load_retrieval_config(self.root_dir.parent)
        self._bm25: BM25Index | None = BM25Index.load(self.dir / _BM25_FILE)
        self._chunks: dict[str, RetrievedChunk] = {}
        self._load_chunks()
        self._manifest = self._load_manifest()
        self._embedder: Any | None = None
        self._embedder_tried = False
        self._reranker: Any | None = None
        self._reranker_tried = False
        self._store: Any | None = None

    def _load_manifest(self) -> dict[str, Any]:
        try:
            from kb.kb_crypto import read_decrypted_text
            p = self.dir / _MANIFEST_FILE
            if p.exists():
                text = read_decrypted_text(p)
                if text is not None:
                    return json.loads(text)
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _load_chunks(self) -> None:
        p = self.dir / _CHUNKS_FILE
        if not p.exists():
            return
        try:
            from kb.kb_crypto import read_decrypted_text
            text = read_decrypted_text(p)
            if text is None:
                self._chunks = {}
                return
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                cid = str(d.get("chunk_id", ""))
                self._chunks[cid] = RetrievedChunk(
                    chunk_id=cid, doc=str(d.get("doc", "")),
                    title=str(d.get("title", "")),
                    text=str(d.get("text", "")),
                    source_priority=float(d.get("source_priority", 0.5) or 0.5),
                    section_path=str(d.get("section_path", "")),
                    document_role=str(d.get("document_role", "unknown")),
                )
        except (OSError, json.JSONDecodeError, ValueError):
            self._chunks = {}
        # Load context overlay chunks (written separately from immutable KB gen)
        ctx_path = self.dir / _CTX_CHUNKS_FILE
        if ctx_path.exists():
            try:
                from kb.kb_crypto import read_decrypted_text as _rdt
                text = _rdt(ctx_path)
                if text:
                    for line in text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        d = json.loads(line)
                        cid = str(d.get("chunk_id", ""))
                        self._chunks[cid] = RetrievedChunk(
                            chunk_id=cid, doc=str(d.get("doc", "")),
                            title=str(d.get("title", "")),
                            text=str(d.get("text", "")),
                            source_priority=float(
                                d.get("source_priority", 0.9) or 0.9),
                            section_path=str(d.get("section_path", "")),
                            document_role=str(
                                d.get("document_role", "context")),
                        )
            except (OSError, json.JSONDecodeError, ValueError):
                pass

    def is_available(self) -> bool:
        return self._bm25 is not None and bool(self._chunks)

    def _ensure_embedder(self) -> Any | None:
        if self._embedder_tried:
            return self._embedder
        self._embedder_tried = True
        if not self._manifest.get("dense"):
            return None
        try:
            from kb.embeddings import get_text_embedder

            self._embedder = get_text_embedder(self._manifest.get("model")
                                                or None)
            if self._embedder is not None:
                dim = int(self._manifest.get("dim", 0)
                          or getattr(self._embedder, "dim", 384))
                self._store = open_vector_store(self.dir, dim)
        except Exception:
            self._embedder = None
            self._store = None
        return self._embedder

    def _ensure_reranker(self) -> Any | None:
        """Build an API-based reranker (no local ONNX). Returns a lightweight
        wrapper with .rerank(query, candidates, top_k).

        Primary path: the gateway's native /rerank cross-encoder. Fallback:
        LLM-as-judge (one completion call). If neither is reachable the wrapper
        returns an empty list and the caller keeps the fused (RRF) order.
        """
        if self._reranker_tried:
            return self._reranker
        self._reranker_tried = True
        try:
            from core.app_config import LLM_API_KEY, LLM_BASE_URL, RERANK_MODEL
            from core.model_router import Task, route
            from core.settings_store import build_llm_client, build_runtime_config

            model = route(Task.LLM_RERANK)
            client = build_llm_client()
            api_key = LLM_API_KEY
            base_url = LLM_BASE_URL
            try:
                ssl_verify = build_runtime_config().build_ssl()
            except Exception:
                ssl_verify = True
            if client is None and not api_key:
                self._reranker = None
                return None

            class _APIRerankerWrapper:
                name = f"rerank:{RERANK_MODEL}|llm:{model}"

                def rerank(self, query: str,
                           candidates: list[tuple[str, str]],
                           top_k: int) -> list[tuple[str, float]]:
                    from kb.reranker import llm_rerank, native_rerank
                    # 1) Native gateway /rerank (preferred).
                    if api_key:
                        native = native_rerank(
                            base_url=base_url, api_key=api_key,
                            model=RERANK_MODEL, query=query,
                            candidates=candidates, top_k=top_k,
                            ssl_verify=ssl_verify,
                        )
                        if native:
                            return native
                    # 2) Fallback: LLM-as-judge ordering.
                    if client is not None:
                        ids = llm_rerank(client, model, query, candidates, top_k)
                        if ids:
                            return [(cid, 1.0 / (1 + i))
                                    for i, cid in enumerate(ids)]
                    return []

            self._reranker = _APIRerankerWrapper()
        except Exception:
            self._reranker = None
        return self._reranker

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.is_available(),
            "n_chunks": len(self._chunks),
            "bm25": self._bm25 is not None,
            "dense": bool(self._manifest.get("dense")),
            "model": self._manifest.get("model", ""),
        }

    def retrieve(
        self,
        query: str,
        top_k: int = _DEFAULT_TOP_K,
        candidate_k: int = _DEFAULT_CANDIDATES,
        use_reranker: bool = True,
    ) -> list[RetrievedChunk]:
        """Return quality-gated, authority-aware, diverse chunks."""
        if not self.is_available() or not (query or "").strip():
            return []
        cfg = self.config
        final_k = max(1, min(top_k, cfg.final_k))
        fetch_k = max(final_k, candidate_k, cfg.fetch_k)
        bm25_hits = self._bm25.top_n(query, fetch_k) if self._bm25 else []
        lexical_max = max((float(score) for _cid, score in bm25_hits), default=0.0)
        lexical = {
            cid: (float(score) / lexical_max if lexical_max > 0 else 0.0)
            for cid, score in bm25_hits
        }

        dense: dict[str, float] = {}
        embedder = self._ensure_embedder()
        if embedder is not None and self._store is not None:
            try:
                query_vector = _embed_texts(embedder, [query])[0]
                dense = {
                    cid: max(0.0, min(1.0, float(score)))
                    for cid, score in self._store.search(query_vector, fetch_k)
                }
            except Exception:
                dense = {}
        candidate_ids = list(dict.fromkeys([*dense, *lexical]))
        if not candidate_ids:
            return []

        reranked: dict[str, float] = {}
        if use_reranker:
            reranker = self._ensure_reranker()
            if reranker is not None:
                candidates = [
                    (cid, self._chunks[cid].text)
                    for cid in candidate_ids if cid in self._chunks
                ]
                try:
                    reranked = dict(reranker.rerank(query, candidates, fetch_k))
                except Exception:
                    reranked = {}

        scored: list[RetrievedChunk] = []
        for cid in candidate_ids:
            chunk = self._chunks.get(cid)
            if chunk is None:
                continue
            semantic = dense.get(cid, lexical.get(cid, 0.0))
            if semantic < cfg.min_semantic_score:
                continue
            rerank_score = max(0.0, min(1.0, float(reranked.get(cid, 0.0))))
            score = (
                cfg.semantic_weight * semantic
                + cfg.lexical_weight * lexical.get(cid, 0.0)
                + cfg.reranker_weight * rerank_score
                + cfg.source_priority_weight * chunk.source_priority
            )
            scored.append(RetrievedChunk(
                chunk_id=chunk.chunk_id, doc=chunk.doc, title=chunk.title,
                text=chunk.text, score=score, semantic_score=semantic,
                lexical_score=lexical.get(cid, 0.0),
                reranker_score=rerank_score,
                source_priority=chunk.source_priority,
                section_path=chunk.section_path,
                document_role=chunk.document_role,
                tier="high" if score >= 0.7 else "medium" if score >= 0.45 else "low",
            ))
        scored.sort(key=lambda item: (-item.score, item.chunk_id))
        if not scored:
            return []

        vectors = self._store.vectors_for([item.chunk_id for item in scored]) \
            if self._store is not None else {}
        representatives: list[RetrievedChunk] = []
        representative_vectors: list[Any] = []
        for item in scored:
            vector = vectors.get(item.chunk_id)
            cluster = ""
            if vector is not None:
                for index, existing in enumerate(representative_vectors):
                    if existing is not None and float(vector @ existing) >= cfg.duplicate_cosine_threshold:
                        cluster = representatives[index].duplicate_cluster
                        break
            if cluster:
                continue
            item.duplicate_cluster = f"cluster-{len(representatives) + 1}"
            representatives.append(item)
            representative_vectors.append(vector)

        selected: list[RetrievedChunk] = []
        source_counts: dict[str, int] = {}
        remaining = list(representatives)
        while remaining and len(selected) < final_k:
            eligible = [
                item for item in remaining
                if source_counts.get(item.doc, 0) < cfg.per_source_cap
            ] or remaining
            def mmr(item: RetrievedChunk) -> tuple[float, str]:
                vector = vectors.get(item.chunk_id)
                similarity = 0.0
                if vector is not None and selected:
                    similarity = max(
                        float(vector @ vectors[chosen.chunk_id])
                        for chosen in selected if chosen.chunk_id in vectors
                    ) if any(chosen.chunk_id in vectors for chosen in selected) else 0.0
                value = cfg.mmr_lambda * item.score - (1.0 - cfg.mmr_lambda) * similarity
                return value, item.chunk_id
            chosen = max(eligible, key=mmr)
            selected.append(chosen)
            source_counts[chosen.doc] = source_counts.get(chosen.doc, 0) + 1
            remaining.remove(chosen)
        return selected


    def multi_query_retrieve(
        self,
        queries: list[str],
        top_k: int = _DEFAULT_TOP_K,
        candidate_k: int = _DEFAULT_CANDIDATES,
    ) -> list[RetrievedChunk]:
        """Retrieve with multiple sub-queries and merge: best score per
        chunk_id wins, then return top_k overall. Sub-queries skip the
        expensive cross-encoder rerank; a single rerank pass runs on the
        merged candidates for speed."""
        if not queries:
            return []
        scores: dict[str, tuple[float, RetrievedChunk]] = {}
        per_q_k = max(top_k, candidate_k // max(1, len(queries)))
        for q in queries:
            results = self.retrieve(
                q, top_k=per_q_k, candidate_k=candidate_k,
                use_reranker=False,
            )
            for ch in results:
                existing = scores.get(ch.chunk_id)
                if existing is None or ch.score > existing[0]:
                    scores[ch.chunk_id] = (ch.score, ch)
        ranked = sorted(scores.values(), key=lambda item: item[0], reverse=True)
        merged = [chunk for _score, chunk in ranked[:candidate_k]]
        reranker = self._ensure_reranker()
        if reranker is not None and merged:
            candidates = [(chunk.chunk_id, chunk.text) for chunk in merged]
            try:
                reranked = reranker.rerank(
                    " ".join(query[:200] for query in queries[:4]),
                    candidates, len(candidates),
                )
                rerank_scores = {
                    chunk_id: max(0.0, min(1.0, float(score)))
                    for chunk_id, score in reranked
                }
                for chunk in merged:
                    chunk.reranker_score = rerank_scores.get(chunk.chunk_id, 0.0)
                    chunk.score += self.config.reranker_weight * chunk.reranker_score
                merged.sort(key=lambda chunk: (-chunk.score, chunk.chunk_id))
            except Exception:
                pass
        # ponytail: sub-query results are already deduplicated and source-capped;
        # a shared selector is the upgrade path if cross-query overlap grows.
        source_counts: dict[str, int] = {}
        out: list[RetrievedChunk] = []
        for chunk in merged:
            if source_counts.get(chunk.doc, 0) >= self.config.per_source_cap:
                continue
            out.append(chunk)
            source_counts[chunk.doc] = source_counts.get(chunk.doc, 0) + 1
            if len(out) >= min(top_k, self.config.final_k):
                break
        return out


def _decompose_query_heuristic(work_item_dump: str) -> list[str]:
    """Split a work item dump into focused sub-queries (no LLM needed).
    Extracts acceptance criteria, Given/When/Then blocks, numbered items,
    field names, and error messages as separate retrieval queries."""
    queries: list[str] = []
    lines = (work_item_dump or "").splitlines()
    # Collect acceptance criteria bullets / numbered items
    ac_section = False
    ac_lines: list[str] = []
    current_block: list[str] = []
    gwt_blocks: list[str] = []
    gwt_current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if "ACCEPTANCE CRITERIA" in line.upper():
            ac_section = True
            continue
        if ac_section:
            if stripped.startswith(("=", "WORK ITEM", "DESCRIPTION:",
                                    "COMMENTS:")):
                ac_section = False
                if current_block:
                    ac_lines.append(" ".join(current_block))
                    current_block = []
                continue
            # Detect numbered/bulleted items
            if re.match(r"^(\d+[\.\)]\s|[-*]\s|Given\b|When\b|Then\b)",
                        stripped):
                if current_block:
                    ac_lines.append(" ".join(current_block))
                    current_block = []
                current_block.append(stripped)
            elif stripped and current_block:
                current_block.append(stripped)
        else:
            # Detect standalone Given/When/Then blocks outside AC section
            if re.match(r"^(Given\b|When\b|Then\b|And\b|But\b)", stripped):
                gwt_current.append(stripped)
            elif gwt_current:
                if stripped:
                    gwt_current.append(stripped)
                else:
                    gwt_blocks.append(" ".join(gwt_current))
                    gwt_current = []
    if current_block:
        ac_lines.append(" ".join(current_block))
    if gwt_current:
        gwt_blocks.append(" ".join(gwt_current))
    # Merge standalone GWT blocks as additional sub-queries
    for gwt in gwt_blocks:
        if len(gwt) > 15:
            ac_lines.append(gwt[:500])
    # Each AC item becomes a sub-query
    for ac in ac_lines:
        if len(ac) > 20:
            queries.append(ac[:500])
    # If we didn't extract enough sub-queries, fall back to the whole dump
    if len(queries) < 2:
        queries = [work_item_dump[:3000]]
    return queries[:12]


def open_retriever(index_dir: Path | str) -> "HybridRetriever | None":
    """Open a built hybrid index for querying, or None if none exists."""
    r = HybridRetriever(index_dir)
    return r if r.is_available() else None


def hybrid_has_dense(index_dir: Path | str) -> bool:
    """True if the built hybrid index actually contains dense vectors (its
    manifest reports dense=True with a positive dimension). Used to verify that
    enforced dense indexing really produced vectors rather than a lexical-only
    index."""
    try:
        from kb.kb_crypto import read_decrypted_text
        p = _active_index_dir(index_dir) / _MANIFEST_FILE
        if not p.exists():
            return False
        text = read_decrypted_text(p)
        if text is None:
            return False
        man = json.loads(text)
        return bool(man.get("dense", False)) and int(man.get("dim", 0)) > 0
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return False


def hybrid_index_is_current(
    index_dir: Path | str, n_chunks: int, min_built_at: float,
    want_dense: bool = False, want_model: str = "", want_dim: int = 0,
) -> bool:
    """Cheap check (reads only the small manifest) to decide whether the
    hybrid index already reflects the current chunk set, so callers can skip
    a redundant rebuild. True only if the manifest exists, its chunk count
    matches, it was built at/after the source index, and - when want_dense
    is True - the existing index already has dense vectors (so flipping
    dense on forces a rebuild that adds them).

    When want_model/want_dim are supplied, the stored embedding model name and
    vector dimension MUST also match. This forces exactly one rebuild whenever
    the configured embedding model or dimension changes (e.g. upgrading from
    text-embedding-3-small/512 to text-embedding-3-large/3072); otherwise stale
    vectors of the wrong dimension would silently mismatch query vectors."""
    try:
        from kb.kb_crypto import read_decrypted_text
        p = _active_index_dir(index_dir) / _MANIFEST_FILE
        if not p.exists():
            return False
        text = read_decrypted_text(p)
        if text is None:
            return False
        man = json.loads(text)
        from kb.retrieval_config import load_retrieval_config

        expected_fingerprint = load_retrieval_config(Path(index_dir).parent).fingerprint()
        if int(man.get("schema", 0)) != _SCHEMA:
            return False
        if str(man.get("config_fingerprint", "")) != expected_fingerprint:
            return False
        if want_dense and not bool(man.get("dense", False)):
            return False
        # Embedding identity guard: a changed model or dim invalidates vectors.
        if want_dense and want_dim and int(man.get("dim", 0)) != int(want_dim):
            return False
        if (want_dense and want_model
                and str(man.get("model", "")) != str(want_model)):
            return False
        return (int(man.get("n_chunks", -1)) == int(n_chunks)
                and float(man.get("built_at", 0.0)) >= float(min_built_at)
                and (Path(index_dir) / _CHUNKS_FILE).exists()
                and (Path(index_dir) / _BM25_FILE).exists())
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------------
# Indexing density: split coarse chunks into fine retrieval chunks
# ---------------------------------------------------------------------
# Retrieval quality (especially lexical/BM25) improves sharply with smaller
# chunks: a query then matches a tight passage instead of a multi-page blob.
# The RLM map step used very large chunks (~6000 tokens); for retrieval we
# re-split that text into ~550-token windows with overlap so context is dense
# and precise. Splitting reuses already-extracted text (no re-extraction).
_PARA_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"\n\s*\n")
_SENT_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")


def _split_text_dense(
    text: str, target_chars: int = 2800, overlap_chars: int = 400,
) -> list[str]:
    """Split one passage into ~target_chars windows with overlap,
    respecting paragraph then sentence boundaries; hard-splits only when a
    single unit exceeds the target."""
    text = (text or "").strip()
    if len(text) <= target_chars:
        return [text] if text else []
    units: list[str] = []
    for para in _PARA_SPLIT_RE.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= target_chars:
            units.append(para)
            continue
        for sent in _SENT_SPLIT_RE.split(para):
            sent = sent.strip()
            if not sent:
                continue
            while len(sent) > target_chars:
                units.append(sent[:target_chars])
                sent = sent[target_chars:]
            if sent:
                units.append(sent)
    windows: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for u in units:
        add = len(u) + (2 if cur else 0)
        if cur_len + add > target_chars and cur:
            windows.append("\n\n".join(cur))
            tail = windows[-1][-overlap_chars:]
            cur = [tail, u] if tail else [u]
            cur_len = (len(tail) + 2 + len(u)) if tail else len(u)
        else:
            cur.append(u)
            cur_len += add
    if cur:
        windows.append("\n\n".join(cur))
    minimum_chars = min(160, max(1, target_chars // 4))
    return [w for w in windows if len(w.strip()) >= minimum_chars] or \
        [text[:target_chars]]


def densify_chunks(
    chunks: list[dict[str, Any]], project_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Re-split oversized chunks while preserving every metadata field."""
    from kb.retrieval_config import load_retrieval_config

    config = load_retrieval_config(project_root)
    target = config.target_chunk_tokens * 4
    overlap = config.overlap_tokens * 4
    out: list[dict[str, Any]] = []
    for chunk in chunks:
        pieces = _split_text_dense(
            str(chunk.get("text", "") or ""), target, overlap,
        )
        parent = str(chunk.get("chunk_id", ""))
        for index, piece in enumerate(pieces):
            item = dict(chunk)
            item["chunk_id"] = parent if len(pieces) == 1 else f"{parent}#{index}"
            item["text"] = piece
            out.append(item)
    return out


def assemble_context(chunks: list[RetrievedChunk], max_chars: int) -> str:
    """Join retrieved chunks into a focused context block for the generator,
    de-duplicated and capped at max_chars. Each chunk is labeled with its
    source document so the model can ground its output."""
    seen: set[str] = set()
    parts: list[str] = []
    total = 0
    for ch in chunks:
        body = (ch.text or "").strip()
        if not body or body in seen:
            continue
        seen.add(body)
        header = f"[Source: {ch.doc}{(' - ' + ch.title) if ch.title else ''}]"
        block = f"{header}\n{body}"
        if total + len(block) > max_chars and parts:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)
