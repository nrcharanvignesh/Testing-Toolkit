"""
model_loader.py
Warm the API embedder client at agent startup. Reranking is a stateless
gateway API call, so it needs no preloading.
Runs in a background thread so the server accepts /health immediately.
"""

from __future__ import annotations

import threading
from typing import Any

_models_ready = threading.Event()
_embedder: Any = None
_reranker: Any = None


def preload_models() -> None:
    """Kick off model loading in a background thread."""
    t = threading.Thread(target=_load, daemon=True)
    t.start()


def _load() -> None:
    global _embedder, _reranker
    try:
        from kb.embeddings import get_text_embedder
        _embedder = get_text_embedder()
    except Exception:
        _embedder = None
    # Reranking is a stateless gateway API call (kb.reranker.native_rerank),
    # so there is no model to preload here.
    _reranker = None
    _models_ready.set()


def models_loaded() -> bool:
    return _models_ready.is_set()


def get_cached_embedder() -> Any:
    _models_ready.wait(timeout=60)
    return _embedder


def get_cached_reranker() -> Any:
    _models_ready.wait(timeout=60)
    return _reranker
