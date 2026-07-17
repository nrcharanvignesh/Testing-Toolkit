"""
kb_crypto.py
File-level encryption for KB data at rest. All KB source data (chunks,
vectors, BM25 index, manifest) is stored encrypted on disk so that
sensitive requirement text is not accessible to casual inspection.

Uses the same DPAPI (Windows) / machine-derived-key (portable) mechanism
as settings_store.py. Generated artifacts (test cases, reports) remain
plaintext since they are user-facing output.

File format: 4-byte magic "KBEV" + encrypted payload.
Magic header allows fast detection of encrypted vs plaintext files.

ASCII-only; fully type-hinted.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Final

_MAGIC: Final[bytes] = b"KBEV"


# -------------------------------------------------------------------------
# Encryption primitives (reuses same approach as settings_store)
# -------------------------------------------------------------------------
def _dpapi_encrypt(data: bytes) -> bytes | None:
    """Encrypt with Windows DPAPI (CurrentUser scope)."""
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
    seed = f"{platform.node()}:{user}:testing_toolkit_kb_v1"
    return hashlib.pbkdf2_hmac("sha256", seed.encode(), b"kb_salt_v1", 100000)


def _fallback_encrypt(data: bytes) -> bytes:
    """XOR with machine-derived key + HMAC integrity tag."""
    key = _machine_key()
    extended_key = (key * ((len(data) // len(key)) + 1))[:len(data)]
    encrypted = bytes(a ^ b for a, b in zip(data, extended_key))
    tag = hashlib.sha256(key + data).digest()[:16]
    return tag + encrypted


def _fallback_decrypt(data: bytes) -> bytes | None:
    """Reverse XOR; verify HMAC integrity."""
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


# -------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------
def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt arbitrary bytes. Returns magic + encrypted payload."""
    if sys.platform.startswith("win"):
        enc = _dpapi_encrypt(data)
        if enc is not None:
            return _MAGIC + b"\x01" + enc  # \x01 = DPAPI variant
    enc = _fallback_encrypt(data)
    return _MAGIC + b"\x02" + enc  # \x02 = machine-key variant


def decrypt_bytes(data: bytes) -> bytes | None:
    """Decrypt bytes previously encrypted with encrypt_bytes.
    Returns None if magic is missing, data is corrupted, or the
    machine/user context has changed (different user/machine)."""
    if not data or len(data) < 6:
        return None
    if data[:4] != _MAGIC:
        return None
    variant = data[4:5]
    payload = data[5:]
    if variant == b"\x01":
        return _dpapi_decrypt(payload)
    elif variant == b"\x02":
        return _fallback_decrypt(payload)
    return None


def is_encrypted(data: bytes) -> bool:
    """Check if bytes start with our magic header."""
    return len(data) >= 5 and data[:4] == _MAGIC


def write_encrypted(path: Path | str, data: bytes) -> None:
    """Encrypt and write bytes to a file (atomic: temp+rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_bytes(encrypt_bytes(data))
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_decrypted(path: Path | str) -> bytes | None:
    """Read and decrypt a file. Returns None if file doesn't exist,
    is not encrypted, or decryption fails."""
    path = Path(path)
    if not path.exists():
        return None
    raw = path.read_bytes()
    if not is_encrypted(raw):
        return raw  # plaintext (legacy/migration)
    return decrypt_bytes(raw)


def write_encrypted_text(path: Path | str, text: str) -> None:
    """Convenience: encrypt utf-8 text to file."""
    write_encrypted(path, text.encode("utf-8"))


def read_decrypted_text(path: Path | str) -> str | None:
    """Convenience: read and decrypt utf-8 text from file."""
    data = read_decrypted(path)
    if data is None:
        return None
    return data.decode("utf-8")
