"""
vector_store.py
Embedded, on-disk vector store for dense retrieval - no server, no network.

Two backends behind one tiny interface:

  * LanceVectorStore  - uses LanceDB when it is installed. LanceDB is an
    embedded (in-process) columnar vector DB that memory-maps from disk, so
    it scales to large KBs within laptop RAM and supports ANN. Preferred in
    production.

  * NumpyVectorStore  - a dependency-light fallback that keeps an L2-
    normalized float32 matrix on disk (vectors.npy + ids.json) and does an
    exact cosine search. Exact and perfectly adequate for the per-project
    KB sizes a desktop app handles; needs only NumPy.

Vectors are assumed L2-normalized (see embeddings.py), so cosine similarity
is a dot product. open_vector_store() picks LanceDB if available, else
NumPy.

ASCII-only; fully type-hinted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import numpy as np


class VectorStore(Protocol):
    def add(self, ids: list[str], vectors: np.ndarray) -> None: ...
    def search(self, query: np.ndarray, k: int) -> list[tuple[str, float]]: ...
    def save(self) -> None: ...
    def count(self) -> int: ...


class NumpyVectorStore:
    """Exact cosine search over an in-memory/on-disk normalized matrix."""

    def __init__(self, store_dir: Path | str, dim: int) -> None:
        self.dir = Path(store_dir)
        self.dim = int(dim)
        self._ids: list[str] = []
        self._mat: np.ndarray = np.zeros((0, self.dim), dtype=np.float32)
        self._pending: list[np.ndarray] = []
        self._vec_path = self.dir / "vectors.npy"
        self._ids_path = self.dir / "vector_ids.json"
        self._load()

    def _load(self) -> None:
        try:
            if self._vec_path.exists() and self._ids_path.exists():
                mat = np.load(str(self._vec_path))
                ids = json.loads(self._ids_path.read_text(encoding="utf-8"))
                if (isinstance(ids, list)
                        and getattr(mat, "ndim", 0) == 2
                        and mat.shape[0] == len(ids)):
                    self._mat = mat.astype(np.float32)
                    self._ids = [str(x) for x in ids]
                    self.dim = int(mat.shape[1]) if mat.shape[0] else self.dim
        except (OSError, ValueError, json.JSONDecodeError):
            self._ids = []
            self._mat = np.zeros((0, self.dim), dtype=np.float32)

    def add(self, ids: list[str], vectors: np.ndarray) -> None:
        if not ids:
            return
        vecs = np.asarray(vectors, dtype=np.float32)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        if vecs.shape[0] != len(ids):
            raise ValueError("ids and vectors length mismatch")
        if vecs.shape[1] != self.dim and self._mat.shape[0] == 0:
            self.dim = int(vecs.shape[1])
            self._mat = np.zeros((0, self.dim), dtype=np.float32)
        if self._mat.shape[0] == 0:
            self._mat = vecs.copy()
        else:
            if vecs.shape[1] != self._mat.shape[1]:
                raise ValueError("vector dim mismatch")
            self._pending.append(vecs)
        self._ids.extend(str(x) for x in ids)

    def _flush_pending(self) -> None:
        if self._pending:
            parts = [self._mat] + self._pending
            self._mat = np.vstack(parts)
            self._pending.clear()

    def search(self, query: np.ndarray, k: int) -> list[tuple[str, float]]:
        self._flush_pending()
        if self._mat.shape[0] == 0:
            return []
        q = np.asarray(query, dtype=np.float32).reshape(-1)
        if q.shape[0] != self._mat.shape[1]:
            return []
        sims = self._mat @ q  # cosine (both normalized)
        n = max(0, min(int(k), sims.shape[0]))
        if n == 0:
            return []
        idx = np.argpartition(sims, -n)[-n:]
        idx = idx[np.argsort(sims[idx])[::-1]]
        return [(self._ids[int(i)], float(sims[int(i)])) for i in idx]

    def save(self) -> None:
        self._flush_pending()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            np.save(str(self._vec_path), self._mat)
            self._ids_path.write_text(
                json.dumps(self._ids, ensure_ascii=True), encoding="utf-8"
            )
        except OSError:
            pass

    def count(self) -> int:
        return len(self._ids)


class LanceVectorStore:
    """LanceDB-backed store (embedded). Only constructed when lancedb is
    importable; otherwise open_vector_store falls back to NumpyVectorStore."""

    def __init__(self, store_dir: Path | str, dim: int) -> None:
        import lancedb  # type: ignore

        self.dir = Path(store_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dim = int(dim)
        self._db = lancedb.connect(str(self.dir / "lance"))
        self._table_name = "chunks"
        self._table = None
        try:
            self._table = self._db.open_table(self._table_name)
        except Exception:
            self._table = None

    def add(self, ids: list[str], vectors: np.ndarray) -> None:
        if not ids:
            return
        vecs = np.asarray(vectors, dtype=np.float32)
        rows = [{"id": str(i), "vector": vecs[n].tolist()}
                for n, i in enumerate(ids)]
        if self._table is None:
            self._table = self._db.create_table(self._table_name, data=rows)
        else:
            self._table.add(rows)

    def search(self, query: np.ndarray, k: int) -> list[tuple[str, float]]:
        if self._table is None:
            return []
        q = np.asarray(query, dtype=np.float32).reshape(-1).tolist()
        try:
            res = (self._table.search(q).metric("cosine")
                   .limit(int(k)).to_list())
        except Exception:
            return []
        out: list[tuple[str, float]] = []
        for r in res:
            rid = str(r.get("id", ""))
            dist = float(r.get("_distance", 1.0))
            # cosine distance is in [0, 2]; clamp similarity to [0, 1]
            out.append((rid, max(0.0, 1.0 - dist)))
        return out

    def save(self) -> None:
        # LanceDB persists on write; nothing to flush explicitly.
        return

    def count(self) -> int:
        try:
            return int(self._table.count_rows()) if self._table else 0
        except Exception:
            return 0


def lancedb_available() -> bool:
    try:
        __import__("lancedb")
        return True
    except Exception:
        return False


def open_vector_store(store_dir: Path | str, dim: int) -> "VectorStore":
    """Open the best available local vector store for store_dir. Prefers
    LanceDB; falls back to the NumPy store. Never raises."""
    if lancedb_available():
        try:
            return LanceVectorStore(store_dir, dim)
        except Exception:
            pass
    return NumpyVectorStore(store_dir, dim)
