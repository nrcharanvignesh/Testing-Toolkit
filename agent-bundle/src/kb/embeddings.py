"""
embeddings.py
Text embeddings via API (primary) or local ONNX (offline fallback).

Primary path: call the GenAI proxy /embeddings endpoint with the configured
embedding model (e.g. azure.text-embedding-3-small). Uses the same API key
and base URL as the LLM chat completions.

Fallback (offline / no API key): local fastembed (ONNX) or sentence-transformers.
If all backends fail, the system runs in lexical-only (BM25) mode.

All vectors are returned L2-normalized float32, so cosine similarity is a
plain dot product.

ASCII-only; fully type-hinted.
"""

from __future__ import annotations

import os
import ssl
import time as _time
from typing import Any, Final, Protocol

import httpx
import numpy as np

from kb.model_bundle import bundled_models_dir

# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------
DEFAULT_MODEL: Final[str] = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM: Final[int] = 384

_RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 529})
_API_BATCH_SIZE: Final[int] = 64
_API_TIMEOUT_SEC: Final[float] = 60.0
_API_RETRY_COUNT: Final[int] = 3
_API_RETRY_BACKOFF: Final[float] = 2.0
# text-embedding-3-small max input is 8191 tokens (~32K chars at ~4 chars/token).
# Truncate to stay safely within the limit.
_MAX_EMBED_CHARS: Final[int] = 30_000


def _pick_embed_batch() -> int:
    try:
        from core.hardware import system_memory_mb
        mem = system_memory_mb()
        if mem <= 4096:
            return 16
        if mem <= 8192:
            return 32
        return 64
    except Exception:
        return 32


_EMBED_BATCH: int = _pick_embed_batch()


# -------------------------------------------------------------------------
# Protocol
# -------------------------------------------------------------------------
class TextEmbedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        ...


# -------------------------------------------------------------------------
# Exceptions
# -------------------------------------------------------------------------
class EmbeddingAPIError(RuntimeError):
    """Raised when the embedding API call fails after retries."""


# -------------------------------------------------------------------------
# Shared helpers
# -------------------------------------------------------------------------
def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat.reshape(1, -1)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (mat / norms).astype(np.float32)


# -------------------------------------------------------------------------
# API Embedder (primary)
# -------------------------------------------------------------------------
class _APIEmbedder:
    """Embedding via the GenAI LiteLLM proxy /embeddings endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dim: int,
        ssl_verify: Any = True,
    ) -> None:
        self.name: str = f"api:{model}"
        self.dim: int = dim
        self._base_url: str = base_url.rstrip("/")
        self._api_key: str = api_key
        self._model: str = model
        self._ssl_verify: Any = ssl_verify

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        # Truncate texts exceeding model token limit
        clamped = [t[:_MAX_EMBED_CHARS] if len(t) > _MAX_EMBED_CHARS else t
                   for t in texts]
        all_vecs: list[np.ndarray] = []
        for i in range(0, len(clamped), _API_BATCH_SIZE):
            batch = clamped[i:i + _API_BATCH_SIZE]
            vecs = self._embed_batch(batch)
            all_vecs.append(vecs)
        mat = np.vstack(all_vecs) if len(all_vecs) > 1 else all_vecs[0]
        return _l2_normalize(mat)

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        """POST /embeddings for a single batch. Retries on transient errors."""
        from core.network_status import report_failure, report_success

        url = f"{self._base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "x-api-key": self._api_key,
            "content-type": "application/json",
            "accept": "application/json",
        }
        body: dict[str, Any] = {
            "input": texts,
            "model": self._model,
            "encoding_format": "float",
            "dimensions": self.dim,
        }
        last_exc: Exception | None = None
        for attempt in range(_API_RETRY_COUNT + 1):
            try:
                with httpx.Client(
                    headers=headers,
                    verify=self._ssl_verify,
                    timeout=httpx.Timeout(_API_TIMEOUT_SEC),
                ) as client:
                    resp = client.post(url, json=body)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                report_failure()
                raise EmbeddingAPIError(
                    f"Cannot reach embedding API at {url}: {e!r}"
                ) from e
            except (ssl.SSLError, ssl.SSLCertVerificationError) as e:
                report_failure()
                raise EmbeddingAPIError(
                    f"TLS error reaching embedding API: {e!r}"
                ) from e
            except httpx.TimeoutException as e:
                last_exc = e
                report_failure()
                _time.sleep(_API_RETRY_BACKOFF * (attempt + 1))
                continue

            if resp.status_code in (401, 403):
                report_failure()
                raise EmbeddingAPIError(
                    f"Embedding API auth failed (HTTP {resp.status_code})"
                )
            if resp.status_code == 400:
                report_failure()
                raise EmbeddingAPIError(
                    f"Embedding API rejected request (HTTP 400): "
                    f"{resp.text[:300]}"
                )
            if resp.status_code in _RETRY_STATUSES:
                last_exc = EmbeddingAPIError(
                    f"Embedding API HTTP {resp.status_code}"
                )
                wait = _API_RETRY_BACKOFF * (attempt + 1)
                try:
                    wait = float(resp.headers.get("retry-after", ""))
                except (TypeError, ValueError):
                    pass
                report_failure()
                _time.sleep(min(wait, 60.0))
                continue
            if resp.status_code != 200:
                report_failure()
                raise EmbeddingAPIError(
                    f"Embedding API HTTP {resp.status_code}: "
                    f"{resp.text[:300]}"
                )

            # Success
            report_success()
            data = resp.json()
            embeddings_data = data.get("data", [])
            embeddings_data.sort(key=lambda x: x.get("index", 0))
            vecs = [item["embedding"] for item in embeddings_data]
            return np.asarray(vecs, dtype=np.float32)

        report_failure()
        raise EmbeddingAPIError(
            f"Embedding API failed after {_API_RETRY_COUNT} retries: "
            f"{last_exc!r}"
        )


# -------------------------------------------------------------------------
# Local Embedders (fallback only)
# -------------------------------------------------------------------------
def _build_text_embedding(text_embedding_cls: Any, model_name: str) -> Any:
    """Construct a fastembed TextEmbedding with optional bundled cache."""
    models_dir: str | None = bundled_models_dir()
    if models_dir is not None:
        try:
            return text_embedding_cls(
                model_name=model_name,
                cache_dir=models_dir,
                local_files_only=True,
            )
        except TypeError:
            return text_embedding_cls(model_name=model_name, cache_dir=models_dir)
    return text_embedding_cls(model_name=model_name)


class _FastEmbedEmbedder:
    """Wraps fastembed.TextEmbedding (ONNX, CPU)."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from fastembed import TextEmbedding  # type: ignore

        self._model = _build_text_embedding(TextEmbedding, model_name)
        self.name = f"fastembed:{model_name}"
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


