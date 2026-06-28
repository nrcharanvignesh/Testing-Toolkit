"""Health and version endpoints."""

from __future__ import annotations

import os
import platform
import time

from fastapi import APIRouter

from agent.model_loader import models_loaded
from agent.version import AGENT_VERSION

router = APIRouter()

# Module-level handle to THIS agent process, kept alive so per-process CPU% can
# be measured as the delta between polls (psutil's cpu_percent(interval=None)
# returns usage since the previous call on the same object). Primed lazily.
_PROC = None

# The app's own data footprint (workspace dir size) is expensive to walk, so it
# is cached and recomputed at most every _DIR_TTL seconds.
_DIR_CACHE: dict = {"value": None, "at": 0.0}
_DIR_TTL = 30.0


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
async def health() -> dict:
    try:
        user = os.getlogin()
    except OSError:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    return {
        "status": "ok",
        "version": AGENT_VERSION,
        "user": user,
        "machine": platform.node(),
        "models_loaded": models_loaded(),
    }


@router.get("/version")
async def version() -> dict:
    import sys
    return {
        "version": AGENT_VERSION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }


@router.get("/metrics")
async def metrics() -> dict:
    """Live system resource usage for the status bar.

    Every field is fail-safe and degrades to ``None`` when it can't be read, so
    this never raises and never requires a dependency that may be missing.
    ``cpu_percent`` / ``proc_mem_mb`` use psutil when it is importable; RAM uses
    core.hardware's OS-level readers (which have their own non-psutil
    fallbacks); GPU uses core.hardware's accelerator detection.
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

    # Per-process CPU% + resident memory for THIS agent (best-effort via psutil).
    try:
        import psutil  # type: ignore

        proc = _proc()
        if proc is not None:
            # cpu_percent can exceed 100% on multi-core boxes (sum across cores);
            # normalize to a share of total machine capacity so "the app alone"
            # reads intuitively (0-100%).
            raw = float(proc.cpu_percent(interval=None))
            cores = psutil.cpu_count(logical=True) or 1
            data["cpu_percent"] = round(min(100.0, raw / cores), 1)
            try:
                data["proc_mem_mb"] = int(
                    proc.memory_info().rss / (1024 * 1024)
                )
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
    try:
        data["app_data_mb"] = _app_data_mb()
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

    # GPU — only reported when an accelerator is actually present/in use.
    try:
        from core.hardware import gpu_available, gpu_device_name

        if gpu_available():
            gpu: dict = {
                "name": gpu_device_name() or "GPU",
                "in_use": True,
                "util_percent": None,
                "mem_used_mb": None,
                "mem_total_mb": None,
            }
            # NVIDIA utilization + memory via NVML when available.
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
