"""
jobs.py
A tiny in-process async job registry for long-running agent operations
(test-case generation, ADO push, bulk-defect upload).

The frontend cannot hold a single HTTP request open for the full duration
of a generation run (corporate proxies and browsers time out, and the user
wants live logs + progress). Instead each long operation is started as an
asyncio task that writes its log lines and progress into a Job record; the
browser polls ``GET .../job/{id}`` to render the live log and progress bar,
exactly like the desktop app's worker + log panel.

Everything runs on the agent's single event loop, so mutating the Job from
the running coroutine and reading it from a poll handler needs no locking.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Final

# Keep finished jobs around briefly so the UI can read the final result, then
# evict to bound memory. The agent is long-lived (auto-start at login).
_MAX_JOBS: Final[int] = 60
_TTL_SECONDS: Final[float] = 3600.0
_MAX_LOG_LINES: Final[int] = 6000


@dataclass(slots=True)
class Job:
    id: str
    kind: str                       # generate | push | defects_upload
    state: str = "running"          # running | done | error | stopped
    logs: list[str] = field(default_factory=list)
    progress_stage: str = ""
    progress_current: int = 0
    progress_total: int = 0
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    stop_event: threading.Event = field(default_factory=threading.Event)

    def log(self, msg: str) -> None:
        if not msg:
            return
        self.logs.append(str(msg))
        self.updated_at = time.time()
        # Bound the log buffer; drop the oldest lines if a run is very chatty.
        if len(self.logs) > _MAX_LOG_LINES:
            del self.logs[: len(self.logs) - _MAX_LOG_LINES]

    def set_progress(self, stage: str, current: int, total: int) -> None:
        self.progress_stage = stage
        self.progress_current = int(current)
        self.progress_total = int(total)
        self.updated_at = time.time()

    def finish(self, result: dict[str, Any] | None = None) -> None:
        if result is not None:
            self.result = result
        self.state = "done"
        self.updated_at = time.time()

    def fail(self, error: str) -> None:
        self.error = error
        self.state = "error"
        self.updated_at = time.time()

    @property
    def stopped(self) -> bool:
        return self.stop_event.is_set()

    def snapshot(self, log_offset: int = 0) -> dict[str, Any]:
        """Serialize for the poll endpoint. ``log_offset`` lets the client
        request only log lines it has not seen yet."""
        if log_offset < 0:
            log_offset = 0
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "logs": self.logs[log_offset:],
            "log_count": len(self.logs),
            "progress": {
                "stage": self.progress_stage,
                "current": self.progress_current,
                "total": self.progress_total,
            },
            "error": self.error,
            "result": self.result,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, kind: str) -> Job:
        self._gc()
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _gc(self) -> None:
        now = time.time()
        # Drop expired terminal jobs.
        for jid in list(self._jobs.keys()):
            j = self._jobs[jid]
            if j.state != "running" and (now - j.updated_at) > _TTL_SECONDS:
                self._jobs.pop(jid, None)
        # Hard cap: evict the oldest terminal jobs first.
        if len(self._jobs) > _MAX_JOBS:
            terminal = sorted(
                (j for j in self._jobs.values() if j.state != "running"),
                key=lambda j: j.updated_at,
            )
            for j in terminal:
                if len(self._jobs) <= _MAX_JOBS:
                    break
                self._jobs.pop(j.id, None)


# Module-level singleton shared by every route module.
JOBS: Final[JobManager] = JobManager()
