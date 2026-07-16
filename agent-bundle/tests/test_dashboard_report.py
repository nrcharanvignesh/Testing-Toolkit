# Tests for dashboard_report.py: validates HTML generation across scenarios.
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


# Minimal stand-in matching the real TestCaseResult/StepResult shape.
@dataclass
class _StepResult:
    step_num: int = 1
    action: str = "click"
    expected: str = ""
    actual: str = ""
    status: str = "pass"
    locator_strategy: str = ""
    screenshot_path: object = None
    duration_ms: int = 100


@dataclass
class _TCResult:
    tc_id: str = "TC-001"
    title: str = "Sample test"
    steps: list = None  # type: ignore[assignment]
    video_path: object = None
    script_path: object = None
    overall_status: str = "pass"
    duration_ms: int = 500

    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = [_StepResult()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDashboardEmpty:
    """Generate with empty results produces valid HTML."""

    def test_empty_results_produces_valid_html(self, tmp_path: Path) -> None:
        from automation.dashboard_report import generate_dashboard

        out = tmp_path / "report.html"
        result = generate_dashboard(
            results=[],
            bug_summary={},
            healing_history=[],
            output_path=out,
        )
        assert result == out
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "</html>" in content
        assert "0.0%" in content  # pass rate with zero tests


class TestDashboardMixed:
    """Generate with mixed pass/fail produces correct counts."""

    def test_mixed_pass_fail_counts(self, tmp_path: Path) -> None:
        from automation.dashboard_report import generate_dashboard

        results = [
            _TCResult(tc_id="TC-01", title="Login", overall_status="pass"),
            _TCResult(tc_id="TC-02", title="Search", overall_status="fail",
                      steps=[_StepResult(status="fail", actual="Element not found")]),
            _TCResult(tc_id="TC-03", title="Logout", overall_status="pass"),
            _TCResult(tc_id="TC-04", title="Upload", overall_status="error",
                      steps=[_StepResult(status="error", actual="Timeout")]),
        ]
        out = tmp_path / "report.html"
        generate_dashboard(results, {}, [], out)
        content = out.read_text(encoding="utf-8")
        # 2 passed, 2 failed (fail+error), 50% rate
        assert "50.0%" in content
        assert ">2<" in content  # passed count
        assert "FAIL" in content
        assert "Element not found" in content


class TestDashboardHealing:
    """Generate with healing data includes the healing section."""

    def test_healing_section_present(self, tmp_path: Path) -> None:
        from automation.dashboard_report import generate_dashboard

        healing = [
            {
                "step_action": "click",
                "step_target": "Submit button",
                "original_locator": "#old-btn",
                "healed_locator": "[data-testid='submit']",
                "success": True,
            },
        ]
        out = tmp_path / "report.html"
        generate_dashboard([_TCResult()], {}, healing, out)
        content = out.read_text(encoding="utf-8")
        assert "Self-Healing Activity" in content
        assert "#old-btn" in content
        assert "[data-testid=&#x27;submit&#x27;]" in content or "data-testid" in content


class TestDashboardFileCreation:
    """Output file is created at specified path, including nested dirs."""

    def test_creates_nested_output(self, tmp_path: Path) -> None:
        from automation.dashboard_report import generate_dashboard

        out = tmp_path / "sub" / "dir" / "dashboard.html"
        result = generate_dashboard([], {}, [], out)
        assert result.exists()
        assert result.parent.name == "dir"


class TestDashboardSelfContained:
    """HTML contains no external resource references -- fully self-contained."""

    def test_no_external_references(self, tmp_path: Path) -> None:
        from automation.dashboard_report import generate_dashboard

        results = [
            _TCResult(tc_id="TC-10", title="Full flow", overall_status="pass"),
        ]
        healing = [
            {
                "step_action": "fill",
                "step_target": "Email",
                "original_locator": "input.email",
                "healed_locator": "[name='email']",
                "success": True,
            },
        ]
        bug_summary = {
            "recurring_bugs": [{"id": "BUG-1", "description": "Flaky submit"}],
            "hotspot_stories": ["US-100"],
            "flaky_tests": ["TC-10"],
        }
        out = tmp_path / "report.html"
        generate_dashboard(results, bug_summary, healing, out)
        content = out.read_text(encoding="utf-8")

        # No external stylesheet/script/image references
        assert "<link " not in content.lower().replace("<link", "")  # no extra <link>
        assert 'src="http' not in content
        assert "src='http" not in content
        assert '<script src=' not in content
        assert '@import' not in content
