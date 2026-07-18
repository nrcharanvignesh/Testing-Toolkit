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
        # Reranking is a gateway API call (native /rerank with LLM fallback);
        # it is available whenever an API key is configured.
        from core.settings_store import has_api_key
        return bool(has_api_key())

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
        detail = ""
        if not ok:
            try:
                from core.app_config import credential_protection_detail
                detail = (credential_protection_detail() or "").strip()
            except Exception:
                detail = ""
        _check(
            checks, "embedding_backend", "Dense embedding backend",
            _PASS if ok else _FAIL,
            reason if not detail else f"{reason} [{detail}]",
            "" if ok else (
                "The centrally managed AI credential is missing or unreadable. "
                "Reinstall or update the Testing Toolkit agent; if it persists, "
                "send the agent version and Doctor report to the administrator."
            ),
        )
    except Exception as e:  # noqa: BLE001
        _check(checks, "embedding_backend", "Dense embedding backend", _WARN,
               f"probe failed: {e!r}")

    # --- Reranker backend ---------------------------------------------------
    try:
        from core.settings_store import has_api_key

        ok = bool(has_api_key())
        _check(
            checks, "reranker", "Cross-encoder reranker (API /rerank)",
            _PASS if ok else _WARN,
            "centrally managed gateway /rerank available" if ok
            else "centrally managed AI credential unavailable",
            "" if ok else (
                "Update or reinstall the agent to restore the centrally managed "
                "AI credential; users do not enter AI keys in Settings."
            ),
        )
    except Exception as e:  # noqa: BLE001
        _check(checks, "reranker", "Cross-encoder reranker (API /rerank)", _WARN,
               f"probe failed: {e!r}")

    # NOTE: This build performs ALL inference (embeddings, reranking, OCR,
    # transcription) through the LLM gateway API - there are no bundled ONNX
    # models, no local execution provider, and GPU acceleration is irrelevant.
    # The former "Offline model cache", "Model execution provider" and
    # "Accelerator" checks were removed because they no longer reflect reality.

    # --- LLM gateway reachability ------------------------------------------
    try:
        from core.settings_store import build_llm_client, has_api_key

        if not has_api_key():
            _check(
                checks, "llm_gateway", "LLM gateway (AI API)", _WARN,
                "centrally managed AI credential unavailable",
                "Update or reinstall the agent. AI credentials are managed by "
                "the Testing Toolkit administrator and are not entered in Settings.",
            )
        else:
            # Do not disclose the private gateway host or proxy path in a user-
            # visible diagnostic. Credential state is enough to troubleshoot.
            build_llm_client()
            from core.app_config import credential_protection_state
            protection = credential_protection_state()
            _check(
                checks, "llm_gateway", "LLM gateway (AI API)", _PASS,
                f"centrally managed; credential protection: {protection}",
            )
    except Exception as e:  # noqa: BLE001
        _check(checks, "llm_gateway", "LLM gateway (AI API)", _WARN,
               f"probe failed: {e!r}")

    # --- OCR (optional) -----------------------------------------------------
    try:
        from kb.ocr import ocr_available

        ok = bool(ocr_available())
        _check(checks, "ocr", "OCR (scanned PDFs / images)",
               _PASS if ok else _WARN,
               "API OCR (centrally managed gateway vision)" if ok
               else "centrally managed AI credential unavailable",
               "" if ok else "Update or reinstall the agent to restore OCR; "
               "users do not enter AI keys in Settings.")
    except Exception as e:  # noqa: BLE001
        _check(checks, "ocr", "OCR (scanned PDFs / images)", _WARN,
               f"probe failed: {e!r}")

    # --- Multimedia transcription (API audio + local ffmpeg for video) ------
    try:
        from kb.multimedia import multimedia_capabilities

        mm = multimedia_capabilities() or {}
        audio = bool(mm.get("audio_whisper"))  # = API transcription available
        video = bool(mm.get("video_ffmpeg"))   # = ffmpeg present for video
        if audio:
            detail = "API transcription available" + (
                " (+ ffmpeg for video)" if video
                else " (audio only; ffmpeg not found for video)"
            )
            _check(checks, "multimedia", "Audio/video transcription", _PASS,
                   detail,
                   "" if video else "Optional: install ffmpeg to also index "
                   "video files (audio track extraction).")
        else:
            _check(
                checks, "multimedia", "Audio/video transcription", _WARN,
                "centrally managed AI credential unavailable",
                "Update or reinstall the agent to restore transcription; users "
                "do not enter AI keys in Settings.",
            )
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

    # --- Update detection wiring -------------------------------------------
    # The updater is intentionally detection-only; never imply that a newer
    # release was applied merely because the manifest URL is configured.
    try:
        from agent.updater import check_for_update

        update = check_for_update()
        if update.get("update_available"):
            _check(
                checks, "updates", "Agent update", _WARN,
                f"v{update.get('current')} is running; v{update.get('latest')} is available",
                "Download and run the current installer to apply the update.",
            )
        elif update.get("reachable"):
            _check(
                checks, "updates", "Agent update", _PASS,
                f"v{update.get('current')} is current",
            )
        else:
            _check(
                checks, "updates", "Agent update", _WARN,
                "update server is not reachable",
                "Check the network connection, then check for updates again.",
            )
    except Exception as e:  # noqa: BLE001
        _check(checks, "updates", "Agent update", _WARN, f"probe failed: {e!r}")

    # --- Self-healing subsystem -----------------------------------------
    try:
        from automation.healing_guardrails import HISTORY_PATH

        if HISTORY_PATH.exists():
            import json as _json
            data = _json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            records = data.get("records", [])
            total_heals = len(records)
            successful = sum(1 for r in records if r.get("success"))
            rate = (successful / total_heals * 100) if total_heals else 0
            _check(
                checks, "self_healing", "Self-healing subsystem",
                _PASS if rate >= 50 or total_heals == 0 else _WARN,
                f"{total_heals} healing attempt(s), {successful} successful ({rate:.0f}%)",
                "" if rate >= 50 else
                "Low healing success rate. Review persistent test failures "
                "and update locators or test definitions.",
            )
        else:
            _check(checks, "self_healing", "Self-healing subsystem", _PASS,
                   "no healing history yet (clean slate)")
    except Exception as e:  # noqa: BLE001
        _check(checks, "self_healing", "Self-healing subsystem", _WARN,
               f"probe failed: {e!r}")

    # Overall = worst severity.
    severity = {_PASS: 0, _WARN: 1, _FAIL: 2}
    overall = _PASS
    for c in checks:
        if severity.get(c["status"], 0) > severity[overall]:
            overall = c["status"]

    return {"status": overall, "checks": checks}
