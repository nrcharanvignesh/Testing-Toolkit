"""Diff engine for bulk test case regeneration workflows.

Compares old vs new generated test case payloads and produces structured diffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TcDiff:
    tc_id: str
    tc_title: str
    change_type: str  # "added" | "removed" | "modified" | "unchanged"
    old_step_count: int
    new_step_count: int
    modified_fields: list[str] = field(default_factory=list)


@dataclass
class PayloadDiff:
    diffs: list[TcDiff] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    modified: int = 0
    unchanged: int = 0


def _extract_tc_map(payload: dict) -> dict[str, dict]:
    """Flatten payload stories into {tc_id: tc_dict}."""
    out: dict[str, dict] = {}
    for story in payload.get("stories", []):
        for tc in story.get("test_cases", []):
            tc_id = tc.get("id", "")
            if tc_id:
                out[tc_id] = tc
    return out


_COMPARE_FIELDS: tuple[str, ...] = ("title", "steps", "category", "priority")


def _compare_tc(old_tc: dict, new_tc: dict) -> TcDiff:
    """Compare two test cases with the same ID."""
    modified_fields: list[str] = []
    for f in _COMPARE_FIELDS:
        if old_tc.get(f) != new_tc.get(f):
            modified_fields.append(f)

    old_steps = old_tc.get("steps", [])
    new_steps = new_tc.get("steps", [])
    change_type = "modified" if modified_fields else "unchanged"

    return TcDiff(
        tc_id=old_tc.get("id", ""),
        tc_title=new_tc.get("title", old_tc.get("title", "")),
        change_type=change_type,
        old_step_count=len(old_steps),
        new_step_count=len(new_steps),
        modified_fields=modified_fields,
    )


def diff_payloads(old_payload: dict, new_payload: dict) -> PayloadDiff:
    """Compare two payloads and return structured diff."""
    old_map = _extract_tc_map(old_payload)
    new_map = _extract_tc_map(new_payload)

    old_ids = frozenset(old_map)
    new_ids = frozenset(new_map)

    result = PayloadDiff()

    # Added
    for tc_id in sorted(new_ids - old_ids):
        tc = new_map[tc_id]
        result.diffs.append(TcDiff(
            tc_id=tc_id,
            tc_title=tc.get("title", ""),
            change_type="added",
            old_step_count=0,
            new_step_count=len(tc.get("steps", [])),
        ))
        result.added += 1

    # Removed
    for tc_id in sorted(old_ids - new_ids):
        tc = old_map[tc_id]
        result.diffs.append(TcDiff(
            tc_id=tc_id,
            tc_title=tc.get("title", ""),
            change_type="removed",
            old_step_count=len(tc.get("steps", [])),
            new_step_count=0,
        ))
        result.removed += 1

    # Common - compare
    for tc_id in sorted(old_ids & new_ids):
        diff = _compare_tc(old_map[tc_id], new_map[tc_id])
        result.diffs.append(diff)
        if diff.change_type == "modified":
            result.modified += 1
        else:
            result.unchanged += 1

    return result
