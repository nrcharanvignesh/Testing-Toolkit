"""Authenticated release envelope for centrally managed GenAI credentials.

The envelope prevents accidental disclosure and detects every modification.  The
wrapping material necessarily ships with this autonomous POC client; it is not a
substitute for a remote secret broker against a determined local administrator.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Final, Mapping
from urllib.parse import urlsplit

_FORMAT: Final[str] = "tt-credential-envelope"
_VERSION: Final[int] = 3
_AAD_V2: Final[bytes] = b"TestingToolkit/GenAI/release-envelope/v2"
_AAD_V3: Final[bytes] = b"TestingToolkit/GenAI/release-envelope/v3"
_KDF_V2: Final[str] = "scrypt-n32768-r8-p1"
_KDF_V3: Final[str] = "pbkdf2-sha256-i600000"
_MAX_ENVELOPE_BYTES: Final[int] = 16_384
_MAX_URL_CHARS: Final[int] = 2_048
_MAX_KEY_CHARS: Final[int] = 8_192
# Split application-bound wrapping material avoids one obvious literal. This is
# defense-in-depth only: all parts and the decryptor are present on the client.
_WRAP_PARTS: Final[tuple[bytes, ...]] = (
    b"TTK/2026/release/",
    b"GenAI/credential/",
    b"authenticated-envelope/v2",
)


class CredentialEnvelopeError(ValueError):
    """Safe, non-secret envelope validation/decryption failure."""


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64d(value: object, *, field: str, expected: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or len(value) > _MAX_ENVELOPE_BYTES:
        raise CredentialEnvelopeError(f"invalid {field}")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except Exception as exc:
        raise CredentialEnvelopeError(f"invalid {field}") from exc
    if expected is not None and len(raw) != expected:
        raise CredentialEnvelopeError(f"invalid {field} length")
    return raw


def _derive_key(salt: bytes, kdf: str) -> bytes:
    """Derive wrapping material using the envelope-declared algorithm.

    Version 3 uses PBKDF2 because OpenSSL's platform-specific scrypt memory
    ceiling can reject the valid v2 parameters on Windows while accepting the
    same envelope on Linux. V2 remains readable only for release migration.
    """
    material = b"".join(_WRAP_PARTS)
    try:
        if kdf == _KDF_V3:
            # Stdlib PBKDF2 (hashlib) is byte-identical to cryptography's
            # PBKDF2HMAC for the same params, needs no native binding, and cannot
            # fail from a missing/broken cryptography wheel on Windows. This is
            # the ONLY key-derivation path for shipped (v3) envelopes.
            import hashlib

            return hashlib.pbkdf2_hmac("sha256", material, salt, 600_000, dklen=32)
        if kdf == _KDF_V2:
            from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

            return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(material)
    except ImportError as exc:
        raise CredentialEnvelopeError("credential cryptography unavailable") from exc
    except Exception as exc:
        raise CredentialEnvelopeError("credential key derivation failed") from exc
    raise CredentialEnvelopeError("unsupported credential key derivation")


def _aesgcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    """Decrypt+verify AES-256-GCM, preferring the native binding.

    Falls back to a stdlib-only pure-Python implementation when the compiled
    ``cryptography`` wheel is missing or its native binding fails to load - a
    real failure mode on some locked-down Windows hosts. The fallback is
    byte-for-byte identical and cross-validated in the test suite.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except CredentialEnvelopeError:
        raise
    except Exception:
        # Native path unavailable/broken: use the dependency-free fallback so the
        # managed AI credential still decrypts. Any auth failure here raises
        # ValueError, which the caller maps to a safe envelope error.
        from core._aesgcm_fallback import decrypt as _pure_decrypt

        return _pure_decrypt(key, nonce, ciphertext, aad)


