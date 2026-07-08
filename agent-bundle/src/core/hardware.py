"""
hardware.py
Hardware detection + optimal resource allocation.

Detects: CPU cores (physical/logical), NUMA topology, CUDA/GPU availability,
ARM/Apple Silicon architecture, system RAM, and configures thread/process
pools accordingly. All detection is fail-safe: inability to read any value
falls back to conservative defaults.

Supports: x86_64 (Intel/AMD), ARM64 (Apple M1/M2/M3/M4, Qualcomm Snapdragon,
Ampere), with appropriate ONNX provider selection per architecture.

Exported:
    optimal_workers()   -> int     (IO-bound: async ADO calls, LLM API)
    optimal_cpu_workers() -> int   (CPU-bound: PDF rendering, OCR, chunking)
    gpu_available()     -> bool
    gpu_device_name()   -> str
    system_memory_mb()  -> int
    is_arm()            -> bool
    is_apple_silicon()  -> bool
    chip_name()         -> str
    set_optimal_thread_env() -> None  (call once at startup)
"""

from __future__ import annotations

import os
import platform
import struct
from typing import Final

_FALLBACK_CORES: Final[int] = 4
_MIN_WORKERS: Final[int] = 2


def is_arm() -> bool:
    """True if running on ARM architecture (aarch64/arm64)."""
    try:
        machine = platform.machine().lower()
        return machine in ("aarch64", "arm64", "armv8l", "armv8b")
    except Exception:
        return False


def is_apple_silicon() -> bool:
    """True if running on Apple M-series chip (M1/M2/M3/M4)."""
    try:
        import sys
        if sys.platform != "darwin":
            return False
        machine = platform.machine().lower()
        return machine in ("arm64", "aarch64")
    except Exception:
        return False


def is_unified_memory() -> bool:
    """True on a unified-memory SoC where the GPU/Neural Engine shares system
    RAM (no separate VRAM pool).

    Today this is reliably detectable for Apple Silicon (M-series), where the
    GPU and Neural Engine address the same physical RAM as the CPU. On such
    systems a separate "GPU memory used / total" figure is meaningless, so the
    metrics layer reports total system RAM as the accelerator's memory pool and
    flags it as unified for the UI. Other ARM SoCs (Snapdragon, Ampere) are
    also typically unified, but we only assert it where we can detect it
    confidently. Fail-safe: returns False on any error.
    """
    try:
        return is_apple_silicon()
    except Exception:
        return False


def chip_name() -> str:
    """Human-readable chip/architecture identifier."""
    try:
        machine = platform.machine()
        if is_apple_silicon():
            # Try to get specific M-series chip name via sysctl
            try:
                import subprocess
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True, text=True, timeout=2,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except Exception:
                pass
            return f"Apple Silicon ({machine})"
        elif is_arm():
            return f"ARM64 ({machine})"
        else:
            # x86_64 - try to get brand string
            try:
                import subprocess
                import sys
                if sys.platform == "win32":
                    import winreg
                    key = winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                    )
                    name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                    winreg.CloseKey(key)
                    return str(name).strip()
                else:
                    result = subprocess.run(
                        ["cat", "/proc/cpuinfo"],
                        capture_output=True, text=True, timeout=2,
                    )
                    for line in result.stdout.splitlines():
                        if "model name" in line:
                            return line.split(":", 1)[1].strip()
            except Exception:
                pass
            return machine
    except Exception:
        return "unknown"