# -------------------------------------------------------------------------
# Factory
# -------------------------------------------------------------------------
_DEFAULT_MODEL_NAME = DEFAULT_MODEL


def set_default_model(model_name: str) -> None:
    global _DEFAULT_MODEL_NAME
    if model_name:
        _DEFAULT_MODEL_NAME = model_name


def _try_build_api_embedder() -> "_APIEmbedder | None":
    """Build API embedder if credentials are configured. Returns None if no
    API key is available (graceful skip to local fallback)."""
    from core.app_config import EMBED_DIM, EMBED_MODEL, LLM_API_KEY, LLM_BASE_URL
    from core.settings_store import KEY_BASE_URL, build_runtime_config, get_setting
    from core.settings_store import load_api_key

    api_key = (load_api_key() or "").strip() or LLM_API_KEY
    if not api_key:
        return None

    base_url = get_setting(KEY_BASE_URL) or LLM_BASE_URL
    ssl_verify = build_runtime_config().build_ssl()

    return _APIEmbedder(
        base_url=base_url,
        api_key=api_key,
        model=EMBED_MODEL,
        dim=EMBED_DIM,
        ssl_verify=ssl_verify,
    )


def embedding_backend_available() -> bool:
    """True if any embedding backend is available (API or local)."""
    return embedding_backend_status()[0]


def embedding_backend_status() -> "tuple[bool, str]":
    """Return (available, reason). Checks API first, then local backends."""
    import sys

    # API embedder available if key is configured
    try:
        from core.app_config import EMBED_MODEL, LLM_API_KEY
        from core.settings_store import load_api_key
        api_key = (load_api_key() or "").strip() or LLM_API_KEY
        if api_key:
            return True, f"API embedding ({EMBED_MODEL})"
    except Exception:
        pass

    # Local fallback check
    errors: list[str] = []
    for mod in ("fastembed", "sentence_transformers"):
        try:
            __import__(mod)
            return True, f"{mod} is importable"
        except ImportError as e:
            errors.append(f"{mod}: {e}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{mod}: {type(e).__name__}: {e}")
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return False, (
            "no embedding backend in this PACKAGED build. A 'pip install' "
            "into your system Python is NOT visible to the packaged app - "
            "bundle fastembed+onnxruntime into the build (see TestingToolkit"
            ".spec) or run from source. Details: " + "; ".join(errors)
        )
    return False, (
        "no embedding backend importable in the running interpreter "
        f"({sys.executable}). Install with: pip install fastembed onnxruntime "
        "- into THIS interpreter. Details: " + "; ".join(errors)
    )


