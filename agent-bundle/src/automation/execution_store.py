"""
execution_store.py
Persists E2E test execution results per project so users can track
test history, re-run failures, and see execution status in the board grid.

Storage: {project_root}/generated/.runs/{run_id}.json
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TestResult:
    tc_id: str
    tc_title: str
    status: str  # "pass" | "fail" | "skip" | "error"
    duration_ms: int
    error_message: str
    screenshot_path: str
    timestamp: float


@dataclass(slots=True)
class ExecutionRun:
    run_id: str
    project_full: str
    started_at: float
    finished_at: float
    results: list[TestResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    user_messages: list[str] = field(default_factory=list)


def _runs_dir(project_full: str) -> Path:
    """Return the .runs directory for a project, creating it if needed."""
    from core.project_store import ensure_project

    paths = ensure_project(project_full)
    runs = paths.generated_dir / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    return runs


def _serialize_run(run: ExecutionRun) -> dict[str, Any]:
    d = asdict(run)
    return d


def _deserialize_run(data: dict[str, Any]) -> ExecutionRun:
    results = [TestResult(**r) for r in data.pop("results", [])]
    return ExecutionRun(**data, results=results)


def save_run(project_full: str, run: ExecutionRun) -> Path:
    """Save an execution run to generated/.runs/{run_id}.json."""
    runs = _runs_dir(project_full)
    dest = runs / f"{run.run_id}.json"
    dest.write_text(
        json.dumps(_serialize_run(run), separators=(",", ":")),
        encoding="utf-8",
    )
    return dest


def load_runs(project_full: str, limit: int = 10) -> list[ExecutionRun]:
    """Load the most recent runs sorted by started_at descending."""
    runs_dir = _runs_dir(project_full)
    files = sorted(runs_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    out: list[ExecutionRun] = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(_deserialize_run(data))
        except (json.JSONDecodeError, TypeError, KeyError, OSError):
            continue
    # Sort by started_at descending (file mtime is close but not exact)
    out.sort(key=lambda r: r.started_at, reverse=True)
    return out


def load_latest_run(project_full: str) -> ExecutionRun | None:
    """Load the single most recent run, or None if no runs exist."""
    runs = load_runs(project_full, limit=1)
    return runs[0] if runs else None



def failed_tc_ids(project_full: str) -> set[str]:
    """Return tc_ids that failed or errored in the latest run."""
    latest = load_latest_run(project_full)
    if latest is None:
        return set()
    return {r.tc_id for r in latest.results if r.status in ("fail", "error")}
