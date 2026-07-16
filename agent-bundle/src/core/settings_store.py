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

def _dpapi_call(func_name: str, data: bytes) -> bytes | None:
    """Call a Windows DPAPI function (CryptProtectData or CryptUnprotectData)."""
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        p_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
        p_out = DATA_BLOB()
        fn = getattr(ctypes.windll.crypt32, func_name)
        if fn(ctypes.byref(p_in), None, None, None, None, 0, ctypes.byref(p_out)):
            result = ctypes.string_at(p_out.pbData, p_out.cbData)
            ctypes.windll.kernel32.LocalFree(p_out.pbData)
            return result
    except Exception:
        pass
    return None


def _dpapi_encrypt(data: bytes) -> bytes | None:
    """Encrypt with Windows DPAPI (CurrentUser scope). Non-portable."""
    return _dpapi_call("CryptProtectData", data)


def _dpapi_decrypt(data: bytes) -> bytes | None:
    """Decrypt with Windows DPAPI (CurrentUser scope)."""
    return _dpapi_call("CryptUnprotectData", data)


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
        path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = _encrypt_value(value)
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(encrypted, encoding="ascii")
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        os.replace(tmp, path)
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


def _migrate_legacy_settings() -> None:
    """One-time move of settings.json from the old workspace location into the
    stable config dir, so connection details survive updates. Best-effort and
    idempotent: it only copies when the new file is absent and the legacy file
    exists."""
    try:
        from core.app_config import LEGACY_SETTINGS_PATH

        if SETTINGS_PATH.exists() or not LEGACY_SETTINGS_PATH.exists():
            return
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            LEGACY_SETTINGS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except Exception:
        pass


_settings_cache: dict[str, str] | None = None
_settings_cache_mtime: float = 0.0


def _load_all() -> dict[str, str]:
    global _settings_cache, _settings_cache_mtime
    if not SETTINGS_PATH.exists():
        _migrate_legacy_settings()
    if not SETTINGS_PATH.exists():
        return {}
    try:
        mtime = SETTINGS_PATH.stat().st_mtime
        if _settings_cache is not None and mtime == _settings_cache_mtime:
            return _settings_cache
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            result = {str(k): str(v) for k, v in data.items()}
            _settings_cache = result
            _settings_cache_mtime = mtime
            return result
    except Exception:
        pass
    return {}


def _write_all(data: dict[str, str]) -> bool:
    global _settings_cache, _settings_cache_mtime
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8"
        )
        os.replace(tmp, SETTINGS_PATH)
        _settings_cache = data
        _settings_cache_mtime = SETTINGS_PATH.stat().st_mtime
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


def _config_dir() -> Path:
    override = (os.environ.get("TT_CONFIG_DIR") or "").strip()
    if override:
        try:
            return Path(override).expanduser()
        except (OSError, ValueError):
            pass
    return Path.home() / ".testing_toolkit"


def _jira_fallback_path() -> Path:
    """JIRA PAT fallback file in the stable config dir (survives updates), with
    one-time migration from the legacy home-directory location."""
    legacy = Path.home() / ".testing_toolkit_jira_pat"
    try:
        target = _config_dir() / "jira_pat.enc"
        if not target.exists() and legacy.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(legacy.read_bytes())
            except OSError:
                return legacy
        return target
    except Exception:
        return legacy


_JIRA_FALLBACK_PATH: Final[Path] = _jira_fallback_path()


def load_jira_pat() -> str:
    """Return the stored JIRA PAT, or an empty string.

    Self-heals: replicates to whichever backend is missing so both
    survive reinstalls and credential-manager resets.
    """
    kr = (_keyring_get(_JIRA_SERVICE, _JIRA_USERNAME) or "").strip()
    fi = (_file_get(_JIRA_FALLBACK_PATH) or "").strip()
    if kr and not fi:
        _file_set(_JIRA_FALLBACK_PATH, kr)
    elif fi and not kr:
        _keyring_set(_JIRA_SERVICE, _JIRA_USERNAME, fi)
    return kr or fi


def save_jira_pat(pat: str) -> bool:
    """Persist the JIRA PAT to both keyring and encrypted file for redundancy."""
    if not pat or not pat.strip():
        return False
    pat = pat.strip()
    keyring_ok = _keyring_set(_JIRA_SERVICE, _JIRA_USERNAME, pat)
    file_ok = _file_set(_JIRA_FALLBACK_PATH, pat)
    return keyring_ok or file_ok


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
