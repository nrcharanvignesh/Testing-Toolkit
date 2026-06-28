"""
embeddings.py
Local text embeddings - GPU-accelerated when available, CPU fallback.

The recommended production backend is Qdrant's "fastembed", which runs
ONNX models (optionally CUDA-accelerated via CUDAExecutionProvider). The
default model is BAAI/bge-small-en-v1.5 in int8 (~32 MB on disk, 384 dims).

When a CUDA GPU is available, ONNX Runtime uses it automatically via the
providers list from core.hardware. On CPU-only machines, the int8 quantized
model is still fast enough for real-time retrieval on U-series laptop CPUs.

This module is capability-gated: if neither "fastembed" nor
"sentence-transformers" is importable, get_text_embedder() returns None and
the rest of the system runs in lexical-only (BM25) mode.

All vectors are returned L2-normalized float32, so cosine similarity is a
plain dot product.

ASCII-only; fully type-hinted.
"""

from __future__ import annotations

import os
from typing import Any, Final, Protocol

import numpy as np

from kb.model_bundle import bundled_models_dir


def dense_enforced() -> bool:
    """Whether dense indexing is ENFORCED (the default).

    When enforced, the KB pipeline must build dense vectors with the bundled
    local model; it must NOT silently fall back to lexical-only retrieval. Set
    the environment variable ``TT_ENFORCE_DENSE=0`` to opt out (e.g. a
    deliberately model-less, lexical-only deployment). Any other value - or an
    unset variable - keeps enforcement ON.
    """
    return (os.environ.get("TT_ENFORCE_DENSE", "1").strip() or "1") != "0"

# Recommended small CPU model (override via set_default_model if desired).
DEFAULT_MODEL: Final[str] = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM: Final[int] = 384
_EMBED_BATCH: Final[int] = 64


class TextEmbedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        ...


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat.reshape(1, -1)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (mat / norms).astype(np.float32)


def _accelerated_providers() -> list[str] | None:
    """ONNX providers to request, preferring an accelerator when one exists.

    Returns the ordered provider list from core.hardware (e.g.
    ["CUDAExecutionProvider", "CPUExecutionProvider"]) only when a real
    accelerator is present; otherwise None so we don't perturb the default
    CPU-only construction. Fully OS/architecture-agnostic (CUDA on
    Windows/Linux, CoreML on Apple Silicon, DirectML on Windows) and fail-safe.
    """
    try:
        from core.hardware import gpu_available, onnx_providers

        if not gpu_available():
            return None
        providers = onnx_providers()
        # Only meaningful if it contains something beyond plain CPU.
        if providers and any(p != "CPUExecutionProvider" for p in providers):
            return providers
    except Exception:
        pass
    return None


def _construct_with_fallbacks(cls: Any, base_kwargs: dict[str, Any]) -> Any:
    """Construct a fastembed model, requesting GPU providers when available and
    degrading gracefully on older fastembed builds.

    ``base_kwargs`` are always kept (e.g. model_name, cache_dir). The optional
    accelerator/offline keywords are layered on top and dropped one at a time
    (newest/most-optional first) if a given fastembed version rejects them with
    a TypeError, so we never lose the offline cache_dir just because
    ``providers`` or ``local_files_only`` is unsupported.
    """
    # Optional kwargs in drop order: try ALL, then drop providers, then drop
    # local_files_only — base_kwargs always survive.
    optional: dict[str, Any] = {}
    providers = _accelerated_providers()
    if providers is not None:
        optional["providers"] = providers
    if base_kwargs.get("cache_dir"):
        optional["local_files_only"] = True

    drop_order = ["providers", "local_files_only"]
    # Build the sequence of optional-kwarg sets to try, progressively dropping.
    trials: list[dict[str, Any]] = [dict(optional)]
    current = dict(optional)
    for key in drop_order:
        if key in current:
            current = dict(current)
            current.pop(key, None)
            trials.append(dict(current))

    last_exc: Exception | None = None
    for extra in trials:
        try:
            return cls(**base_kwargs, **extra)
        except TypeError as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    return cls(**base_kwargs)


def _build_text_embedding(text_embedding_cls: Any, model_name: str) -> Any:
    """Construct a fastembed TextEmbedding.

    If a project-local model cache is bundled (models/ folder), load STRICTLY
    offline from it via cache_dir + local_files_only=True. Older fastembed
    builds without the local_files_only kwarg fall back to cache_dir only.
    With no bundled cache, use the default behavior (system cache / download).
    When a GPU/accelerator is detected, request the accelerated ONNX providers
    so embedding actually runs on the GPU (CPU otherwise).
    """
    models_dir: str | None = bundled_models_dir()
    if models_dir is not None:
        return _construct_with_fallbacks(
            text_embedding_cls,
            {"model_name": model_name, "cache_dir": models_dir},
        )
    return _construct_with_fallbacks(
        text_embedding_cls, {"model_name": model_name}
    )


