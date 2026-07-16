"""
process_metrics.py
Per-process resource metrics (this app only, not system-wide).
Uses platform-native APIs - no external dependencies.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Final

_LAST_CPU_TIME: float = 0.0
_LAST_WALL_TIME: float = 0.0
_LAST_CPU_PCT: float = 0.0


def _get_memory_mb() -> float:
    """RSS of the current process in MB."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            import ctypes.wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.wintypes.DWORD),
                    ("PageFaultCount", ctypes.wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(pmc)
            handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
            _fn = ctypes.windll.kernel32.K32GetProcessMemoryInfo  # type: ignore[attr-defined]
            _fn.restype = ctypes.wintypes.BOOL
            _fn.argtypes = [
                ctypes.wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                ctypes.wintypes.DWORD,
            ]
            if _fn(handle, ctypes.byref(pmc), ctypes.sizeof(pmc)):
                return pmc.WorkingSetSize / (1024 * 1024)
        except Exception:
            pass
    elif sys.platform == "linux":
        # Linux: read current RSS from /proc/self/status (VmRSS line)
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # Value in kB
                        return int(line.split()[1]) / 1024.0
        except Exception:
            pass
        # Fallback to resource (gives peak, not current, but better than 0)
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        except Exception:
            pass
    else:
        # macOS / other Unix
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # ru_maxrss is in bytes on macOS
            return usage.ru_maxrss / (1024.0 * 1024.0)
        except Exception:
            pass
    return 0.0


_NUM_CPUS: int = os.cpu_count() or 1


def _get_cpu_percent() -> float:
    """CPU usage of current process as a percentage (0-100).
    Normalized by logical core count to match Task Manager display."""
    global _LAST_CPU_TIME, _LAST_WALL_TIME, _LAST_CPU_PCT
    try:
        if sys.platform.startswith("win"):
            import ctypes
            import ctypes.wintypes

            class FILETIME(ctypes.Structure):
                _fields_ = [
                    ("dwLowDateTime", ctypes.wintypes.DWORD),
                    ("dwHighDateTime", ctypes.wintypes.DWORD),
                ]

            creation = FILETIME()
            exit_t = FILETIME()
            kernel = FILETIME()
            user = FILETIME()
            handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
            _gpt = ctypes.windll.kernel32.GetProcessTimes  # type: ignore[attr-defined]
            _gpt.restype = ctypes.wintypes.BOOL
            _gpt.argtypes = [
                ctypes.wintypes.HANDLE,
                ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME),
            ]
            _gpt(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            k = (kernel.dwHighDateTime << 32) + kernel.dwLowDateTime
            u = (user.dwHighDateTime << 32) + user.dwLowDateTime
            cpu_time = (k + u) / 1e7  # 100ns units to seconds
        else:
            times = os.times()
            cpu_time = times.user + times.system

        wall_now = time.monotonic()
        if _LAST_WALL_TIME > 0:
            wall_delta = wall_now - _LAST_WALL_TIME
            cpu_delta = cpu_time - _LAST_CPU_TIME
            if wall_delta > 0:
                # Divide by core count so idle shows ~0% not N% on N cores
                raw = (cpu_delta / wall_delta) * 100.0
                _LAST_CPU_PCT = min(100.0, raw / _NUM_CPUS)
        _LAST_CPU_TIME = cpu_time
        _LAST_WALL_TIME = wall_now
    except Exception:
        pass
    return _LAST_CPU_PCT


def _get_disk_usage_mb() -> float:
    """Total disk occupied by the app workspace (KB, artifacts, chats, projects)."""
    try:
        from core.app_config import WORKSPACE

        total = 0
        if not WORKSPACE.exists():
            return 0.0
        for f in WORKSPACE.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
        return total / (1024 * 1024)
    except Exception:
        return 0.0


_CACHED_DISK: float = 0.0
_DISK_LAST_CHECK: float = 0.0
_DISK_INTERVAL: Final[float] = 60.0  # recheck disk every 60s (was 30s; rglob is slow on HDD)
_DISK_SCAN_RUNNING: bool = False

# GPU info is constant for the lifetime of the process; probe once then cache.
_CACHED_GPU: str | None = None  # None = not yet probed


def _get_gpu_info() -> str:
    """Return GPU usage string if GPU is detected, else empty. Cached after
    first call - the GPU does not appear/vanish mid-session."""
    global _CACHED_GPU
    if _CACHED_GPU is not None:
        return _CACHED_GPU
    try:
        from core.hardware import gpu_available, gpu_device_name
        if gpu_available():
            _CACHED_GPU = gpu_device_name() or "GPU active"
        else:
            _CACHED_GPU = ""
    except Exception:
        _CACHED_GPU = ""
    return _CACHED_GPU


def _refresh_disk_async() -> None:
    """Refresh disk usage in a daemon thread so the UI thread never blocks
    on rglob. Result lands in _CACHED_DISK for the next timer tick."""
    global _DISK_SCAN_RUNNING, _CACHED_DISK, _DISK_LAST_CHECK
    if _DISK_SCAN_RUNNING:
        return
    _DISK_SCAN_RUNNING = True

    def _scan() -> None:
        global _CACHED_DISK, _DISK_LAST_CHECK, _DISK_SCAN_RUNNING
        try:
            _CACHED_DISK = _get_disk_usage_mb()
            _DISK_LAST_CHECK = time.monotonic()
        except Exception:
            pass
        finally:
            _DISK_SCAN_RUNNING = False

    import threading
    t = threading.Thread(target=_scan, daemon=True)
    t.start()
