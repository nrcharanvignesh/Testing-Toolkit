"""
diagnostics.py
Agent self-diagnosis: a machine-readable capabilities map and a "doctor"
report the web app can render so users (and we) can see, at a glance, what the
agent can actually do on this machine and what - if anything - is degraded.

Two entry points, both fully fail-safe (every probe is wrapped; a broken probe
degrades to "unknown" instead of raising):

    capabilities() -> dict     compact feature flags + actual model runtime
    run_doctor()   -> dict     ordered list of checks with pass/warn/fail and a
                               plain-language remediation for anything not OK

Nothing here imports a heavy/optional dependency at module load; probes import
lazily so this is safe to call from /health even on a minimal install.

ASCII only; stdlib + first-party only; fully type-hinted.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

# Hugging Face cache folder names for the two bundled models (see model_bundle).
_EMBEDDER_CACHE = "models--qdrant--bge-small-en-v1.5-onnx-q"
_RERANKER_CACHE = "models--Xenova--ms-marco-MiniLM-L-6-v2"

_PASS = "pass"
_WARN = "warn"
_FAIL = "fail"


def _safe(fn: Any, default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


def _workspace_dir() -> str | None:
    for path in (
        os.environ.get("TT_WORKSPACE_DIR"),
        os.path.join(os.path.expanduser("~"), "TestingToolkitWeb"),
    ):
        if path and os.path.isdir(path):
            return path
    return None


def capabilities() -> dict[str, Any]:
    """Compact, fail-safe feature map. Booleans are best-effort; ``None`` means
    the probe could not run. Includes the REAL model runtime (which execution
    provider the loaded ONNX models bound to), not just GPU capability."""
    caps: dict[str, Any] = {}

    # Dense retrieval + reranker backends (import-only; do not load models).
    def _dense() -> bool:
        from kb.embeddings import embedding_backend_available
        return bool(embedding_backend_available())

    def _rerank() -> bool:
        from kb.reranker import reranker_available
        return bool(reranker_available())

    caps["dense_retrieval"] = _safe(_dense)
    caps["reranker"] = _safe(_rerank)

    # Bundled offline model cache.
    def _bundle() -> bool:
        from kb.model_bundle import bundled_models_dir
        return bundled_models_dir() is not None

    def _emb_files() -> bool:
        from kb.model_bundle import has_model
        return has_model(_EMBEDDER_CACHE)

    def _rer_files() -> bool:
        from kb.model_bundle import has_model
        return has_model(_RERANKER_CACHE)

    caps["model_bundle"] = _safe(_bundle)
    caps["embedder_model_files"] = _safe(_emb_files)
    caps["reranker_model_files"] = _safe(_rer_files)

    # Hardware capability vs. ACTUAL runtime binding.
    caps["gpu_capable"] = _safe(
        lambda: bool(__import__("core.hardware", fromlist=["gpu_available"])
                     .gpu_available())
    )

    def _runtime() -> dict[str, Any]:
        from kb.embeddings import (
            active_execution_provider,
            model_runtime_info,
            runtime_accelerated,
        )
        return {
            "models": model_runtime_info(),
            "accelerated": bool(runtime_accelerated()),
            "active_provider": active_execution_provider(),
        }

    caps["model_runtime"] = _safe(_runtime, {})

    # Optional extraction backends.
    caps["ocr"] = _safe(
        lambda: bool(__import__("kb.ocr", fromlist=["ocr_available"])
                     .ocr_available())
    )
    mm = _safe(
        lambda: __import__("kb.multimedia", fromlist=["multimedia_capabilities"])
        .multimedia_capabilities(),
        {},
    ) or {}
    caps["audio_transcription"] = bool(mm.get("audio_whisper"))
    caps["video"] = bool(mm.get("video_ffmpeg"))

    # Always-on features of this agent build.
    caps["incremental_hash_indexing"] = True

    # Self-update wiring.
    caps["updates_configured"] = _safe(
        lambda: bool(__import__("agent.updater", fromlist=["resolve_manifest_url"])
                     .resolve_manifest_url())
    )

    return caps


def _check(checks: list[dict[str, Any]], cid: str, label: str, status: str,
           detail: str = "", fix: str = "") -> None:
    checks.append({
        "id": cid, "label": label, "status": status,
        "detail": detail, "fix": fix,
    })


def run_doctor() -> dict[str, Any]:
    """Run ordered diagnostic checks and return a structured report:

        {"status": "pass|warn|fail", "checks": [{id,label,status,detail,fix}]}

    ``status`` is the worst severity across checks (fail > warn > pass) so the UI
    can show a single headline. Every check is individually wrapped so one
    failing probe never aborts the report.
    """
    checks: list[dict[str, Any]] = []

    # --- Dense embedding backend -------------------------------------------
    try:
        from kb.embeddings import embedding_backend_status

        ok, reason = embedding_backend_status()
        _check(
            checks, "embedding_backend", "Dense embedding backend",
            _PASS if ok else _FAIL,
            reason,
            "" if ok else (
                "Reinstall the agent so the bundled embedding model + "
                "onnxruntime are restored, or set TT_ENFORCE_DENSE=0 to run "
                "lexical-only."
            ),
        )
    except Exception as e:  # noqa: BLE001
        _check(checks, "embedding_backend", "Dense embedding backend", _WARN,
               f"probe failed: {e!r}")

    # --- Reranker backend ---------------------------------------------------
    try:
        from kb.reranker import reranker_available

        ok = bool(reranker_available())
        _check(
            checks, "reranker", "Cross-encoder reranker",
            _PASS if ok else _WARN,
            "available" if ok else "fastembed reranker not importable",
            "" if ok else "Reinstall the agent to restore the reranker model.",
        )
    except Exception as e:  # noqa: BLE001
        _check(checks, "reranker", "Cross-encoder reranker", _WARN,
               f"probe failed: {e!r}")

    # --- Bundled offline model files ---------------------------------------
    try:
        from kb.model_bundle import bundled_models_dir, has_model

        root = bundled_models_dir()
        if root is None:
            _check(checks, "model_bundle", "Offline model cache", _WARN,
                   "no bundled models dir found; models would be downloaded "
                   "on first use (fails offline)",
                   "Reinstall the agent to restore the bundled models.")
        else:
            emb = has_model(_EMBEDDER_CACHE)
            rer = has_model(_RERANKER_CACHE)
            if emb and rer:
                _check(checks, "model_bundle", "Offline model cache", _PASS,
                       f"both models present under {root}")
            else:
                missing = ", ".join(
                    n for n, ok in (("embedder", emb), ("reranker", rer))
                    if not ok
                ) or "some files"
                _check(checks, "model_bundle", "Offline model cache", _FAIL,
                       f"missing: {missing} (under {root})",
                       "Reinstall the agent to restore the bundled models.")
    except Exception as e:  # noqa: BLE001
        _check(checks, "model_bundle", "Offline model cache", _WARN,
               f"probe failed: {e!r}")

    # --- Actual model runtime (which EP did models bind to?) ---------------
    try:
        from kb.embeddings import (
            active_execution_provider,
            model_runtime_info,
        )

        info = model_runtime_info()
        if not info:
            _check(checks, "model_runtime", "Model execution provider", _PASS,
                   "no model loaded yet (built on first index/retrieval)")
        else:
            ep = active_execution_provider()
            if ep:
                _check(checks, "model_runtime", "Model execution provider",
                       _PASS, f"running on accelerator: {ep}")
            else:
                provs = next(
                    (i.get("providers") for i in info.values()
                     if i.get("providers")), None
                )
                _check(checks, "model_runtime", "Model execution provider",
                       _PASS,
                       f"running on CPU (providers: {provs})")
    except Exception as e:  # noqa: BLE001
        _check(checks, "model_runtime", "Model execution provider", _WARN,
               f"probe failed: {e!r}")

    # --- GPU / accelerator (informational) ---------------------------------
    try:
        from core.hardware import (
            chip_name,
            gpu_available,
            gpu_device_name,
        )

        if gpu_available():
            _check(checks, "gpu", "Accelerator", _PASS,
                   f"{gpu_device_name() or 'GPU'} ({chip_name() or 'unknown'})")
        else:
            _check(checks, "gpu", "Accelerator", _WARN,
                   f"no accelerator detected; using CPU "
                   f"({chip_name() or 'unknown'})",
                   "This is fine - retrieval runs on CPU. A supported GPU "
                   "would speed up embedding/reranking.")
    except Exception as e:  # noqa: BLE001
        _check(checks, "gpu", "Accelerator", _WARN, f"probe failed: {e!r}")

    # --- OCR (optional) -----------------------------------------------------
    try:
        from kb.ocr import ocr_available

        ok = bool(ocr_available())
        _check(checks, "ocr", "OCR (scanned PDFs / images)",
               _PASS if ok else _WARN,
               "available" if ok else "no OCR engine installed",
               "" if ok else "Optional: install Tesseract to index scanned "
               "PDFs and text in images.")
    except Exception as e:  # noqa: BLE001
        _check(checks, "ocr", "OCR (scanned PDFs / images)", _WARN,
               f"probe failed: {e!r}")

    # --- Multimedia (optional) ---------------------------------------------
    try:
        from kb.multimedia import multimedia_capabilities

        mm = multimedia_capabilities() or {}
        audio = bool(mm.get("audio_whisper"))
        video = bool(mm.get("video_ffmpeg"))
        if audio and video:
            _check(checks, "multimedia", "Audio/video transcription", _PASS,
                   "whisper + ffmpeg available")
        else:
            have = []
            if audio:
                have.append("audio")
            if video:
                have.append("video")
            _check(checks, "multimedia", "Audio/video transcription", _WARN,
                   f"partial/none ({', '.join(have) or 'neither'} available)",
                   "Optional: install whisper (audio) and ffmpeg (video) to "
                   "index media files.")
    except Exception as e:  # noqa: BLE001
        _check(checks, "multimedia", "Audio/video transcription", _WARN,
               f"probe failed: {e!r}")

    # --- Workspace writable -------------------------------------------------
    try:
        ws = _workspace_dir()
        if not ws:
            _check(checks, "workspace", "Workspace directory", _WARN,
                   "workspace dir not found yet (created on first use)")
        else:
            fd, tmp = tempfile.mkstemp(prefix=".doctor-", dir=ws)
            os.close(fd)
            os.unlink(tmp)
            _check(checks, "workspace", "Workspace directory", _PASS,
                   f"writable: {ws}")
    except Exception as e:  # noqa: BLE001
        _check(checks, "workspace", "Workspace directory", _FAIL,
               f"not writable: {e!r}",
               "Check folder permissions / disk space for the workspace.")

    # --- Disk space ---------------------------------------------------------
    try:
        import shutil

        target = _workspace_dir() or os.path.expanduser("~")
        free_mb = int(shutil.disk_usage(target).free / (1024 * 1024))
        if free_mb < 500:
            _check(checks, "disk", "Free disk space", _FAIL,
                   f"only {free_mb} MB free",
                   "Free up disk space; indexing and model loading need room.")
        elif free_mb < 2048:
            _check(checks, "disk", "Free disk space", _WARN,
                   f"{free_mb} MB free (low)")
        else:
            _check(checks, "disk", "Free disk space", _PASS,
                   f"{free_mb} MB free")
    except Exception as e:  # noqa: BLE001
        _check(checks, "disk", "Free disk space", _WARN, f"probe failed: {e!r}")

    # --- Self-update wiring -------------------------------------------------
    try:
        from agent.updater import resolve_manifest_url

        if resolve_manifest_url():
            _check(checks, "updates", "Auto-update", _PASS, "configured")
        else:
            _check(checks, "updates", "Auto-update", _WARN,
                   "not configured yet",
                   "The web app heals this automatically on next launch; no "
                   "action needed.")
    except Exception as e:  # noqa: BLE001
        _check(checks, "updates", "Auto-update", _WARN, f"probe failed: {e!r}")

    # Overall = worst severity.
    severity = {_PASS: 0, _WARN: 1, _FAIL: 2}
    overall = _PASS
    for c in checks:
        if severity.get(c["status"], 0) > severity[overall]:
            overall = c["status"]

    return {"status": overall, "checks": checks}