class _FastEmbedEmbedder:
    """Wraps fastembed.TextEmbedding (ONNX, CPU)."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from fastembed import TextEmbedding  # type: ignore

        self._model = _build_text_embedding(TextEmbedding, model_name)
        self.name = f"fastembed:{model_name}"
        # Probe the dimension once from a trivial embedding.
        probe = np.asarray(list(self._model.embed(["probe"]))[0],
                           dtype=np.float32)
        self.dim = int(probe.shape[-1])

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vecs = list(self._model.embed(texts, batch_size=_EMBED_BATCH))
        return _l2_normalize(np.asarray(vecs, dtype=np.float32))


class _SentenceTransformerEmbedder:
    """Secondary backend if sentence-transformers is present instead."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        device = "cpu"
        try:
            from core.hardware import gpu_available
            if gpu_available():
                device = "cuda"
        except Exception:
            pass
        self._model = SentenceTransformer(model_name, device=device)
        self.name = f"sentence-transformers:{model_name}"
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vecs = self._model.encode(
            texts, batch_size=_EMBED_BATCH, convert_to_numpy=True,
            normalize_embeddings=True, show_progress_bar=False,
        )
        return np.asarray(vecs, dtype=np.float32)


_DEFAULT_MODEL_NAME = DEFAULT_MODEL


def set_default_model(model_name: str) -> None:
    global _DEFAULT_MODEL_NAME
    if model_name:
        _DEFAULT_MODEL_NAME = model_name


def embedding_backend_available() -> bool:
    """True if a local embedding backend can be imported. Does not load the
    model."""
    return embedding_backend_status()[0]


def embedding_backend_status() -> "tuple[bool, str]":
    """Return (available, reason). 'available' is True only if fastembed or
    sentence-transformers actually imports. 'reason' explains the state in
    plain language - including the real import error and the frozen-app
    pitfall - so the UI/log can tell the user exactly why dense is off."""
    import sys

    errors: list[str] = []
    for mod in ("fastembed", "sentence_transformers"):
        try:
            __import__(mod)
            return True, f"{mod} is importable"
        except ImportError as e:
            errors.append(f"{mod}: {e}")
        except Exception as e:  # noqa: BLE001 - any import-time failure
            errors.append(f"{mod}: {type(e).__name__}: {e}")
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return False, (
            "no embedding backend in this PACKAGED build. A 'pip install' "
            "into your system Python is NOT visible to the packaged app - "
            "bundle fastembed+onnxruntime into the build (see TestingToolkit"
            ".spec) or run from source. Details: " + "; ".join(errors)
        )
    # Running from source: most often simply not installed, or installed in a
    # different interpreter than the one running the app.
    return False, (
        "no embedding backend importable in the running interpreter "
        f"({sys.executable}). Install with: pip install fastembed onnxruntime "
        "- into THIS interpreter. Details: " + "; ".join(errors)
    )


# Records why the last get_text_embedder() build failed, for diagnostics.
_LAST_BUILD_ERROR: str = ""


def last_build_error() -> str:
    return _LAST_BUILD_ERROR


def get_text_embedder(model_name: str | None = None) -> "TextEmbedder | None":
    """Build the best available local embedder, or None if neither backend
    is installed (or model construction failed, e.g. a blocked first-run
    model download). Never raises; records the failure for diagnostics."""
    global _LAST_BUILD_ERROR
    name = model_name or _DEFAULT_MODEL_NAME
    errs: list[str] = []
    try:
        return _FastEmbedEmbedder(name)
    except Exception as e:  # noqa: BLE001
        errs.append(f"fastembed: {type(e).__name__}: {e}")
    try:
        return _SentenceTransformerEmbedder(name)
    except Exception as e:  # noqa: BLE001
        errs.append(f"sentence-transformers: {type(e).__name__}: {e}")
    _LAST_BUILD_ERROR = "; ".join(errs)
    return None


def get_text_embedder_strict(model_name: str | None = None) -> "TextEmbedder":
    """Like get_text_embedder() but RAISES instead of returning None.

    Used when dense indexing is enforced: the caller wants a hard, visible
    failure (surfaced in the index job log) rather than a silent downgrade to
    lexical-only retrieval. The error message includes the backend status and
    the last construction error (e.g. a missing bundled model file) so the
    cause is actionable.
    """
    emb = get_text_embedder(model_name)
    if emb is not None:
        return emb
    avail, reason = embedding_backend_status()
    detail = last_build_error() or reason
    raise RuntimeError(
        "Dense indexing is enforced but the local embedding model could not "
        "be initialized. " + detail + " (Ensure the bundled model files are "
        "present; reinstall the agent to restore them, or set "
        "TT_ENFORCE_DENSE=0 to allow lexical-only retrieval.)"
    )
