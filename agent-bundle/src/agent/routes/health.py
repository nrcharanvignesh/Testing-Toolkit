"""Health and version endpoints."""

from __future__ import annotations

import asyncio
import os
import platform
import time

from fastapi import APIRouter

from agent.model_loader import models_loaded
from agent.version import AGENT_VERSION
from core.trace import trace

router = APIRouter()

# Module-level handle to THIS agent process, kept alive so per-process CPU% can
# be measured as the delta between polls (psutil's cpu_percent(interval=None)
# returns usage since the previous call on the same object). Primed lazily.
_PROC = None

# The app's own data footprint (workspace dir size) is expensive to walk, so it
# is cached and recomputed at most every _DIR_TTL seconds.
_DIR_CACHE: dict = {"value": None, "at": 0.0}
_DIR_TTL = 60.0


def _proc():
    """Cached psutil handle for this process, with the CPU% baseline primed."""
    global _PROC
    try:
        import psutil  # type: ignore

        if _PROC is None:
            _PROC = psutil.Process()
            # First call establishes the baseline and returns 0.0; ignore it.
            _PROC.cpu_percent(interval=None)
    except Exception:
        _PROC = None
    return _PROC


def _workspace_dir() -> str | None:
    """Best-effort path to the app's workspace (its real data footprint)."""
    candidates = [
        os.environ.get("TT_WORKSPACE_DIR"),
        os.path.join(os.path.expanduser("~"), "TestingToolkitWeb"),
    ]
    for path in candidates:
        if path and os.path.isdir(path):
            return path
    return None


def _app_data_mb() -> int | None:
    """Total size (MB) of the app's workspace directory, cached for _DIR_TTL."""
    now = time.time()
    if _DIR_CACHE["value"] is not None and now - _DIR_CACHE["at"] < _DIR_TTL:
        return _DIR_CACHE["value"]
    target = _workspace_dir()
    if not target:
        return None
    total = 0
    try:
        for root, _dirs, files in os.walk(target):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except Exception:
        return _DIR_CACHE["value"]
    mb = int(total / (1024 * 1024))
    _DIR_CACHE["value"] = mb
    _DIR_CACHE["at"] = now
    return mb


@router.get("/health")
@trace
async def health() -> dict:
    try:
        user = os.getlogin()
    except OSError:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    # Architecture info (best-effort) so the app can show the chip and adapt to
    # ARM / Apple Silicon / unified-memory SoCs. Fail-safe: omitted on error.
    arch: dict = {}
    try:
        from core.hardware import chip_name, is_arm, is_unified_memory

        arch = {
            "arch": platform.machine(),
            "chip": chip_name(),
            "is_arm": is_arm(),
            "is_unified_memory": is_unified_memory(),
        }
    except Exception:
        arch = {"arch": platform.machine()}
    # Capabilities: what this agent can actually do on this machine (dense
    # retrieval, reranker, OCR, GPU/EP in use, updates configured, ...). Fail-
    # safe and cheap (no model loads); omitted on error.
    caps: dict = {}
    try:
        from core.diagnostics import capabilities

        caps = capabilities()
    except Exception:
        caps = {}
    return {
        "status": "ok",
        "version": AGENT_VERSION,
        "user": user,
        "machine": platform.node(),
        "models_loaded": models_loaded(),
        "hardware": arch,
        "capabilities": caps,
    }


@router.get("/capabilities")
@trace
async def capabilities_route() -> dict:
    """Standalone capabilities map (same content as health.capabilities)."""
    try:
        from core.diagnostics import capabilities

        return capabilities()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


@router.get("/doctor")
@trace
async def doctor_route() -> dict:
    """Run agent self-diagnostics and return a pass/warn/fail report with
    remediation for anything degraded. Never raises."""
    try:
        from core.diagnostics import run_doctor

        return await asyncio.to_thread(run_doctor)
    except Exception as e:  # noqa: BLE001
        return {
            "status": "warn",
            "checks": [{
                "id": "doctor", "label": "Diagnostics", "status": "warn",
                "detail": f"could not run: {type(e).__name__}: {e}", "fix": "",
            }],
        }


