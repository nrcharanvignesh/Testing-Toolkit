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
