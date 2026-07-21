"""
automation/page_observer.py
Smart page observation layer for autonomous E2E execution.

After every action, captures and analyzes the current page state:
- Accessibility tree snapshot (what's visible)
- URL and navigation state
- Error/warning indicators (toasts, alerts, validation messages)
- Loading state (spinners, skeleton screens)
- Changes from previous observation (what shifted)

The observations feed into:
1. The reasoning layer (business rule validation)
2. The navigation layer (am I on the expected screen?)
3. The artifact layer (AI-refined observations for the PDF report)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

LogFn = Callable[[str], None]

# Signals that indicate the page has an error state
_ERROR_SIGNALS: frozenset[str] = frozenset({
    "error", "failed", "failure", "exception", "invalid",
    "not found", "404", "500", "403", "unauthorized",
    "something went wrong", "oops", "try again",
})

# Signals that indicate the page is still loading
_LOADING_SIGNALS: frozenset[str] = frozenset({
    "loading", "please wait", "spinner", "skeleton",
    "progressbar", "fetching",
})

# Signals that indicate a success state
_SUCCESS_SIGNALS: frozenset[str] = frozenset({
    "success", "saved", "created", "updated", "submitted",
    "complete", "done", "confirmed",
})


@dataclass(slots=True)
class PageObservation:
    """A snapshot of the current page state after an action."""

    url: str = ""
    title: str = ""
    timestamp: float = 0.0
    # Accessibility tree summary (truncated)
    a11y_summary: str = ""
    # Detected state signals
    has_error: bool = False
    has_loading: bool = False
    has_success: bool = False
    error_text: str = ""
    success_text: str = ""
    # Visible text elements (key UI labels)
    visible_headings: list[str] = field(default_factory=list)
    visible_buttons: list[str] = field(default_factory=list)
    visible_inputs: list[str] = field(default_factory=list)
    # Navigation state
    is_same_page: bool = False
    url_changed: bool = False
    # Confidence that the action succeeded (0.0-1.0)
    confidence: float = 0.5
    # Human-readable observation summary
    summary: str = ""


@dataclass(slots=True)
class ObservationDelta:
    """What changed between two observations."""

    url_changed: bool = False
    new_headings: list[str] = field(default_factory=list)
    disappeared_headings: list[str] = field(default_factory=list)
    new_errors: bool = False
    errors_cleared: bool = False
    new_success: bool = False
    summary: str = ""


class PageObserver:
    """Captures and analyzes page state for autonomous decision-making.

    Usage:
        observer = PageObserver()
        obs = await observer.observe(page)
        # ... perform action ...
        obs2 = await observer.observe(page)
        delta = observer.compare(obs, obs2)
    """

    def __init__(self, *, on_log: LogFn | None = None) -> None:
        self._log = on_log or (lambda _: None)
        self._last_observation: PageObservation | None = None

    async def observe(self, page: Any) -> PageObservation:
        """Capture current page state as a PageObservation."""
        obs = PageObservation(timestamp=time.time())

        try:
            obs.url = page.url or ""
            obs.title = await page.title() or ""
        except Exception:
            pass

        # Capture accessibility snapshot
        try:
            snapshot = await page.accessibility.snapshot()
            obs.a11y_summary = _summarize_a11y(snapshot)
            _extract_elements(snapshot, obs)
        except Exception:
            pass

        # Detect page state signals from visible text
        all_text = f"{obs.title} {obs.a11y_summary}".lower()
        obs.has_error = any(sig in all_text for sig in _ERROR_SIGNALS)
        obs.has_loading = any(sig in all_text for sig in _LOADING_SIGNALS)
        obs.has_success = any(sig in all_text for sig in _SUCCESS_SIGNALS)

        if obs.has_error:
            obs.error_text = _extract_signal_text(all_text, _ERROR_SIGNALS)
        if obs.has_success:
            obs.success_text = _extract_signal_text(all_text, _SUCCESS_SIGNALS)

        # Compare with last observation
        if self._last_observation:
            obs.url_changed = obs.url != self._last_observation.url
            obs.is_same_page = not obs.url_changed

        # Compute confidence
        obs.confidence = _compute_confidence(obs)

        # Generate summary
        obs.summary = _generate_summary(obs)

        self._last_observation = obs
        return obs

    def compare(
        self, before: PageObservation, after: PageObservation,
    ) -> ObservationDelta:
        """Compute what changed between two observations."""
        delta = ObservationDelta()
        delta.url_changed = before.url != after.url
        before_headings = frozenset(before.visible_headings)
        after_headings = frozenset(after.visible_headings)
        delta.new_headings = sorted(after_headings - before_headings)
        delta.disappeared_headings = sorted(before_headings - after_headings)
        delta.new_errors = after.has_error and not before.has_error
        delta.errors_cleared = before.has_error and not after.has_error
        delta.new_success = after.has_success and not before.has_success

        parts: list[str] = []
        if delta.url_changed:
            parts.append(f"Navigated: {before.url} -> {after.url}")
        if delta.new_headings:
            parts.append(f"New content: {', '.join(delta.new_headings[:3])}")
        if delta.new_errors:
            parts.append(f"Error appeared: {after.error_text[:100]}")
        if delta.errors_cleared:
            parts.append("Previous error cleared")
        if delta.new_success:
            parts.append(f"Success: {after.success_text[:100]}")
        delta.summary = "; ".join(parts) if parts else "No significant change"
        return delta

    @property
    def last(self) -> PageObservation | None:
        return self._last_observation


def _summarize_a11y(snapshot: dict[str, Any] | None, max_chars: int = 2000) -> str:
    """Flatten accessibility tree into a compact text summary."""
    if not snapshot:
        return ""
    parts: list[str] = []
    _walk_a11y(snapshot, parts, depth=0, max_chars=max_chars)
    return "\n".join(parts)[:max_chars]


def _walk_a11y(
    node: dict[str, Any], parts: list[str], depth: int, max_chars: int,
) -> None:
    """Recursively walk the a11y tree, collecting role:name pairs."""
    if sum(len(p) for p in parts) > max_chars:
        return
    role = node.get("role", "")
    name = node.get("name", "")
    if role and name:
        indent = "  " * min(depth, 4)
        parts.append(f"{indent}{role}: {name}")
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            _walk_a11y(child, parts, depth + 1, max_chars)


def _extract_elements(snapshot: dict[str, Any] | None, obs: PageObservation) -> None:
    """Extract headings, buttons, and inputs from the a11y tree."""
    if not snapshot:
        return
    _collect_elements(snapshot, obs)


def _collect_elements(node: dict[str, Any], obs: PageObservation) -> None:
    """Walk tree collecting element names by role."""
    role = (node.get("role") or "").lower()
    name = node.get("name") or ""
    if name:
        if role == "heading":
            obs.visible_headings.append(name)
        elif role == "button":
            obs.visible_buttons.append(name)
        elif role in ("textbox", "combobox", "searchbox", "spinbutton"):
            obs.visible_inputs.append(name)
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            _collect_elements(child, obs)


def _extract_signal_text(text: str, signals: frozenset[str]) -> str:
    """Extract the sentence containing the first signal match."""
    sentences = re.split(r"[.!?\n]", text)
    for sentence in sentences:
        if any(sig in sentence for sig in signals):
            return sentence.strip()[:200]
    return ""


def _compute_confidence(obs: PageObservation) -> float:
    """Heuristic confidence that the page is in a valid state."""
    score = 0.5
    if obs.has_error:
        score -= 0.3
    if obs.has_success:
        score += 0.3
    if obs.has_loading:
        score -= 0.1
    if obs.visible_headings:
        score += 0.1
    return max(0.0, min(1.0, score))


def _generate_summary(obs: PageObservation) -> str:
    """Generate a one-line human-readable summary."""
    parts: list[str] = []
    if obs.title:
        parts.append(f"Page: {obs.title}")
    if obs.has_error:
        parts.append(f"ERROR: {obs.error_text[:80]}")
    elif obs.has_success:
        parts.append(f"OK: {obs.success_text[:80]}")
    elif obs.has_loading:
        parts.append("Loading...")
    if obs.visible_headings:
        parts.append(f"Headings: {', '.join(obs.visible_headings[:3])}")
    return " | ".join(parts) if parts else f"URL: {obs.url}"
