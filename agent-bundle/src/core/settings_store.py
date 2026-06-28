"""
settings_store.py
Global settings for the Testing Toolkit.

Secret values (the LLM API key, PAT, base URL) go in the OS keyring
first; fallback uses machine-locked encryption (Windows DPAPI via
CryptProtectData, or HMAC-derived key on other platforms). The encrypted
file is non-portable: copying it to another user/machine produces garbage.

Non-secret values (model ids, organization, project prefix) live in a
plain JSON file in the workspace.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Final

from core.app_config import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_FAST_MODEL,
    DEFAULT_MODEL,
    DEFAULT_PROJECT_PREFIX,
    SETTINGS_PATH,
)
from core.pat_store import load_pat, save_pat  # reused PAT storage

# ---------------------------------------------------------------------
# Machine-locked encryption (DPAPI on Windows, derived-key elsewhere)
# ---------------------------------------------------------------------

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
    seed = f"{platform.node()}:{user}:testing_toolkit_v2"
    return hashlib.pbkdf2_hmac("sha256", seed.encode(), b"tt_salt_v2", 100000)


def _fallback_encrypt(data: bytes) -> bytes:
    """XOR-based encryption with machine-derived key (portable fallback)."""
    key = _machine_key()
    # Simple repeating-key XOR with HMAC integrity tag
    extended_key = (key * ((len(data) // len(key)) + 1))[:len(data)]
    encrypted = bytes(a ^ b for a, b in zip(data, extended_key))
    tag = hashlib.sha256(key + data).digest()[:16]
    return tag + encrypted


def _fallback_decrypt(data: bytes) -> bytes | None:
    """Decrypt XOR-based encryption with machine-derived key."""
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


def _encrypt_value(value: str) -> str:
    """Encrypt a string value, return base64-encoded result."""
    data = value.encode("utf-8")
    if sys.platform.startswith("win"):
        enc = _dpapi_encrypt(data)
        if enc is not None:
            return "DPAPI:" + base64.b64encode(enc).decode("ascii")
    enc = _fallback_encrypt(data)
    return "MKEY:" + base64.b64encode(enc).decode("ascii")


def _decrypt_value(stored: str) -> str | None:
    """Decrypt a previously encrypted value."""
    if stored.startswith("DPAPI:"):
        raw = base64.b64decode(stored[6:])
        result = _dpapi_decrypt(raw)
        return result.decode("utf-8") if result else None
    elif stored.startswith("MKEY:"):
        raw = base64.b64decode(stored[5:])
        result = _fallback_decrypt(raw)
        return result.decode("utf-8") if result else None
    # Legacy plain base64 (migration path)
    try:
        return base64.b64decode(stored.encode("ascii")).decode("utf-8")
    except Exception:
        return None


# ---------------------------------------------------------------------
# API key secure storage (keyring -> encrypted file fallback)
# ---------------------------------------------------------------------
_API_SERVICE: Final[str] = "testing_toolkit_llm"
_API_USERNAME: Final[str] = "default"
_API_FALLBACK_PATH: Final[Path] = Path.home() / ".testing_toolkit_apikey"


def _keyring_get(service: str, user: str) -> str | None:
    try:
        import keyring  # type: ignore
        val = keyring.get_password(service, user)
        return val if val else None
    except Exception:
        return None


def _keyring_set(service: str, user: str, value: str) -> bool:
    try:
        import keyring  # type: ignore
        keyring.set_password(service, user, value)
        return True
    except Exception:
        return False


def _keyring_delete(service: str, user: str) -> bool:
    try:
        import keyring  # type: ignore
        keyring.delete_password(service, user)
        return True
    except Exception:
        return False


def _file_get(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="ascii").strip()
        if not raw:
            return None
        return _decrypt_value(raw)
    except Exception:
        return None


def _file_set(path: Path, value: str) -> bool:
    try:
        encrypted = _encrypt_value(value)
        path.write_text(encrypted, encoding="ascii")
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _file_delete(path: Path) -> bool:
    try:
        if path.exists():
            path.unlink()
        return True
    except Exception:
        return False


def load_api_key() -> str | None:
    """Return the stored LLM API key, or None."""
    val = _keyring_get(_API_SERVICE, _API_USERNAME)
    if val:
        return val
    return _file_get(_API_FALLBACK_PATH)


def save_api_key(key: str) -> bool:
    """Persist the LLM API key. Returns True if any backend took it."""
    if not key or not key.strip():
        return False
    key = key.strip()
    if _keyring_set(_API_SERVICE, _API_USERNAME, key):
        return True
    return _file_set(_API_FALLBACK_PATH, key)


def clear_api_key() -> bool:
    a = _keyring_delete(_API_SERVICE, _API_USERNAME)
    b = _file_delete(_API_FALLBACK_PATH)
    return a or b


# ---------------------------------------------------------------------
# Non-secret settings (plain JSON)
# ---------------------------------------------------------------------
KEY_BASE_URL:   Final[str] = "anthropic_base_url"
KEY_MODEL:          Final[str] = "anthropic_model"
KEY_FAST_MODEL:     Final[str] = "anthropic_fast_model"
KEY_FALLBACK_MODEL: Final[str] = "anthropic_fallback_model"
KEY_ORG:            Final[str] = "organization"
KEY_PREFIX:     Final[str] = "project_prefix"
KEY_TLS_MODE:   Final[str] = "tls_mode"
KEY_TOUR_DONE:  Final[str] = "tour_completed"

_DEFAULTS: Final[dict[str, str]] = {
    KEY_BASE_URL:   DEFAULT_ANTHROPIC_BASE_URL,
    KEY_MODEL:          DEFAULT_MODEL,
    KEY_FAST_MODEL:     DEFAULT_FAST_MODEL,
    KEY_FALLBACK_MODEL: DEFAULT_FALLBACK_MODEL,
    KEY_ORG:        "",
    KEY_PREFIX:     DEFAULT_PROJECT_PREFIX,
    KEY_TLS_MODE:   "system",
}


def _load_all() -> dict[str, str]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _write_all(data: dict[str, str]) -> bool:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8"
        )
        return True
    except Exception:
        return False


def get_setting(key: str, default: str | None = None) -> str:
    """Return a setting, falling back to the module default then the
    supplied default. Always a stripped string."""
    val = _load_all().get(key, "")
    if val:
        return str(val).strip()
    if default is not None:
        return default
    return _DEFAULTS.get(key, "")


def save_setting(key: str, value: str) -> bool:
    data = _load_all()
    data[key] = (value or "").strip()
    return _write_all(data)


def save_settings(values: dict[str, str]) -> bool:
    data = _load_all()
    for k, v in values.items():
        data[k] = (v or "").strip()
    return _write_all(data)


def get_tour_completed() -> bool:
    """True once the user has finished or skipped the first-run guided tour.
    Persisted server-side so it survives the web origin/port changing between
    launches (which would otherwise wipe the browser localStorage copy)."""
    return get_setting(KEY_TOUR_DONE, "").strip().lower() in ("1", "true", "yes")


def set_tour_completed(value: bool) -> bool:
    return save_setting(KEY_TOUR_DONE, "true" if value else "false")


# ---------------------------------------------------------------------
# First-run detection
# ---------------------------------------------------------------------
def is_configured() -> bool:
    """True when the minimum one-time setup is complete: an LLM API
    key, an ADO PAT, and an organization are all present. (Manual mode
    can run without the API key, but first-run setup still collects it;
    the user may skip it and the app falls back to manual mode.)"""
    has_pat = bool((load_pat() or "").strip())
    has_org = bool(get_setting(KEY_ORG).strip())
    return has_pat and has_org


def has_api_key() -> bool:
    return bool((load_api_key() or "").strip())


# Convenience wrapper so callers do not need to import pat_store too.
def load_pat_value() -> str:
    return (load_pat() or "").strip()


def save_pat_value(pat: str) -> bool:
    return save_pat(pat)


def build_runtime_config() -> "RuntimeConfig":
    """Construct a RuntimeConfig from stored settings (PAT + TLS mode).
    org/project/work_item_ids are filled in by the caller per task."""
    from core.runtime_config import RuntimeConfig
    cfg = RuntimeConfig.from_env_defaults()
    cfg.pat = load_pat_value()
    cfg.organization = get_setting(KEY_ORG)
    cfg.tls_mode = get_setting(KEY_TLS_MODE) or "system"
    return cfg


def build_llm_client(cfg: "RuntimeConfig | None" = None):
    """Return a configured LLMClient, or None when no API key is
    stored (the signal that the app must use Manual Mode)."""
    from core.anthropic_client import LLMClient
    key = (load_api_key() or "").strip()
    if not key:
        return None
    if cfg is None:
        cfg = build_runtime_config()
    return LLMClient(
        api_key=key,
        base_url=get_setting(KEY_BASE_URL),
        ssl_verify=cfg.build_ssl(),
    )


# Backwards-compat alias
build_anthropic_client = build_llm_client


def model_pair() -> tuple[str, str]:
    """(primary_model, fast_model). fast_model falls back to primary."""
    primary = get_setting(KEY_MODEL)
    fast = _load_all().get(KEY_FAST_MODEL, "").strip() or primary
    return primary, fast


def model_triple() -> tuple[str, str, str]:
    """(primary, fast, fallback). Each falls back to primary if blank."""
    primary = get_setting(KEY_MODEL)
    fast = _load_all().get(KEY_FAST_MODEL, "").strip() or primary
    fallback = _load_all().get(KEY_FALLBACK_MODEL, "").strip() or primary
    return primary, fast, fallback


def runtime_summary() -> dict[str, Any]:
    """Non-secret snapshot for logging / about dialogs (no secrets)."""
    return {
        "base_url": get_setting(KEY_BASE_URL),
        "model": get_setting(KEY_MODEL),
        "fast_model": get_setting(KEY_FAST_MODEL) or get_setting(KEY_MODEL),
        "fallback_model": get_setting(KEY_FALLBACK_MODEL) or get_setting(KEY_MODEL),
        "organization": get_setting(KEY_ORG),
        "project_prefix": get_setting(KEY_PREFIX),
        "tls_mode": get_setting(KEY_TLS_MODE),
        "has_api_key": has_api_key(),
        "has_pat": bool(load_pat_value()),
    }