def _physical_cores() -> int:
    """Number of physical CPU cores (excludes hyperthreads).
    ARM chips (Apple Silicon, Snapdragon) have no hyperthreading so
    physical == logical; the /2 heuristic only applies to x86 HT."""
    try:
        cores = os.cpu_count()
        if cores and cores > 0:
            try:
                import psutil  # type: ignore
                phys = psutil.cpu_count(logical=False)
                if phys and phys > 0:
                    return int(phys)
            except Exception:
                pass
            # ARM: no hyperthreading, all cores are physical
            if is_arm():
                return int(cores)
            # x86 heuristic: physical ~ logical / 2
            return max(1, cores // 2)
    except Exception:
        pass
    return _FALLBACK_CORES


def _logical_cores() -> int:
    """Total logical CPU cores (including hyperthreads)."""
    try:
        cores = os.cpu_count()
        if cores and cores > 0:
            return int(cores)
    except Exception:
        pass
    return _FALLBACK_CORES


def optimal_workers() -> int:
    """Thread count for IO-bound work (HTTP, file reads). Uses all logical
    cores since these threads spend most time waiting."""
    return max(_MIN_WORKERS, min(_logical_cores() * 2, 32))


def optimal_cpu_workers() -> int:
    """Process/thread count for CPU-bound work (OCR, embedding, PDF render).
    Uses physical cores to avoid contention on shared execution units."""
    return max(_MIN_WORKERS, _physical_cores())


def gpu_available() -> bool:
    """True if hardware acceleration is accessible (CUDA, CoreML, or Metal)."""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return True
        # Apple Silicon MPS (Metal Performance Shaders)
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return True
    except Exception:
        pass
    # Check ONNX Runtime accelerated EPs
    try:
        import onnxruntime as ort  # type: ignore
        available = set(ort.get_available_providers())
        accelerated = {"CUDAExecutionProvider", "CoreMLExecutionProvider",
                       "DmlExecutionProvider"}
        if available & accelerated:
            return True
    except Exception:
        pass
    return False


def gpu_device_name() -> str:
    """Human-readable GPU/accelerator name, or empty string."""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return f"Apple Metal ({chip_name()})"
    except Exception:
        pass
    # CoreML on Apple Silicon without torch
    if is_apple_silicon():
        try:
            import onnxruntime as ort  # type: ignore
            if "CoreMLExecutionProvider" in ort.get_available_providers():
                return f"CoreML ({chip_name()})"
        except Exception:
            pass
    return ""


def system_memory_mb() -> int:
    """Total system RAM in MB. Returns 4096 if detection fails."""
    try:
        import psutil  # type: ignore
        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:
        pass
    # Fallback: read from OS directly
    try:
        import sys as _sys
        if _sys.platform == "darwin":
            # macOS (works on Apple Silicon and Intel Mac)
            import subprocess
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) // (1024 * 1024)
        elif _sys.platform == "linux":
            # Linux (ARM or x86)
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        # Value is in kB
                        return int(line.split()[1]) // 1024
        else:
            # Windows
            import ctypes
            if hasattr(ctypes, "windll"):
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                mem = MEMORYSTATUSEX()
                mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
                return int(mem.ullTotalPhys / (1024 * 1024))
    except Exception:
        pass
    return 4096


def available_memory_mb() -> int:
    """Currently available (free + cached) RAM in MB."""
    try:
        import psutil  # type: ignore
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        pass
    return system_memory_mb() // 2


def onnx_providers() -> list[str]:
    """Best available ONNX Runtime execution providers, ordered by speed.
    Priority: CUDA > CoreML (Apple Silicon) > DML (Windows) > CPU.
    Safe to pass directly to ONNX session."""
    providers: list[str] = []
    try:
        import onnxruntime as ort  # type: ignore
        available = set(ort.get_available_providers())
        if "CUDAExecutionProvider" in available:
            providers.append("CUDAExecutionProvider")
        if "CoreMLExecutionProvider" in available:
            providers.append("CoreMLExecutionProvider")
        if "DmlExecutionProvider" in available:
            providers.append("DmlExecutionProvider")
        providers.append("CPUExecutionProvider")
    except Exception:
        providers = ["CPUExecutionProvider"]
    return providers


def set_optimal_thread_env() -> None:
    """Set environment variables that control thread pools for numpy, ONNX,
    OpenMP, MKL, and Apple Accelerate. Call ONCE at startup, BEFORE importing
    heavy libs. Conservative: uses physical cores to avoid oversubscription."""
    n = str(optimal_cpu_workers())
    for key in (
        "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(key, n)
    # ONNX Runtime intra-op parallelism
    os.environ.setdefault("ORT_NUM_THREADS", n)
    # Apple Accelerate framework (used by numpy on macOS ARM)
    if is_apple_silicon():
        os.environ.setdefault("ACCELERATE_NUM_THREADS", n)


def hardware_summary() -> dict[str, object]:
    """Summary dict for diagnostics/logging."""
    return {
        "arch": platform.machine(),
        "is_arm": is_arm(),
        "is_apple_silicon": is_apple_silicon(),
        "is_unified_memory": is_unified_memory(),
        "chip": chip_name(),
        "logical_cores": _logical_cores(),
        "physical_cores": _physical_cores(),
        "io_workers": optimal_workers(),
        "cpu_workers": optimal_cpu_workers(),
        "system_ram_mb": system_memory_mb(),
        "available_ram_mb": available_memory_mb(),
        "gpu": gpu_available(),
        "gpu_name": gpu_device_name(),
        "onnx_providers": onnx_providers(),
    }


def platform_tag() -> str:
    """Short platform+arch string for logs, e.g. 'Windows/AMD64' or
    'Darwin/arm64'. Safe to call at any point."""
    import platform as _p

    return f"{_p.system()}/{_p.machine()}"
