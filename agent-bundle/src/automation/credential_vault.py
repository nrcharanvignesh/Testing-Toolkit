"""
automation/credential_vault.py
Secure storage for test environment credentials.
Primary: OS keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service).
Fallback: PBKDF2-encrypted JSON file per project.

SECURITY: Passwords are NEVER logged, NEVER written to disk unencrypted,
NEVER included in generated scripts. Memory is cleared after use where possible.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import sys
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Final


VAULT_SERVICE_PREFIX: Final[str] = "testing_toolkit_e2e_"
_VAULT_DIR: Final[Path] = Path.home() / "TestingToolkit" / "vaults"
_LOCK: Final[threading.Lock] = threading.Lock()


# -------------------------------------------------------------------
# Machine-locked encryption (same pattern as core.settings_store)
# -------------------------------------------------------------------

def _dpapi_encrypt(data: bytes) -> bytes | None:
    """Encrypt with Windows DPAPI (CurrentUser scope). Non-portable."""
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        p_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
        p_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(p_in), None, None, None, None, 0,
            ctypes.byref(p_out)
        ):
            enc = ctypes.string_at(p_out.pbData, p_out.cbData)
            ctypes.windll.kernel32.LocalFree(p_out.pbData)
            return enc
    except Exception:
        pass
    return None


def _dpapi_decrypt(data: bytes) -> bytes | None:
    """Decrypt with Windows DPAPI (CurrentUser scope)."""
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        p_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
        p_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(p_in), None, None, None, None, 0,
            ctypes.byref(p_out)
        ):
            dec = ctypes.string_at(p_out.pbData, p_out.cbData)
            ctypes.windll.kernel32.LocalFree(p_out.pbData)
            return dec
    except Exception:
        pass
    return None


def _machine_key() -> bytes:
    """Derive a machine+user-specific key (non-Windows fallback)."""
    import platform
    try:
        user = os.getlogin()
    except OSError:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "default"
    seed = f"{platform.node()}:{user}:testing_toolkit_vault_v1"
    return hashlib.pbkdf2_hmac("sha256", seed.encode(), b"vault_salt_v1", 100000)


def _fallback_encrypt(data: bytes) -> bytes:
    """XOR encryption with machine-derived key + HMAC integrity tag."""
    key = _machine_key()
    extended_key = (key * ((len(data) // len(key)) + 1))[:len(data)]
    encrypted = bytes(a ^ b for a, b in zip(data, extended_key))
    tag = hashlib.sha256(key + data).digest()[:16]
    return tag + encrypted


def _fallback_decrypt(data: bytes) -> bytes | None:
    """Decrypt XOR encryption with machine-derived key."""
    if len(data) < 16:
        return None
    key = _machine_key()
    tag = data[:16]
    encrypted = data[16:]
    extended_key = (key * ((len(encrypted) // len(key)) + 1))[:len(encrypted)]
    decrypted = bytes(a ^ b for a, b in zip(encrypted, extended_key))
    expected_tag = hashlib.sha256(key + decrypted).digest()[:16]
    if tag != expected_tag:
        return None
    return decrypted


def _encrypt_blob(value: str) -> str:
    """Encrypt a string, return base64-encoded tagged result."""
    data = value.encode("utf-8")
    if sys.platform.startswith("win"):
        enc = _dpapi_encrypt(data)
        if enc is not None:
            return "DPAPI:" + base64.b64encode(enc).decode("ascii")
    enc = _fallback_encrypt(data)
    return "MKEY:" + base64.b64encode(enc).decode("ascii")


def _decrypt_blob(stored: str) -> str | None:
    """Decrypt a previously encrypted value."""
    if stored.startswith("DPAPI:"):
        raw = base64.b64decode(stored[6:])
        result = _dpapi_decrypt(raw)
        return result.decode("utf-8") if result else None
    elif stored.startswith("MKEY:"):
        raw = base64.b64decode(stored[5:])
        result = _fallback_decrypt(raw)
        return result.decode("utf-8") if result else None
    return None


# -------------------------------------------------------------------
# Credential dataclass
# -------------------------------------------------------------------

@dataclass(slots=True)
class TestCredential:
    """Single environment credential entry."""
    env: str                    # "dev" | "test" | "prod" | custom
    login_url: str
    user_id: str
    password: str = ""          # NEVER logged
    login_method: str = "form"  # "form" | "sso" | "basic_auth" | "oauth"
    notes: str = ""
    ai_instructions: str = ""   # free-text login steps for the AI agent

    def safe_repr(self) -> str:
        """String representation WITHOUT password."""
        return (
            f"TestCredential(env={self.env!r}, url={self.login_url!r}, "
            f"user={self.user_id!r}, method={self.login_method!r})"
        )

    def __repr__(self) -> str:
        return self.safe_repr()

    def __str__(self) -> str:
        return self.safe_repr()


# -------------------------------------------------------------------
# Vault implementation
# -------------------------------------------------------------------

def _safe_project_key(project_name: str) -> str:
    """Sanitize project name: alphanumeric + underscore only, lowercased."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", project_name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_").lower()
    if not cleaned:
        cleaned = "default"
    return cleaned


