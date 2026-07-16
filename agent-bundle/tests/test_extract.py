"""Tests for ado.extract — pure functions and data classes."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from ado.extract import (
    build_auth_header,
    html_to_text,
    extract_image_urls,
    _safe_filename,
    WIPaths,
    ExtractResult,
    set_loggers,
    log_info,
    log_error,
    log_success,
)


# ---------------------------------------------------------------------------
# build_auth_header
# ---------------------------------------------------------------------------


class TestBuildAuthHeader:
    def test_returns_basic_auth(self) -> None:
        headers = build_auth_header("fake_pat_value")
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")

    def test_encoding_correct(self) -> None:
        pat = "my_test_pat"
        expected_token = base64.b64encode(f":{pat}".encode("ascii")).decode("ascii")
        headers = build_auth_header(pat)
        assert headers["Authorization"] == f"Basic {expected_token}"

    def test_accept_header(self) -> None:
        headers = build_auth_header("x")
        assert headers["Accept"] == "application/json"

    def test_empty_pat(self) -> None:
        headers = build_auth_header("")
        expected = base64.b64encode(b":").decode("ascii")
        assert headers["Authorization"] == f"Basic {expected}"


# ---------------------------------------------------------------------------
# html_to_text
# ---------------------------------------------------------------------------


class TestHtmlToText:
    def test_none_returns_empty(self) -> None:
        assert html_to_text(None) == ""

    def test_empty_string(self) -> None:
        assert html_to_text("") == ""

    def test_whitespace_only(self) -> None:
        assert html_to_text("   \n\t  ") == ""

    def test_simple_paragraph(self) -> None:
        result = html_to_text("<p>Hello world</p>")
        assert "Hello world" in result

    def test_nested_tags(self) -> None:
        result = html_to_text("<div><p><strong>Bold</strong> text</p></div>")
        assert "Bold" in result
        assert "text" in result

    def test_list_items_get_bullets(self) -> None:
        html = "<ul><li>First</li><li>Second</li></ul>"
        result = html_to_text(html)
        assert "- First" in result
        assert "- Second" in result

    def test_br_becomes_newline(self) -> None:
        result = html_to_text("line1<br>line2")
        assert "line1" in result
        assert "line2" in result

    def test_multiple_whitespace_collapsed(self) -> None:
        result = html_to_text("<p>Hello     world</p>")
        assert "Hello world" in result

    def test_multiple_blank_lines_collapsed(self) -> None:
        result = html_to_text("<p>A</p><p></p><p></p><p>B</p>")
        assert "\n\n\n" not in result

    def test_preserves_text_content(self) -> None:
        result = html_to_text("<h1>Title</h1><p>Body paragraph here.</p>")
        assert "Title" in result
        assert "Body paragraph here." in result


# ---------------------------------------------------------------------------
# extract_image_urls
# ---------------------------------------------------------------------------


class TestExtractImageUrls:
    def test_none_returns_empty(self) -> None:
        assert extract_image_urls(None) == []

    def test_empty_string(self) -> None:
        assert extract_image_urls("") == []

    def test_single_img(self) -> None:
        html = '<img src="https://example.com/img.png" />'
        assert extract_image_urls(html) == ["https://example.com/img.png"]

    def test_multiple_imgs(self) -> None:
        html = '<img src="a.png"><img src="b.jpg">'
        assert extract_image_urls(html) == ["a.png", "b.jpg"]

    def test_no_img_tags(self) -> None:
        assert extract_image_urls("<p>No images here</p>") == []

    def test_img_without_src(self) -> None:
        html = '<img alt="no source">'
        assert extract_image_urls(html) == []

    def test_single_quotes(self) -> None:
        html = "<img src='https://example.com/pic.gif' />"
        assert extract_image_urls(html) == ["https://example.com/pic.gif"]

    def test_data_uri(self) -> None:
        html = '<img src="data:image/png;base64,iVBORw0KGgo=" />'
        result = extract_image_urls(html)
        assert len(result) == 1
        assert result[0].startswith("data:image/png")

    def test_mixed_content(self) -> None:
        html = '<p>Text</p><img src="x.png"><p>More</p><img src="y.jpg">'
        assert extract_image_urls(html) == ["x.png", "y.jpg"]


# ---------------------------------------------------------------------------
# _safe_filename
# ---------------------------------------------------------------------------


class TestSafeFilename:
    def test_normal_name_unchanged(self) -> None:
        assert _safe_filename("report.pdf") == "report.pdf"

    def test_strips_bad_chars(self) -> None:
        result = _safe_filename('file<name>:with"bad|chars?.txt')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert '"' not in result
        assert "|" not in result
        assert "?" not in result
        assert "*" not in result

    def test_replaces_bad_with_underscore(self) -> None:
        result = _safe_filename("a<b")
        assert result == "a_b"

    def test_strips_leading_trailing_dots_spaces(self) -> None:
        result = _safe_filename("...file.txt...")
        assert not result.startswith(".")
        assert not result.endswith(".")

    def test_empty_name_fallback(self) -> None:
        assert _safe_filename("") == "unnamed_attachment"

    def test_only_bad_chars(self) -> None:
        assert _safe_filename("...") == "unnamed_attachment"

    def test_truncates_long_names(self) -> None:
        long_name = "a" * 300 + ".pdf"
        result = _safe_filename(long_name)
        assert len(result) <= 200

    def test_backslash_replaced(self) -> None:
        result = _safe_filename("path\\file.txt")
        assert "\\" not in result


# ---------------------------------------------------------------------------
# WIPaths
# ---------------------------------------------------------------------------


class TestWIPaths:
    def test_for_id_creates_correct_structure(self, tmp_path: Path) -> None:
        paths = WIPaths.for_id(12345, tmp_path)
        assert paths.wi_id == 12345
        assert paths.root == tmp_path / "12345"
        assert paths.attachments_dir == tmp_path / "12345" / "attachments"
        assert paths.meta_json == tmp_path / "12345" / "_meta.json"
        assert paths.desc_txt == tmp_path / "12345" / "_description.txt"
        assert paths.ac_txt == tmp_path / "12345" / "_ac.txt"
        assert paths.comments_txt == tmp_path / "12345" / "_comments.txt"

    def test_ensure_creates_directories(self, tmp_path: Path) -> None:
        paths = WIPaths.for_id(99, tmp_path)
        paths.ensure()
        assert paths.attachments_dir.exists()
        assert paths.root.exists()

    def test_different_ids_different_roots(self, tmp_path: Path) -> None:
        p1 = WIPaths.for_id(1, tmp_path)
        p2 = WIPaths.for_id(2, tmp_path)
        assert p1.root != p2.root


# ---------------------------------------------------------------------------
# ExtractResult
# ---------------------------------------------------------------------------


class TestExtractResult:
    def test_defaults(self) -> None:
        r = ExtractResult(wi_id=1, ok=True)
        assert r.title == ""
        assert r.board_lane == ""
        assert r.n_attachments == 0
        assert r.n_comments == 0
        assert r.error == ""

    def test_with_values(self) -> None:
        r = ExtractResult(
            wi_id=42, ok=False, title="Bug", board_lane="Active",
            n_attachments=3, n_comments=5, error="timeout"
        )
        assert r.wi_id == 42
        assert r.ok is False
        assert r.title == "Bug"
        assert r.error == "timeout"


# ---------------------------------------------------------------------------
# set_loggers
# ---------------------------------------------------------------------------


class TestSetLoggers:
    def test_redirects_loggers(self) -> None:
        import ado.extract as mod

        calls: list[tuple[str, str]] = []
        set_loggers(
            info=lambda m: calls.append(("info", m)),
            error=lambda m: calls.append(("error", m)),
            success=lambda m: calls.append(("success", m)),
        )
        mod.log_info("hello")
        mod.log_error("oops")
        mod.log_success("done")
        assert ("info", "hello") in calls
        assert ("error", "oops") in calls
        assert ("success", "done") in calls

        # Restore defaults
        from ado.extract import _stdout_info, _stderr_error, _stdout_success
        set_loggers(_stdout_info, _stderr_error, _stdout_success)
