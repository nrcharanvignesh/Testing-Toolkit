"""Tests for core.app_config — configuration resolution and env parsing."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def test_parse_env_text_basic() -> None:
    from core.app_config import _parse_env_text

    text = "KEY=value\nOTHER=123\n"
    assert _parse_env_text(text) == {"KEY": "value", "OTHER": "123"}


def test_parse_env_text_skips_comments_and_blanks() -> None:
    from core.app_config import _parse_env_text

    text = "# comment\n\n  \nA=1\n#B=2\n"
    assert _parse_env_text(text) == {"A": "1"}


def test_parse_env_text_handles_equals_in_value() -> None:
    from core.app_config import _parse_env_text

    text = "URL=https://host.com?a=1&b=2\n"
    assert _parse_env_text(text) == {"URL": "https://host.com?a=1&b=2"}


def test_parse_env_text_strips_whitespace() -> None:
    from core.app_config import _parse_env_text

    text = "  KEY  =  value  \n"
    assert _parse_env_text(text) == {"KEY": "value"}


def test_parse_env_text_empty() -> None:
    from core.app_config import _parse_env_text

    assert _parse_env_text("") == {}
    assert _parse_env_text("# only comments\n") == {}


def test_resolve_workspace_default(tmp_path) -> None:
    with patch.dict(os.environ, {"TT_WORKSPACE_DIR": ""}, clear=False):
        from core.app_config import _resolve_workspace, WEB_WORKSPACE_SLUG
        from pathlib import Path

        result = _resolve_workspace()
        assert result == Path.home() / WEB_WORKSPACE_SLUG


def test_resolve_workspace_override(tmp_path) -> None:
    override = str(tmp_path / "custom_ws")
    with patch.dict(os.environ, {"TT_WORKSPACE_DIR": override}, clear=False):
        from core.app_config import _resolve_workspace
        from pathlib import Path

        result = _resolve_workspace()
        assert result == Path(override)


def test_credential_protection_state_returns_string() -> None:
    from core.app_config import credential_protection_state

    state = credential_protection_state()
    assert isinstance(state, str)
    assert len(state) > 0


def test_credential_protection_detail_returns_string() -> None:
    from core.app_config import credential_protection_detail

    detail = credential_protection_detail()
    assert isinstance(detail, str)


def test_cfg_prefers_env_over_default() -> None:
    from core.app_config import _cfg

    with patch.dict(os.environ, {"TEST_CFG_VAR_XYZ": "from_env"}, clear=False):
        assert _cfg("TEST_CFG_VAR_XYZ", "fallback") == "from_env"


def test_cfg_returns_default_when_env_empty() -> None:
    from core.app_config import _cfg

    with patch.dict(os.environ, {"TEST_CFG_VAR_ABSENT": ""}, clear=False):
        result = _cfg("TEST_CFG_VAR_ABSENT_NEVER_SET", "my_default")
        assert result == "my_default"


# --- _resolve_config_dir ---


def test_resolve_config_dir_default() -> None:
    from core.app_config import _resolve_config_dir
    from pathlib import Path

    with patch.dict(os.environ, {"TT_CONFIG_DIR": ""}, clear=False):
        result = _resolve_config_dir()
        assert result == Path.home() / ".testing_toolkit"


def test_resolve_config_dir_override(tmp_path) -> None:
    from core.app_config import _resolve_config_dir

    override = str(tmp_path / "custom_config")
    with patch.dict(os.environ, {"TT_CONFIG_DIR": override}, clear=False):
        result = _resolve_config_dir()
        assert result == tmp_path / "custom_config"


# --- _resolve_int_env ---


def test_resolve_int_env_returns_default_when_empty() -> None:
    from core.app_config import _resolve_int_env

    with patch.dict(os.environ, {"TT_TEST_INT": ""}, clear=False):
        assert _resolve_int_env("TT_TEST_INT", 42) == 42


def test_resolve_int_env_parses_valid_int() -> None:
    from core.app_config import _resolve_int_env

    with patch.dict(os.environ, {"TT_TEST_INT": "8"}, clear=False):
        assert _resolve_int_env("TT_TEST_INT", 42) == 8


def test_resolve_int_env_clamps_negative_to_zero() -> None:
    from core.app_config import _resolve_int_env

    with patch.dict(os.environ, {"TT_TEST_INT": "-5"}, clear=False):
        assert _resolve_int_env("TT_TEST_INT", 42) == 0


def test_resolve_int_env_returns_default_on_invalid() -> None:
    from core.app_config import _resolve_int_env

    with patch.dict(os.environ, {"TT_TEST_INT": "not_a_number"}, clear=False):
        assert _resolve_int_env("TT_TEST_INT", 42) == 42


# --- resolve_index_workers ---


def test_resolve_index_workers_caps_at_n_items() -> None:
    from core.app_config import resolve_index_workers

    assert resolve_index_workers(2) <= 2


def test_resolve_index_workers_returns_one_for_zero_items() -> None:
    from core.app_config import resolve_index_workers

    assert resolve_index_workers(0) == 1
    assert resolve_index_workers(-1) == 1


def test_resolve_index_workers_at_least_one() -> None:
    from core.app_config import resolve_index_workers

    assert resolve_index_workers(1000) >= 1


# --- display_project_name ---


def test_display_project_name_strips_prefix() -> None:
    from core.app_config import display_project_name

    assert display_project_name("InteractionsHub_Abbott", "InteractionsHub_") == "Abbott"


def test_display_project_name_case_insensitive() -> None:
    from core.app_config import display_project_name

    assert display_project_name("INTERACTIONSHUB_Abbott", "InteractionsHub_") == "Abbott"


def test_display_project_name_no_match() -> None:
    from core.app_config import display_project_name

    assert display_project_name("OtherProject", "InteractionsHub_") == "OtherProject"


def test_display_project_name_empty_after_strip() -> None:
    from core.app_config import display_project_name

    assert display_project_name("PREFIX", "PREFIX") == "PREFIX"


def test_display_project_name_empty_prefix() -> None:
    from core.app_config import display_project_name

    assert display_project_name("MyProject", "") == "MyProject"


# --- _base_dir / asset_path ---


def test_base_dir_from_source() -> None:
    from core.app_config import _base_dir
    from pathlib import Path

    result = _base_dir()
    assert isinstance(result, Path)
    assert result.exists()


def test_base_dir_from_pyinstaller() -> None:
    import sys
    from core.app_config import _base_dir
    from pathlib import Path

    with patch.object(sys, "_MEIPASS", "/fake/meipass", create=True):
        result = _base_dir()
        assert result == Path("/fake/meipass")


def test_asset_path_under_assets_dir() -> None:
    from core.app_config import asset_path

    result = asset_path("icon.png")
    assert "assets" in str(result)
    assert str(result).endswith("icon.png")


# --- Constants ---


def test_default_project_prefix_is_string() -> None:
    from core.app_config import DEFAULT_PROJECT_PREFIX

    assert isinstance(DEFAULT_PROJECT_PREFIX, str)


def test_path_constants_are_paths() -> None:
    from core.app_config import PROJECTS_DIR, SETTINGS_PATH, LOGS_DIR
    from pathlib import Path

    assert isinstance(PROJECTS_DIR, Path)
    assert isinstance(SETTINGS_PATH, Path)
    assert isinstance(LOGS_DIR, Path)
