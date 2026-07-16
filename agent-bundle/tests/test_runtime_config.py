"""Tests for core.runtime_config module."""
from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.runtime_config import (
    RuntimeConfig,
    _env_float,
    _env_int,
    _env_str,
)


# -- _env_int ----------------------------------------------------------------

class TestEnvInt:
    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_INT_VAR", raising=False)
        assert _env_int("TEST_INT_VAR", 42) == 42

    def test_returns_default_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "")
        assert _env_int("TEST_INT_VAR", 7) == 7

    def test_returns_default_when_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "   ")
        assert _env_int("TEST_INT_VAR", 7) == 7

    def test_parses_valid_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "16")
        assert _env_int("TEST_INT_VAR", 1) == 16

    def test_returns_default_on_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "abc")
        assert _env_int("TEST_INT_VAR", 5) == 5

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "  10  ")
        assert _env_int("TEST_INT_VAR", 1) == 10


# -- _env_float ---------------------------------------------------------------

class TestEnvFloat:
    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_FLOAT_VAR", raising=False)
        assert _env_float("TEST_FLOAT_VAR", 1.5) == 1.5

    def test_parses_valid_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_FLOAT_VAR", "3.14")
        assert _env_float("TEST_FLOAT_VAR", 0.0) == pytest.approx(3.14)

    def test_returns_default_on_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_FLOAT_VAR", "not_a_float")
        assert _env_float("TEST_FLOAT_VAR", 9.9) == 9.9


# -- _env_str -----------------------------------------------------------------

class TestEnvStr:
    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_STR_VAR", raising=False)
        assert _env_str("TEST_STR_VAR", "fallback") == "fallback"

    def test_returns_stripped_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_STR_VAR", "  hello  ")
        assert _env_str("TEST_STR_VAR", "") == "hello"

    def test_returns_default_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_STR_VAR", "")
        assert _env_str("TEST_STR_VAR", "default") == "default"


# -- RuntimeConfig.from_env_defaults ------------------------------------------

class TestFromEnvDefaults:
    def test_picks_up_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONCURRENCY", "4")
        monkeypatch.setenv("HTTP_TIMEOUT_SEC", "30.0")
        monkeypatch.setenv("RETRY_COUNT", "5")
        monkeypatch.setenv("TLS_MODE", "OFF")
        monkeypatch.setenv("PAPER_SIZE", "letter")
        cfg = RuntimeConfig.from_env_defaults()
        assert cfg.concurrency == 4
        assert cfg.http_timeout_sec == pytest.approx(30.0)
        assert cfg.retry_count == 5
        assert cfg.tls_mode == "off"
        assert cfg.paper_size == "LETTER"

    def test_uses_defaults_when_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("CONCURRENCY", "HTTP_TIMEOUT_SEC", "DOWNLOAD_TIMEOUT_SEC",
                    "RETRY_COUNT", "RETRY_BACKOFF_SEC", "TLS_MODE",
                    "TLS_CA_BUNDLE", "PAPER_SIZE"):
            monkeypatch.delenv(key, raising=False)
        cfg = RuntimeConfig.from_env_defaults()
        assert cfg.concurrency == 8
        assert cfg.http_timeout_sec == pytest.approx(60.0)
        assert cfg.download_timeout_sec == pytest.approx(300.0)
        assert cfg.retry_count == 3
        assert cfg.retry_backoff_sec == pytest.approx(2.0)
        assert cfg.tls_mode == "system"
        assert cfg.paper_size == "A4"


# -- RuntimeConfig.validate ---------------------------------------------------

