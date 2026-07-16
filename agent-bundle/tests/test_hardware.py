"""Tests for core.hardware module."""
from __future__ import annotations

import os
import platform
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.hardware import (
    _FALLBACK_CORES,
    _MIN_WORKERS,
    _logical_cores,
    _physical_cores,
    available_memory_mb,
    chip_name,
    gpu_available,
    gpu_device_name,
    hardware_summary,
    is_apple_silicon,
    is_arm,
    is_unified_memory,
    onnx_providers,
    optimal_cpu_workers,
    optimal_workers,
    platform_tag,
    set_optimal_thread_env,
    system_memory_mb,
)


# -- is_arm -------------------------------------------------------------------

class TestIsArm:
    def test_returns_bool(self) -> None:
        result = is_arm()
        assert isinstance(result, bool)

    @patch("platform.machine", return_value="aarch64")
    def test_aarch64_is_arm(self, _mock: Any) -> None:
        assert is_arm() is True

    @patch("platform.machine", return_value="arm64")
    def test_arm64_is_arm(self, _mock: Any) -> None:
        assert is_arm() is True

    @patch("platform.machine", return_value="x86_64")
    def test_x86_is_not_arm(self, _mock: Any) -> None:
        assert is_arm() is False

    @patch("platform.machine", side_effect=RuntimeError("fail"))
    def test_exception_returns_false(self, _mock: Any) -> None:
        assert is_arm() is False


# -- is_apple_silicon ----------------------------------------------------------

class TestIsAppleSilicon:
    def test_returns_bool(self) -> None:
        result = is_apple_silicon()
        assert isinstance(result, bool)

    def test_darwin_arm64_is_apple_silicon(self) -> None:
        with patch("sys.platform", "darwin"):
            with patch("platform.machine", return_value="arm64"):
                # Re-import to pick up patched sys.platform
                from core.hardware import is_apple_silicon as fn
                assert fn() is True

    @patch("platform.machine", return_value="x86_64")
    def test_non_arm_not_apple_silicon(self, _mock: Any) -> None:
        assert is_apple_silicon() is False


# -- is_unified_memory ---------------------------------------------------------

class TestIsUnifiedMemory:
    def test_returns_bool(self) -> None:
        assert isinstance(is_unified_memory(), bool)

    @patch("core.hardware.is_apple_silicon", return_value=True)
    def test_true_on_apple_silicon(self, _mock: Any) -> None:
        assert is_unified_memory() is True

    @patch("core.hardware.is_apple_silicon", return_value=False)
    def test_false_when_not_apple_silicon(self, _mock: Any) -> None:
        assert is_unified_memory() is False


# -- _logical_cores / _physical_cores -----------------------------------------

class TestCoreDetection:
    def test_logical_cores_positive(self) -> None:
        assert _logical_cores() >= 1

    def test_physical_cores_positive(self) -> None:
        assert _physical_cores() >= 1

    def test_physical_lte_logical(self) -> None:
        assert _physical_cores() <= _logical_cores()

    @patch("os.cpu_count", return_value=None)
    def test_fallback_when_cpu_count_none(self, _mock: Any) -> None:
        assert _logical_cores() == _FALLBACK_CORES

    @patch("os.cpu_count", return_value=None)
    def test_physical_fallback_when_cpu_count_none(self, _mock: Any) -> None:
        assert _physical_cores() == _FALLBACK_CORES


# -- optimal_workers / optimal_cpu_workers ------------------------------------

class TestOptimalWorkers:
    def test_io_workers_at_least_min(self) -> None:
        assert optimal_workers() >= _MIN_WORKERS

    def test_io_workers_at_most_32(self) -> None:
        assert optimal_workers() <= 32

    def test_cpu_workers_at_least_min(self) -> None:
        assert optimal_cpu_workers() >= _MIN_WORKERS

    @patch("core.hardware._logical_cores", return_value=1)
    def test_io_workers_respects_min(self, _mock: Any) -> None:
        assert optimal_workers() == _MIN_WORKERS

    @patch("core.hardware._physical_cores", return_value=1)
    def test_cpu_workers_respects_min(self, _mock: Any) -> None:
        assert optimal_cpu_workers() == _MIN_WORKERS


