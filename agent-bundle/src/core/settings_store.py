"""Persist user-managed work-item source settings and UI preferences.

ADO/JIRA secrets use the OS keyring first, with a machine-locked encrypted
file fallback. AI endpoint, credential, and model selection are intentionally
central configuration in :mod:`core.app_config`; they are never user settings.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from core.app_config import DEFAULT_PROJECT_PREFIX, SETTINGS_PATH
from core.pat_store import load_pat, save_pat  # reused PAT storage

if TYPE_CHECKING:
    from core.runtime_config import RuntimeConfig

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
# Source credential storage (keyring -> encrypted file fallback)
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Non-secret source settings (plain JSON)
# ---------------------------------------------------------------------
KEY_ORG: Final[str] = "organization"
KEY_PREFIX:     Final[str] = "project_prefix"
KEY_TLS_MODE:   Final[str] = "tls_mode"

# --- JIRA source (non-secret parts; the JIRA PAT lives in the keyring) ---
KEY_JIRA_URL:    Final[str] = "jira_url"
KEY_JIRA_USER:   Final[str] = "jira_user"
KEY_JIRA_PREFIX: Final[str] = "jira_project_prefix"

_DEFAULTS: Final[dict[str, str]] = {
    KEY_ORG:        "",
    KEY_PREFIX:     DEFAULT_PROJECT_PREFIX,
    KEY_TLS_MODE:   "system",
    KEY_JIRA_URL:    "",
    KEY_JIRA_USER:   "",
    KEY_JIRA_PREFIX: "",
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


# ---------------------------------------------------------------------
# First-run detection
# ---------------------------------------------------------------------
def is_configured() -> bool:
    """True when Azure DevOps is usable (PAT + organization)."""
    has_pat = bool((load_pat() or "").strip())
    has_org = bool(get_setting(KEY_ORG).strip())
    return has_pat and has_org


def has_api_key() -> bool:
    """True when the centrally managed AI service credential is available."""
    from core.app_config import LLM_API_KEY

    return bool(LLM_API_KEY)


# Convenience wrapper so callers do not need to import pat_store too.
def load_pat_value() -> str:
    return (load_pat() or "").strip()


def save_pat_value(pat: str) -> bool:
    return save_pat(pat)


# ---------------------------------------------------------------------
# JIRA PAT secure storage (keyring -> encrypted file fallback), kept
# separate from the ADO PAT so both sources can be configured at once.
# ---------------------------------------------------------------------
_JIRA_SERVICE: Final[str] = "testing_toolkit_jira"
_JIRA_USERNAME: Final[str] = "default"
_JIRA_FALLBACK_PATH: Final[Path] = Path.home() / ".testing_toolkit_jira_pat"


def load_jira_pat() -> str:
    """Return the stored JIRA PAT, or an empty string."""
    val = _keyring_get(_JIRA_SERVICE, _JIRA_USERNAME)
    if val:
        return val.strip()
    return (_file_get(_JIRA_FALLBACK_PATH) or "").strip()


def save_jira_pat(pat: str) -> bool:
    """Persist the JIRA PAT. Returns True if any backend took it."""
    if not pat or not pat.strip():
        return False
    pat = pat.strip()
    if _keyring_set(_JIRA_SERVICE, _JIRA_USERNAME, pat):
        return True
    return _file_set(_JIRA_FALLBACK_PATH, pat)


def clear_jira_pat() -> bool:
    a = _keyring_delete(_JIRA_SERVICE, _JIRA_USERNAME)
    b = _file_delete(_JIRA_FALLBACK_PATH)
    return a or b


def is_jira_configured() -> bool:
    """True when a JIRA URL, username, and PAT are all present."""
    return bool(
        get_setting(KEY_JIRA_URL).strip()
        and get_setting(KEY_JIRA_USER).strip()
        and load_jira_pat()
    )


def build_jira_runtime_config() -> "RuntimeConfig":
    """RuntimeConfig carrying the JIRA PAT + TLS mode. The JIRA URL/user are
    passed to the jira.* functions explicitly, not via RuntimeConfig."""
    from core.runtime_config import RuntimeConfig
    cfg = RuntimeConfig.from_env_defaults()
    cfg.pat = load_jira_pat()
    cfg.tls_mode = get_setting(KEY_TLS_MODE) or "system"
    return cfg


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
    """Build a client from centrally managed AI configuration only."""
    from core.anthropic_client import LLMClient
    from core.app_config import LLM_API_KEY, LLM_BASE_URL, LLM_PROVIDER_FORMAT

    if not LLM_API_KEY:
        return None
    if cfg is None:
        cfg = build_runtime_config()
    return LLMClient(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        ssl_verify=cfg.build_ssl(),
        provider_format=LLM_PROVIDER_FORMAT,
    )


def runtime_summary() -> dict[str, Any]:
    """Non-secret source/runtime snapshot for diagnostics."""
    from core.app_config import credential_protection_state

    return {
        "organization": get_setting(KEY_ORG),
        "project_prefix": get_setting(KEY_PREFIX),
        "tls_mode": get_setting(KEY_TLS_MODE),
        "ai_service_configured": has_api_key(),
        "ai_credential_protection": credential_protection_state(),
        "has_pat": bool(load_pat_value()),
    }
