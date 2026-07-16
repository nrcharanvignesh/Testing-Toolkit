"""
jira/adf.py
Atlassian Document Format (ADF) helpers for JIRA Cloud/Server.

Provides safe extraction of plain text from ADF nodes (rich description
fields) and append-only mutation of ADF documents for bug logging.

ADF spec: https://developer.atlassian.com/cloud/jira/platform/apis/
          document/structure/

Design: append-only -- never overwrite existing description content.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def extract_text(node: dict[str, Any] | None) -> str:
    """Recursively extract plain text from an ADF node tree.

    Handles nested doc -> paragraph -> text structure. Paragraphs are
    joined with newlines; inline text nodes are concatenated.
    Returns empty string for None/malformed input.
    """
    if not node or not isinstance(node, dict):
        return ""
    node_type = node.get("type", "")
    if node_type == "text":
        return node.get("text", "")
    children = node.get("content")
    if not isinstance(children, list):
        return ""
    separator = "\n" if node_type in ("doc", "paragraph", "bulletList",
                                       "orderedList", "listItem") else ""
    parts: list[str] = []
    for child in children:
        if isinstance(child, dict):
            text = extract_text(child)
            if text:
                parts.append(text)
    return separator.join(parts)


def build_paragraph(text: str) -> dict[str, Any]:
    """Build a single ADF paragraph node from plain text."""
    return {
        "type": "paragraph",
        "content": [{"type": "text", "text": text}],
    }


def append_to_description(
    existing: dict[str, Any] | None,
    text: str,
) -> dict[str, Any]:
    """Append a paragraph to an existing ADF description. Never overwrites.

    If existing is None or malformed, creates a new doc with the paragraph.
    """
    if not existing or not isinstance(existing, dict):
        existing = {"type": "doc", "version": 1, "content": []}
    content = existing.get("content")
    if not isinstance(content, list):
        content = []
    new_doc = {
        **existing,
        "content": [*content, build_paragraph(text)],
    }
    return new_doc


def build_bug_comment(
    test_case_id: str,
    step_num: int,
    error_message: str,
    *,
    healed: bool = False,
) -> str:
    """Format a structured bug/healing comment for Jira description append.

    Returns plain text suitable for append_to_description().
    """
    status = "[HEALED]" if healed else "[FAILED]"
    return (
        f"{status} Test: {test_case_id} | Step {step_num}\n"
        f"Error: {error_message}"
    )
