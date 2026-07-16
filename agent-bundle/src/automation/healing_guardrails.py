"""
automation/healing_guardrails.py
Self-healing guardrails -- constrains what the recompile/heal loop is allowed to do.
NEVER heal assertion failures, modify test data/expected values, or weaken checks.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_HEAL_ATTEMPTS: int = 3
PERSISTENT_FAILURE_THRESHOLD: int = 3  # same step fails this many runs -> flag
HISTORY_PATH: Path = Path(__file__).resolve().parents[2] / "data" / "healing_history.json"

# Actions whose failure means a real app bug, not a test bug.
_ASSERTION_ACTIONS: frozenset[str] = frozenset({
    "assert_text", "assert_url", "assert_element", "assert_not_present",
})

# Signals that indicate a locator/element resolution problem (healable).
_LOCATOR_FAILURE_SIGNALS: tuple[str, ...] = (
    "not found", "not visible", "no element", "could not find",
    "locator resolved", "strict mode violation",
)

# Signals that indicate an assertion mismatch (app bug, not healable).
_ASSERTION_FAILURE_SIGNALS: tuple[str, ...] = (
    "text not found", "url mismatch", "does not contain",
    "element still present", "did not appear",
)


# ---------------------------------------------------------------------------
# Enum and dataclasses
# ---------------------------------------------------------------------------

class HealingDecision(Enum):
    HEAL_LOCATOR = "heal_locator"
    HEAL_WAIT = "heal_wait"
    REPORT_APP_BUG = "report_app_bug"
    REPORT_TEST_DEBT = "report_test_debt"
    SKIP = "skip"


@dataclass(slots=True)
class HealingRecord:
    step_action: str
    step_target: str
    original_locator: str
    healed_locator: str
    error_message: str
    success: bool
    timestamp: float = field(default_factory=time.time)
    run_id: str = ""


@dataclass(slots=True)
class HealingHistory:
    records: list[HealingRecord] = field(default_factory=list)

    # ------------------------------------------------------------------
    def persistent_failure_count(self, action: str, target: str) -> int:
        """Count distinct failed healing attempts for a specific step."""
        return sum(
            1 for r in self.records
            if r.step_action == action and r.step_target == target and not r.success
        )

    def add(self, record: HealingRecord) -> None:
        self.records.append(record)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_history() -> HealingHistory:
    if not HISTORY_PATH.exists():
        return HealingHistory()
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        records = [
            HealingRecord(**r) for r in data.get("records", [])
            if isinstance(r, dict)
        ]
        return HealingHistory(records=records)
    except (json.JSONDecodeError, OSError, TypeError):
        return HealingHistory()


def _save_history(history: HealingHistory) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"records": [asdict(r) for r in history.records]}
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    tmp.replace(HISTORY_PATH)


# ---------------------------------------------------------------------------
# Core decision logic
# ---------------------------------------------------------------------------

def should_heal(
    step: dict[str, Any],
    error_message: str,
    attempt: int,
) -> HealingDecision:
    """Decide whether self-healing is permitted for a failed step.

    Args:
        step: The step dict (action, target, value, expected, locator).
        error_message: The failure message from the runner.
        attempt: Current attempt number (1-based).

    Returns:
        A HealingDecision indicating what the runner should do.
    """
    action = step.get("action", "").lower().strip()
    error_lower = error_message.lower()

    # Rule 1: Never heal assertion steps with assertion-style failures.
    # An assertion that fails because the element is missing might still be
    # a locator issue (assert_element with a stale selector), but if the
    # element was FOUND and the value mismatched, it's an app bug.
    if action in _ASSERTION_ACTIONS:
        # Distinguish: element not found (test bug) vs assertion mismatch (app bug)
        is_locator_issue = any(sig in error_lower for sig in _LOCATOR_FAILURE_SIGNALS)
        is_assertion_mismatch = any(sig in error_lower for sig in _ASSERTION_FAILURE_SIGNALS)
        if is_assertion_mismatch and not is_locator_issue:
            return HealingDecision.REPORT_APP_BUG
        # assert_element with "not found" -> locator might be stale, allow heal
        if not is_locator_issue:
            return HealingDecision.REPORT_APP_BUG

    # Rule 2: Max attempts exceeded -> flag as test debt
    if attempt > MAX_HEAL_ATTEMPTS:
        return HealingDecision.REPORT_TEST_DEBT

    # Rule 3: Check persistent failure history
    history = _load_history()
    fail_count = history.persistent_failure_count(action, step.get("target", ""))
    if fail_count >= PERSISTENT_FAILURE_THRESHOLD:
        return HealingDecision.REPORT_TEST_DEBT

    # Rule 4: Classify the failure type
    is_locator_failure = any(sig in error_lower for sig in _LOCATOR_FAILURE_SIGNALS)
    is_timeout = "timeout" in error_lower

    if is_locator_failure:
        return HealingDecision.HEAL_LOCATOR
    if is_timeout:
        return HealingDecision.HEAL_WAIT

    # Unknown error type -- don't guess, report it
    return HealingDecision.SKIP


def record_healing(
    step: dict[str, Any],
    original_step: dict[str, Any],
    healed_step: dict[str, Any] | None,
    success: bool,
    run_id: str = "",
) -> None:
    """Persist a healing attempt to history for cross-run analysis.

    Args:
        step: The step that was attempted.
        original_step: The step before healing.
        healed_step: The corrected step (None if healing was not attempted).
        success: Whether the healed step passed.
        run_id: Optional identifier for the current test run.
    """
    record = HealingRecord(
        step_action=step.get("action", ""),
        step_target=step.get("target", ""),
        original_locator=f"{original_step.get('locator', '')}:{original_step.get('target', '')}",
        healed_locator=(
            f"{healed_step.get('locator', '')}:{healed_step.get('target', '')}"
            if healed_step else ""
        ),
        error_message=step.get("_last_error", "")[:200],
        success=success,
        run_id=run_id,
    )
    history = _load_history()
    history.add(record)
    _save_history(history)
