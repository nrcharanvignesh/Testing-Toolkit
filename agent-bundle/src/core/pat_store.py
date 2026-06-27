"""
pat_store.py
Cross-platform secure storage for the ADO Personal Access Token.

Strategy:
  1. Try OS keyring (Windows Credential Manager / macOS Keychain /
     Linux Secret Service).
  2. On failure, fall back to a base64-encoded file in the user's home
     directory with 0600 permissions where supported.

Note: base64 is NOT encryption. It only prevents shoulder-surfing. The
keyring backend is used whenever available.
"""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path
from typing import Final

# Hardcoded paths
HOME_DIR: Final[Path] = Path.home()
FALLBACK_PATH: Final[Path] = HOME_DIR / ".ado_pdf_packager_token"
SERVICE_NAME: Final[str] = "ado_pdf_packager"
USERNAME: Final[str] = "default"


def _try_keyring_get() -> str | None:
    try:
        import keyring  # type: ignore
        val = keyring.get_password(SERVICE_NAME, USERNAME)
        return val if val else None
    except Exception:
        return None


def _try_keyring_set(token: str) -> bool:
    try:
        import keyring  # type: ignore
        keyring.set_password(SERVICE_NAME, USERNAME, token)
        return True
    except Exception:
        return False


def _try_keyring_delete() -> bool:
    try:
        import keyring  # type: ignore
        keyring.delete_password(SERVICE_NAME, USERNAME)
        return True
    except Exception:
        return False


def _file_get() -> str | None:
    if not FALLBACK_PATH.exists():
        return None
    try:
        raw = FALLBACK_PATH.read_text(encoding="ascii").strip()
        if not raw:
            return None
        return base64.b64decode(raw.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def _file_set(token: str) -> bool:
    try:
        encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
        FALLBACK_PATH.write_text(encoded, encoding="ascii")
        try:
            os.chmod(FALLBACK_PATH, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _file_delete() -> bool:
    try:
        if FALLBACK_PATH.exists():
            FALLBACK_PATH.unlink()
        return True
    except Exception:
        return False


def load_pat() -> str | None:
    """Return stored PAT or None if absent."""
    val = _try_keyring_get()
    if val:
        return val
    return _file_get()


def save_pat(token: str) -> bool:
    """Persist PAT. Returns True if any backend succeeded."""
    if not token or not token.strip():
        return False
    token = token.strip()
    if _try_keyring_set(token):
        return True
    return _file_set(token)


def clear_pat() -> bool:
    """Remove the stored PAT from all backends."""
    a = _try_keyring_delete()
    b = _file_delete()
    return a or b
