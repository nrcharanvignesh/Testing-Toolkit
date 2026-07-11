from __future__ import annotations

import json
import os
import stat

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


def test_envelope_roundtrip_is_random_and_contains_no_plaintext():
    first = seal_credentials(VALUES)
    second = seal_credentials(VALUES)
    assert first != second
    assert VALUES["API_KEY"].encode() not in first
    assert VALUES["BASE_URL"].encode() not in first
    assert open_credentials(first) == VALUES
    outer = json.loads(first)
    assert outer["cipher"] == "aes-256-gcm"
    assert outer["kdf"] == "scrypt-n32768-r8-p1"
    assert outer["version"] == 2


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