_LAST_BUILD_ERROR: str = ""


def last_build_error() -> str:
    return _LAST_BUILD_ERROR


def get_text_embedder(model_name: str | None = None) -> "TextEmbedder | None":
    """Build the API embedder. All embedding is done via API -- no local
    ONNX/fastembed/sentence-transformers. Returns None (BM25-only) if
    no API key is configured.
    Never raises; records the failure for diagnostics."""
    global _LAST_BUILD_ERROR
    errs: list[str] = []

    try:
        embedder = _try_build_api_embedder()
        if embedder is not None:
            return embedder
        errs.append("api: no API key configured")
    except EmbeddingAPIError as e:
        errs.append(f"api: {e}")
    except Exception as e:  # noqa: BLE001
        errs.append(f"api: {type(e).__name__}: {e}")

    _LAST_BUILD_ERROR = "; ".join(errs)
    return None


# -------------------------------------------------------------------------
# Web-server compatibility layer
# -------------------------------------------------------------------------
# These functions are consumed by the web agent's server files
# (core/project_store.py, core/diagnostics.py, agent/routes/health.py,
# agent/model_loader.py). Embedding now runs via the API, so the
# ONNX-execution-provider introspection is not applicable; the runtime
# reporters honestly report "API mode / no local runtime" instead of a
# hardware EP. dense_enforced()/*_strict() keep their enforcement contract.

# Per-process record of what each loaded model is running on. In API mode this
# stays empty (no local model is loaded), and the reporters below reflect that.
_RUNTIME: dict[str, dict[str, Any]] = {}


def dense_enforced() -> bool:
    """Whether dense indexing is ENFORCED (the default).

    When enforced, the KB pipeline must build dense vectors; it must NOT
    silently fall back to lexical-only retrieval. Set ``TT_ENFORCE_DENSE=0``
    to opt out (e.g. a deliberately lexical-only deployment). Any other value
    - or an unset variable - keeps enforcement ON.
    """
    return (os.environ.get("TT_ENFORCE_DENSE", "1").strip() or "1") != "0"


def get_text_embedder_strict(model_name: str | None = None) -> "TextEmbedder":
    """Like get_text_embedder() but RAISES instead of returning None.

    Used when dense indexing is enforced: the caller wants a hard, visible
    failure (surfaced in the index job log) rather than a silent downgrade to
    lexical-only retrieval. The error message includes the backend status and
    the last construction error so the cause is actionable.
    """
    emb = get_text_embedder(model_name)
    if emb is not None:
        return emb
    avail, reason = embedding_backend_status()
    detail = last_build_error() or reason
    raise RuntimeError(
        "Dense indexing is enforced but the embedding backend could not be "
        "initialized. " + detail + " (Configure an API key/base URL for the "
        "embedding model, or set TT_ENFORCE_DENSE=0 to allow lexical-only "
        "retrieval.)"
    )


def record_model_runtime(role: str, model_name: str, model_obj: Any) -> None:
    """Record the runtime backend for a loaded model (role = 'embedder' |
    'reranker'). In API mode there is no local ONNX object to probe, so the
    entry records the API model with no accelerator. Never raises."""
    try:
        providers: list[str] = []
        active: str | None = None
        # Local ONNX objects (fallback path) expose get_providers(); probe if
        # present so a bundled-model deployment still reports truthfully.
        probe = getattr(model_obj, "model", None)
        getter = getattr(probe, "get_providers", None) if probe else None
        if callable(getter):
            try:
                providers = [str(p) for p in getter() or []]
            except Exception:
                providers = []
        accelerated = bool(
            providers and any(p != "CPUExecutionProvider" for p in providers)
        )
        if accelerated:
            active = providers[0]
        _RUNTIME[role] = {
            "model": model_name,
            "providers": providers,
            "accelerated": accelerated,
            "active_provider": active,
        }
    except Exception:
        pass


def model_runtime_info() -> dict[str, dict[str, Any]]:
    """Snapshot of what the loaded models are actually running on. Empty until
    at least one model has been recorded this process (API mode stays empty)."""
    return {role: dict(info) for role, info in _RUNTIME.items()}


def runtime_accelerated() -> bool:
    """True if ANY loaded model bound to a non-CPU execution provider. Always
    False in API mode (no local model loaded)."""
    return any(i.get("accelerated") for i in _RUNTIME.values())


def active_execution_provider() -> str | None:
    """The accelerator EP a loaded model actually bound to, or None if
    everything is on CPU / API mode / unknown."""
    for info in _RUNTIME.values():
        if info.get("accelerated") and info.get("active_provider"):
            return str(info["active_provider"])
    return None
