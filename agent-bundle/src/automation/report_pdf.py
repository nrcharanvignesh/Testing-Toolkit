"""
automation/report_pdf.py
Per-WI PDF report for E2E execution results (v3.70.0).

Enhanced with:
- Executive summary from Report Synthesizer sub-agent
- Per-TC narrative sections (human-readable)
- AI reasoning column in step table
- Patterns and recommendations section

Uses reportlab (already in requirements). ASCII-only output.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


@dataclass(slots=True)
class E2EReportData:
    """Input data for generating an E2E PDF report."""

    wi_id: str
    wi_title: str
    environment: str
    started_at: float
    finished_at: float
    results: list[Any]  # list of TestCaseResult
    observations: list[str] = None  # type: ignore[assignment]
    video_path: str = ""
    narrative: Any | None = None  # NarrativeReport from synthesizer

    def __post_init__(self) -> None:
        if self.observations is None:
            self.observations = []


_CELL_STYLE = ParagraphStyle(
    "CellWrap", fontSize=8, leading=10, wordWrap="CJK",
)

_REASONING_STYLE = ParagraphStyle(
    "ReasoningWrap", fontSize=7, leading=9, wordWrap="CJK",
    textColor=colors.Color(0.3, 0.3, 0.5),
)

_NARRATIVE_STYLE = ParagraphStyle(
    "NarrativeBody", fontSize=9, leading=12, spaceAfter=6,
    textColor=colors.Color(0.2, 0.2, 0.2),
)

_STATUS_COLORS: dict[str, colors.Color] = {
    "pass": colors.Color(0.2, 0.7, 0.2),
    "pass_fallback": colors.Color(0.6, 0.8, 0.2),
    "fail": colors.Color(0.8, 0.2, 0.2),
    "error": colors.Color(0.7, 0.0, 0.0),
    "skip": colors.Color(0.5, 0.5, 0.5),
    "blocked": colors.Color(0.6, 0.4, 0.0),
}


def generate_e2e_pdf(data: E2EReportData, output_path: Path) -> Path:
    """Generate a PDF report for one work item's E2E execution.

    Returns the output path on success.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    story: list[Any] = []

    # -- Header --
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Heading1"], fontSize=16, spaceAfter=12,
    )
    story.append(Paragraph(f"E2E Test Report - WI {data.wi_id}", title_style))
    story.append(Paragraph(f"<b>Title:</b> {_safe(data.wi_title)}", styles["Normal"]))
    story.append(Paragraph(f"<b>Environment:</b> {_safe(data.environment)}", styles["Normal"]))
    story.append(Paragraph(
        f"<b>Executed:</b> {_format_time(data.started_at)} - {_format_time(data.finished_at)}",
        styles["Normal"],
    ))
    duration_s = data.finished_at - data.started_at
    story.append(Paragraph(f"<b>Duration:</b> {duration_s:.1f}s", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    # -- Executive Summary (from Report Synthesizer) --
    narrative = data.narrative
    if narrative and hasattr(narrative, "executive_summary") and narrative.executive_summary:
        story.append(Paragraph("Executive Summary", styles["Heading2"]))
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(_safe(narrative.executive_summary), _NARRATIVE_STYLE))
        story.append(Spacer(1, 0.4 * cm))

    # -- Summary Table --
    total = len(data.results)
    passed = sum(1 for r in data.results if getattr(r, "overall_status", "") == "pass")
    failed = sum(1 for r in data.results if getattr(r, "overall_status", "") in ("fail", "error"))
    skipped = total - passed - failed
    summary_data = [
        ["Total TCs", "Passed", "Failed", "Skipped"],
        [str(total), str(passed), str(failed), str(skipped)],
    ]
    summary_table = Table(summary_data, colWidths=[3 * cm] * 4)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.5)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.8 * cm))

    # -- Patterns & Recommendations --
    if narrative:
        if hasattr(narrative, "patterns_observed") and narrative.patterns_observed:
            story.append(Paragraph("Patterns Observed", styles["Heading2"]))
            story.append(Spacer(1, 0.2 * cm))
            for pattern in narrative.patterns_observed:
                story.append(Paragraph(f"- {_safe(pattern)}", _NARRATIVE_STYLE))
            story.append(Spacer(1, 0.3 * cm))

        if hasattr(narrative, "recommendations") and narrative.recommendations:
            story.append(Paragraph("Recommendations", styles["Heading2"]))
            story.append(Spacer(1, 0.2 * cm))
            for rec in narrative.recommendations:
                story.append(Paragraph(f"- {_safe(rec)}", _NARRATIVE_STYLE))
            story.append(Spacer(1, 0.5 * cm))

    # -- Test Case Results --
    story.append(Paragraph("Test Case Results", styles["Heading2"]))
    story.append(Spacer(1, 0.3 * cm))

    # Build narrative lookup by tc_id
    tc_narrative_map: dict[str, Any] = {}
    if narrative and hasattr(narrative, "tc_narratives"):
        for n in narrative.tc_narratives:
            if hasattr(n, "tc_id"):
                tc_narrative_map[n.tc_id] = n

    for result in data.results:
        tc_id = getattr(result, "tc_id", "?")
        tc_title = getattr(result, "title", "Untitled")
        status = getattr(result, "overall_status", "?")
        duration_ms = getattr(result, "duration_ms", 0)
        escalation_count = getattr(result, "escalation_count", 0)

        # TC header
        status_color = _STATUS_COLORS.get(status, colors.black)
        header_text = (
            f"<b>{_safe(tc_id)}</b> - {_safe(tc_title)} "
            f"[{status.upper()}] ({duration_ms}ms)"
        )
        if escalation_count:
            header_text += f" | KB consulted {escalation_count}x"
        story.append(Paragraph(header_text, styles["Heading3"]))

        # Per-TC narrative (if available)
        tc_narr = tc_narrative_map.get(tc_id)
        if tc_narr:
            if hasattr(tc_narr, "summary") and tc_narr.summary:
                story.append(Paragraph(_safe(tc_narr.summary), _NARRATIVE_STYLE))
            if hasattr(tc_narr, "verdict_reasoning") and tc_narr.verdict_reasoning:
                story.append(Paragraph(
                    f"<i>Verdict: {_safe(tc_narr.verdict_reasoning)}</i>",
                    _NARRATIVE_STYLE,
                ))
            if hasattr(tc_narr, "challenges_encountered") and tc_narr.challenges_encountered:
                for ch in tc_narr.challenges_encountered:
                    story.append(Paragraph(
                        f"  Challenge: {_safe(ch)}", _NARRATIVE_STYLE,
                    ))
            story.append(Spacer(1, 0.2 * cm))

        # Steps table (with reasoning column)
        steps = getattr(result, "steps", []) or []
        if steps:
            step_data = [["#", "Action", "Expected", "Actual", "Reasoning", "Status"]]
            for step in steps:
                reasoning = getattr(step, "reasoning", "")
                # Truncate reasoning for table cell
                reasoning_short = reasoning[:120] + "..." if len(reasoning) > 120 else reasoning
                step_data.append([
                    str(getattr(step, "step_num", "")),
                    Paragraph(_safe(getattr(step, "action", "")), _CELL_STYLE),
                    Paragraph(_safe(getattr(step, "expected", "")), _CELL_STYLE),
                    Paragraph(_safe(getattr(step, "actual", "")), _CELL_STYLE),
                    Paragraph(_safe(reasoning_short), _REASONING_STYLE),
                    getattr(step, "status", "").upper(),
                ])
            step_table = Table(
                step_data,
                colWidths=[0.8 * cm, 3 * cm, 3.5 * cm, 3.5 * cm, 4.5 * cm, 1.8 * cm],
            )
            step_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.85, 0.85, 0.85)),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.Color(0.7, 0.7, 0.7)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(step_table)
        story.append(Spacer(1, 0.5 * cm))

    # -- AI Observations (legacy section, kept for backward compat) --
    if data.observations:
        story.append(Paragraph("AI Observations", styles["Heading2"]))
        story.append(Spacer(1, 0.3 * cm))
        for obs in data.observations:
            story.append(Paragraph(f"- {_safe(obs)}", styles["Normal"]))
        story.append(Spacer(1, 0.5 * cm))

    # -- Video reference --
    if data.video_path:
        story.append(Paragraph("Artifacts", styles["Heading2"]))
        story.append(Paragraph(
            f"<b>Video:</b> {_safe(data.video_path)}", styles["Normal"],
        ))

    doc.build(story)
    return output_path


def _safe(text: str) -> str:
    """Escape XML special chars for reportlab Paragraph."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _format_time(ts: float) -> str:
    """Format a timestamp as a human-readable string."""
    if not ts:
        return "N/A"
    import datetime
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
