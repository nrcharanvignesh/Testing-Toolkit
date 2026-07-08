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
import json
import re
import time
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
_SCHEMA: Final[int] = 1

_CHUNKS_FILE: Final[str] = "chunks.jsonl"
_BM25_FILE: Final[str] = "bm25.json"
_MANIFEST_FILE: Final[str] = "manifest.json"


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


# ---------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------
def build_hybrid_index(
    index_dir: Path | str,
    chunks: list[dict[str, Any]],
    embedder: Any | None = None,
    on_log: LogFn | None = None,
    should_stop: StopFn | None = None,
    enforce_dense: bool = False,
) -> bool:
    """Build (or refresh) the hybrid index from chunk dicts. Each chunk dict
    needs keys: chunk_id, doc, title, text, and optional context.

    BM25 is always built (fast). If an embedder is provided, dense vectors
    are built incrementally and saved in batches so a crash resumes the
    embedding step (the vector store already on disk is reused; only missing
    chunk ids are embedded). Returns True on success."""
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)

    ids = [str(c.get("chunk_id", "")) for c in chunks]
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
            store = open_vector_store(index_dir, dim or 384)
            already = store.count()
            if already < len(ids):
                _log(on_log, f"[INFO] Embedding {len(ids) - already} chunk(s) "
                             f"with {model_name} (CPU)...")
                i = already
                while i < len(ids):
                    if should_stop is not None and should_stop():
                        store.save()
                        _log(on_log, "[WARN] Embedding paused; will resume.")
                        return True
                    batch_ids = ids[i:i + _EMBED_BATCH]
                    batch_txt = texts[i:i + _EMBED_BATCH]
                    vecs = _embed_texts(embedder, batch_txt)
                    store.add(batch_ids, vecs)
                    store.save()
                    i += _EMBED_BATCH
                    del vecs
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
        write_encrypted_text(index_dir / _MANIFEST_FILE, json.dumps({
            "schema": _SCHEMA,
            "n_chunks": len(ids),
            "dense": bool(dim),
            "dim": int(dim),
            "model": model_name,
            "built_at": time.time(),
        }, ensure_ascii=True))
    except OSError:
        pass
    return True


# ---------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------
class HybridRetriever:
    """Loads a built hybrid index and answers retrieve(query, k). Dense and
    rerank stages activate only if their backends are importable."""

    def __init__(self, index_dir: Path | str) -> None:
        self.dir = Path(index_dir)
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
                )
        except (OSError, json.JSONDecodeError, ValueError):
            self._chunks = {}

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
            from core.app_config import RERANK_MODEL
            from core.model_router import Task, route
            from core.settings_store import (
                KEY_BASE_URL, build_llm_client, build_runtime_config,
                get_setting, load_api_key,
            )

            model = route(Task.LLM_RERANK)
            client = build_llm_client()
            api_key = (load_api_key() or "").strip()
            base_url = get_setting(KEY_BASE_URL)
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
        """Return up to top_k chunks most relevant to query, best-first."""
        if not self.is_available() or not (query or "").strip():
            return []
        rankings: list[list[str]] = []

        bm25_hits = self._bm25.top_n(query, candidate_k) if self._bm25 else []
        if bm25_hits:
            rankings.append([cid for cid, _s in bm25_hits])

        embedder = self._ensure_embedder()
        if embedder is not None and self._store is not None:
            try:
                qv = _embed_texts(embedder, [query])[0]
                dense_hits = self._store.search(qv, candidate_k)
                if dense_hits:
                    rankings.append([cid for cid, _s in dense_hits])
            except Exception:
                pass

        if not rankings:
            return []
        fused_ids = rrf_fuse(rankings, top_n=candidate_k)

        # Optional cross-encoder rerank over the fused candidate texts.
        ordered_ids = fused_ids
        if use_reranker:
            reranker = self._ensure_reranker()
            if reranker is not None:
                cand = [(cid, self._chunks[cid].text)
                        for cid in fused_ids if cid in self._chunks]
                try:
                    reranked = reranker.rerank(query, cand, top_k)
                    if reranked:
                        ordered_ids = [cid for cid, _s in reranked]
                except Exception:
                    pass

        # Build the final list, skipping near-duplicate passages so the
        # top_k are DISTINCT. KBs often carry DRAFT / OLD DRAFT / final copies
        # of the same requirements; without this, several near-identical
        # chunks would consume the budget. Signature = normalized (collapsed
        # whitespace, lowercased) prefix; first (best-ranked) copy wins.
        out: list[RetrievedChunk] = []
        seen_sigs: set[str] = set()
        missing_count = 0
        for cid in ordered_ids:
            if len(out) >= top_k:
                break
            ch = self._chunks.get(cid)
            if ch is None:
                missing_count += 1
                continue
            sig = " ".join((ch.text or "").split()).lower()[:512]
            if sig and sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            out.append(RetrievedChunk(
                chunk_id=ch.chunk_id, doc=ch.doc, title=ch.title,
                text=ch.text, score=1.0 / (1 + len(out)),
            ))
        if missing_count > 0 and missing_count > len(ordered_ids) * 0.1:
            import warnings
            warnings.warn(
                f"[KB] {missing_count}/{len(ordered_ids)} candidate chunk IDs "
                f"missing from store - index may need rebuild",
                stacklevel=2,
            )
        return out


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
        ranked = sorted(scores.values(), key=lambda t: t[0], reverse=True)
        merged = [ch for _s, ch in ranked[:candidate_k]]
        # Single rerank pass on the merged candidates.
        reranker = self._ensure_reranker()
        if reranker is not None and merged:
            cand = [(ch.chunk_id, ch.text) for ch in merged]
            try:
                reranked = reranker.rerank(
                    " ".join(q[:200] for q in queries[:4]), cand, top_k
                )
                if reranked:
                    id_to_ch = {ch.chunk_id: ch for ch in merged}
                    return [id_to_ch[cid] for cid, _s in reranked
                            if cid in id_to_ch]
            except Exception:
                pass
        return merged[:top_k]


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
            if re.match(r"^(\d+[\.\)]\s|[-*•]\s|Given\b|When\b|Then\b)",
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
        p = Path(index_dir) / _MANIFEST_FILE
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
    want_dense: bool = False,
) -> bool:
    """Cheap check (reads only the small manifest) to decide whether the
    hybrid index already reflects the current chunk set, so callers can skip
    a redundant rebuild. True only if the manifest exists, its chunk count
    matches, it was built at/after the source index, and - when want_dense
    is True - the existing index already has dense vectors (so flipping
    dense on forces a rebuild that adds them)."""
    try:
        from kb.kb_crypto import read_decrypted_text
        p = Path(index_dir) / _MANIFEST_FILE
        if not p.exists():
            return False
        text = read_decrypted_text(p)
        if text is None:
            return False
        man = json.loads(text)
        if want_dense and not bool(man.get("dense", False)):
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
_DENSE_TARGET_CHARS: Final[int] = 2200      # ~550 tokens
_DENSE_OVERLAP_CHARS: Final[int] = 280      # ~70 tokens of overlap
_DENSE_MIN_CHARS: Final[int] = 160

