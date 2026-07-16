"""Tests for bug_tracker module."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from automation.bug_tracker import (
    HISTORY_PATH,
    MAX_RUNS_PER_CASE,
    RunEntry,
    get_bug_summary,
    get_flaky_tests,
    get_hotspots,
    get_recurring_bugs,
    record_run_result,
    _read_history,
    _write_history,
)


@pytest.fixture(autouse=True)
def clean_history(tmp_path):
    """Redirect history to a temp file for isolation."""
    fake_path = tmp_path / "bug_history.json"
    with patch("automation.bug_tracker.HISTORY_PATH", fake_path):
        with patch("automation.bug_tracker._DATA_DIR", tmp_path):
            yield fake_path


def _make_entry(
    case_id: str = "TC-001",
    step: int = 1,
    status: str = "fail",
    error_type: str = "TimeoutError",
    error_msg: str = "element not found",
    run_id: str | None = None,
) -> RunEntry:
    return RunEntry(
        run_id=run_id or f"run-{time.time_ns()}",
        timestamp=time.time(),
        test_case_id=case_id,
        step_num=step,
        error_type=error_type,
        error_message=error_msg,
        status=status,
        locator_strategy="css",
        healed=False,
    )


# -- 1. Record a single run result ----------------------------------------
def test_record_single_run(clean_history):
    entry = _make_entry(run_id="run-1")
    record_run_result(entry)
    data = json.loads(clean_history.read_text(encoding="utf-8"))
    assert "TC-001" in data
    assert len(data["TC-001"]) == 1
    assert data["TC-001"][0]["run_id"] == "run-1"


# -- 2. Recurring bug detection (>= 3 fails same case+step) ---------------
def test_recurring_bug_detection(clean_history):
    for i in range(4):
        record_run_result(_make_entry(run_id=f"run-{i}", step=2))
    bugs = get_recurring_bugs()
    assert len(bugs) == 1
    assert bugs[0].test_case_id == "TC-001"
    assert bugs[0].step_num == 2
    assert bugs[0].fail_count == 4


# -- 3. Hotspot detection (>= 3 distinct failure modes) --------------------
def test_hotspot_detection(clean_history):
    error_types = ["TimeoutError", "AssertionError", "ElementNotVisible", "NetworkError"]
    for i, et in enumerate(error_types):
        record_run_result(_make_entry(
            run_id=f"run-{i}", error_type=et, error_msg=f"msg-{i}"
        ))
    hotspots = get_hotspots()
    assert len(hotspots) == 1
    assert hotspots[0].test_case_id == "TC-001"
    assert hotspots[0].distinct_failure_modes == 4


# -- 4. Flakiness score calculation ----------------------------------------
def test_flakiness_score(clean_history):
    # 5 fails out of 10 runs = 0.5 flakiness (between 0.3 and 0.8 -> flaky)
    for i in range(10):
        status = "fail" if i < 5 else "pass"
        record_run_result(_make_entry(run_id=f"run-{i}", status=status))
    flaky = get_flaky_tests()
    assert len(flaky) == 1
    assert flaky[0].flakiness_score == 0.5
    assert flaky[0].fail_count == 5
    assert flaky[0].total_runs == 10


# -- 5. Max history cap (101 entries -> oldest trimmed) --------------------
def test_max_history_cap(clean_history):
    for i in range(101):
        record_run_result(_make_entry(run_id=f"run-{i}"))
    data = json.loads(clean_history.read_text(encoding="utf-8"))
    assert len(data["TC-001"]) == MAX_RUNS_PER_CASE  # 100
    # oldest (run-0) trimmed, newest (run-100) present
    assert data["TC-001"][0]["run_id"] == "run-1"
    assert data["TC-001"][-1]["run_id"] == "run-100"


# -- 6. get_bug_summary returns correct structure --------------------------
def test_bug_summary_structure(clean_history):
    for i in range(3):
        record_run_result(_make_entry(run_id=f"run-{i}"))
    summary = get_bug_summary()
    assert summary.total_tracked_cases == 1
    assert isinstance(summary.recurring_bugs, list)
    assert isinstance(summary.hotspots, list)
    assert isinstance(summary.flaky_tests, list)
    assert isinstance(summary.real_bugs, list)
    # 3 fails out of 3 runs = 1.0 >= 0.8 -> real bug
    assert len(summary.real_bugs) == 1


# -- 7. Empty state (no runs yet) -----------------------------------------
def test_empty_state(clean_history):
    assert get_recurring_bugs() == []
    assert get_hotspots() == []
    assert get_flaky_tests() == []
    summary = get_bug_summary()
    assert summary.total_tracked_cases == 0


# -- 8. All passes (no bugs) ----------------------------------------------
def test_all_passes_no_bugs(clean_history):
    for i in range(5):
        record_run_result(_make_entry(run_id=f"run-{i}", status="pass"))
    assert get_recurring_bugs() == []
    assert get_hotspots() == []
    assert get_flaky_tests() == []
    summary = get_bug_summary()
    assert summary.total_tracked_cases == 1
    assert summary.real_bugs == []
