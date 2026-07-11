from __future__ import annotations

from pathlib import Path

import numpy as np

from kb.retrieval import HybridRetriever, RetrievedChunk
from kb.retrieval_config import RetrievalConfig


class _FakeBm25:
    def __init__(self, hits: dict[str, list[tuple[str, float]]]) -> None:
        self._hits = hits

    def top_n(self, query: str, n: int) -> list[tuple[str, float]]:
        return self._hits.get(query, [])[:n]


class _FakeStore:
    def __init__(self, hits: dict[str, list[tuple[str, float]]], vectors: dict[str, np.ndarray]) -> None:
        self._hits = hits
        self._vectors = vectors
        self.query = ""

    def search(self, _vector: np.ndarray, n: int) -> list[tuple[str, float]]:
        return self._hits.get(self.query, [])[:n]

    def vectors_for(self, ids: list[str]) -> dict[str, np.ndarray]:
        return {chunk_id: self._vectors[chunk_id] for chunk_id in ids if chunk_id in self._vectors}


class _FakeEmbedder:
    dim = 3
    name = "eval"

    def embed(self, texts: list[str]) -> np.ndarray:
        del texts
        return np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)


def _chunk(chunk_id: str, doc: str, priority: float, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id, doc=doc, title=text, text=text,
        source_priority=priority, section_path="Requirements",
        document_role="specification",
    )


def _retriever(tmp_path: Path, query: str) -> HybridRetriever:
    chunks = {
        "auth": _chunk("auth", "approved/spec.md", 0.95, "Authentication requires a signed access token."),
        "auth_copy": _chunk("auth_copy", "archive/spec-copy.md", 0.4, "Authentication requires a signed access token."),
        "manual": _chunk("manual", "guide/manual.md", 0.7, "The login workflow validates the user session."),
        "template": _chunk("template", "templates/locale.txt", 0.2, "Generic translated labels and placeholders."),
    }
    vectors = {
        "auth": np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        "auth_copy": np.asarray([0.999, 0.001, 0.0], dtype=np.float32),
        "manual": np.asarray([0.8, 0.6, 0.0], dtype=np.float32),
        "template": np.asarray([0.2, 0.0, 0.98], dtype=np.float32),
    }
    hits = {
        "authentication": [("auth", 0.92), ("auth_copy", 0.91), ("manual", 0.76), ("template", 0.25)],
        "weather on mars": [("template", 0.1), ("manual", 0.09)],
    }
    retriever = object.__new__(HybridRetriever)
    retriever.dir = tmp_path
    retriever.config = RetrievalConfig(final_k=3, fetch_k=20, min_semantic_score=0.3, per_source_cap=2)
    retriever._bm25 = _FakeBm25(hits)
    retriever._chunks = chunks
    retriever._manifest = {"dense": True, "dim": 3}
    retriever._embedder = _FakeEmbedder()
    retriever._embedder_tried = True
    retriever._reranker = None
    retriever._reranker_tried = True
    store = _FakeStore(hits, vectors)
    store.query = query
    retriever._store = store
    return retriever


def _print_results(question: str, results: list[RetrievedChunk]) -> None:
    print(f"[INFO] question={question!r}")
    if not results:
        print("[INFO] insufficient context")
        return
    for result in results:
        print(
            f"[INFO] source={result.doc} score={result.score:.3f} "
            f"semantic={result.semantic_score:.3f} tier={result.tier} "
            f"cluster={result.duplicate_cluster}"
        )


def test_generic_ranking_eval(tmp_path: Path) -> None:
    relevant = _retriever(tmp_path, "authentication").retrieve(
        "authentication", top_k=3, candidate_k=20, use_reranker=False,
    )
    _print_results("authentication", relevant)
    assert relevant[0].doc == "approved/spec.md"
    assert sum(item.chunk_id in {"auth", "auth_copy"} for item in relevant) == 1
    assert len({item.doc for item in relevant}) == len(relevant)

    weak = _retriever(tmp_path, "weather on mars").retrieve(
        "weather on mars", top_k=3, candidate_k=20, use_reranker=False,
    )
    _print_results("weather on mars", weak)
    assert weak == []

