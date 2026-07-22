"""
automation/sub_agents.py
Data structures and orchestration logic for the multi-agent E2E architecture.

Agent Roles:
1. Planner (pre-TC) - strategizes navigation and identifies risks
2. Executor (main loop) - drives the browser with CoT capture
3. KB Consultant (on-demand) - advises when executor is stuck
4. Sign-Out Agent (post-TC) - attempts sign-out after test completion
5. Report Synthesizer (post-suite) - produces human-readable narrative
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ThoughtRecord:
    """Chain-of-thought record for a single executor turn."""

    step_num: int
    reasoning_text: str
    tool_chosen: str
    tool_input_summary: str
    timestamp: float = field(default_factory=time.time)
    is_escalation_response: bool = False


@dataclass(slots=True)
class TestStrategy:
    """Output from the Planner Sub-Agent."""

    approach: str
    navigation_hints: list[str]
    risk_areas: list[str]
    precondition_checks: list[str]
    key_assertions: list[str]
    estimated_complexity: str  # "simple" | "moderate" | "complex"


@dataclass(slots=True)
class EscalationContext:
    """Context sent to the KB Consultant when the executor is stuck."""

    current_goal: str
    test_step_text: str
    failures: list[str]
    page_state_summary: str
    actions_tried: list[str]
    tc_title: str
    tc_id: str


@dataclass(slots=True)
class ConsultantAdvice:
    """Response from the KB Consultant Sub-Agent."""

    advice_text: str
    confidence: str  # "high" | "medium" | "low"
    kb_chunks_used: int


@dataclass(slots=True)
class NarrativeSection:
    """Human-readable narrative for one test case in the report."""

    tc_id: str
    tc_title: str
    summary: str
    approach_taken: str
    key_findings: list[str]
    challenges_encountered: list[str]
    verdict_reasoning: str


@dataclass(slots=True)
class NarrativeReport:
    """Full synthesized report output from Report Synthesizer."""

    executive_summary: str
    patterns_observed: list[str]
    recommendations: list[str]
    tc_narratives: list[NarrativeSection]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_test_strategy(raw: str) -> TestStrategy | None:
    """Parse planner JSON output into TestStrategy. Returns None on failure."""
    try:
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        data = json.loads(text)
        return TestStrategy(
            approach=data.get("approach", ""),
            navigation_hints=data.get("navigation_hints", []),
            risk_areas=data.get("risk_areas", []),
            precondition_checks=data.get("precondition_checks", []),
            key_assertions=data.get("key_assertions", []),
            estimated_complexity=data.get("estimated_complexity", "moderate"),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def parse_narrative_report(raw: str) -> NarrativeReport | None:
    """Parse report synthesizer JSON output. Returns None on failure."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        data = json.loads(text)
        narratives: list[NarrativeSection] = []
        for tc in data.get("tc_narratives", []):
            narratives.append(NarrativeSection(
                tc_id=tc.get("tc_id", ""),
                tc_title=tc.get("tc_title", ""),
                summary=tc.get("summary", ""),
                approach_taken=tc.get("approach_taken", ""),
                key_findings=tc.get("key_findings", []),
                challenges_encountered=tc.get("challenges_encountered", []),
                verdict_reasoning=tc.get("verdict_reasoning", ""),
            ))
        return NarrativeReport(
            executive_summary=data.get("executive_summary", ""),
            patterns_observed=data.get("patterns_observed", []),
            recommendations=data.get("recommendations", []),
            tc_narratives=narratives,
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def summarize_tool_input(tool_input: dict[str, Any]) -> str:
    """Produce a safe one-line summary of tool input (no credentials)."""
    sensitive = frozenset({"password", "passwd", "pwd", "secret", "token"})
    parts: list[str] = []
    for k, v in tool_input.items():
        if k.lower() in sensitive:
            continue
        val = str(v)[:80]
        parts.append(f"{k}={val}")
    return ", ".join(parts)[:150]
