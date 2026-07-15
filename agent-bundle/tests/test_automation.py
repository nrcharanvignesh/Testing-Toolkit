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
    # _esc is the renamed helper (was _escape_str in an older revision)
    from automation.script_generator import _esc

    # embedding a value with quotes/newlines stays valid when parsed
    out = _esc("he said \"hi\"\n'bye'")
    ast.parse(f"x = {out}")


# --------------------------------------------------------------------------
# Script generator - new action coverage
# --------------------------------------------------------------------------
def test_generated_script_new_actions():
    """All new action types must produce valid, parseable Python."""
    from automation.script_generator import generate_playwright_script

    steps = [
        {"action": "navigate", "target": "", "value": "http://app"},
        {"action": "fill",     "target": "Email",    "value": "a@b.com", "locator": "label"},
        {"action": "type",     "target": "Query",    "value": "hello",   "locator": "placeholder"},
        {"action": "click",    "target": "button:Submit", "locator": "role"},
        {"action": "double_click", "target": "cell",  "locator": "text"},
        {"action": "hover",    "target": "tooltip",   "locator": "text"},
        {"action": "select",   "target": "dropdown:Status", "value": "Active", "locator": "role"},
        {"action": "check",    "target": "checkbox:Accept",  "locator": "label"},
        {"action": "uncheck",  "target": "checkbox:Accept",  "locator": "label"},
        {"action": "clear",    "target": "Search",   "locator": "placeholder"},
        {"action": "press_key","target": "", "value": "Enter"},
        {"action": "scroll",   "target": "", "value": "down"},
        {"action": "wait",     "target": "", "value": "500"},
        {"action": "wait_for_text", "target": "", "value": "Success"},
        {"action": "wait_for_url",  "target": "", "value": "dashboard"},
        {"action": "assert_text",   "target": "", "value": "Welcome"},
        {"action": "assert_url",    "target": "", "value": "dashboard"},
        {"action": "assert_element","target": "heading:Dashboard", "locator": "role"},
        {"action": "assert_not_present", "target": "Spinner", "locator": "text"},
        {"action": "screenshot","target": ""},
    ]
    script = generate_playwright_script(
        tc_id="TC-NEW", title="All actions", steps=steps,
        login_url="http://app", username="testuser")

    assert not script.startswith("# ERROR"), f"generator error:\n{script}"
    ast.parse(script)
    # assert_url must use to_have_url, not legacy to_contain
    assert "to_have_url" in script
    assert ".to_contain(" not in script


def test_generated_script_stop_signal_field():
    """Script must include stop signal check (E2E_PASSWORD not in plain text)."""
    from automation.script_generator import generate_playwright_script

    steps = [
        {"action": "fill", "target": "Password", "value": "{{password}}", "locator": "label"},
    ]
    script = generate_playwright_script(
        tc_id="TC-SEC", title="Security check", steps=steps,
        login_url="http://app", username="user")

    assert "E2E_PASSWORD" in script
    # The literal string "{{password}}" must NOT appear
    assert "{{password}}" not in script


def test_script_indentation_is_valid():
    """Step code must land at 8-space indent inside the async block."""
    from automation.script_generator import generate_playwright_script

    steps = [{"action": "navigate", "target": "", "value": "http://x"}]
    script = generate_playwright_script(
        tc_id="TC-IND", title="Indent", steps=steps,
        login_url="http://x", username="u")

    tree = ast.parse(script)
    # Collect all Await nodes and assert they exist (means code is inside async)
    awaits = [n for n in ast.walk(tree) if isinstance(n, ast.Await)]
    assert len(awaits) >= 1, "no await nodes found - indentation probably broken"


# --------------------------------------------------------------------------
# E2E plan compiler validation and cache
# --------------------------------------------------------------------------
def test_validate_steps_rejects_unsafe_or_incomplete_actions():
    from automation.e2e_plan import PlanValidationError, validate_steps

    with pytest.raises(PlanValidationError, match="unsupported action"):
        validate_steps([{"action": "run_javascript", "value": "alert(1)"}])
    with pytest.raises(PlanValidationError, match="requires target"):
        validate_steps([{"action": "click", "target": ""}])
    with pytest.raises(PlanValidationError, match="requires value"):
        validate_steps([{"action": "assert_text", "value": ""}])