_PARA_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"\n\s*\n")
_SENT_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")


def _split_text_dense(text: str) -> list[str]:
    """Split one passage into ~_DENSE_TARGET_CHARS windows with overlap,
    respecting paragraph then sentence boundaries; hard-splits only when a
    single unit exceeds the target."""
    text = (text or "").strip()
    if len(text) <= _DENSE_TARGET_CHARS:
        return [text] if text else []
    units: list[str] = []
    for para in _PARA_SPLIT_RE.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= _DENSE_TARGET_CHARS:
            units.append(para)
            continue
        for sent in _SENT_SPLIT_RE.split(para):
            sent = sent.strip()
            if not sent:
                continue
            while len(sent) > _DENSE_TARGET_CHARS:
                units.append(sent[:_DENSE_TARGET_CHARS])
                sent = sent[_DENSE_TARGET_CHARS:]
            if sent:
                units.append(sent)
    windows: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for u in units:
        add = len(u) + (2 if cur else 0)
        if cur_len + add > _DENSE_TARGET_CHARS and cur:
            windows.append("\n\n".join(cur))
            tail = windows[-1][-_DENSE_OVERLAP_CHARS:]
            cur = [tail, u] if tail else [u]
            cur_len = (len(tail) + 2 + len(u)) if tail else len(u)
        else:
            cur.append(u)
            cur_len += add
    if cur:
        windows.append("\n\n".join(cur))
    return [w for w in windows if len(w.strip()) >= _DENSE_MIN_CHARS] or \
        [text[:_DENSE_TARGET_CHARS]]


def densify_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-split coarse chunk dicts into fine, overlapping retrieval chunks.
    Each input chunk (chunk_id/doc/title/text) becomes one or more chunks
    with deterministic ids '<parent>#<n>', stable across rebuilds."""
    out: list[dict[str, Any]] = []
    for c in chunks:
        pieces = _split_text_dense(str(c.get("text", "") or ""))
        if not pieces:
            continue
        parent = str(c.get("chunk_id", ""))
        doc = str(c.get("doc", ""))
        title = str(c.get("title", ""))
        if len(pieces) == 1:
            out.append({"chunk_id": parent, "doc": doc, "title": title,
                        "text": pieces[0]})
        else:
            for n, piece in enumerate(pieces):
                out.append({"chunk_id": f"{parent}#{n}", "doc": doc,
                            "title": title, "text": piece})
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
