"""
reranker.py
Final-stage precision reranking - local and CPU-friendly, no API required.

A small cross-encoder rescoring the top hybrid candidates is the single
biggest precision win in offline RAG. The recommended model is
ms-marco-MiniLM-L-6-v2 (int8 ONNX, ~22-25 MB) via fastembed's
TextCrossEncoder - fast enough to rerank 20-50 candidates in tens of ms on a
U-series CPU.

Capability-gated: if fastembed (with the cross-encoder) is not installed,
get_reranker() returns None and callers keep the fused (RRF) order. An
LLM-based reranker is also provided for environments where only the
completions API is available and maximum precision is wanted on a small
candidate set.

ASCII-only; fully type-hinted.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final, Protocol

from kb.model_bundle import bundled_models_dir

DEFAULT_RERANKER: Final[str] = "Xenova/ms-marco-MiniLM-L-6-v2"
_INT_RE: Final[re.Pattern[str]] = re.compile(r"\d+")


class Reranker(Protocol):
    name: str

    def rerank(
        self, query: str, candidates: list[tuple[str, str]], top_k: int,
    ) -> list[tuple[str, float]]:
        """candidates: list of (id, text). Returns (id, score) best-first."""
        ...


def _build_cross_encoder(cross_encoder_cls: Any, model_name: str) -> Any:
    """Construct a fastembed TextCrossEncoder.

    Mirrors the embedder logic: when a project-local model cache is bundled,
    load strictly offline via cache_dir + local_files_only=True (falling back
    to cache_dir only on older fastembed builds), and request the accelerated
    ONNX providers when a GPU is detected so reranking runs on the GPU. Reuses
    the embedder's construction ladder so both models behave identically.
    """
    from kb.embeddings import _construct_with_fallbacks

    models_dir: str | None = bundled_models_dir()
    if models_dir is not None:
        return _construct_with_fallbacks(
            cross_encoder_cls,
            {"model_name": model_name, "cache_dir": models_dir},
        )
    return _construct_with_fallbacks(
        cross_encoder_cls, {"model_name": model_name}
    )


class _FastEmbedReranker:
    def __init__(self, model_name: str = DEFAULT_RERANKER) -> None:
        from fastembed.rerank.cross_encoder import (  # type: ignore
            TextCrossEncoder,
        )

        self._model = _build_cross_encoder(TextCrossEncoder, model_name)
        self.name = f"fastembed:{model_name}"
        # Record the ACTUAL execution provider(s) this session bound to, so
        # diagnostics report GPU-vs-CPU truthfully for the reranker too.
        try:
            from kb.embeddings import record_model_runtime

            record_model_runtime("reranker", self.name, self._model)
        except Exception:
            pass

    def rerank(
        self, query: str, candidates: list[tuple[str, str]], top_k: int,
    ) -> list[tuple[str, float]]:
        if not candidates:
            return []
        docs = [text for _id, text in candidates]
        scores = list(self._model.rerank(query, docs))
        paired = [
            (candidates[i][0], float(scores[i])) for i in range(len(docs))
        ]
        paired.sort(key=lambda kv: kv[1], reverse=True)
        return paired[:max(0, int(top_k))]


def reranker_available() -> bool:
    try:
        __import__("fastembed")
        return True
    except Exception:
        return False


def get_reranker(model_name: str | None = None) -> "Reranker | None":
    """Best available local reranker, or None. Never raises."""
    try:
        return _FastEmbedReranker(model_name or DEFAULT_RERANKER)
    except Exception:
        return None


def get_reranker_strict(model_name: str | None = None) -> "Reranker":
    """Like get_reranker() but RAISES instead of returning None.

    Used when dense indexing is enforced so that BOTH local models (the dense
    embedder and this cross-encoder reranker) are verified at index time. The
    error message includes the underlying construction error so a missing
    bundled model file is actionable.
    """
    try:
        return _FastEmbedReranker(model_name or DEFAULT_RERANKER)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Dense indexing is enforced but the local reranker model could "
            f"not be initialized: {type(e).__name__}: {e}. (Ensure the bundled "
            "model files are present; reinstall the agent to restore them, or "
            "set TT_ENFORCE_DENSE=0 to allow lexical-only retrieval.)"
        ) from e


# ---------------------------------------------------------------------
# Optional: LLM-as-reranker (completions API only). Use on a SMALL
# candidate set; it costs one API call. Returns ids best-first.
# ---------------------------------------------------------------------
def llm_rerank(
    client: Any,
    model: str,
    query: str,
    candidates: list[tuple[str, str]],
    top_k: int,
) -> list[str] | None:
    """Ask the LLM to order candidate ids by relevance to the query. Returns
    an ordered list of ids, or None on any failure (caller keeps RRF order).
    Deterministic prompt; temperature 0.0."""
    if client is None or not candidates:
        return None
    listing = []
    for idx, (_cid, text) in enumerate(candidates):
        snippet = " ".join((text or "").split())[:500]
        listing.append(f"[{idx}] {snippet}")
    user = (
        "You are ranking passages by how well they help answer the QUERY.\n"
        "Return ONLY a JSON array of passage indices, most relevant first, "
        "including only passages that are actually relevant.\n\n"
        f"QUERY:\n{query}\n\nPASSAGES:\n" + "\n".join(listing)
    )
    try:
        resp = client.complete_async  # presence check only
    except Exception:
        return None
    try:
        import asyncio

        out = asyncio.run(client.complete_async(
            model=model,
            system="You are a precise passage reranker. Output JSON only.",
            user=user, max_tokens=256, temperature=0.0,
        ))
        text = getattr(out, "text", "") or ""
        m = re.search(r"\[[^\]]*\]", text)
        if not m:
            return None
        order = json.loads(m.group(0))
        ids: list[str] = []
        for v in order:
            try:
                i = int(v)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(candidates):
                ids.append(candidates[i][0])
        return ids[:max(0, int(top_k))] or None
    except Exception:
        return None
