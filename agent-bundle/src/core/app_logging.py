"""
app_logging.py
Central application logging for the Testing Toolkit.

A single rotating log file is written so QA reviewers can attach it to bug
reports. The same [INFO]/[ERROR]/[SUCCESS]/[WARN] lines shown in each tool's
in-app log panel are mirrored to this file, plus session start/stop markers,
environment details, and any uncaught exceptions.

Crash capture is deliberately broad so a failure in a leadership demo is
always recorded:
  * sys.excepthook       - uncaught exceptions on the main thread
  * threading.excepthook - uncaught exceptions on worker threads
  * Qt message handler    - Qt's own warnings / criticals / fatals
  * faulthandler          - native crashes (segfaults) -> separate trace file

Log location (first writable wins):
  1. %LOCALAPPDATA%\\TestingToolkit\\logs   (Windows)
  2. ~/.testing_toolkit/logs                (any OS fallback)
  3. ./logs                                 (last resort)

Public API:
    init_logging() -> Path | None   # call once at startup; returns log path
    get_logger(name) -> Logger
    log_line(text)                  # mirror an in-app [TAG] line to the file
    log_path() -> Path | None       # current log file path (or None)
    log_dir() -> Path | None        # directory holding the log
    tail_log(max_bytes) -> str      # recent log text (for in-app display)
    install_excepthook()            # main-thread uncaught exceptions
    install_threading_excepthook()  # worker-thread uncaught exceptions
    install_qt_message_handler()    # Qt's own message stream (call post-QApp)
"""

from __future__ import annotations

import faulthandler
import logging
import os
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_APP_DIR_NAME = "TestingToolkit"
_LOG_FILE_NAME = "testing_toolkit.log"
_FAULT_FILE_NAME = "testing_toolkit_fault.log"
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB per file (verbose logging, long history)
_BACKUP_COUNT = 10             # keep 10 rotated files (~200 MB of history)

_log_path: Path | None = None
_initialized: bool = False
_qt_handler_installed: bool = False
_fault_file: Any = None        # keep the faulthandler stream alive
_ROOT_NAME = "testing_toolkit"
_SECRET_TOKEN_RE = re.compile(r"(?i)\b(?:sk|key|token|bearer)[-_][A-Za-z0-9._~+/=-]{12,}\b")


def redact_secrets(text: object) -> str:
    """Remove configured credentials and credential-like tokens from log text."""
    value = str(text)
    secrets: list[str] = []
    for name in ("LLM_UPSTREAM_API_KEY", "API_KEY", "LLM_UPSTREAM_BASE_URL", "BASE_URL"):
        candidate = (os.environ.get(name) or "").strip()
        if candidate:
            secrets.append(candidate)
    try:
        from core.app_config import LLM_API_KEY, LLM_BASE_URL
        secrets.extend((LLM_API_KEY, LLM_BASE_URL))
    except Exception:
        pass
    for secret in sorted({s for s in secrets if len(s) >= 8}, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return _SECRET_TOKEN_RE.sub("[REDACTED]", value)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    # Preferred: the single TestingToolkitWeb workspace, so the log sits
    # next to projects/KB/runs/outputs (and honours TT_WORKSPACE_DIR).
    try:
        from core.app_config import LOGS_DIR
        dirs.append(LOGS_DIR)
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / _APP_DIR_NAME / "logs")
    dirs.append(Path.home() / ".testing_toolkit" / "logs")
    # Last resort: alongside the running program / cwd.
    try:
        dirs.append(Path.cwd() / "logs")
    except Exception:
        pass
    return dirs


def _resolve_log_dir() -> Path | None:
    for d in _candidate_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            # Confirm writability with a touch.
            probe = d / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return d
        except Exception:
            continue
    return None


