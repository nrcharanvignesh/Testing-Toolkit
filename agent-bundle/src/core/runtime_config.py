"""
runtime_config.py
Runtime configuration. Replaces .env-only flow.

Values from GUI: pat, organization, project, work_item_ids, output_dir
Env-overridable: CONCURRENCY, HTTP_TIMEOUT_SEC, DOWNLOAD_TIMEOUT_SEC,
                 RETRY_COUNT, RETRY_BACKOFF_SEC, TLS_MODE, TLS_CA_BUNDLE,
                 PAPER_SIZE
"""

from __future__ import annotations

import os
import ssl
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

API_VER_WI: Final[str] = "7.1"
API_VER_COMMENTS: Final[str] = "7.1-preview.4"
API_VER_WIQL: Final[str] = "7.1"
API_VER_CORE: Final[str] = "7.1"


def _env_int(key: str, default: int) -> int:
    raw = (os.environ.get(key, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = (os.environ.get(key, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_str(key: str, default: str) -> str:
    return (os.environ.get(key, default) or default).strip()


@dataclass(slots=True)
class RuntimeConfig:
    pat: str = ""
    organization: str = ""
    project: str = ""
    work_item_ids: list[int] = field(default_factory=list)
    output_dir: Path = Path("./packets").resolve()
    work_dir: Path = Path("./work").resolve()

    concurrency: int = min(os.cpu_count() or 8, 16)
    http_timeout_sec: float = 60.0
    download_timeout_sec: float = 300.0
    retry_count: int = 3
    retry_backoff_sec: float = 2.0

    # 'system' = combined certifi + OS-store CAs (handles corp proxies)
    # 'truststore' = OS cert store via truststore lib (opt-in; can recurse)
    # 'bundle' = explicit CAfile from tls_ca_bundle
    # 'off' = no verification (insecure; testing only)
    tls_mode: str = "system"
    tls_ca_bundle: str = ""

    paper_size: str = "A4"

    @classmethod
    def from_env_defaults(cls) -> "RuntimeConfig":
        return cls(
            concurrency=_env_int("CONCURRENCY", 8),
            http_timeout_sec=_env_float("HTTP_TIMEOUT_SEC", 60.0),
            download_timeout_sec=_env_float("DOWNLOAD_TIMEOUT_SEC", 300.0),
            retry_count=_env_int("RETRY_COUNT", 3),
            retry_backoff_sec=_env_float("RETRY_BACKOFF_SEC", 2.0),
            tls_mode=_env_str("TLS_MODE", "system").lower(),
            tls_ca_bundle=_env_str("TLS_CA_BUNDLE", ""),
            paper_size=_env_str("PAPER_SIZE", "A4").upper(),
        )

    def build_ssl(self) -> Any:
        if self.tls_mode == "off":
            return False
        if self.tls_mode == "bundle":
            if not self.tls_ca_bundle:
                raise RuntimeError("TLS_MODE='bundle' requires TLS_CA_BUNDLE")
            return ssl.create_default_context(cafile=self.tls_ca_bundle)
        if self.tls_mode == "truststore":
            try:
                import truststore  # type: ignore
                return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except Exception as e:
                print(
                    f"[WARN] truststore failed ({type(e).__name__}: {e!r}); "
                    f"falling back to combined bundle",
                    file=sys.stderr,
                )
        # system: combined certifi + OS-store roots
        try:
            from core.tls_helper import build_combined_ca_bundle
            return ssl.create_default_context(
                cafile=build_combined_ca_bundle()
            )
        except Exception as e:
            print(
                f"[WARN] combined bundle failed ({type(e).__name__}: {e!r}); "
                f"falling back to certifi",
                file=sys.stderr,
            )
            try:
                import certifi  # type: ignore
                return ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                return ssl.create_default_context()

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.pat:
            errors.append("PAT is empty")
        if not self.organization:
            errors.append("Organization is empty")
        if not self.project:
            errors.append("Project is empty")
        if not self.work_item_ids:
            errors.append("No Work Item IDs supplied")
        if self.tls_mode not in ("system", "truststore", "bundle", "off"):
            errors.append(f"Invalid tls_mode='{self.tls_mode}'")
        if self.paper_size not in ("A4", "LETTER"):
            errors.append(f"Invalid paper_size='{self.paper_size}'")
        return errors
