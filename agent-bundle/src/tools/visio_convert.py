"""
visio_convert.py
Read, analyze, and structure Visio diagrams (.vsdx) into text and PDF.

.vsdx files are Office Open XML packages (ZIP archives) containing:
    - visio/pages/pageN.xml   shape geometry, text, and connections
    - visio/pages/pages.xml   page metadata (names, order)
    - visio/masters/*.xml     master shape definitions (stencils)

This module extracts:
    1. All text from shapes (labels, annotations, callouts)
    2. Shape connectivity (which shapes connect to which)
    3. Page structure (page names, ordering)

Public API:
    extract_visio_text(path) -> str
        Structured plain-text extraction for knowledge-base indexing.

    convert_visio_to_pdf(src, out_pdf, paper="A4") -> None
        Render a structured text representation to PDF via reportlab.
"""

from __future__ import annotations

import gc
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from xml.etree import ElementTree as ET

# Visio Open XML namespaces
_NS: Final[dict[str, str]] = {
    "v": "http://schemas.microsoft.com/office/visio/2012/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


@dataclass(slots=True)
class VisioShape:
    shape_id: str
    name: str
    text: str
    master_name: str = ""
    connects_to: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VisioPage:
    name: str
    shapes: list[VisioShape] = field(default_factory=list)
    connections: list[tuple[str, str]] = field(default_factory=list)


def _clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    return _WHITESPACE_RE.sub(" ", raw).strip()


def _collect_text(element: ET.Element) -> str:
    """Recursively collect all text content from a Visio shape element."""
    parts: list[str] = []
    for text_elem in element.iter(f"{{{_NS['v']}}}Text"):
        if text_elem.text:
            parts.append(text_elem.text)
        for child in text_elem:
            if child.text:
                parts.append(child.text)
            if child.tail:
                parts.append(child.tail)
    return _clean_text(" ".join(parts))


def _parse_shapes(page_root: ET.Element) -> list[VisioShape]:
    """Extract all shapes with text from a page XML root."""
    shapes: list[VisioShape] = []
    for shape_el in page_root.iter(f"{{{_NS['v']}}}Shape"):
        shape_id = shape_el.get("ID", "")
        name = shape_el.get("Name", "") or shape_el.get("NameU", "") or ""
        master = shape_el.get("Master", "")
        text = _collect_text(shape_el)
        if text or name:
            shapes.append(VisioShape(
                shape_id=shape_id,
                name=name,
                text=text,
                master_name=master,
            ))
    return shapes


def _parse_connections(page_root: ET.Element) -> list[tuple[str, str]]:
    """Extract connector relationships (from_shape_id -> to_shape_id)."""
    connections: list[tuple[str, str]] = []
    connects_section = page_root.find(f".//{{{_NS['v']}}}Connects")
    if connects_section is None:
        return connections
    connect_pairs: dict[str, dict[str, str]] = {}
    for connect in connects_section.findall(f"{{{_NS['v']}}}Connect"):
        from_sheet = connect.get("FromSheet", "")
        to_sheet = connect.get("ToSheet", "")
        from_cell = connect.get("FromCell", "")
        if not from_sheet or not to_sheet:
            continue
        if from_sheet not in connect_pairs:
            connect_pairs[from_sheet] = {}
        if from_cell == "BeginX":
            connect_pairs[from_sheet]["begin"] = to_sheet
        elif from_cell == "EndX":
            connect_pairs[from_sheet]["end"] = to_sheet
    for connector_id, endpoints in connect_pairs.items():
        begin = endpoints.get("begin", "")
        end = endpoints.get("end", "")
        if begin and end:
            connections.append((begin, end))
    return connections


def _get_page_names(pages_xml: bytes) -> dict[int, str]:
    """Parse pages.xml to get page index -> name mapping."""
    root = ET.fromstring(pages_xml)
    names: dict[int, str] = {}
    for i, page_el in enumerate(root.findall(f".//{{{_NS['v']}}}Page")):
        name = page_el.get("Name", "") or page_el.get("NameU", "") or f"Page {i + 1}"
        names[i] = name
    return names


def _read_vsdx(path: Path) -> list[VisioPage]:
    """Open a .vsdx and parse all pages into structured data."""
    pages: list[VisioPage] = []
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            namelist = zf.namelist()
            page_names: dict[int, str] = {}
            if "visio/pages/pages.xml" in namelist:
                page_names = _get_page_names(zf.read("visio/pages/pages.xml"))
            page_files = sorted(
                f for f in namelist
                if re.match(r"visio/pages/page\d+\.xml$", f, re.IGNORECASE)
            )
            for idx, page_file in enumerate(page_files):
                page_xml = zf.read(page_file)
                root = ET.fromstring(page_xml)
                shapes = _parse_shapes(root)
                connections = _parse_connections(root)
                page_name = page_names.get(idx, f"Page {idx + 1}")
                pages.append(VisioPage(
                    name=page_name,
                    shapes=shapes,
                    connections=connections,
                ))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        pass
    return pages


def _build_shape_index(shapes: list[VisioShape]) -> dict[str, str]:
    """Map shape_id -> display label (text or name)."""
    index: dict[str, str] = {}
    for s in shapes:
        label = s.text or s.name or f"Shape {s.shape_id}"
        index[s.shape_id] = label
    return index


def extract_visio_text(path: Path) -> str:
    """Structured plain-text extraction from a .vsdx file.

    Output format:
        # Page: <name>
        ## Shapes
        - <shape text> [<shape name>]
        ## Connections
        - <source label> -> <target label>
    """
    pages = _read_vsdx(path)
    if not pages:
        return ""
    parts: list[str] = []
    for page in pages:
        parts.append(f"# Page: {page.name}")
        if page.shapes:
            parts.append("## Shapes")
            for shape in page.shapes:
                label = shape.text or shape.name
                suffix = f" [{shape.name}]" if shape.name and shape.text else ""
                parts.append(f"- {label}{suffix}")
        if page.connections:
            shape_index = _build_shape_index(page.shapes)
            parts.append("## Connections")
            for from_id, to_id in page.connections:
                from_label = shape_index.get(from_id, f"#{from_id}")
                to_label = shape_index.get(to_id, f"#{to_id}")
                parts.append(f"- {from_label} -> {to_label}")
        parts.append("")
    return "\n".join(parts)


def convert_visio_to_pdf(src: Path, out_pdf: Path, paper: str = "A4") -> None:
    """Render Visio diagram structure to PDF using reportlab.

    Produces a structured document with page headings, shape listings,
    and connection diagrams represented as text flow diagrams.
    """
    from reportlab.lib.pagesizes import A4, LETTER, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    pagesize = LETTER if paper.upper() == "LETTER" else A4
    margin = 2 * cm
    doc = SimpleDocTemplate(
        str(out_pdf), pagesize=pagesize,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin,
        title=src.name,
    )

    base = getSampleStyleSheet()
    sty = {
        "h1": ParagraphStyle(
            "VIS_H1", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=14, leading=18, spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "VIS_H2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, spaceBefore=8, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "VIS_Body", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9, leading=12,
        ),
        "conn": ParagraphStyle(
            "VIS_Conn", parent=base["BodyText"], fontName="Courier",
            fontSize=8, leading=11, leftIndent=12,
        ),
    }

    def _safe_html(text: str) -> str:
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    pages = _read_vsdx(src)
    story: list = [Paragraph(_safe_html(f"Visio Diagram: {src.name}"), sty["h1"])]

    if not pages:
        story.append(Paragraph(
            "(No readable content found in this .vsdx file)", sty["body"]
        ))
    else:
        for page in pages:
            story.append(Paragraph(_safe_html(f"Page: {page.name}"), sty["h2"]))

            if page.shapes:
                story.append(Paragraph("<b>Shapes:</b>", sty["body"]))
                for shape in page.shapes:
                    label = _safe_html(shape.text or shape.name)
                    name_note = (
                        f" <i>[{_safe_html(shape.name)}]</i>"
                        if shape.name and shape.text else ""
                    )
                    story.append(Paragraph(
                        f"&bull; {label}{name_note}", sty["body"]
                    ))

            if page.connections:
                shape_index = _build_shape_index(page.shapes)
                story.append(Spacer(1, 0.2 * cm))
                story.append(Paragraph("<b>Connections:</b>", sty["body"]))
                for from_id, to_id in page.connections:
                    from_label = _safe_html(
                        shape_index.get(from_id, f"#{from_id}")
                    )
                    to_label = _safe_html(
                        shape_index.get(to_id, f"#{to_id}")
                    )
                    story.append(Paragraph(
                        f"{from_label}  &rarr;  {to_label}", sty["conn"]
                    ))

            story.append(Spacer(1, 0.4 * cm))

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.build(story)
    gc.collect()