def init_logging() -> Path | None:
    """Configure the root app logger with a rotating file handler.
    Idempotent: safe to call more than once. Returns the log file
    path, or None if no writable location was found (logging then
    silently degrades to a null handler so the app never crashes)."""
    global _log_path, _initialized
    if _initialized:
        return _log_path

    logger = logging.getLogger(_ROOT_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    log_dir = _resolve_log_dir()
    if log_dir is None:
        logger.addHandler(logging.NullHandler())
        _initialized = True
        return None

    _log_path = log_dir / _LOG_FILE_NAME
    try:
        handler = RotatingFileHandler(
            str(_log_path), maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT, encoding="utf-8",
        )
        fmt = _RedactingFormatter(
            "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    except Exception:
        logger.addHandler(logging.NullHandler())
        _log_path = None
        _initialized = True
        return None

    _initialized = True

    # Native-crash capture: write low-level tracebacks (incl. segfaults) to a
    # dedicated file that survives a hard crash. Best-effort; never fatal.
    global _fault_file
    try:
        _fault_file = open(str(log_dir / _FAULT_FILE_NAME), "a",
                           encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception:
        _fault_file = None

    # Session banner + environment for debugging shared logs.
    logger.info("=" * 60)
    logger.info("Testing Toolkit session start")
    logger.info("python:   %s", sys.version.split()[0])
    logger.info("platform: %s", sys.platform)
    logger.info("frozen:   %s", bool(getattr(sys, "frozen", False)))
    logger.info("log file: %s", _log_path)
    logger.info("=" * 60)
    return _log_path


def get_logger(name: str = "") -> logging.Logger:
    """Return a child logger under the app root. init_logging() must
    have been called first; if not, this still works but writes
    nowhere until it is."""
    if name:
        return logging.getLogger(f"{_ROOT_NAME}.{name}")
    return logging.getLogger(_ROOT_NAME)


class _CallbackLogHandler(logging.Handler):
    """A logging handler that forwards every formatted record to an arbitrary
    callback (e.g. a job's ``log`` method). Used to surface the agent's rich
    internal DEBUG logging in the live in-app / web Activity Log, not just the
    handful of explicit ``[INFO]`` lines each operation emits."""

    def __init__(self, callback: Any, level: int = logging.DEBUG) -> None:
        super().__init__(level=level)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = redact_secrets(record.getMessage())
            # Preserve any existing [TAG] prefix so the UI colors it correctly;
            # otherwise annotate WARNING/ERROR so they stand out, and leave
            # INFO/DEBUG unprefixed (the UI treats unprefixed as INFO).
            if not msg.lstrip().startswith("["):
                if record.levelno >= logging.ERROR:
                    msg = f"[ERROR] {msg}"
                elif record.levelno >= logging.WARNING:
                    msg = f"[WARN] {msg}"
                elif record.levelno <= logging.DEBUG:
                    msg = f"[DEBUG] {msg}"
            self._callback(msg)
        except Exception:
            # Logging must never break the operation it is observing.
            pass


class stream_agent_logs:
    """Context manager that mirrors the agent's internal logger output into a
    callback for the duration of a block. Attach a job's ``log`` method to get
    verbose, real-time logs (LLM calls, KB retrieval, HTTP retries, MCP, etc.)
    in the Activity Log:

        with stream_agent_logs(job.log):
            await do_work(...)

    The handler is removed on exit, so it never leaks across jobs. ``level``
    controls verbosity (DEBUG = everything)."""

    def __init__(self, callback: Any, level: int = logging.DEBUG) -> None:
        self._handler = _CallbackLogHandler(callback, level=level)
        self._logger = logging.getLogger(_ROOT_NAME)

    def __enter__(self) -> "stream_agent_logs":
        try:
            self._logger.addHandler(self._handler)
        except Exception:
            pass
        return self

    def __exit__(self, *_exc: Any) -> None:
        try:
            self._logger.removeHandler(self._handler)
        except Exception:
            pass


# Map in-app [TAG] prefixes to log levels.
_TAG_LEVELS = {
    "[ERROR]": logging.ERROR,
    "[WARN]": logging.WARNING,
    "[WARNING]": logging.WARNING,
    "[SUCCESS]": logging.INFO,
    "[INFO]": logging.INFO,
    "[DEBUG]": logging.DEBUG,
}


def log_line(text: str, source: str = "ui") -> None:
    """Mirror a single in-app log line (already carrying a [TAG]
    prefix) into the rotating file at the matching level. Never
    raises."""
    try:
        logger = get_logger(source)
        level = logging.INFO
        stripped = text.lstrip()
        for tag, lvl in _TAG_LEVELS.items():
            if stripped.startswith(tag):
                level = lvl
                break
        logger.log(level, redact_secrets(text))
    except Exception:
        pass


def log_path() -> Path | None:
    return _log_path


def log_dir() -> Path | None:
    return _log_path.parent if _log_path is not None else None


def tail_log(max_bytes: int = 20000) -> str:
    """Return the last max_bytes of the current log file as text (for an
    in-app 'view recent log' view). Never raises."""
    p = _log_path
    if p is None:
        return "(no log file configured)"
    try:
        size = p.stat().st_size
        with open(str(p), "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read()
        text = data.decode("utf-8", errors="replace")
        if size > max_bytes:
            # Drop a partial first line for cleanliness.
            nl = text.find("\n")
            if nl != -1:
                text = text[nl + 1:]
            text = "...(truncated)...\n" + text
        return text
    except Exception as e:  # noqa: BLE001
        return f"(could not read log: {e!r})"


def install_excepthook() -> None:
    """Route uncaught exceptions to the log before the default
    handler runs, so crashes are captured in shared log files."""
    prev = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb) -> None:
        try:
            get_logger("crash").critical(
                "Uncaught exception (main thread)",
                exc_info=(exc_type, exc_value, exc_tb),
            )
        except Exception:
            pass
        if prev is not None:
            prev(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def install_threading_excepthook() -> None:
    """Capture uncaught exceptions raised on non-main (worker) threads.
    Python 3.8+ provides threading.excepthook; we chain to the previous
    one. Best-effort and never fatal."""
    prev = getattr(threading, "excepthook", None)

    def _hook(args: Any) -> None:
        try:
            get_logger("crash").critical(
                "Uncaught exception (thread %s)",
                getattr(getattr(args, "thread", None), "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:
            pass
        if prev is not None and prev is not _hook:
            try:
                prev(args)
            except Exception:
                pass

    try:
        threading.excepthook = _hook
    except Exception:
        pass


def install_qt_message_handler() -> None:
    """Route Qt's own message stream (qDebug/qWarning/qCritical/qFatal,
    including binding and rendering warnings) into the log. Must be called
    after the QApplication/Qt is importable. Idempotent; never fatal."""
    global _qt_handler_installed
    if _qt_handler_installed:
        return
    try:
        from PySide6.QtCore import (
            QtMsgType,
            qInstallMessageHandler,
        )
    except Exception:
        return

    _level_for = {
        getattr(QtMsgType, "QtDebugMsg", None): logging.DEBUG,
        getattr(QtMsgType, "QtInfoMsg", None): logging.INFO,
        getattr(QtMsgType, "QtWarningMsg", None): logging.WARNING,
        getattr(QtMsgType, "QtCriticalMsg", None): logging.ERROR,
        getattr(QtMsgType, "QtFatalMsg", None): logging.CRITICAL,
    }

    def _handler(mode: Any, context: Any, message: str) -> None:
        try:
            level = _level_for.get(mode, logging.INFO)
            get_logger("qt").log(level, "%s", message)
        except Exception:
            pass

    try:
        qInstallMessageHandler(_handler)
        _qt_handler_installed = True
    except Exception:
        pass
