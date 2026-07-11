"""OS-bound storage and release-envelope migration for GenAI credentials.

Windows uses DPAPI CurrentUser directly. macOS and Linux use the system keyring
only when its active backend reports secure priority. If unavailable, runtime
falls back to the authenticated release envelope without writing plaintext.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Final

from core.credential_envelope import CredentialEnvelopeError, open_credentials, validate_credentials

_SERVICE: Final[str] = "TestingToolkit.GenAI.ReleaseCredential.v2"
_ACCOUNT: Final[str] = "central-service"
_MAX_STORED_BYTES: Final[int] = 16_384


def _workspace() -> Path:
    override = (os.environ.get("TT_WORKSPACE_DIR") or "").strip()
    return Path(override).expanduser() if override else Path.home() / "TestingToolkitWeb"


def _dpapi_path() -> Path:
    return _workspace() / ".credentials" / "genai.dpapi"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(data: bytes) -> bytes | None:
    if sys.platform != "win32" or not data:
        return None
    try:
        inp_buf = ctypes.create_string_buffer(data, len(data))
        inp = _Blob(len(data), ctypes.cast(inp_buf, ctypes.POINTER(ctypes.c_char)))
        out = _Blob()
        # CRYPTPROTECT_UI_FORBIDDEN: fail rather than prompting in agent startup.
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(inp), "Testing Toolkit GenAI", None, None, None, 0x1, ctypes.byref(out)
        ):
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return None


def _dpapi_unprotect(data: bytes) -> bytes | None:
    if sys.platform != "win32" or not data or len(data) > _MAX_STORED_BYTES:
        return None
    try:
        inp_buf = ctypes.create_string_buffer(data, len(data))
        inp = _Blob(len(data), ctypes.cast(inp_buf, ctypes.POINTER(ctypes.c_char)))
        out = _Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out)
        ):
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return None


def _write_private(path: Path, data: bytes) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        temp = Path(temp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return True
        finally:
            temp.unlink(missing_ok=True)
    except OSError:
        return False


def _secure_keyring():
    if sys.platform == "win32":
        return None  # DPAPI is explicit and deterministic on Windows.
    try:
        import keyring
        backend = keyring.get_keyring()
        # Fail closed on null/fail/plaintext backends. Real Keychain and Secret
        # Service backends report positive priority.
        priority = float(getattr(backend, "priority", 0))
        module = type(backend).__module__.lower()
        if priority <= 0 or any(token in module for token in ("fail", "null", "plaintext")):
            return None
        return keyring
    except Exception:
        return None


def _encode(values: dict[str, str], release_id: str) -> str:
    body = {"version": 2, "release_id": release_id, "credentials": validate_credentials(values)}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _decode(text: str | bytes | None) -> tuple[dict[str, str], str] | None:
    if not text or len(text) > _MAX_STORED_BYTES:
        return None
    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        body = json.loads(text)
        if not isinstance(body, dict) or set(body) != {"version", "release_id", "credentials"}:
            return None
        release_id = body["release_id"]
        if body["version"] != 2 or not isinstance(release_id, str) or len(release_id) != 64:
            return None
        return validate_credentials(body["credentials"]), release_id
    except Exception:
        return None


def _load_os_bound() -> tuple[dict[str, str], str] | None:
    if sys.platform == "win32":
        try:
            clear = _dpapi_unprotect(_dpapi_path().read_bytes())
        except OSError:
            return None
        return _decode(clear)
    keyring = _secure_keyring()
    if keyring is None:
        return None
    try:
        return _decode(keyring.get_password(_SERVICE, _ACCOUNT))
    except Exception:
        return None


def _save_os_bound(values: dict[str, str], release_id: str) -> bool:
    encoded = _encode(values, release_id)
    if sys.platform == "win32":
        protected = _dpapi_protect(encoded.encode("utf-8"))
        return bool(protected and _write_private(_dpapi_path(), protected))
    keyring = _secure_keyring()
    if keyring is None:
        return False
    try:
        keyring.set_password(_SERVICE, _ACCOUNT, encoded)
        return True
    except Exception:
        return False


def load_release_credentials(envelope_path: Path) -> tuple[dict[str, str], str]:
    """Load current credentials and return ``(values, protection_state)``.

    A changed release envelope is authenticated before replacing a valid
    OS-bound value, so interrupted or corrupt updates cannot destroy the last
    working credential.
    """
    stored = _load_os_bound()
    try:
        try:
            envelope_path.chmod(0o600)
        except OSError:
            pass
        envelope = envelope_path.read_bytes()
        release_id = hashlib.sha256(envelope).hexdigest()
    except OSError:
        if stored:
            return stored[0], "os-bound"
        return {}, "unavailable"

    if stored and stored[1] == release_id:
        return stored[0], "os-bound"

    try:
        current = open_credentials(envelope)
    except CredentialEnvelopeError:
        if stored:
            return stored[0], "os-bound-stale-release"
        return {}, "invalid-envelope"

    if _save_os_bound(current, release_id):
        return current, "os-bound"
    return current, "release-envelope"
