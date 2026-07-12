from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from core.credential_envelope import (
    CredentialEnvelopeError,
    open_credentials,
    seal_credentials,
    validate_credentials,
    write_envelope_atomic,
)


VALUES = {
    "BASE_URL": "https://gateway.example.test/genai",
    "API_KEY": "sentinel-secret-key-1234567890",
    "LLM_PROVIDER_FORMAT": "anthropic",
}

# The real credential envelope shipped inside the agent bundle.
SHIPPED_ENVELOPE = Path(__file__).resolve().parent.parent / "src" / ".env.enc"


def test_pure_python_aes256_matches_fips197_known_answer():
    from core._aesgcm_fallback import _AES256

    # FIPS-197 AES-256 block known-answer + key-schedule word 8 (Appendix A.3).
    aes = _AES256(bytes(range(32)))
    ct = aes.encrypt_block(bytes.fromhex("00112233445566778899aabbccddeeff"))
    assert ct.hex() == "8ea2b7ca516745bfeafc49904b496089"
    assert bytes(_AES256._expand_key(bytes(range(32)))[2][:4]).hex() == "a573c29f"


def test_pure_python_gcm_matches_cryptography_across_random_vectors():
    """The stdlib fallback must be byte-identical to the native binding."""
    crypto = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")
    from core._aesgcm_fallback import decrypt as pure_decrypt

    AESGCM = crypto.AESGCM
    for n in range(40):
        key = os.urandom(32)
        nonce = os.urandom(12)
        aad = os.urandom(n % 30)
        msg = os.urandom((n * 17) % 300)
        blob = AESGCM(key).encrypt(nonce, msg, aad)
        assert pure_decrypt(key, nonce, blob, aad) == msg


def test_pure_python_gcm_rejects_tampered_tag():
    from core._aesgcm_fallback import decrypt as pure_decrypt

    key = os.urandom(32)
    nonce = os.urandom(12)
    # Tamper a known-good cryptography blob and confirm the fallback rejects it.
    crypto = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")
    blob = bytearray(crypto.AESGCM(key).encrypt(nonce, b"payload", b""))
    blob[-1] ^= 0x01
    with pytest.raises(ValueError):
        pure_decrypt(key, nonce, bytes(blob), b"")


def test_envelope_opens_without_cryptography(monkeypatch):
    """The managed credential must decrypt even if cryptography is unavailable.

    cryptography is imported lazily inside _derive_key / _aesgcm_decrypt, so
    blocking the import at call time exercises the pure-Python fallback WITHOUT
    reloading the module (a reload would swap out CredentialEnvelopeError and
    break every other test's pytest.raises).
    """
    import builtins

    from core.credential_envelope import open_credentials

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("simulated: cryptography binding unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    sealed = json.loads(SHIPPED_ENVELOPE.read_text())  # ensure it is v3 json
    assert sealed["version"] == 3
    values = open_credentials(SHIPPED_ENVELOPE.read_bytes())
    assert values.get("API_KEY")
    assert values.get("BASE_URL")


def test_envelope_roundtrip_is_random_and_contains_no_plaintext():
    first = seal_credentials(VALUES)
    second = seal_credentials(VALUES)
    assert first != second
    assert VALUES["API_KEY"].encode() not in first
    assert VALUES["BASE_URL"].encode() not in first
    assert open_credentials(first) == VALUES
    outer = json.loads(first)
    assert outer["cipher"] == "aes-256-gcm"
    assert outer["kdf"] == "pbkdf2-sha256-i600000"
    assert outer["version"] == 3


@pytest.mark.parametrize("field", ["ciphertext", "nonce", "salt"])
def test_envelope_tamper_fails_closed(field):
    outer = json.loads(seal_credentials(VALUES))
    value = outer[field]
    outer[field] = ("A" if value[0] != "A" else "B") + value[1:]
    with pytest.raises(CredentialEnvelopeError):
        open_credentials(json.dumps(outer).encode())


@pytest.mark.parametrize(
    "data",
    [b"", b"not-json", b"{}", b"{" + b"x" * 20_000, seal_credentials(VALUES)[:30]],
)
def test_malformed_or_truncated_envelopes_fail(data):
    with pytest.raises(CredentialEnvelopeError):
        open_credentials(data)


@pytest.mark.parametrize(
    "url",
    ["http://gateway.example.test", "file:///tmp/key", "https://user:pass@example.test", "not-a-url"],
)
def test_unsafe_service_urls_rejected(url):
    with pytest.raises(CredentialEnvelopeError):
        validate_credentials({**VALUES, "BASE_URL": url})


def test_atomic_envelope_write_is_owner_only(tmp_path):
    path = tmp_path / ".env.enc"
    write_envelope_atomic(path, seal_credentials(VALUES))
    assert open_credentials(path.read_bytes()) == VALUES
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("..env.enc.*"))


