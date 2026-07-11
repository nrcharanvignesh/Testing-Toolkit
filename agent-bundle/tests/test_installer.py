"""Regression tests for the cross-platform installer's combination handling.

These guard the wheel-tag parsing, version-aware wheelhouse coverage, and the
interpreter-selection policy that decides whether an offline install is
possible or an online (PyPI) fallback is required. The bug class these prevent:
a "covered" platform (Windows / Linux x86_64) whose only bundled wheels are for
one CPython minor (e.g. cp312) must NOT report itself covered for a DIFFERENT
Python version -- otherwise the online fallback never engages and the install
dead-ends.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_INSTALL_PY = Path(__file__).resolve().parent.parent / "install.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("tt_installer", _INSTALL_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


inst = _load_installer()


# --- wheel filename -> supported CPython minors ---------------------------
def test_wheel_pyminors_exact_cp():
    assert inst._wheel_pyminors("numpy-2.5.1-cp312-cp312-win_amd64.whl") == {12}


def test_wheel_pyminors_abi3_is_a_floor_range():
    # cp39-abi3 runs on 3.9 .. CEILING (forward-compatible stable ABI).
    got = inst._wheel_pyminors("lancedb-0.34.0-cp39-abi3-manylinux_2_28_x86_64.whl")
    assert got == set(range(9, inst._CEILING_MINOR + 1))


def test_wheel_pyminors_pure_python_has_no_constraint():
    assert inst._wheel_pyminors("fastapi-0.111.0-py3-none-any.whl") is None
    assert inst._wheel_pyminors("packaging-24.0-py3-none-any.whl") is None


# --- version-aware wheelhouse coverage against the REAL bundled wheelhouse -
def test_covered_platform_is_pinned_to_bundled_cp_version():
    """Windows/Linux x86_64 are covered offline only for the version(s) the
    bundled binary wheels actually target (currently cp312)."""
    win = inst._wheelhouse_pyversions("windows", "amd64")
    lin = inst._wheelhouse_pyversions("linux", "amd64")
    assert isinstance(win, set) and win, "windows-amd64 must have a version set"
    assert win == lin, "win/linux x86_64 should agree on the bundled version"
    # Sanity: it is a concrete '3.x' set, not the pure-python wildcard.
    assert "*" not in win and all(v.startswith("3.") for v in win)


def test_uncovered_platforms_return_none():
    # No macOS or arm64 binary wheels ship -> offline impossible -> None.
    for osn, arch in [("macos", "arm64"), ("macos", "amd64"),
                      ("linux", "arm64"), ("windows", "arm64")]:
        assert inst._wheelhouse_pyversions(osn, arch) is None, f"{osn}-{arch}"


def test_wheelhouse_supports_is_version_aware():
    supported = inst._wheelhouse_pyversions("windows", "amd64")
    good = sorted(supported)[0]  # e.g. "3.12"
    gmaj, gmin = (int(x) for x in good.split("."))
    # Exact supported version -> covered.
    assert inst.wheelhouse_supports("windows", "amd64", (gmaj, gmin)) is True
    # A different version on the SAME covered platform -> NOT covered, so the
    # caller enables the online fallback instead of dead-ending.
    assert inst.wheelhouse_supports("windows", "amd64", (gmaj, gmin + 1)) is False
    # No version supplied -> "covered for some version".
    assert inst.wheelhouse_supports("windows", "amd64", None) is True
    # Uncovered platform -> never covered, with or without a version.
    assert inst.wheelhouse_supports("macos", "arm64", (gmaj, gmin)) is False
    assert inst.wheelhouse_supports("macos", "arm64", None) is False


# --- interpreter selection prefers a wheelhouse-matching version ----------
def test_find_system_python_prefers_matching_version(monkeypatch):
    monkeypatch.setattr(
        inst, "_candidate_system_pythons",
        lambda: ["/py311", "/py312", "/py313"],
    )
    vers = {"/py311": (3, 11), "/py312": (3, 12), "/py313": (3, 13)}
    monkeypatch.setattr(inst, "_py_version", lambda exe: vers[exe])
    # Wheelhouse supports only 3.12 -> that interpreter must win.
    assert inst.find_system_python({"3.12"}) == "/py312"


def test_find_system_python_falls_back_to_newest_when_no_match(monkeypatch):
    monkeypatch.setattr(
        inst, "_candidate_system_pythons",
        lambda: ["/py311", "/py313"],
    )
    vers = {"/py311": (3, 11), "/py313": (3, 13)}
    monkeypatch.setattr(inst, "_py_version", lambda exe: vers[exe])
    # No 3.12 available -> newest wins so the online fallback can install it.
    assert inst.find_system_python({"3.12"}) == "/py313"


def test_find_system_python_none_when_no_candidates(monkeypatch):
    monkeypatch.setattr(inst, "_candidate_system_pythons", lambda: [])
    assert inst.find_system_python({"3.12"}) is None


# --- platform / arch normalisation ----------------------------------------
def test_detect_platform_returns_known_vocab():
    osn, arch = inst.detect_platform()
    assert osn in {"windows", "macos", "linux"}
    assert arch in {"amd64", "arm64"} or arch  # any non-empty machine tag


# --- launched-agent identity ----------------------------------------------
def test_health_matches_exact_installed_agent():
    assert inst._health_matches_installed_agent(
        {"status": "ok", "version": "2.10.7", "capabilities": {}},
        "2.10.7",
    )


def test_health_rejects_stale_or_legacy_process():
    assert not inst._health_matches_installed_agent(
        {"status": "ok", "version": "2.10.6", "capabilities": {}},
        "2.10.7",
    )
    assert not inst._health_matches_installed_agent(
        {"status": "ok", "version": "2.10.7"},
        "2.10.7",
    )


def test_agent_contract_requires_every_modern_route(monkeypatch):
    requested = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_ok(url, timeout):
        requested.append((url, timeout))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_ok)
    assert inst._agent_contract_is_current("http://127.0.0.1:7842")
    assert [url for url, _ in requested] == [
        "http://127.0.0.1:7842/metrics",
        "http://127.0.0.1:7842/sources/projects",
        "http://127.0.0.1:7842/update/status",
    ]


def test_agent_contract_rejects_legacy_404(monkeypatch):
    class Response:
        status = 404

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    assert not inst._agent_contract_is_current("http://127.0.0.1:7842")


def test_port_owner_pids_reads_windows_listener(monkeypatch):
    class Result:
        stdout = "  TCP  127.0.0.1:7842  0.0.0.0:0  LISTENING  10912\n"

    monkeypatch.setattr(inst.os, "name", "nt")
    monkeypatch.setattr(inst, "_run", lambda *_args, **_kwargs: Result())
    assert inst._port_owner_pids(7842) == {10912}


def test_release_agent_port_kills_recognized_orphan(monkeypatch):
    states = iter([False, True])
    killed = []
    monkeypatch.setattr(inst.os, "name", "nt")
    monkeypatch.setattr(inst, "_port_free", lambda _port: next(states))
    monkeypatch.setattr(inst, "_port_owner_pids", lambda _port: {10912})
    monkeypatch.setattr(inst, "_windows_process_is_agent", lambda _pid: True)
    monkeypatch.setattr(inst, "_kill_pid", killed.append)
    assert inst._release_agent_port(7842)
    assert killed == [10912]


def test_release_agent_port_refuses_unknown_owner(monkeypatch):
    monkeypatch.setattr(inst.os, "name", "nt")
    monkeypatch.setattr(inst, "_port_free", lambda _port: False)
    monkeypatch.setattr(inst, "_port_owner_pids", lambda _port: {777})
    monkeypatch.setattr(inst, "_windows_process_is_agent", lambda _pid: False)
    monkeypatch.setattr(inst, "error", lambda _message: None)
    monkeypatch.setattr(inst, "_kill_pid", lambda _pid: (_ for _ in ()).throw(AssertionError()))
    assert not inst._release_agent_port(7842)


def test_python_progress_is_log_only(monkeypatch):
    logged = []
    monkeypatch.setattr(inst, "_log_line", lambda level, message: logged.append((level, message)))
    inst.progress("installing_deps", "Installing packages", 78)
    assert logged == [("PROGRESS", "78% installing_deps: Installing packages")]


# --- source/binary drift guard --------------------------------------------
def test_every_hard_requirement_has_a_bundled_wheel():
    """Every HARD dep in requirements.txt must have a wheel in the wheelhouse.

    This is the exact regression that broke the 2.10.1/2.10.2 offline install:
    `cryptography>=42.0` was added to requirements.txt but its wheel was not in
    the shipped bundle wheelhouse, so `pip install --no-index -r requirements`
    failed the ENTIRE dependency install on covered platforms. Deps that cannot
    guarantee a bundled wheel (cryptography, playwright, mcp) must be installed
    as OPTIONAL non-fatal steps, never as hard requirements.
    """
    import re

    root = _INSTALL_PY.parent
    wheelhouse = root / "wheelhouse"
    assert wheelhouse.is_dir(), "wheelhouse missing"

    def norm(n: str) -> str:
        return re.sub(r"[-_.]+", "-", n).lower()

    present = set()
    for whl in wheelhouse.glob("*.whl"):
        m = re.match(r"([a-z0-9][a-z0-9._-]*?)-\d", whl.name.lower())
        if m:
            present.add(norm(m.group(1)))

    missing = []
    for line in (root / "requirements.txt").read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        name = re.split(r"[<>=!~;\[ ]", s, maxsplit=1)[0].strip()
        if name and norm(name) not in present:
            missing.append(name)

    assert not missing, (
        "requirements.txt lists hard deps with no bundled wheel "
        f"(offline install would fail): {missing}. Make them optional install "
        "steps instead."
    )


def test_optional_cryptography_deps_are_deliverable():
    """install.py installs cryptography as an OPTIONAL offline step, so its full
    dep closure (cryptography + cffi + pycparser) must be deliverable via the
    extra-wheels overlay for both shipped platforms (win_amd64 + manylinux).

    This guards the bundled-service-key path: without these the app silently
    drops to Manual Mode. cryptography (abi3) covers 3.11+; cffi is per-minor
    (cp312) matching the version-aware interpreter selection.
    """
    import re

    root = _INSTALL_PY.parent
    extra = root / "extra-wheels"
    assert extra.is_dir(), "extra-wheels dir missing"
    names = [p.name.lower() for p in extra.glob("*.whl")]

    def has(dist: str, *, plat: str | None = None) -> bool:
        return any(
            re.match(rf"{dist}-\d", n) and (plat is None or plat in n)
            for n in names
        )

    # pycparser is pure-python (one wheel serves every platform).
    assert has("pycparser"), "pycparser wheel missing from extra-wheels"
    for plat in ("win_amd64", "manylinux"):
        assert has("cryptography", plat=plat), f"cryptography {plat} wheel missing"
        assert has("cffi", plat=plat), f"cffi {plat} wheel missing"
