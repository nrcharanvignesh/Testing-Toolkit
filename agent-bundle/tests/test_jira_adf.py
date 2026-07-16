"""Tests for jira.adf -- Atlassian Document Format helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jira.adf import (
    append_to_description,
    build_bug_comment,
    build_paragraph,
    extract_text,
)


def test_extract_text_simple_paragraph() -> None:
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Hello world"}],
            }
        ],
    }
    assert extract_text(doc) == "Hello world"


def test_extract_text_multiple_paragraphs() -> None:
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Line 1"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Line 2"}]},
        ],
    }
    assert "Line 1" in extract_text(doc)
    assert "Line 2" in extract_text(doc)


def test_extract_text_none_input() -> None:
    assert extract_text(None) == ""


def test_extract_text_empty_dict() -> None:
    assert extract_text({}) == ""


def test_build_paragraph_structure() -> None:
    result = build_paragraph("Test text")
    assert result["type"] == "paragraph"
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["text"] == "Test text"


def test_append_to_description_new_doc() -> None:
    result = append_to_description(None, "New content")
    assert result["type"] == "doc"
    assert result["version"] == 1
    assert len(result["content"]) == 1
    assert result["content"][0]["content"][0]["text"] == "New content"


def test_append_to_description_preserves_existing() -> None:
    existing = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Original"}]},
        ],
    }
    result = append_to_description(existing, "Appended")
    assert len(result["content"]) == 2
    assert result["content"][0]["content"][0]["text"] == "Original"
    assert result["content"][1]["content"][0]["text"] == "Appended"


def test_build_bug_comment_failed() -> None:
    msg = build_bug_comment("TC-001", 3, "Element not found")
    assert "[FAILED]" in msg
    assert "TC-001" in msg
    assert "Step 3" in msg
    assert "Element not found" in msg


def test_build_bug_comment_healed() -> None:
    msg = build_bug_comment("TC-002", 1, "Timeout", healed=True)
    assert "[HEALED]" in msg
    assert "TC-002" in msg