def test_os_store_rewrap_and_rotation(monkeypatch, tmp_path):
    import core.credential_store as store

    saved: dict[str, object] = {}
    monkeypatch.setattr(store, "_load_os_bound", lambda: saved.get("value"))
    monkeypatch.setattr(
        store,
        "_save_os_bound",
        lambda values, release_id: saved.update(value=(values, release_id)) is None or True,
    )
    path = tmp_path / ".env.enc"
    write_envelope_atomic(path, seal_credentials(VALUES))
    values, state = store.load_release_credentials(path)
    assert values == VALUES and state == "os-bound"

    rotated = {**VALUES, "API_KEY": "rotated-sentinel-secret-987654321"}
    write_envelope_atomic(path, seal_credentials(rotated))
    values, state = store.load_release_credentials(path)
    assert values == rotated and state == "os-bound"


def test_corrupt_rotation_preserves_last_os_bound_value(monkeypatch, tmp_path):
    import core.credential_store as store

    monkeypatch.setattr(store, "_load_os_bound", lambda: (VALUES, "a" * 64))
    monkeypatch.setattr(store, "_save_os_bound", lambda *_: pytest.fail("must not overwrite"))
    path = tmp_path / ".env.enc"
    path.write_bytes(b"tampered-update")
    values, state = store.load_release_credentials(path)
    assert values == VALUES
    assert state == "os-bound-stale-release"


def test_shipped_release_envelope_authenticates_and_configures_ai():
    """Release gate: never publish a malformed, stale, or empty AI envelope."""
    release = Path(__file__).resolve().parents[1] / "src" / ".env.enc"
    values = open_credentials(release.read_bytes())
    assert values["BASE_URL"].startswith("https://")
    assert len(values["API_KEY"]) >= 20
    assert values["LLM_PROVIDER_FORMAT"] in {"anthropic", "openai"}
    assert values["API_KEY"].encode() not in release.read_bytes()


def test_secure_store_unavailable_uses_encrypted_release_only(monkeypatch, tmp_path):
    import core.credential_store as store

    monkeypatch.setattr(store, "_load_os_bound", lambda: None)
    monkeypatch.setattr(store, "_save_os_bound", lambda *_: False)
    path = tmp_path / ".env.enc"
    envelope = seal_credentials(VALUES)
    path.write_bytes(envelope)
    values, state = store.load_release_credentials(path)
    assert values == VALUES
    assert state == "release-envelope"
    assert VALUES["API_KEY"].encode() not in path.read_bytes()


def test_doctor_reports_managed_ai_without_obsolete_settings_instructions():
    from core.diagnostics import run_doctor

    report = run_doctor()
    by_id = {check["id"]: check for check in report["checks"]}
    for check_id in ("embedding_backend", "reranker", "llm_gateway", "ocr", "multimedia"):
        assert by_id[check_id]["status"] == "pass"
    rendered = json.dumps(report)
    assert "Add an LLM API key" not in rendered
    assert "credential protection" in by_id["llm_gateway"]["detail"]


def _simulate_windows(monkeypatch):
    """Reproduce the Windows runtime: no os.fchmod, win32 platform, DPAPI stub.

    os.fchmod is Unix-only, so on Windows referencing it raises AttributeError
    (NOT OSError). This is the exact class of defect that passes on the Linux
    build/test host and fails only on the user's machine.
    """
    import core.credential_store as store

    monkeypatch.setattr(store.sys, "platform", "win32")
    monkeypatch.delattr(os, "fchmod", raising=False)
    monkeypatch.setattr(
        store, "_dpapi_protect",
        lambda data: (b"DPAPI\x00" + data) if data else None,
    )
    monkeypatch.setattr(
        store, "_dpapi_unprotect",
        lambda data: data[6:] if data and data.startswith(b"DPAPI\x00") else None,
    )
    return store