def test_validate_steps_normalizes_known_actions():
    from automation.e2e_plan import validate_steps

    steps = validate_steps([
        {"action": "navigate", "value": "https://example.test"},
        {"action": "fill", "target": "Email", "value": "{{username}}", "locator": "label"},
        {"action": "click", "target": "Sign in", "locator": "role"},
        {"action": "assert_text", "value": "Dashboard"},
    ])
    assert [step["action"] for step in steps] == [
        "navigate", "fill", "click", "assert_text"
    ]
    assert steps[1]["value"] == "{{username}}"


def test_normalize_locator_recovers_compound_locators():
    """LLM sometimes jams type+value into the locator field; normalization must recover."""
    from automation.e2e_plan import _normalize_locator

    # role:button[name='Log In'] -> locator=role, target=button:Log In
    loc, tgt = _normalize_locator("role:button[name='Log In']", "")
    assert loc == "role"
    assert tgt == "button:Log In"

    # role:button (no target provided) -> locator=role, target=button
    loc, tgt = _normalize_locator("role:button", "")
    assert loc == "role"
    assert tgt == "button"

    # label:Email Address -> locator=label, target=Email Address
    loc, tgt = _normalize_locator("label:Email Address", "")
    assert loc == "label"
    assert tgt == "Email Address"

    # Already valid: pass through unchanged
    loc, tgt = _normalize_locator("role", "button:Log In")
    assert loc == "role"
    assert tgt == "button:Log In"

    # Unknown prefix: leave unchanged for rejection by validator
    loc, tgt = _normalize_locator("xpath://div", "")
    assert loc == "xpath://div"

    # target already set and different from compound: keep original target
    loc, tgt = _normalize_locator("role:textbox", "Email")
    assert loc == "role"
    assert tgt == "Email"


def test_validate_steps_normalizes_compound_locators():
    """End-to-end: a step with a compound locator should pass validation after normalization."""
    from automation.e2e_plan import validate_steps

    steps = validate_steps([
        {"action": "navigate", "value": "https://example.test"},
        {"action": "click", "target": "", "locator": "role:button[name='Submit']"},
        {"action": "assert_text", "value": "Done"},
    ])
    assert steps[1]["locator"] == "role"
    assert steps[1]["target"] == "button:Submit"


@pytest.mark.asyncio
async def test_compile_test_case_caches_validated_plan(tmp_path):
    from types import SimpleNamespace
    from automation.e2e_plan import compile_test_case

    class Client:
        calls = 0

        async def complete_async(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(text='{"steps":[{"action":"navigate","value":"https://example.test"},{"action":"assert_text","value":"Ready"}]}')

    client = Client()
    case = {"id": "TC-1", "title": "Open app", "steps": [{"action": "Open the app"}]}
    first = await compile_test_case(
        case, login_url="https://example.test", username="tester",
        ai_instructions="", cache_dir=tmp_path, client=client, model="test-model",
    )
    second = await compile_test_case(
        case, login_url="https://example.test", username="tester",
        ai_instructions="", cache_dir=tmp_path, client=client, model="test-model",
    )
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert client.calls == 1
    assert second.test_case["steps"][1]["action"] == "assert_text"


@pytest.mark.asyncio
async def test_compile_test_case_transport_error_is_plan_error(tmp_path):
    from automation.e2e_plan import PlanValidationError, compile_test_case

    class Client:
        async def complete_async(self, **_kwargs):
            raise TimeoutError("secret upstream details")

    with pytest.raises(PlanValidationError, match="TimeoutError"):
        await compile_test_case(
            {"id": "TC-2", "title": "Timeout", "steps": [{"action": "Open"}]},
            login_url="https://example.test", username="tester",
            ai_instructions="", cache_dir=tmp_path, client=Client(), model="test-model",
        )


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