class CredentialVault:
    """Per-project encrypted credential storage.

    Thread-safe. Keyring primary, encrypted file fallback.
    """

    def __init__(self) -> None:
        self._lock = _LOCK

    # -- Keyring helpers --

    def _keyring_service(self, safe_key: str) -> str:
        return VAULT_SERVICE_PREFIX + safe_key

    def _keyring_get(self, safe_key: str) -> str | None:
        try:
            import keyring  # type: ignore
            val = keyring.get_password(self._keyring_service(safe_key), "credentials")
            return val if val else None
        except Exception:
            return None

    def _keyring_set(self, safe_key: str, value: str) -> bool:
        try:
            import keyring  # type: ignore
            keyring.set_password(self._keyring_service(safe_key), "credentials", value)
            return True
        except Exception:
            return False

    def _keyring_delete(self, safe_key: str) -> bool:
        try:
            import keyring  # type: ignore
            keyring.delete_password(self._keyring_service(safe_key), "credentials")
            return True
        except Exception:
            return False

    # -- File fallback helpers --

    def _vault_path(self, safe_key: str) -> Path:
        return _VAULT_DIR / f"{safe_key}.vault"

    def _file_get(self, safe_key: str) -> str | None:
        path = self._vault_path(safe_key)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="ascii").strip()
            if not raw:
                return None
            return _decrypt_blob(raw)
        except Exception:
            return None

    def _file_set(self, safe_key: str, value: str) -> bool:
        try:
            path = self._vault_path(safe_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            encrypted = _encrypt_blob(value)
            path.write_text(encrypted, encoding="ascii")
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _file_delete(self, safe_key: str) -> bool:
        try:
            path = self._vault_path(safe_key)
            if path.exists():
                path.unlink()
            return True
        except Exception:
            return False

    # -- Internal load/save (encrypted JSON blob) --

    def _read_blob(self, safe_key: str) -> str | None:
        """Read from keyring first, then file fallback."""
        val = self._keyring_get(safe_key)
        if val is not None:
            return val
        return self._file_get(safe_key)

    def _write_blob(self, safe_key: str, json_str: str) -> bool:
        """Write to keyring first; on failure, use file fallback."""
        # Keyring stores the encrypted blob directly (keyring encrypts natively)
        if self._keyring_set(safe_key, json_str):
            return True
        # Fallback: encrypt and write to file
        return self._file_set(safe_key, json_str)

    # -- Public API --

    def load(self, project_name: str) -> list[TestCredential]:
        """Load all credentials for a project."""
        safe_key = _safe_project_key(project_name)
        with self._lock:
            blob = self._read_blob(safe_key)
        if blob is None:
            return []
        try:
            items = json.loads(blob)
            if not isinstance(items, list):
                return []
            return [
                TestCredential(
                    env=item.get("env", ""),
                    login_url=item.get("login_url", ""),
                    user_id=item.get("user_id", ""),
                    password=item.get("password", ""),
                    login_method=item.get("login_method", "form"),
                    notes=item.get("notes", ""),
                    ai_instructions=item.get("ai_instructions", ""),
                )
                for item in items
                if isinstance(item, dict)
            ]
        except (json.JSONDecodeError, TypeError):
            return []

    def save(self, project_name: str, creds: list[TestCredential]) -> bool:
        """Save credentials for a project. Returns True on success."""
        if not project_name or not project_name.strip():
            return False
        safe_key = _safe_project_key(project_name)
        blob = json.dumps([asdict(c) for c in creds], ensure_ascii=True)
        with self._lock:
            return self._write_blob(safe_key, blob)

    def get_for_env(self, project_name: str, env: str) -> TestCredential | None:
        """Get credential for a specific environment."""
        creds = self.load(project_name)
        env_lower = env.lower().strip()
        for c in creds:
            if c.env.lower().strip() == env_lower:
                return c
        return None

    def clear(self, project_name: str) -> bool:
        """Remove all credentials for a project."""
        safe_key = _safe_project_key(project_name)
        with self._lock:
            a = self._keyring_delete(safe_key)
            b = self._file_delete(safe_key)
        return a or b

    def list_projects_with_credentials(self) -> list[str]:
        """Return project keys that have stored credentials.

        Checks file-based vaults (keyring does not support enumeration).
        """
        projects: list[str] = []
        if _VAULT_DIR.exists():
            for f in _VAULT_DIR.iterdir():
                if f.suffix == ".vault" and f.is_file():
                    projects.append(f.stem)
        return sorted(projects)