# -- gpu_available -------------------------------------------------------------

class TestGpuAvailable:
    def test_returns_bool(self) -> None:
        assert isinstance(gpu_available(), bool)

    @patch.dict("sys.modules", {"torch": None, "onnxruntime": None})
    def test_false_when_no_libs(self) -> None:
        # With both torch and onnxruntime unimportable, should return False
        assert gpu_available() is False

    def test_cuda_available(self) -> None:
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert gpu_available() is True


# -- gpu_device_name -----------------------------------------------------------

class TestGpuDeviceName:
    def test_returns_string(self) -> None:
        assert isinstance(gpu_device_name(), str)

    @patch.dict("sys.modules", {"torch": None, "onnxruntime": None})
    @patch("core.hardware.is_apple_silicon", return_value=False)
    def test_empty_when_no_gpu(self, _mock: Any) -> None:
        assert gpu_device_name() == ""


# -- system_memory_mb ----------------------------------------------------------

class TestSystemMemory:
    def test_returns_positive_int(self) -> None:
        result = system_memory_mb()
        assert isinstance(result, int)
        assert result > 0

    def test_reasonable_range(self) -> None:
        # Any machine running tests should have at least 1GB
        assert system_memory_mb() >= 1024


# -- available_memory_mb -------------------------------------------------------

class TestAvailableMemory:
    def test_returns_positive_int(self) -> None:
        result = available_memory_mb()
        assert isinstance(result, int)
        assert result > 0

    def test_available_lte_total(self) -> None:
        assert available_memory_mb() <= system_memory_mb()


# -- onnx_providers ------------------------------------------------------------

class TestOnnxProviders:
    def test_returns_list(self) -> None:
        result = onnx_providers()
        assert isinstance(result, list)

    def test_always_has_cpu(self) -> None:
        result = onnx_providers()
        assert "CPUExecutionProvider" in result

    @patch.dict("sys.modules", {"onnxruntime": None})
    def test_fallback_cpu_only(self) -> None:
        result = onnx_providers()
        assert result == ["CPUExecutionProvider"]


# -- set_optimal_thread_env ----------------------------------------------------

class TestSetOptimalThreadEnv:
    def test_sets_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Clear relevant vars first
        for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "ORT_NUM_THREADS"):
            monkeypatch.delenv(key, raising=False)

        set_optimal_thread_env()

        assert os.environ.get("OMP_NUM_THREADS") is not None
        assert os.environ.get("MKL_NUM_THREADS") is not None
        assert os.environ.get("ORT_NUM_THREADS") is not None
        val = int(os.environ["OMP_NUM_THREADS"])
        assert val >= _MIN_WORKERS

    def test_does_not_override_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OMP_NUM_THREADS", "99")
        set_optimal_thread_env()
        assert os.environ["OMP_NUM_THREADS"] == "99"


# -- chip_name -----------------------------------------------------------------

class TestChipName:
    def test_returns_nonempty_string(self) -> None:
        result = chip_name()
        assert isinstance(result, str)
        assert len(result) > 0


# -- hardware_summary ----------------------------------------------------------

class TestHardwareSummary:
    def test_returns_dict_with_expected_keys(self) -> None:
        summary = hardware_summary()
        expected_keys = {
            "arch", "is_arm", "is_apple_silicon", "is_unified_memory",
            "chip", "logical_cores", "physical_cores", "io_workers",
            "cpu_workers", "system_ram_mb", "available_ram_mb",
            "gpu", "gpu_name", "onnx_providers",
        }
        assert expected_keys.issubset(set(summary.keys()))

    def test_values_are_sensible(self) -> None:
        summary = hardware_summary()
        assert isinstance(summary["is_arm"], bool)
        assert isinstance(summary["logical_cores"], int)
        assert summary["logical_cores"] >= 1  # type: ignore[operator]


# -- platform_tag --------------------------------------------------------------

class TestPlatformTag:
    def test_format(self) -> None:
        tag = platform_tag()
        assert "/" in tag
        parts = tag.split("/")
        assert len(parts) == 2
        assert len(parts[0]) > 0
        assert len(parts[1]) > 0
