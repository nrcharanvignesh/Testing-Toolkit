"""Tests for core.prefs_store module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.prefs_store import (
    KEY_ORG,
    KEY_OUTPUT_DIR,
    KEY_PROJECT,
    KEY_THEME,
    PREFS_PATH,
    _load_all,
    _write_all,
    clear_pref,
    get_pref,
    save_pref,
)


@pytest.fixture()
def fake_prefs(tmp_path: Path) -> Path:
    """Redirect PREFS_PATH to a temp file for isolation."""
    fake_path = tmp_path / "prefs.json"
    with patch("core.prefs_store.PREFS_PATH", fake_path):
        yield fake_path


# -- _load_all -----------------------------------------------------------------

class TestLoadAll:
    def test_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "no_such_file.json"
        with patch("core.prefs_store.PREFS_PATH", missing):
            assert _load_all() == {}

    def test_loads_valid_json(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        prefs_file.write_text(json.dumps({"org": "acme", "theme": "dark"}), encoding="utf-8")
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            data = _load_all()
            assert data == {"org": "acme", "theme": "dark"}

    def test_returns_empty_on_corrupt_json(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        prefs_file.write_text("not valid json {{{", encoding="utf-8")
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            assert _load_all() == {}

    def test_returns_empty_on_non_dict_json(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        prefs_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            assert _load_all() == {}

    def test_coerces_values_to_str(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        prefs_file.write_text(json.dumps({"count": 42, "flag": True}), encoding="utf-8")
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            data = _load_all()
            assert data == {"count": "42", "flag": "True"}


# -- _write_all ----------------------------------------------------------------

class TestWriteAll:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            result = _write_all({"key": "value"})
            assert result is True
            content = json.loads(prefs_file.read_text(encoding="utf-8"))
            assert content == {"key": "value"}

    def test_returns_false_on_write_failure(self, tmp_path: Path) -> None:
        # Point at a directory (can't write a file as a dir)
        bad_path = tmp_path / "nonexistent_dir" / "sub" / "prefs.json"
        with patch("core.prefs_store.PREFS_PATH", bad_path):
            result = _write_all({"key": "value"})
            assert result is False


# -- get_pref ------------------------------------------------------------------

class TestGetPref:
    def test_returns_default_when_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "no.json"
        with patch("core.prefs_store.PREFS_PATH", missing):
            assert get_pref("anything", "fallback") == "fallback"

    def test_returns_stored_value(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        prefs_file.write_text(json.dumps({KEY_THEME: "dark"}), encoding="utf-8")
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            assert get_pref(KEY_THEME) == "dark"

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        prefs_file.write_text(json.dumps({KEY_ORG: "  acme  "}), encoding="utf-8")
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            assert get_pref(KEY_ORG) == "acme"


# -- save_pref -----------------------------------------------------------------

class TestSavePref:
    def test_saves_and_retrieves(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            assert save_pref(KEY_PROJECT, "my-project") is True
            assert get_pref(KEY_PROJECT) == "my-project"

    def test_strips_value(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            save_pref(KEY_ORG, "  spaced  ")
            assert get_pref(KEY_ORG) == "spaced"

    def test_returns_false_for_none_value(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            result = save_pref(KEY_ORG, None)  # type: ignore[arg-type]
            assert result is False

    def test_overwrites_existing_key(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            save_pref(KEY_THEME, "light")
            save_pref(KEY_THEME, "dark")
            assert get_pref(KEY_THEME) == "dark"

    def test_preserves_other_keys(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            save_pref(KEY_ORG, "org1")
            save_pref(KEY_PROJECT, "proj1")
            assert get_pref(KEY_ORG) == "org1"
            assert get_pref(KEY_PROJECT) == "proj1"


# -- clear_pref ----------------------------------------------------------------

class TestClearPref:
    def test_removes_existing_key(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            save_pref(KEY_THEME, "dark")
            assert clear_pref(KEY_THEME) is True
            assert get_pref(KEY_THEME) == ""

    def test_returns_true_for_nonexistent_key(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        prefs_file.write_text("{}", encoding="utf-8")
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            assert clear_pref("no_such_key") is True

    def test_preserves_other_keys_on_clear(self, tmp_path: Path) -> None:
        prefs_file = tmp_path / "prefs.json"
        with patch("core.prefs_store.PREFS_PATH", prefs_file):
            save_pref(KEY_ORG, "acme")
            save_pref(KEY_THEME, "light")
            clear_pref(KEY_THEME)
            assert get_pref(KEY_ORG) == "acme"
            assert get_pref(KEY_THEME) == ""
