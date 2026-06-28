"""Health and version endpoints."""

from __future__ import annotations

import os
import platform

from fastapi import APIRouter

from agent.model_loader import models_loaded
from agent.version import AGENT_VERSION

router = APIRouter()


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
        "cpu_percent": None,
        "ram_used_mb": None,
        "ram_total_mb": None,
        "ram_percent": None,
        "proc_mem_mb": None,
        "disk_used_mb": None,
        "disk_total_mb": None,
        "disk_percent": None,
        "gpu": None,
    }

    # CPU% + this process's resident memory (best-effort via psutil).
    try:
        import psutil  # type: ignore

        # interval=None -> non-blocking; % is measured since the previous call,
        # which the web client makes every few seconds.
        data["cpu_percent"] = round(float(psutil.cpu_percent(interval=None)), 1)
        try:
            data["proc_mem_mb"] = int(
                psutil.Process().memory_info().rss / (1024 * 1024)
            )
        except Exception:
            pass
    except Exception:
        pass

    # RAM total/used/percent via hardware helpers (psutil OR OS fallbacks).
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

    # Disk ("Data" / ROM) usage for the drive that hosts the workspace, via the
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
