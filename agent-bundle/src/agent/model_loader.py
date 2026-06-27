"""
model_loader.py
Preload ONNX embedding + reranker models at agent startup.
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
        from kb.embeddings import get_embedder
        _embedder = get_embedder()
    except Exception:
        _embedder = None
    try:
        from kb.reranker import get_reranker
        _reranker = get_reranker()
    except Exception:
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
