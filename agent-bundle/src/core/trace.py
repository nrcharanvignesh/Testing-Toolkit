"""
trace.py
Dynatrace-level tracing for the Testing Toolkit agent.

Provides:
- TRACE level (5) below DEBUG for ultra-verbose instrumentation
- Structured JSON event entries with correlation IDs
- @trace decorator for sync/async function entry/exit/duration/exception
- TraceContext for distributed correlation (request -> function -> dependency)
- Dependency call tracking (HTTP, DB, file I/O)
- Stack capture on exceptions
- Session-level user journey reconstruction via session_id

All trace entries write to trace.json via the dedicated RotatingFileHandler
configured in app_logging.init_logging(). The human-readable log is unaffected.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import logging
import os
import sys
import time
import traceback
import uuid
from typing import Any, Callable, TypeVar

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

_F = TypeVar("_F", bound=Callable[..., Any])

# -- Correlation context (thread-local via contextvars) -----------------------

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)
_span_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "span_id", default=""
)
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id", default=""
)
_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_id", default=""
)


class TraceContext:
    """Manages distributed trace correlation across a request lifecycle.

    Usage in middleware:
        ctx = TraceContext.start(session_id="...", user_id="...")
        # ... handle request ...
        ctx.end()

    Child spans inherit the trace_id and link via parent_span_id.
    """

    __slots__ = ("trace_id", "span_id", "parent_span_id", "_tokens", "_t0")

    def __init__(
        self,
        trace_id: str = "",
        span_id: str = "",
        parent_span_id: str = "",
    ) -> None:
        self.trace_id = trace_id or uuid.uuid4().hex
        self.span_id = span_id or uuid.uuid4().hex[:16]
        self.parent_span_id = parent_span_id
        self._tokens: list[Any] = []
        self._t0 = time.perf_counter()

    @classmethod
    def start(
        cls,
        *,
        session_id: str = "",
        user_id: str = "",
        parent_trace_id: str = "",
        parent_span_id: str = "",
    ) -> "TraceContext":
        """Start a new trace context and push it onto the contextvar stack."""
        ctx = cls(
            trace_id=parent_trace_id or "",
            parent_span_id=parent_span_id,
        )
        ctx._tokens.append(_trace_id.set(ctx.trace_id))
        ctx._tokens.append(_span_id.set(ctx.span_id))
        if session_id:
            ctx._tokens.append(_session_id.set(session_id))
        if user_id:
            ctx._tokens.append(_user_id.set(user_id))
        return ctx

    def child_span(self) -> "TraceContext":
        """Create a child span inheriting this trace_id."""
        return TraceContext(
            trace_id=self.trace_id,
            parent_span_id=self.span_id,
        )

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 2)

    def end(self) -> None:
        """Reset contextvars to their prior values."""
        for token in reversed(self._tokens):
            try:
                token.var.reset(token)
            except (ValueError, LookupError):
                pass
        self._tokens.clear()


def current_trace_id() -> str:
    return _trace_id.get()


def current_span_id() -> str:
    return _span_id.get()


def current_session_id() -> str:
    return _session_id.get()


# -- Structured JSON entry builder --------------------------------------------

def _json_entry(
    level: str,
    event_type: str,
    source: str,
    action: str,
    *,
    user_context: str = "",
    duration_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
    stack: str | None = None,
) -> str:
    """Build a structured JSON log line with full correlation context."""
    entry: dict[str, Any] = {
        "ts": time.time(),
        "level": level,
        "event_type": event_type,
        "source": source,
        "action": action,
        "pid": os.getpid(),
    }
    # Correlation IDs (Dynatrace-style distributed trace linking)
    tid = _trace_id.get()
    if tid:
        entry["trace_id"] = tid
    sid = _span_id.get()
    if sid:
        entry["span_id"] = sid
    sess = _session_id.get()
    if sess:
        entry["session_id"] = sess
    uid = _user_id.get()
    if uid:
        entry["user_id"] = uid
    if user_context:
        entry["user_context"] = user_context
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if metadata:
        entry["meta"] = metadata
    if stack:
        entry["stack"] = stack
    return json.dumps(entry, ensure_ascii=True, default=str)


# -- @trace decorator (Dynatrace method-level instrumentation) ----------------

def trace(func: _F) -> _F:
    """Decorator: logs function entry/exit/duration at TRACE level.

    Captures:
    - Function module + qualified name (source)
    - Entry timestamp and correlation IDs
    - Exit with duration_ms
    - Exception with full stack trace and error classification
    """
    source = f"{func.__module__}.{func.__qualname__}"
    logger = logging.getLogger("testing_toolkit.trace")

    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            call_id = uuid.uuid4().hex[:8]
            parent = _span_id.get()
            token = _span_id.set(call_id)
            logger.log(TRACE, _json_entry(
                "TRACE", "fn_enter", source, func.__name__,
                metadata={"call_id": call_id, "parent_span": parent},
            ))
            t0 = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                elapsed = round((time.perf_counter() - t0) * 1000, 2)
                logger.log(TRACE, _json_entry(
                    "TRACE", "fn_exit", source, func.__name__,
                    duration_ms=elapsed,
                    metadata={"call_id": call_id},
                ))
                return result
            except Exception as exc:
                elapsed = round((time.perf_counter() - t0) * 1000, 2)
                logger.log(TRACE, _json_entry(
                    "TRACE", "fn_error", source, func.__name__,
                    duration_ms=elapsed,
                    metadata={
                        "call_id": call_id,
                        "error_type": type(exc).__name__,
                        "error_msg": str(exc)[:500],
                    },
                    stack=traceback.format_exc(limit=10),
                ))
                raise
            finally:
                _span_id.reset(token)
        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        call_id = uuid.uuid4().hex[:8]
        parent = _span_id.get()
        token = _span_id.set(call_id)
        logger.log(TRACE, _json_entry(
            "TRACE", "fn_enter", source, func.__name__,
            metadata={"call_id": call_id, "parent_span": parent},
        ))
        t0 = time.perf_counter()
        try:
            result = func(*args, **kwargs)
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.log(TRACE, _json_entry(
                "TRACE", "fn_exit", source, func.__name__,
                duration_ms=elapsed,
                metadata={"call_id": call_id},
            ))
            return result
        except Exception as exc:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            logger.log(TRACE, _json_entry(
                "TRACE", "fn_error", source, func.__name__,
                duration_ms=elapsed,
                metadata={
                    "call_id": call_id,
                    "error_type": type(exc).__name__,
                    "error_msg": str(exc)[:500],
                },
                stack=traceback.format_exc(limit=10),
            ))
            raise
        finally:
            _span_id.reset(token)
    return wrapper  # type: ignore[return-value]


# -- Dependency call tracking (HTTP, file, external) --------------------------

def trace_dependency(
    dep_type: str,
    target: str,
    action: str,
    *,
    duration_ms: float,
    success: bool = True,
    status_code: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an outbound dependency call (HTTP, file read, DB, etc.).

    Mirrors Dynatrace's "service call" concept: every external dependency
    is tracked with timing, success/failure, and linked to the current trace.
    """
    logger = logging.getLogger("testing_toolkit.trace")
    meta: dict[str, Any] = {"dep_type": dep_type, "target": target, "success": success}
    if status_code is not None:
        meta["status_code"] = status_code
    if metadata:
        meta.update(metadata)
    logger.log(TRACE, _json_entry(
        "TRACE", "dependency", f"dep.{dep_type}", action,
        duration_ms=duration_ms,
        metadata=meta,
    ))


def trace_custom_event(
    event_name: str,
    source: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a custom application event (state change, feature flag, config load)."""
    logger = logging.getLogger("testing_toolkit.trace")
    logger.log(TRACE, _json_entry(
        "TRACE", "custom_event", source, event_name,
        metadata=metadata,
    ))


def trace_user_action(
    action: str,
    source: str,
    *,
    user_context: str = "",
    duration_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a user-initiated action (button click, navigation, form submit)."""
    logger = logging.getLogger("testing_toolkit.trace")
    logger.log(TRACE, _json_entry(
        "TRACE", "user_action", source, action,
        user_context=user_context,
        duration_ms=duration_ms,
        metadata=metadata,
    ))


def trace_state_change(
    field: str,
    old_value: Any,
    new_value: Any,
    source: str,
) -> None:
    """Log a state mutation (Dynatrace session property change equivalent)."""
    logger = logging.getLogger("testing_toolkit.trace")
    logger.log(TRACE, _json_entry(
        "TRACE", "state_change", source, f"set:{field}",
        metadata={"old": str(old_value)[:200], "new": str(new_value)[:200]},
    ))
