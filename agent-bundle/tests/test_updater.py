"""Tests for agent.updater — version detection and update checking."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.updater import (
    _is_newer,
    _version_tuple,
    _manifest_url_for,
    _auth_headers,
    _fetch_manifest,
    _load_config,
    _invalidate_update_cache,
    resolve_manifest_url,
    install_dir,
    check_for_update,
    DEFAULT_REPO,
    DEFAULT_REF,
    AGENT_VERSION,
)


# ---------------------------------------------------------------------------
# _version_tuple
# ---------------------------------------------------------------------------


class TestVersionTuple:
    def test_simple_semver(self) -> None:
        assert _version_tuple("1.2.3") == (1, 2, 3)

    def test_leading_zeros(self) -> None:
        assert _version_tuple("01.02.03") == (1, 2, 3)

    def test_single_digit(self) -> None:
        assert _version_tuple("5") == (5,)

    def test_two_part(self) -> None:
        assert _version_tuple("2.10") == (2, 10)

    def test_prerelease_suffix(self) -> None:
        assert _version_tuple("1.2.3-beta1") == (1, 2, 3)

    def test_whitespace_stripped(self) -> None:
        assert _version_tuple("  3.0.1  ") == (3, 0, 1)

    def test_empty_string(self) -> None:
        assert _version_tuple("") == (0,)

    def test_non_numeric_chunks(self) -> None:
        assert _version_tuple("abc.def") == (0, 0)

    def test_mixed_numeric_and_alpha(self) -> None:
        assert _version_tuple("1.2rc1.3") == (1, 2, 3)

    def test_zero_version(self) -> None:
        assert _version_tuple("0.0.0") == (0, 0, 0)

    def test_large_numbers(self) -> None:
        assert _version_tuple("100.200.300") == (100, 200, 300)


# ---------------------------------------------------------------------------
# _is_newer
# ---------------------------------------------------------------------------


class TestIsNewer:
    def test_newer_patch(self) -> None:
        assert _is_newer("1.0.2", "1.0.1") is True

    def test_newer_minor(self) -> None:
        assert _is_newer("1.1.0", "1.0.9") is True

    def test_newer_major(self) -> None:
        assert _is_newer("2.0.0", "1.9.9") is True

    def test_equal_versions(self) -> None:
        assert _is_newer("1.0.0", "1.0.0") is False

    def test_older_version(self) -> None:
        assert _is_newer("1.0.0", "1.0.1") is False

    def test_different_length_newer(self) -> None:
        assert _is_newer("1.0.0.1", "1.0.0") is True

    def test_different_length_equal(self) -> None:
        assert _is_newer("1.0.0", "1.0.0.0") is False

    def test_different_length_older(self) -> None:
        assert _is_newer("1.0", "1.0.1") is False

    def test_empty_latest(self) -> None:
        assert _is_newer("", "1.0.0") is False

    def test_empty_current(self) -> None:
        assert _is_newer("1.0.0", "") is True

    def test_prerelease_stripped_not_newer(self) -> None:
        # "2.0.0-beta" parses as (2, 0, 0) which equals (2, 0, 0)
        assert _is_newer("2.0.0-beta", "2.0.0") is False

    def test_patch_bump(self) -> None:
        assert _is_newer("3.2.1", "3.2.0") is True


# ---------------------------------------------------------------------------
# _manifest_url_for
# ---------------------------------------------------------------------------


class TestManifestUrlFor:
    def test_default_repo_and_ref(self) -> None:
        url = _manifest_url_for("", "")
        assert DEFAULT_REPO in url
        assert f"ref={DEFAULT_REF}" in url

    def test_custom_repo_and_ref(self) -> None:
        url = _manifest_url_for("owner/repo", "main")
        assert "owner/repo" in url
        assert "ref=main" in url

    def test_whitespace_stripped(self) -> None:
        url = _manifest_url_for("  owner/repo  ", "  branch  ")
        assert "owner/repo" in url
        assert "ref=branch" in url
        assert "  " not in url

    def test_none_falls_to_default(self) -> None:
        url = _manifest_url_for(None, None)  # type: ignore[arg-type]
        assert DEFAULT_REPO in url
        assert f"ref={DEFAULT_REF}" in url

    def test_url_structure(self) -> None:
        url = _manifest_url_for("a/b", "c")
        assert url == "https://api.github.com/repos/a/b/contents/agent-update.json?ref=c"


# ---------------------------------------------------------------------------
# install_dir
# ---------------------------------------------------------------------------


class TestInstallDir:
    def test_default_path(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TT_INSTALL_DIR", None)
            result = install_dir()
            assert result == Path.home() / "TestingToolkitWeb"

    def test_env_override(self, tmp_path: Path) -> None:
        override = str(tmp_path / "custom_install")
        with patch.dict(os.environ, {"TT_INSTALL_DIR": override}, clear=False):
            result = install_dir()
            assert result == Path(override)

    def test_expanduser(self) -> None:
        with patch.dict(os.environ, {"TT_INSTALL_DIR": "~/agent"}, clear=False):
            result = install_dir()
            assert "~" not in str(result)
            assert result == Path.home() / "agent"


# ---------------------------------------------------------------------------
# _load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def setup_method(self) -> None:
        import agent.updater as mod
        mod._cached_config = None
        mod._cached_config_mtime = 0.0

    def test_loads_valid_json(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(json.dumps({"token": "fake_abc123"}), encoding="utf-8")

        with patch("agent.updater._config_path", return_value=config_file):
            result = _load_config()
            assert result == {"token": "fake_abc123"}

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.json"
        with patch("agent.updater._config_path", return_value=missing):
            result = _load_config()
            assert result == {}

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text("not json {{{", encoding="utf-8")

        with patch("agent.updater._config_path", return_value=config_file):
            result = _load_config()
            assert result == {}

    def test_non_dict_json_returns_empty(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        with patch("agent.updater._config_path", return_value=config_file):
            result = _load_config()
            assert result == {}

    def test_caches_by_mtime(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(json.dumps({"v": 1}), encoding="utf-8")

        with patch("agent.updater._config_path", return_value=config_file):
            r1 = _load_config()
            r2 = _load_config()
            assert r1 is r2  # same object = cache hit


# ---------------------------------------------------------------------------
# resolve_manifest_url
# ---------------------------------------------------------------------------


class TestResolveManifestUrl:
    def setup_method(self) -> None:
        import agent.updater as mod
        mod._cached_config = None
        mod._cached_config_mtime = 0.0

    def test_returns_empty_without_token(self) -> None:
        with patch.dict(os.environ, {
            "AGENT_MANIFEST_URL": "",
            "TT_UPDATE_TOKEN": "",
        }, clear=False):
            os.environ.pop("AGENT_MANIFEST_URL", None)
            os.environ.pop("TT_UPDATE_TOKEN", None)
            _invalidate_update_cache()
            url = resolve_manifest_url()
            assert isinstance(url, str)

    def test_explicit_manifest_url_from_env(self) -> None:
        sentinel = "https://example.com/manifest.json"
        with patch.dict(os.environ, {
            "AGENT_MANIFEST_URL": sentinel,
        }, clear=False):
            url = resolve_manifest_url()
            assert url == sentinel

    def test_explicit_url_from_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(
            json.dumps({"manifest_url": "https://example.com/manifest.json"}),
            encoding="utf-8",
        )
        with patch("agent.updater._config_path", return_value=config_file):
            assert resolve_manifest_url() == "https://example.com/manifest.json"

    def test_token_builds_url_from_repo_ref(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(
            json.dumps({"token": "fake_tok", "repo": "org/r", "ref": "dev"}),
            encoding="utf-8",
        )
        with patch("agent.updater._config_path", return_value=config_file):
            url = resolve_manifest_url()
            assert "org/r" in url
            assert "ref=dev" in url


# ---------------------------------------------------------------------------
# _auth_headers
# ---------------------------------------------------------------------------


class TestAuthHeaders:
    def setup_method(self) -> None:
        import agent.updater as mod
        mod._cached_config = None
        mod._cached_config_mtime = 0.0

    def test_no_token(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(json.dumps({}), encoding="utf-8")
        with patch("agent.updater._config_path", return_value=config_file):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TT_UPDATE_TOKEN", None)
                headers = _auth_headers()
                assert "Authorization" not in headers
                assert headers["Accept"] == "application/vnd.github.raw"
                assert headers["User-Agent"] == "TestingToolkit-Agent"

    def test_with_token_from_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(json.dumps({"token": "fake_ghp_123"}), encoding="utf-8")
        with patch("agent.updater._config_path", return_value=config_file):
            headers = _auth_headers()
            assert headers["Authorization"] == "Bearer fake_ghp_123"

    def test_with_token_from_env(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(json.dumps({}), encoding="utf-8")
        with patch("agent.updater._config_path", return_value=config_file):
            with patch.dict(os.environ, {"TT_UPDATE_TOKEN": "fake_env_tok"}):
                headers = _auth_headers()
                assert headers["Authorization"] == "Bearer fake_env_tok"


# ---------------------------------------------------------------------------
# _fetch_manifest
# ---------------------------------------------------------------------------


class TestFetchManifest:
    def test_success_returns_dict(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"version": "2.0.0"}

        with patch("httpx.get", return_value=mock_resp):
            result = _fetch_manifest("https://example.com/manifest.json")
            assert result == {"version": "2.0.0"}

    def test_non_200_returns_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.get", return_value=mock_resp):
            assert _fetch_manifest("https://example.com/404") is None

    def test_non_dict_response_returns_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [1, 2, 3]

        with patch("httpx.get", return_value=mock_resp):
            assert _fetch_manifest("https://example.com/list") is None

    def test_network_error_returns_none(self) -> None:
        with patch("httpx.get", side_effect=Exception("timeout")):
            assert _fetch_manifest("https://example.com/err") is None

    def test_passes_headers_and_timeout(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"version": "1.0.0"}

        with patch("httpx.get", return_value=mock_resp) as mock_get:
            with patch("agent.updater._auth_headers", return_value={"X": "Y"}):
                _fetch_manifest("https://example.com/m.json")
                mock_get.assert_called_once_with(
                    "https://example.com/m.json",
                    headers={"X": "Y"},
                    timeout=10,
                    follow_redirects=True,
                )


# ---------------------------------------------------------------------------
# check_for_update
# ---------------------------------------------------------------------------


class TestCheckForUpdate:
    def setup_method(self) -> None:
        import agent.updater as mod
        mod._cached_result = None
        mod._cached_result_time = 0.0
        mod._cached_config = None
        mod._cached_config_mtime = 0.0

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

    def test_no_manifest_url(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(json.dumps({}), encoding="utf-8")
        with patch("agent.updater._config_path", return_value=config_file):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AGENT_MANIFEST_URL", None)
                os.environ.pop("TT_UPDATE_TOKEN", None)
                result = check_for_update()
                assert result["configured"] is False
                assert result["reachable"] is False
                assert result["update_available"] is False
                assert result["latest"] is None

    def test_update_available(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(
            json.dumps({"manifest_url": "https://example.com/m.json"}),
            encoding="utf-8",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"version": "99.99.99"}

        with patch("agent.updater._config_path", return_value=config_file):
            with patch("httpx.get", return_value=mock_resp):
                result = check_for_update()
                assert result["configured"] is True
                assert result["reachable"] is True
                assert result["update_available"] is True
                assert result["latest"] == "99.99.99"
                assert result["current"] == AGENT_VERSION

    def test_no_update_when_current(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(
            json.dumps({"manifest_url": "https://example.com/m.json"}),
            encoding="utf-8",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"version": AGENT_VERSION}

        with patch("agent.updater._config_path", return_value=config_file):
            with patch("httpx.get", return_value=mock_resp):
                result = check_for_update()
                assert result["update_available"] is False

    def test_unreachable_manifest(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(
            json.dumps({"manifest_url": "https://example.com/m.json"}),
            encoding="utf-8",
        )
        with patch("agent.updater._config_path", return_value=config_file):
            with patch("httpx.get", side_effect=Exception("network")):
                result = check_for_update()
                assert result["configured"] is True
                assert result["reachable"] is False
                assert result["update_available"] is False

    def test_install_dir_in_result(self, tmp_path: Path) -> None:
        config_file = tmp_path / "update.json"
        config_file.write_text(json.dumps({}), encoding="utf-8")
        with patch("agent.updater._config_path", return_value=config_file):
            with patch.dict(os.environ, {"TT_INSTALL_DIR": str(tmp_path)}, clear=False):
                os.environ.pop("AGENT_MANIFEST_URL", None)
                os.environ.pop("TT_UPDATE_TOKEN", None)
                result = check_for_update()
                assert result["install_dir"] == str(tmp_path)


# ---------------------------------------------------------------------------
# _invalidate_update_cache
# ---------------------------------------------------------------------------


class TestInvalidateCache:
    def test_clears_cached_result(self) -> None:
        import agent.updater as mod

        mod._cached_result = {"fake": True}
        mod._cached_result_time = 999.0
        _invalidate_update_cache()
        assert mod._cached_result is None
        assert mod._cached_result_time == 0.0
