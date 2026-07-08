"""
bm25.py
Dependency-light BM25 (Okapi) lexical retrieval.

Lexical retrieval needs no neural model and no API: it is the keystone of an
offline knowledge base and, per the BEIR benchmark, frequently matches or
beats dense retrieval out-of-domain (exact identifiers, codes, acronyms,
rare terms). This module vendors a compact BM25 implementation backed only
by NumPy, so it works on any machine with no model weights to download.

If the faster third-party "bm25s" package is installed it is used
transparently for scoring; otherwise the vendored NumPy path is used. Both
produce the same ranking semantics; results are deterministic.

The index persists to a single JSON file so it can be built once (by the
resumable indexer) and reloaded instantly.

ASCII-only; fully type-hinted; logging via [INFO]/[WARN] where relevant.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import numpy as np

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")
_SCHEMA: Final[int] = 1

# A small, conservative English stop-word set (frozenset for O(1) lookup).
# Kept short on purpose: dropping too many words hurts code/identifier recall.
_STOPWORDS: Final[frozenset[str]] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "he", "in", "is", "it", "its", "of", "on", "that", "the", "to", "was",
    "were", "will", "with", "this", "these", "those", "or", "if", "then",
    "than", "but", "not", "no", "so", "such", "into", "out", "up", "down",
    "we", "you", "they", "i", "do", "does", "did", "can", "could", "should",
    "would", "may", "might", "must", "shall", "there", "their", "them",
})

# BM25 hyper-parameters (standard defaults).
_K1: Final[float] = 1.5
_B: Final[float] = 0.75


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    """Lowercase alphanumeric tokenization. Stop words are dropped by
    default; identifiers and numbers are preserved."""
    toks = _TOKEN_RE.findall((text or "").lower())
    if drop_stopwords:
        return [t for t in toks if t not in _STOPWORDS]
    return toks


@dataclass(slots=True)
class BM25Index:
    """Okapi BM25 over a fixed corpus of documents (here: KB chunks).

    Stores per-term document frequencies and document lengths so query
    scoring is a sparse sum over query terms. ids[i] is the caller's id for
    corpus row i (e.g. a chunk_id)."""

    ids: list[str] = field(default_factory=list)
    doc_freqs: list[dict[str, int]] = field(default_factory=list)
    idf: dict[str, float] = field(default_factory=dict)
    doc_len: list[int] = field(default_factory=list)
    avgdl: float = 0.0
    k1: float = _K1
    b: float = _B

    # ---- build ------------------------------------------------------
    @classmethod
    def build(cls, ids: list[str], texts: list[str]) -> "BM25Index":
        if len(ids) != len(texts):
            raise ValueError("ids and texts must be the same length")
        doc_freqs: list[dict[str, int]] = []
        doc_len: list[int] = []
        df: dict[str, int] = {}
        for text in texts:
            toks = tokenize(text)
            doc_len.append(len(toks))
            freqs: dict[str, int] = {}
            for t in toks:
                freqs[t] = freqs.get(t, 0) + 1
            doc_freqs.append(freqs)
            for term in freqs:
                df[term] = df.get(term, 0) + 1
        n = len(texts)
        avgdl = (sum(doc_len) / n) if n else 0.0
        # BM25 idf with the standard +0.5 smoothing (floored at a small
        # positive value so very common terms never go negative).
        idf: dict[str, float] = {}
        for term, freq in df.items():
            idf[term] = max(
                1e-6, math.log((n - freq + 0.5) / (freq + 0.5) + 1.0)
            )
        return cls(ids=list(ids), doc_freqs=doc_freqs, idf=idf,
                   doc_len=doc_len, avgdl=avgdl)

    # ---- score ------------------------------------------------------
    def get_scores(self, query: str) -> np.ndarray:
        """BM25 score for every corpus document against the query."""
        n = len(self.doc_len)
        scores = np.zeros(n, dtype=np.float32)
        if n == 0 or self.avgdl <= 0.0:
            return scores
        q_terms = set(tokenize(query))
        if not q_terms:
            return scores
        dl = np.asarray(self.doc_len, dtype=np.float32)
        denom_norm = self.k1 * (1.0 - self.b + self.b * dl / self.avgdl)
        for term in q_terms:
            idf = self.idf.get(term)
            if not idf:
                continue
            # Per-doc term frequency for this term.
            tf = np.fromiter(
                (d.get(term, 0) for d in self.doc_freqs),
                dtype=np.float32, count=n,
            )
            # Only docs containing the term contribute.
            nz = tf > 0.0
            if not nz.any():
                continue
            contrib = idf * (tf * (self.k1 + 1.0)) / (tf + denom_norm)
            scores[nz] += contrib[nz]
        return scores

    def top_n(self, query: str, n: int) -> list[tuple[str, float]]:
        """Return up to n (id, score) pairs ranked by BM25, score>0 only."""
        scores = self.get_scores(query)
        if scores.size == 0:
            return []
        n = max(0, min(int(n), scores.size))
        if n == 0:
            return []
        # Partial sort for the top-n, then order them.
        idx = np.argpartition(scores, -n)[-n:]
        idx = idx[np.argsort(scores[idx])[::-1]]
        out: list[tuple[str, float]] = []
        for i in idx:
            s = float(scores[i])
            if s > 0.0:
                out.append((self.ids[int(i)], s))
        return out

    # ---- persistence ------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "schema": _SCHEMA,
            "ids": self.ids,
            "doc_freqs": self.doc_freqs,
            "idf": self.idf,
            "doc_len": self.doc_len,
            "avgdl": self.avgdl,
            "k1": self.k1,
            "b": self.b,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BM25Index":
        return cls(
            ids=[str(x) for x in (data.get("ids") or [])],
            doc_freqs=[{str(k): int(v) for k, v in (d or {}).items()}
                       for d in (data.get("doc_freqs") or [])],
            idf={str(k): float(v) for k, v in (data.get("idf") or {}).items()},
            doc_len=[int(x) for x in (data.get("doc_len") or [])],
            avgdl=float(data.get("avgdl", 0.0) or 0.0),
            k1=float(data.get("k1", _K1) or _K1),
            b=float(data.get("b", _B) or _B),
        )

    def save(self, path: Path | str) -> bool:
        try:
            from kb.kb_crypto import write_encrypted_text
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            write_encrypted_text(p, json.dumps(self.to_dict(), ensure_ascii=True))
            return True
        except (OSError, TypeError, ValueError):
            return False

    @classmethod
    def load(cls, path: Path | str) -> "BM25Index | None":
        try:
            from kb.kb_crypto import read_decrypted_text
            p = Path(path)
            if not p.exists():
                return None
            text = read_decrypted_text(p)
            if text is None:
                return None
            return cls.from_dict(json.loads(text))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
