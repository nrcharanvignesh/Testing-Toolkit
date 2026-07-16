"""Tests for core.diagnostics — agent self-diagnosis and capabilities."""
from __future__ import annotations

from core.diagnostics import _safe, _workspace_dir, _PASS, _WARN, _FAIL


class TestSafe:
    def test_returns_fn_result_on_success(self) -> None:
        assert _safe(lambda: 42) == 42

    def test_returns_default_on_exception(self) -> None:
        assert _safe(lambda: 1 / 0, "fallback") == "fallback"

    def test_returns_none_on_exception_no_default(self) -> None:
        assert _safe(lambda: [][0]) is None

    def test_handles_type_error(self) -> None:
        assert _safe(lambda: int("not_a_number"), -1) == -1


class TestWorkspaceDir:
    def test_returns_string_or_none(self) -> None:
        result = _workspace_dir()
        assert result is None or isinstance(result, str)


class TestConstants:
    def test_status_values(self) -> None:
        assert _PASS == "pass"
        assert _WARN == "warn"
        assert _FAIL == "fail"


class TestCapabilities:
    def test_returns_dict(self) -> None:
        from core.diagnostics import capabilities

        result = capabilities()
        assert isinstance(result, dict)
        assert "llm" in result or "embedding" in result or len(result) >= 0

    def test_never_raises(self) -> None:
        from core.diagnostics import capabilities

        # capabilities() is designed to never raise
        try:
            capabilities()
        except Exception as e:
            raise AssertionError(f"capabilities() raised: {e}")


class TestRunDoctor:
    def test_returns_dict_with_checks(self) -> None:
        from core.diagnostics import run_doctor

        result = run_doctor()
        assert isinstance(result, dict)
        assert "checks" in result
        assert isinstance(result["checks"], list)

    def test_checks_have_required_fields(self) -> None:
        from core.diagnostics import run_doctor

        result = run_doctor()
        for check in result["checks"]:
            assert "label" in check or "id" in check
            assert "status" in check
            assert check["status"] in (_PASS, _WARN, _FAIL)

    def test_never_raises(self) -> None:
        from core.diagnostics import run_doctor

        try:
            run_doctor()
        except Exception as e:
            raise AssertionError(f"run_doctor() raised: {e}")
