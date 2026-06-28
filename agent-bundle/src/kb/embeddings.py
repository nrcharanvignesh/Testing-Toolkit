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
    degrading gracefully on older fastembed builds AND on accelerators that
    fail at session-build time.

    ``base_kwargs`` are always kept (e.g. model_name, cache_dir). Two optional
    keywords are layered on top:

    * ``providers``         - request a hardware accelerator (CUDA / CoreML /
                              DirectML) when one is detected.
    * ``local_files_only``  - force strictly-offline load from the bundled
                              cache.

    These are tried in priority order and dropped independently. We catch a
    BROAD ``Exception`` per trial (not just ``TypeError``) because:

    * Old fastembed builds reject an unknown kwarg with ``TypeError``.
    * An accelerator EP can be *present* yet fail when the session is actually
      built - e.g. Apple's ``CoreMLExecutionProvider`` on unsupported ops, or
      Windows ``DmlExecutionProvider`` - raising an ONNX Runtime
      ``RuntimeException``/``Fail`` rather than ``TypeError``.

    Catching only ``TypeError`` would let such a runtime failure abort the whole
    construction and silently disable dense retrieval on Apple Silicon / DML
    machines. By falling back, we keep dense retrieval working on the CPU EP
    (and keep the offline cache) instead of losing it entirely.

    The trial order maximizes what survives regardless of which optional kwarg
    is the culprit: full -> drop accelerator (keep offline) -> drop offline-flag
    (keep accelerator) -> base only. A successful trial returns immediately, so
    healthy machines incur no overhead.
    """
    providers = _accelerated_providers()
    offline = bool(base_kwargs.get("cache_dir"))

    with_providers: dict[str, Any] = (
        {"providers": providers} if providers is not None else {}
    )
    with_offline: dict[str, Any] = {"local_files_only": True} if offline else {}

    # Ordered, de-duplicated optional-kwarg sets to try.
    raw_trials: list[dict[str, Any]] = [
        {**with_providers, **with_offline},  # best: accelerator + offline
        {**with_offline},                    # CoreML/DML build failed: CPU+offline
        {**with_providers},                  # old fastembed (no lfo): keep accel
        {},                                  # last resort: CPU only
    ]
    trials: list[dict[str, Any]] = []
    for t in raw_trials:
        if t not in trials:
            trials.append(t)

    last_exc: Exception | None = None
    for extra in trials:
        try:
            return cls(**base_kwargs, **extra)
        except Exception as exc:  # noqa: BLE001 - any build failure -> degrade
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


# ---------------------------------------------------------------------
# Runtime execution-provider telemetry
# ---------------------------------------------------------------------
# What execution provider(s) the loaded ONNX models ACTUALLY bound to, recorded
# at build time. This is distinct from core.hardware capability detection: an
# accelerator can be *present* yet the model still fall back to CPU (e.g. CoreML
# rejected the graph). Diagnostics/metrics read this to show the truth, not the
# theoretical capability. Keyed by role: "embedder" / "reranker".
_RUNTIME: dict[str, dict[str, Any]] = {}


def _probe_onnx_providers(obj: Any, _depth: int = 0,
                          _seen: set[int] | None = None) -> list[str] | None:
    """Best-effort discovery of the onnxruntime ``get_providers()`` of whatever
    ONNX session a fastembed model wraps.

    fastembed's internal layout (TextEmbedding -> worker model(s) -> ORT
    InferenceSession) changes across versions and may hold a *list* of worker
    models, so we walk a small, bounded graph of likely attributes rather than
    hard-coding one path. Returns the active provider list (e.g.
    ["CoreMLExecutionProvider", "CPUExecutionProvider"]) or None. Never raises.
    """
    if obj is None or _depth > 5:
        return None
    if _seen is None:
        _seen = set()
    oid = id(obj)
    if oid in _seen:
        return None
    _seen.add(oid)

    getp = getattr(obj, "get_providers", None)
    if callable(getp):
        try:
            provs = getp()
            if provs:
                return [str(p) for p in provs]
        except Exception:
            pass

    # A fastembed model can keep a list of per-thread worker models.
    for seq_attr in ("models", "_models"):
        seq = getattr(obj, seq_attr, None)
        if isinstance(seq, (list, tuple)):
            for item in seq:
                r = _probe_onnx_providers(item, _depth + 1, _seen)
                if r:
                    return r

    for name in ("model", "_model", "session", "_session", "onnx_session",
                 "ort_session", "embedding", "encoder"):
        child = getattr(obj, name, None)
        if child is not None:
            r = _probe_onnx_providers(child, _depth + 1, _seen)
            if r:
                return r

    # Last resort: shallow scan of instance attributes for a nested session.
    try:
        for v in list(vars(obj).values())[:24]:
            if hasattr(v, "get_providers") or hasattr(v, "__dict__"):
                r = _probe_onnx_providers(v, _depth + 1, _seen)
                if r:
                    return r
    except Exception:
        pass
    return None


def record_model_runtime(role: str, model_name: str, model_obj: Any) -> None:
    """Record the ACTUAL runtime backend for a loaded model (role =
    'embedder' | 'reranker'). Captures the real ONNX providers so diagnostics
    can report GPU-vs-CPU truthfully. Never raises."""
    try:
        provs = _probe_onnx_providers(model_obj)
        accelerated = bool(
            provs and any(p != "CPUExecutionProvider" for p in provs)
        )
        _RUNTIME[role] = {
            "model": model_name,
            "providers": provs,
            "accelerated": accelerated,
            # The active EP is the first one ORT lists (highest priority bound).
            "active_provider": provs[0] if provs else None,
        }
    except Exception:
        pass


def model_runtime_info() -> dict[str, dict[str, Any]]:
    """Snapshot of what the loaded models are actually running on. Empty until
    at least one model has been built this process."""
    return {role: dict(info) for role, info in _RUNTIME.items()}


def runtime_accelerated() -> bool:
    """True if ANY loaded model bound to a non-CPU execution provider."""
    return any(i.get("accelerated") for i in _RUNTIME.values())


def active_execution_provider() -> str | None:
    """The accelerator EP a loaded model actually bound to (e.g.
    'CoreMLExecutionProvider'), or None if everything is on CPU / unknown."""
    for info in _RUNTIME.values():
        if info.get("accelerated") and info.get("active_provider"):
            return str(info["active_provider"])
    return None


class _FastEmbedEmbedder:
    """Wraps fastembed.TextEmbedding (ONNX, CPU)."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from fastembed import TextEmbedding  # type: ignore

        self._model = _build_text_embedding(TextEmbedding, model_name)
        self.name = f"fastembed:{model_name}"
        # Record the ACTUAL execution provider(s) this session bound to.
        record_model_runtime("embedder", self.name, self._model)
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
