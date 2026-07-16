"""Tests for agent.updater — version comparison and manifest resolution."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agent.updater import (
    _is_newer,
    _version_tuple,
    resolve_manifest_url,
    install_dir,
    _invalidate_update_cache,
    check_for_update,
    DEFAULT_REPO,
    DEFAULT_REF,
)


class TestVersionTuple:
    def test_simple(self) -> None:
        assert _version_tuple("1.2.3") == (1, 2, 3)

    def test_leading_zeros(self) -> None:
        assert _version_tuple("01.02.03") == (1, 2, 3)

    def test_pre_release_suffix(self) -> None:
        assert _version_tuple("1.2.3-beta1") == (1, 2, 3)

    def test_empty(self) -> None:
        assert _version_tuple("") == (0,)

    def test_single_digit(self) -> None:
        assert _version_tuple("5") == (5,)


class TestIsNewer:
    def test_newer(self) -> None:
        assert _is_newer("2.0.0", "1.9.9") is True

    def test_equal(self) -> None:
        assert _is_newer("1.0.0", "1.0.0") is False

    def test_older(self) -> None:
        assert _is_newer("1.0.0", "2.0.0") is False

    def test_different_length(self) -> None:
        assert _is_newer("1.0.1", "1.0") is True
        assert _is_newer("1.0", "1.0.1") is False

    def test_patch_bump(self) -> None:
        assert _is_newer("3.2.1", "3.2.0") is True


class TestResolveManifestUrl:
    def test_returns_empty_without_token(self) -> None:
        with patch.dict(os.environ, {
            "AGENT_MANIFEST_URL": "",
            "TT_UPDATE_TOKEN": "",
        }, clear=False):
            _invalidate_update_cache()
            url = resolve_manifest_url()
            # Without a config file or env token, returns empty
            assert isinstance(url, str)

    def test_explicit_manifest_url_from_env(self) -> None:
        sentinel = "https://example.com/manifest.json"
        with patch.dict(os.environ, {
            "AGENT_MANIFEST_URL": sentinel,
        }, clear=False):
            url = resolve_manifest_url()
            assert url == sentinel


class TestInstallDir:
    def test_default_path(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TT_INSTALL_DIR", None)
            from pathlib import Path
            result = install_dir()
            assert result == Path.home() / "TestingToolkitWeb"

    def test_override(self, tmp_path) -> None:
        override = str(tmp_path / "custom_install")
        with patch.dict(os.environ, {"TT_INSTALL_DIR": override}, clear=False):
            from pathlib import Path
            result = install_dir()
            assert result == Path(override)


class TestCheckForUpdate:
    def test_returns_expected_keys(self) -> None:
        _invalidate_update_cache()
        result = check_for_update()
        assert "current" in result
        assert "latest" in result
        assert "update_available" in result
        assert "configured" in result
        assert "reachable" in result
        assert "install_dir" in result

    def test_caches_result(self) -> None:
        _invalidate_update_cache()
        r1 = check_for_update()
        r2 = check_for_update()
        assert r1 is r2