def test_write_private_never_raises_without_fchmod(monkeypatch, tmp_path):
    import core.credential_store as store

    monkeypatch.delattr(os, "fchmod", raising=False)
    target = tmp_path / ".credentials" / "genai.dpapi"
    assert store._write_private(target, b"payload") is True
    assert target.read_bytes() == b"payload"


def test_windows_end_to_end_decrypts_and_persists_without_fchmod(monkeypatch, tmp_path):
    """The full Windows path must reach a working state, never invalid/unavailable."""
    store = _simulate_windows(monkeypatch)
    monkeypatch.setenv("TT_WORKSPACE_DIR", str(tmp_path / "ws"))
    path = tmp_path / ".env.enc"
    path.write_bytes(seal_credentials(VALUES))

    values, state = store.load_release_credentials(path)
    assert values == VALUES
    assert state == "os-bound"  # DPAPI stub + fixed _write_private now succeed

    # Second load reads the cached OS-bound copy back (proves persistence works).
    values2, state2 = store.load_release_credentials(path)
    assert values2 == VALUES and state2 == "os-bound"


def test_windows_persistence_failure_degrades_to_release_envelope(monkeypatch, tmp_path):
    """If OS-bound caching fails on Windows, a working key must NOT be lost."""
    store = _simulate_windows(monkeypatch)
    monkeypatch.setattr(store, "_load_os_bound", lambda: None)
    monkeypatch.setattr(store, "_write_private", lambda *a, **k: False)
    path = tmp_path / ".env.enc"
    path.write_bytes(seal_credentials(VALUES))

    values, state = store.load_release_credentials(path)
    assert values == VALUES
    assert state == "release-envelope"


def test_windows_save_exception_never_downgrades_to_unavailable(monkeypatch, tmp_path):
    """Even an unexpected save exception keeps the decrypted credential usable."""
    store = _simulate_windows(monkeypatch)
    monkeypatch.setattr(store, "_load_os_bound", lambda: None)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated persistence fault")

    monkeypatch.setattr(store, "_save_os_bound", _boom)
    path = tmp_path / ".env.enc"
    path.write_bytes(seal_credentials(VALUES))

    values, state = store.load_release_credentials(path)
    assert values == VALUES
    assert state == "release-envelope"


def test_app_config_records_nonsecret_detail_on_failure(monkeypatch):
    import core.app_config as cfg
    import core.credential_store as store
    from core.credential_envelope import CredentialEnvelopeError

    def _boom(_path):
        raise CredentialEnvelopeError("credential envelope authentication failed")

    monkeypatch.setattr(store, "load_release_credentials", _boom)
    try:
        assert cfg._load_env() == {}
        assert cfg.credential_protection_state() == "unavailable"
        detail = cfg.credential_protection_detail()
        assert "CredentialEnvelopeError" in detail
        assert "authentication failed" in detail
        assert VALUES["API_KEY"] not in detail  # never leaks key material
    finally:
        monkeypatch.undo()
        cfg._load_env()  # restore the real shipped state for other tests


def test_app_config_detail_empty_when_credential_loads_cleanly():
    import importlib

    import core.app_config as cfg

    importlib.reload(cfg)
    assert cfg.LLM_API_KEY
    assert cfg.credential_protection_state() in {"os-bound", "release-envelope"}
    assert cfg.credential_protection_detail() == ""


def test_log_redaction_removes_configured_values_and_token(monkeypatch):
    from core.app_logging import redact_secrets

    monkeypatch.setenv("LLM_UPSTREAM_API_KEY", VALUES["API_KEY"])
    monkeypatch.setenv("LLM_UPSTREAM_BASE_URL", VALUES["BASE_URL"])
    rendered = redact_secrets(
        f"request {VALUES['BASE_URL']} Authorization Bearer {VALUES['API_KEY']} token-abcdefghijklmnop"
    )
    assert VALUES["API_KEY"] not in rendered
    assert VALUES["BASE_URL"] not in rendered
    assert "token-abcdefghijklmnop" not in rendered
    assert rendered.count("[REDACTED]") >= 3
