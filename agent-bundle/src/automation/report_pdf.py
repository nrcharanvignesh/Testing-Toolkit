"""
automation/report_pdf.py
Per-WI PDF report for E2E execution results.

Generates a professional test execution report containing:
- WI header (ID, title, environment, timestamp)
- Test case results table (step, action, expected, actual, status)
- AI observations summary (from page observer)
- Execution metadata (duration, pass/fail counts, video path)

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

    def __post_init__(self) -> None:
        if self.observations is None:
            self.observations = []


_CELL_STYLE = ParagraphStyle(
    "CellWrap", fontSize=7, leading=9, wordWrap="CJK",
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

    # -- Summary --
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

    # -- Test Case Results --
    story.append(Paragraph("Test Case Results", styles["Heading2"]))
    story.append(Spacer(1, 0.3 * cm))

    for result in data.results:
        tc_id = getattr(result, "tc_id", "?")
        tc_title = getattr(result, "title", "Untitled")
        status = getattr(result, "overall_status", "?")
        duration_ms = getattr(result, "duration_ms", 0)

        # TC header
        status_color = _STATUS_COLORS.get(status, colors.black)
        story.append(Paragraph(
            f"<b>{_safe(tc_id)}</b> - {_safe(tc_title)} "
            f"[{status.upper()}] ({duration_ms}ms)",
            styles["Heading3"],
        ))

        # Steps table
        steps = getattr(result, "steps", []) or []
        if steps:
            step_data = [["#", "Action", "Expected", "Actual", "Status"]]
            for step in steps:
                step_data.append([
                    str(getattr(step, "step_num", "")),
                    Paragraph(_safe(getattr(step, "action", "")), _CELL_STYLE),
                    Paragraph(_safe(getattr(step, "expected", "")), _CELL_STYLE),
                    Paragraph(_safe(getattr(step, "actual", "")), _CELL_STYLE),
                    getattr(step, "status", "").upper(),
                ])
            step_table = Table(
                step_data,
                colWidths=[1 * cm, 4 * cm, 5 * cm, 5.6 * cm, 2.5 * cm],
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

    # -- AI Observations --
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