def validate_credentials(values: Mapping[str, object]) -> dict[str, str]:
    base_url = str(values.get("BASE_URL") or "").strip().rstrip("/")
    api_key = str(values.get("API_KEY") or "").strip()
    provider = str(values.get("LLM_PROVIDER_FORMAT") or "anthropic").strip().lower()
    try:
        parsed = urlsplit(base_url)
    except ValueError as exc:
        raise CredentialEnvelopeError("invalid service URL") from exc
    if (
        not base_url
        or len(base_url) > _MAX_URL_CHARS
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise CredentialEnvelopeError("service URL must be an HTTPS origin or path without credentials")
    if not api_key or len(api_key) > _MAX_KEY_CHARS or any(ord(ch) < 32 for ch in api_key):
        raise CredentialEnvelopeError("invalid service credential")
    if provider not in {"anthropic", "openai"}:
        raise CredentialEnvelopeError("unsupported provider format")
    return {"BASE_URL": base_url, "API_KEY": api_key, "LLM_PROVIDER_FORMAT": provider}


def seal_credentials(values: Mapping[str, object]) -> bytes:
    """Return a versioned AES-256-GCM envelope containing validated values."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise CredentialEnvelopeError("credential cryptography unavailable") from exc
    payload = validate_credentials(values)
    plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(_derive_key(salt, _KDF_V3)).encrypt(nonce, plaintext, _AAD_V3)
    envelope = {
        "format": _FORMAT,
        "version": _VERSION,
        "kdf": _KDF_V3,
        "cipher": "aes-256-gcm",
        "salt": _b64e(salt),
        "nonce": _b64e(nonce),
        "ciphertext": _b64e(ciphertext),
    }
    encoded = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    if len(encoded) > _MAX_ENVELOPE_BYTES:
        raise CredentialEnvelopeError("credential envelope too large")
    return encoded


def open_credentials(data: bytes) -> dict[str, str]:
    """Authenticate, decrypt, strictly validate, and return credential fields."""
    if not data or len(data) > _MAX_ENVELOPE_BYTES:
        raise CredentialEnvelopeError("invalid credential envelope size")
    try:
        outer = json.loads(data.decode("ascii"))
    except Exception as exc:
        raise CredentialEnvelopeError("invalid credential envelope encoding") from exc
    if not isinstance(outer, dict) or set(outer) != {
        "format", "version", "kdf", "cipher", "salt", "nonce", "ciphertext"
    }:
        raise CredentialEnvelopeError("invalid credential envelope schema")
    version = outer["version"]
    kdf = outer["kdf"]
    if (
        outer["format"] != _FORMAT
        or (version, kdf) not in {(_VERSION, _KDF_V3), (2, _KDF_V2)}
        or outer["cipher"] != "aes-256-gcm"
    ):
        raise CredentialEnvelopeError("unsupported credential envelope")
    aad = _AAD_V3 if version == _VERSION else _AAD_V2
    salt = _b64d(outer["salt"], field="salt", expected=16)
    nonce = _b64d(outer["nonce"], field="nonce", expected=12)
    ciphertext = _b64d(outer["ciphertext"], field="ciphertext")
    try:
        key = _derive_key(salt, kdf)
        plaintext = _aesgcm_decrypt(key, nonce, ciphertext, aad)
        payload = json.loads(plaintext.decode("utf-8"))
    except CredentialEnvelopeError:
        raise
    except Exception as exc:
        raise CredentialEnvelopeError("credential envelope authentication failed") from exc
    if not isinstance(payload, dict) or set(payload) != {"BASE_URL", "API_KEY", "LLM_PROVIDER_FORMAT"}:
        raise CredentialEnvelopeError("invalid credential payload schema")
    return validate_credentials(payload)


def write_envelope_atomic(path: Path, data: bytes) -> None:
    """Atomically write an envelope with owner-only POSIX permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        if hasattr(os, "fchmod"):  # Unix-only; skipped on Windows.
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temp.unlink(missing_ok=True)
        raise
