"""
defects/review_excel.py
Generate a review Excel from parsed defects (with embedded images) and
read back the reviewed version for ADO upload.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.drawing.image import Image as XlImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from defects.parser import DefectImage, ParsedDefect

_HEADERS = [
    "Parent WI ID", "Title", "Description", "Repro Steps",
    "Severity", "Expected Result", "Actual Result", "Skip", "Images",
]
_SEVERITY_VALUES = ("Critical", "High", "Medium", "Low")
_HEADER_FILL = PatternFill("solid", fgColor="2B579A")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)


def defects_to_xlsx(
    defects: list[ParsedDefect], out_path: Path | str,
) -> int:
    """Write defects to a review Excel file. Returns the number of rows."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Defects Review"

    # Header row.
    for col, header in enumerate(_HEADERS, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Column widths.
    widths = [12, 40, 50, 50, 12, 35, 35, 6, 15]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # Data rows.
    for row_idx, defect in enumerate(defects, 2):
        ws.cell(row=row_idx, column=1, value=defect.parent_id or "")
        ws.cell(row=row_idx, column=2, value=defect.title)
        ws.cell(row=row_idx, column=3, value=defect.description)
        ws.cell(row=row_idx, column=4, value=defect.repro_steps)
        ws.cell(row=row_idx, column=5, value=defect.severity or "Medium")
        ws.cell(row=row_idx, column=6, value=defect.expected_result)
        ws.cell(row=row_idx, column=7, value=defect.actual_result)
        ws.cell(row=row_idx, column=8, value="No")

        # Embed first image (if any) in the Images column.
        if defect.images:
            try:
                img_data = base64.b64decode(defect.images[0].data_b64)
                img_stream = io.BytesIO(img_data)
                img = XlImage(img_stream)
                img.width = 100
                img.height = 75
                anchor = f"{get_column_letter(9)}{row_idx}"
                ws.add_image(img, anchor)
                ws.row_dimensions[row_idx].height = 60
            except Exception:
                ws.cell(row=row_idx, column=9,
                        value=f"({len(defect.images)} image(s))")
        else:
            ws.cell(row=row_idx, column=9, value="")

        # Wrap text for long fields.
        for col in (3, 4, 6, 7):
            ws.cell(row=row_idx, column=col).alignment = Alignment(
                wrap_text=True, vertical="top"
            )

    wb.save(str(out_path))
    return len(defects)


@dataclass(slots=True)
class ReviewedDefect:
    parent_id: int
    title: str
    description: str
    repro_steps: str
    severity: str
    expected_result: str
    actual_result: str
    skip: bool = False


def xlsx_to_defects(path: Path | str) -> tuple[list[ReviewedDefect], list[str]]:
    """Read back a reviewed defect Excel. Returns (defects, warnings).
    Uses header-based column mapping so reordered columns still work."""
    path = Path(path)
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    except Exception as e:
        raise ValueError(
            f"Cannot open review Excel '{path.name}': {e!r}"
        ) from e
    ws = wb.active
    warnings: list[str] = []
    defects: list[ReviewedDefect] = []

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        wb.close()
        return [], []

    # Build header->index map from row 1.
    _HEADER_MAP = {
        "parent wi id": "parent_id", "parent id": "parent_id",
        "parent": "parent_id",
        "title": "title", "defect title": "title",
        "description": "description",
        "repro steps": "repro_steps", "steps to reproduce": "repro_steps",
        "severity": "severity", "priority": "severity",
        "expected result": "expected_result", "expected": "expected_result",
        "actual result": "actual_result", "actual": "actual_result",
        "skip": "skip",
    }
    header_row = rows[0]
    col_map: dict[str, int] = {}
    for idx, cell_val in enumerate(header_row):
        key = str(cell_val or "").strip().lower()
        field = _HEADER_MAP.get(key)
        if field and field not in col_map:
            col_map[field] = idx

    def _cell(row: tuple, field: str) -> str:
        idx = col_map.get(field)
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx] or "").strip()

    for i, row in enumerate(rows[1:], 2):
        title = _cell(row, "title")
        if not title:
            continue
        skip_val = _cell(row, "skip").lower()
        if skip_val in ("yes", "y", "true", "1", "skip"):
            continue

        parent_raw = _cell(row, "parent_id")
        parent_id = 0
        try:
            parent_id = int(float(parent_raw)) if parent_raw else 0
        except (ValueError, TypeError):
            warnings.append(f"Row {i}: invalid parent ID '{parent_raw}'")

        severity = _cell(row, "severity") or "Medium"
        if severity not in _SEVERITY_VALUES:
            warnings.append(f"Row {i}: severity '{severity}' not standard; "
                            f"using as-is.")

        defects.append(ReviewedDefect(
            parent_id=parent_id,
            title=title,
            description=_cell(row, "description"),
            repro_steps=_cell(row, "repro_steps"),
            severity=severity,
            expected_result=_cell(row, "expected_result"),
            actual_result=_cell(row, "actual_result"),
        ))

    wb.close()
    return defects, warnings
