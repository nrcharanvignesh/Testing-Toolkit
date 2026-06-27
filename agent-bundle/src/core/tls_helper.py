"""
tls_helper.py
Build a combined CA bundle: certifi base + OS-store root CAs.
Handles corporate TLS-intercepting proxies (Zscaler, Netskope) where
the proxy's root CA is in the OS trust store but not in certifi.
"""

from __future__ import annotations

import ssl
import subprocess
import sys
from pathlib import Path
from typing import Final

_CACHE_PATH: Final[Path] = Path.home() / ".ado_pdf_packager_combined_ca.pem"


def _certifi_pem() -> str:
    try:
        import certifi  # type: ignore
        return Path(certifi.where()).read_text(encoding="ascii", errors="replace")
    except Exception:
        return ""


def _windows_root_pems() -> str:
    out: list[str] = []
    for store in ("ROOT", "CA"):
        try:
            for cert_bytes, encoding, _trust in ssl.enum_certificates(store):
                if encoding == "x509_asn":
                    out.append(ssl.DER_cert_to_PEM_cert(cert_bytes))
        except Exception:
            pass
    return "\n".join(out)


def _macos_root_pems() -> str:
    pems: list[str] = []
    for kc in (
        "/System/Library/Keychains/SystemRootCertificates.keychain",
        "/Library/Keychains/System.keychain",
    ):
        try:
            r = subprocess.run(
                ["security", "find-certificate", "-a", "-p", kc],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                pems.append(r.stdout)
        except Exception:
            pass
    try:
        r = subprocess.run(
            ["security", "find-certificate", "-a", "-p"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            pems.append(r.stdout)
    except Exception:
        pass
    return "\n".join(pems)


def _linux_root_pems() -> str:
    for path in (
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/ca-bundle.pem",
        "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    ):
        try:
            return Path(path).read_text(encoding="ascii", errors="replace")
        except Exception:
            continue
    return ""


def build_combined_ca_bundle(force_rebuild: bool = False) -> str:
    """Build (or reuse cached) combined CA bundle. Returns path to PEM."""
    if _CACHE_PATH.exists() and not force_rebuild:
        return str(_CACHE_PATH)
    parts: list[str] = []
    base = _certifi_pem()
    if base:
        parts.append(base)
    if sys.platform == "win32":
        parts.append(_windows_root_pems())
    elif sys.platform == "darwin":
        parts.append(_macos_root_pems())
    elif sys.platform.startswith("linux"):
        parts.append(_linux_root_pems())
    combined = "\n".join(p for p in parts if p)
    _CACHE_PATH.write_text(combined, encoding="ascii", errors="replace")
    return str(_CACHE_PATH)


def clear_combined_ca_cache() -> bool:
    try:
        if _CACHE_PATH.exists():
            _CACHE_PATH.unlink()
        return True
    except Exception:
        return False


def cache_path() -> Path:
    return _CACHE_PATH
