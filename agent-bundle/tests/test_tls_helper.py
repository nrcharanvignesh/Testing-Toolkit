"""Tests for core.tls_helper — combined CA bundle builder."""
from __future__ import annotations

from pathlib import Path

from core.tls_helper import (
    _certifi_pem,
    _CACHE_PATH,
    build_combined_ca_bundle,
    cache_path,
)


def test_certifi_pem_returns_string() -> None:
    result = _certifi_pem()
    assert isinstance(result, str)
    # certifi should be installed; expect non-empty PEM content
    assert len(result) > 100
    assert "BEGIN CERTIFICATE" in result


def test_cache_path_is_pem() -> None:
    p = cache_path()
    assert isinstance(p, Path)
    assert p.suffix == ".pem"


def test_build_combined_ca_bundle_returns_path() -> None:
    result = build_combined_ca_bundle()
    assert isinstance(result, str)
    assert result.endswith(".pem")
    assert Path(result).exists()


def test_build_combined_ca_bundle_force_rebuild() -> None:
    path_str = build_combined_ca_bundle(force_rebuild=True)
    p = Path(path_str)
    assert p.exists()
    content = p.read_text(encoding="ascii", errors="replace")
    assert "BEGIN CERTIFICATE" in content
