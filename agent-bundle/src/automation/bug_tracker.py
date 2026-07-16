"""Bug recurrence tracking module.

Tracks test failures across execution runs to identify recurring bugs,
hotspot test cases, and flaky tests vs real bugs.
"""
from __future__ import annotations

import gc
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# -- paths -----------------------------------------------------------------
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
HISTORY_PATH = _DATA_DIR / "bug_history.json"

# -- constants -------------------------------------------------------------
MAX_RUNS_PER_CASE = 100
RECURRING_THRESHOLD = 3
HOTSPOT_DISTINCT_FAILURES = 3
FLAKY_LOW = 0.3
FLAKY_HIGH = 0.8

_lock = threading.Lock()


# -- data model ------------------------------------------------------------
@dataclass
class RunEntry:
    run_id: str
    timestamp: float
    test_case_id: str
    step_num: int
    error_type: str
    error_message: str
    status: str  # "pass" | "fail"
    locator_strategy: str = ""
    healed: bool = False


@dataclass
class RecurringBug:
    test_case_id: str
    step_num: int
    fail_count: int
    last_error_type: str
    last_error_message: str


@dataclass
class Hotspot:
    test_case_id: str
    distinct_failure_modes: int
    failure_types: list[str] = field(default_factory=list)


@dataclass
class FlakyTest:
    test_case_id: str
    flakiness_score: float
    fail_count: int
    total_runs: int


@dataclass
class BugSummary:
    total_tracked_cases: int
    recurring_bugs: list[RecurringBug]
    hotspots: list[Hotspot]
    flaky_tests: list[FlakyTest]
    real_bugs: list[FlakyTest]


# -- persistence -----------------------------------------------------------
def _read_history() -> dict[str, list[dict[str, Any]]]:
    """Read history file. Returns {test_case_id: [entries]}."""
    if not HISTORY_PATH.exists():
        return {}
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def _write_history(data: dict[str, list[dict[str, Any]]]) -> None:
    """Write history to file with directory creation."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    # atomic-ish rename
    if os.name == "nt":
        # Windows: remove target first
        if HISTORY_PATH.exists():
            HISTORY_PATH.unlink()
    tmp.rename(HISTORY_PATH)
    gc.collect()


# -- public API ------------------------------------------------------------
def record_run_result(entry: RunEntry) -> None:
    """Record a single test run result. Thread-safe."""
    with _lock:
        data = _read_history()
        case_id = entry.test_case_id
        if case_id not in data:
            data[case_id] = []
        data[case_id].append(asdict(entry))
        # cap at MAX_RUNS_PER_CASE -- trim oldest
        if len(data[case_id]) > MAX_RUNS_PER_CASE:
            data[case_id] = data[case_id][-MAX_RUNS_PER_CASE:]
        _write_history(data)
    print(f"[INFO] Recorded run {entry.run_id} for {case_id} step {entry.step_num}")


def get_recurring_bugs() -> list[RecurringBug]:
    """Identify bugs where same test_case + step_num fails >= 3 runs."""
    data = _read_history()
    results: list[RecurringBug] = []
    for case_id, entries in data.items():
        # group failures by step_num
        step_fails: dict[int, list[dict[str, Any]]] = {}
        for e in entries:
            if e["status"] == "fail":
                step = e["step_num"]
                step_fails.setdefault(step, []).append(e)
        for step_num, fails in step_fails.items():
            if len(fails) >= RECURRING_THRESHOLD:
                last = fails[-1]
                results.append(RecurringBug(
                    test_case_id=case_id,
                    step_num=step_num,
                    fail_count=len(fails),
                    last_error_type=last["error_type"],
                    last_error_message=last["error_message"],
                ))
    return results


def get_hotspots() -> list[Hotspot]:
    """Identify test cases with >= 3 distinct failure modes."""
    data = _read_history()
    results: list[Hotspot] = []
    for case_id, entries in data.items():
        failure_types: set[str] = set()
        for e in entries:
            if e["status"] == "fail":
                failure_types.add(e["error_type"])
        if len(failure_types) >= HOTSPOT_DISTINCT_FAILURES:
            results.append(Hotspot(
                test_case_id=case_id,
                distinct_failure_modes=len(failure_types),
                failure_types=sorted(failure_types),
            ))
    return results


def get_flaky_tests() -> list[FlakyTest]:
    """Return tests with flakiness_score > 0.3 and < 0.8."""
    data = _read_history()
    results: list[FlakyTest] = []
    for case_id, entries in data.items():
        total = len(entries)
        if total == 0:
            continue
        fail_count = sum(1 for e in entries if e["status"] == "fail")
        score = fail_count / total
        if FLAKY_LOW < score < FLAKY_HIGH:
            results.append(FlakyTest(
                test_case_id=case_id,
                flakiness_score=round(score, 4),
                fail_count=fail_count,
                total_runs=total,
            ))
    return results


def _get_real_bugs() -> list[FlakyTest]:
    """Return tests with flakiness_score >= 0.8 (consistently failing)."""
    data = _read_history()
    results: list[FlakyTest] = []
    for case_id, entries in data.items():
        total = len(entries)
        if total == 0:
            continue
        fail_count = sum(1 for e in entries if e["status"] == "fail")
        score = fail_count / total
        if score >= FLAKY_HIGH:
            results.append(FlakyTest(
                test_case_id=case_id,
                flakiness_score=round(score, 4),
                fail_count=fail_count,
                total_runs=total,
            ))
    return results


def get_bug_summary() -> BugSummary:
    """Full summary: recurring bugs, hotspots, flaky tests, real bugs."""
    data = _read_history()
    return BugSummary(
        total_tracked_cases=len(data),
        recurring_bugs=get_recurring_bugs(),
        hotspots=get_hotspots(),
        flaky_tests=get_flaky_tests(),
        real_bugs=_get_real_bugs(),
    )