class TestValidate:
    def test_valid_config_returns_no_errors(self) -> None:
        cfg = RuntimeConfig(
            pat="FAKE_PAT_SENTINEL",
            organization="my-org",
            project="my-proj",
            work_item_ids=[1, 2, 3],
        )
        assert cfg.validate() == []

    def test_empty_pat_reports_error(self) -> None:
        cfg = RuntimeConfig(organization="o", project="p", work_item_ids=[1])
        errors = cfg.validate()
        assert any("PAT" in e for e in errors)

    def test_empty_org_reports_error(self) -> None:
        cfg = RuntimeConfig(pat="FAKE_PAT", project="p", work_item_ids=[1])
        errors = cfg.validate()
        assert any("Organization" in e for e in errors)

    def test_empty_project_reports_error(self) -> None:
        cfg = RuntimeConfig(pat="FAKE_PAT", organization="o", work_item_ids=[1])
        errors = cfg.validate()
        assert any("Project" in e for e in errors)

    def test_no_work_items_reports_error(self) -> None:
        cfg = RuntimeConfig(pat="FAKE_PAT", organization="o", project="p")
        errors = cfg.validate()
        assert any("Work Item" in e for e in errors)

    def test_invalid_tls_mode_reports_error(self) -> None:
        cfg = RuntimeConfig(
            pat="FAKE_PAT", organization="o", project="p",
            work_item_ids=[1], tls_mode="bogus",
        )
        errors = cfg.validate()
        assert any("tls_mode" in e for e in errors)

    def test_invalid_paper_size_reports_error(self) -> None:
        cfg = RuntimeConfig(
            pat="FAKE_PAT", organization="o", project="p",
            work_item_ids=[1], paper_size="LEGAL",
        )
        errors = cfg.validate()
        assert any("paper_size" in e for e in errors)

    def test_multiple_errors_reported(self) -> None:
        cfg = RuntimeConfig()
        errors = cfg.validate()
        assert len(errors) >= 4  # pat, org, project, work_items


# -- RuntimeConfig.build_ssl --------------------------------------------------

class TestBuildSsl:
    def test_off_returns_false(self) -> None:
        cfg = RuntimeConfig(tls_mode="off")
        assert cfg.build_ssl() is False

    def test_bundle_without_path_raises(self) -> None:
        cfg = RuntimeConfig(tls_mode="bundle", tls_ca_bundle="")
        with pytest.raises(RuntimeError, match="TLS_CA_BUNDLE"):
            cfg.build_ssl()

    def test_bundle_with_path_creates_context(self, tmp_path: Path) -> None:
        ca_file = tmp_path / "ca.pem"
        # Write a minimal (invalid but existing) PEM file
        ca_file.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")
        cfg = RuntimeConfig(tls_mode="bundle", tls_ca_bundle=str(ca_file))
        # ssl.create_default_context will accept an existing file path even if
        # the cert is invalid for actual TLS; it returns an SSLContext.
        with patch("ssl.create_default_context") as mock_ctx:
            mock_ctx.return_value = MagicMock(spec=ssl.SSLContext)
            result = cfg.build_ssl()
            mock_ctx.assert_called_once_with(cafile=str(ca_file))
            assert result is mock_ctx.return_value

    def test_truststore_mode_uses_truststore(self) -> None:
        cfg = RuntimeConfig(tls_mode="truststore")
        mock_ctx = MagicMock()
        mock_truststore = MagicMock()
        mock_truststore.SSLContext.return_value = mock_ctx
        with patch.dict("sys.modules", {"truststore": mock_truststore}):
            result = cfg.build_ssl()
            assert result is mock_ctx

    def test_system_mode_fallback_chain(self) -> None:
        """When truststore import fails, falls back to combined bundle helper."""
        cfg = RuntimeConfig(tls_mode="system")
        mock_ctx = MagicMock(spec=ssl.SSLContext)
        with patch.dict("sys.modules", {"truststore": None}):
            with patch("ssl.create_default_context", return_value=mock_ctx) as mock_ssl:
                with patch("core.tls_helper.build_combined_ca_bundle", return_value="/fake/ca.pem"):
                    result = cfg.build_ssl()
                    mock_ssl.assert_called_with(cafile="/fake/ca.pem")
                    assert result is mock_ctx
