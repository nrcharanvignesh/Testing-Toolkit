# Tests for the E2E automation stack: credential vault (encrypted at rest,
# password never logged), Playwright script generation (no plaintext secrets,
# valid Python), and execution store round-trips.
from __future__ import annotations

import ast

import pytest


# --------------------------------------------------------------------------
# Credential vault
# --------------------------------------------------------------------------
def test_credential_safe_repr_hides_password():
    from automation.credential_vault import TestCredential

    cred = TestCredential(env="test", login_url="http://x", user_id="alice",
                          password="hunter2")
    assert "hunter2" not in cred.safe_repr()
    assert "hunter2" not in repr(cred)
    assert "hunter2" not in str(cred)
    assert "alice" in cred.safe_repr()


def test_vault_roundtrip_encrypted(tmp_install):
    from automation.credential_vault import CredentialVault, TestCredential

    vault = CredentialVault()
    creds = [
        TestCredential(env="dev", login_url="http://dev", user_id="u1",
                       password="secret-pw-A"),
        TestCredential(env="prod", login_url="http://prod", user_id="u2",
                       password="secret-pw-B"),
    ]
    assert vault.save("My Project", creds) is True

    loaded = vault.load("My Project")
    assert len(loaded) == 2
    by_env = {c.env: c for c in loaded}
    assert by_env["dev"].password == "secret-pw-A"
    assert by_env["prod"].user_id == "u2"

    # passwords must NOT sit in plaintext anywhere on disk
    leaked = False
    for p in tmp_install.rglob("*"):
        if p.is_file():
            try:
                blob = p.read_bytes()
                if b"secret-pw-A" in blob or b"secret-pw-B" in blob:
                    leaked = True
            except OSError:
                pass
    assert not leaked, "credential password stored in plaintext"


def test_vault_get_for_env(tmp_install):
    from automation.credential_vault import CredentialVault, TestCredential

    vault = CredentialVault()
    vault.save("Proj", [TestCredential(env="test", login_url="http://t",
                                       user_id="uu", password="pp")])
    got = vault.get_for_env("Proj", "test")
    assert got is not None and got.user_id == "uu"
    assert vault.get_for_env("Proj", "nonexistent") is None


def test_vault_clear(tmp_install):
    from automation.credential_vault import CredentialVault, TestCredential

    vault = CredentialVault()
    vault.save("Proj", [TestCredential(env="dev", login_url="u",
                                       user_id="u", password="p")])
    assert vault.clear("Proj") is True
    assert vault.load("Proj") == []


def test_safe_project_key():
    from automation.credential_vault import _safe_project_key

    assert _safe_project_key("My Project!") == "my_project"
    assert _safe_project_key("  ") == "default"
    assert _safe_project_key("a---b") == "a_b"


# --------------------------------------------------------------------------
# Playwright script generation
# --------------------------------------------------------------------------
def test_generated_script_is_valid_python_and_hides_password():
    from automation.script_generator import generate_playwright_script

    steps = [
        {"action": "navigate", "target": "", "value": "http://app/login"},
        {"action": "fill", "target": "Username", "value": "alice",
         "locator": "label"},
        {"action": "fill", "target": "Password", "value": "TOPSECRET",
         "locator": "label"},
        {"action": "click", "target": "button:Sign In", "locator": "role"},
        {"action": "assert_text", "target": "", "value": "Welcome"},
    ]
    script = generate_playwright_script(
        tc_id="TC-1", title="Login", steps=steps,
        login_url="http://app/login", username="alice")

    # regression guard: the generator must NOT fall back to the error stub
    assert not script.startswith("# ERROR"), "generator produced invalid Python"
    # must be valid, parseable Python
    ast.parse(script)
    # plaintext password must NEVER be embedded (password field -> env var)
    assert "TOPSECRET" not in script
    assert "E2E_PASSWORD" in script


def test_is_password_target():
    from automation.script_generator import _is_password_target

    assert _is_password_target("Password")
    assert _is_password_target("passwd field")
    assert not _is_password_target("Username")


def test_escape_str_safe():
    from automation.script_generator import _escape_str

    # embedding a value with quotes/newlines stays valid when parsed
    out = _escape_str("he said \"hi\"\n'bye'")
    ast.parse(f"x = {out}")


# --------------------------------------------------------------------------
# Execution store round-trip
# --------------------------------------------------------------------------
def test_execution_store_roundtrip(tmp_install):
    from automation.execution_store import (
        ExecutionRun, TestResult, save_run, load_runs, load_latest_run,
        failed_tc_ids,
    )

    def _tr(tc_id: str, status: str) -> TestResult:
        return TestResult(
            tc_id=tc_id, tc_title=f"Title {tc_id}", status=status,
            duration_ms=10, error_message="", screenshot_path="",
            timestamp=1000.0)

    run = ExecutionRun(
        run_id="r1", project_full="Proj",
        started_at=1000.0, finished_at=1001.0,
        results=[_tr("TC-1", "pass"), _tr("TC-2", "fail")],
        total=2, passed=1, failed=1,
    )
    save_run("Proj", run)
    runs = load_runs("Proj")
    assert len(runs) >= 1
    latest = load_latest_run("Proj")
    assert latest is not None and latest.run_id == "r1"
    assert "TC-2" in failed_tc_ids("Proj")
    assert "TC-1" not in failed_tc_ids("Proj")