@router.post("/open-log-folder")
@trace
async def open_log_folder() -> dict:
    """Best-effort: open the log directory in the OS file explorer."""
    import subprocess
    import sys

    try:
        from core.app_logging import log_dir

        target = log_dir()
        if target is None:
            return {"ok": False, "detail": "log directory not available"}

        folder = str(target)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", folder])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

        return {"ok": True, "detail": folder}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


@router.get("/version")
@trace
async def version() -> dict:
    import sys
    return {
        "version": AGENT_VERSION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }


@router.get("/metrics")
@trace
async def metrics() -> dict:
    """Live system resource usage for the status bar.

    Every field is fail-safe and degrades to ``None`` when it can't be read, so
    this never raises and never requires a dependency that may be missing.
    ``cpu_percent`` / ``proc_mem_mb`` come from core.process_metrics, which uses
    platform-native APIs (Windows ctypes, Linux /proc, os.times) and needs NO
    external dependency — matching the desktop app. psutil, when importable, is
    used only as an optional refinement. RAM uses core.hardware's OS-level
    readers; GPU uses core.hardware's accelerator detection.
    """
    data: dict = {
        # All resource figures below are scoped to THIS app/agent process alone,
        # not the whole machine.
        # cpu_percent: the app's CPU usage as a % of total machine capacity.
        "cpu_percent": None,
        # proc_mem_mb: the app's actual resident memory (RAM) in MB.
        "proc_mem_mb": None,
        # ram_used_mb / ram_total_mb / ram_percent: system RAM context, kept for
        # back-compat (older web builds) and tooltips; not the primary display.
        "ram_used_mb": None,
        "ram_total_mb": None,
        "ram_percent": None,
        # app_data_mb: actual disk space the app's workspace directory occupies.
        "app_data_mb": None,
        # disk_* : whole-drive context, kept for back-compat / tooltips.
        "disk_used_mb": None,
        "disk_total_mb": None,
        "disk_percent": None,
        "gpu": None,
    }

    # Per-process CPU% + resident memory for THIS agent. Primary source is the
    # dependency-free core.process_metrics (platform-native APIs) so this works
    # on every install without psutil — exactly like the desktop app. CPU% is a
    # delta between successive calls; the status bar polls every few seconds so
    # the reading converges after the first poll.
    try:
        from core.process_metrics import _get_cpu_percent, _get_memory_mb

        cpu = _get_cpu_percent()
        if cpu is not None:
            data["cpu_percent"] = round(float(cpu), 1)
        mem = _get_memory_mb()
        if mem and mem > 0:
            data["proc_mem_mb"] = int(mem)
    except Exception:
        pass

    # Optional refinement via psutil when it happens to be installed (not
    # bundled by default). Only overrides when it returns a usable value.
    try:
        import psutil  # type: ignore

        proc = _proc()
        if proc is not None:
            raw = float(proc.cpu_percent(interval=None))
            cores = psutil.cpu_count(logical=True) or 1
            # psutil's first call after priming can read 0.0; keep the native
            # value in that case so the display isn't reset to zero.
            pct = round(min(100.0, raw / cores), 1)
            if pct > 0 or data["cpu_percent"] is None:
                data["cpu_percent"] = pct
            try:
                data["proc_mem_mb"] = int(proc.memory_info().rss / (1024 * 1024))
            except Exception:
                pass
    except Exception:
        pass

    # System RAM context via hardware helpers (psutil OR OS fallbacks) — used for
    # the RAM tooltip and as a fallback figure for older web builds.
    try:
        from core.hardware import system_memory_mb, available_memory_mb

        total = int(system_memory_mb())
        avail = int(available_memory_mb())
        used = max(0, total - avail)
        data["ram_total_mb"] = total
        data["ram_used_mb"] = used
        if total > 0:
            data["ram_percent"] = round(used / total * 100, 1)
    except Exception:
        pass

    # The app's own data footprint: total size of its workspace directory.
    # Offloaded to a thread because os.walk can block for hundreds of ms.
    try:
        data["app_data_mb"] = await asyncio.to_thread(_app_data_mb)
    except Exception:
        pass

    # Whole-drive context (kept for back-compat and the Data tooltip), via the
    # stdlib (no dependency). Falls back to home, then the filesystem root.
    try:
        import shutil

        candidates = [
            os.environ.get("TT_WORKSPACE_DIR"),
            os.path.expanduser("~"),
            os.path.abspath(os.sep),
        ]
        target = next((p for p in candidates if p and os.path.exists(p)), None)
        if target:
            usage = shutil.disk_usage(target)
            total = int(usage.total / (1024 * 1024))
            used = int(usage.used / (1024 * 1024))
            data["disk_total_mb"] = total
            data["disk_used_mb"] = used
            if total > 0:
                data["disk_percent"] = round(used / total * 100, 1)
    except Exception:
        pass

    # GPU — reported when an accelerator is present OR a loaded model actually
    # bound to a non-CPU execution provider. We distinguish capability (the GPU
    # exists) from real usage (a model's ONNX session is running on it).
    try:
        from core.hardware import (
            gpu_available,
            gpu_device_name,
            is_unified_memory,
        )

        # What the loaded models ACTUALLY run on (truth, not capability).
        ep_in_use: str | None = None
        models_accelerated = False
        try:
            from kb.embeddings import (
                active_execution_provider,
                runtime_accelerated,
            )

            ep_in_use = active_execution_provider()
            models_accelerated = bool(runtime_accelerated())
        except Exception:
            pass

        if gpu_available() or models_accelerated:
            unified = bool(is_unified_memory())
            gpu: dict = {
                "name": gpu_device_name() or "GPU",
                # in_use reflects REAL model binding when a model has loaded;
                # before any model loads it falls back to capability so the UI
                # still shows the accelerator exists.
                "in_use": models_accelerated or gpu_available(),
                # The execution provider a model actually bound to, e.g.
                # "CoreMLExecutionProvider" / "CUDAExecutionProvider". None
                # until a model loads (or if models are on CPU).
                "ep": ep_in_use,
                # True once we've confirmed a model is running off-CPU.
                "accelerated": models_accelerated,
                "util_percent": None,
                "mem_used_mb": None,
                "mem_total_mb": None,
                # On a unified-memory SoC (e.g. Apple Silicon) the accelerator
                # shares system RAM, so there is no separate VRAM pool. The UI
                # uses this to label memory as "unified" instead of "VRAM".
                "unified_memory": unified,
            }
            if unified:
                # The shared pool IS system RAM. Report total RAM as the
                # accelerator's memory pool; a GPU-specific "used" figure isn't
                # cheaply attributable on these SoCs, so leave mem_used_mb null.
                if data.get("ram_total_mb"):
                    gpu["mem_total_mb"] = int(data["ram_total_mb"])
            else:
                # Discrete GPU: NVIDIA utilization + VRAM via NVML when present.
                try:
                    import pynvml  # type: ignore

                    pynvml.nvmlInit()
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    meminfo = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    gpu["util_percent"] = float(util.gpu)
                    gpu["mem_used_mb"] = int(meminfo.used / (1024 * 1024))
                    gpu["mem_total_mb"] = int(meminfo.total / (1024 * 1024))
                    pynvml.nvmlShutdown()
                except Exception:
                    # Fallback: CUDA memory via torch when present.
                    try:
                        import torch  # type: ignore

                        if torch.cuda.is_available():
                            gpu["mem_used_mb"] = int(
                                torch.cuda.memory_allocated(0) / (1024 * 1024)
                            )
                            gpu["mem_total_mb"] = int(
                                torch.cuda.get_device_properties(0).total_memory
                                / (1024 * 1024)
                            )
                    except Exception:
                        pass
            data["gpu"] = gpu
    except Exception:
        pass

    return data
