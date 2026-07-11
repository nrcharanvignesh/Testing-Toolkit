#!/usr/bin/env python3
"""
Testing Toolkit Agent - Cross-platform offline installer.

This script installs the local compute agent using ONLY the resources that
ship inside this `agent-bundle/` folder. It never contacts the public
internet (no python.org, no PyPI, no GitHub), which makes it safe to run on
locked-down corporate networks.

Resources are resolved relative to this file:
    agent-bundle/
      install.py          <- you are here
      requirements.txt
      wheelhouse/         <- offline pip packages (--find-links)
      src/                <- agent source code
      runtime/<os>-<arch> <- optional bundled portable Python

Run it directly with any Python 3.9+:
    python install.py
or via the thin launchers `install.cmd` (Windows) / `install.sh` (Unix),
which will locate a Python for you (preferring the bundled runtime).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AGENT_PORT = 7842
MIN_PY = (3, 9)

# On Windows, suppress the console window for EVERY child process we spawn
# (pip, venv, the agent itself). 0 elsewhere (accepted but ignored on POSIX),
# so we can pass it unconditionally and keep the whole install windowless.
CREATE_NO_WINDOW = 0x08000000
_CF = CREATE_NO_WINDOW if os.name == "nt" else 0

# --- Resolve bundle layout (everything is relative to this file) ----------
BUNDLE_DIR = Path(__file__).resolve().parent
WHEELHOUSE = BUNDLE_DIR / "wheelhouse"
# No local ML models are bundled — embeddings/reranking/OCR/audio are all
# served by the GenAI proxy API (see the API-first migration).
SRC_DIR = BUNDLE_DIR / "src"
RUNTIME_DIR = BUNDLE_DIR / "runtime"
REQUIREMENTS = BUNDLE_DIR / "requirements.txt"

# EVERYTHING lives under one centralized root: ~/TestingToolkitWeb
# (e.g. C:\\Users\\cnr002\\TestingToolkitWeb). The installed agent code, venv,
# bundled models, update.json, pid, caches AND all logs go here, right next to
# the runtime workspace (projects/KB/runs/outputs/settings) which already uses
# this same root. This matches updater.install_dir() and core.app_config so the
# on-login autostart (which passes no env vars and relies on these defaults)
# finds update.json and the workspace in the same place the installer wrote them.
INSTALL_DIR = Path(
    os.environ.get("TT_INSTALL_DIR", Path.home() / "TestingToolkitWeb")
).expanduser()
AGENT_DIR = INSTALL_DIR / "agent"
VENV_DIR = INSTALL_DIR / "venv"
LIB_DIR = INSTALL_DIR / "lib"  # used for the --target fallback path
# Logs live here: agent.log + installer trace logs. Honor TT_LOG_DIR (the
# bootstrap installer forwards the SAME folder) so all logs land together.
LOG_DIR = Path(
    os.environ.get("TT_LOG_DIR") or (INSTALL_DIR / "logs")
).expanduser()


# --------------------------------------------------------------------------
# Logging helpers
# --------------------------------------------------------------------------
# The offline installer usually runs under a HIDDEN PowerShell worker, so its
# stdout is thrown away. We therefore ALWAYS also write a trace-level log to a
# documented, stable folder shared with the bootstrap installer and the agent:
#   <INSTALL_DIR>/logs/install-<stamp>.log   (timestamped, this run)
#   <INSTALL_DIR>/logs/install-last.log       (stable, always the latest run)
# Logging is set up on the very first line of main() and never raises.
import datetime as _dt

_LOG_FH = None            # open file handle for install-<stamp>.log
_LOG_PATH: Path | None = None
_LAST_LOG_PATH: Path | None = None


def _setup_logging() -> None:
    """Open the trace log file. Never raises; falls back to TEMP, then off."""
    global _LOG_FH, _LOG_PATH, _LAST_LOG_PATH
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidates = [LOG_DIR, Path(tempfile.gettempdir()) / "TestingToolkitWeb" / "logs"]
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            _LOG_PATH = d / f"install-{stamp}.log"
            _LAST_LOG_PATH = d / "install-last.log"
            _LOG_FH = open(_LOG_PATH, "w", encoding="utf-8")
            break
        except Exception:
            _LOG_FH = None
            _LOG_PATH = None
            _LAST_LOG_PATH = None
    _log_line("INFO", "================ offline installer started ================")
    if _LOG_PATH:
        _log_line("INFO", f"trace log: {_LOG_PATH}")


def _log_line(level: str, msg: str) -> None:
    """Append one timestamped line to the trace log (best-effort)."""
    if not _LOG_FH:
        return
    try:
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        _LOG_FH.write(f"{ts}  [{level}] {msg}\n")
        _LOG_FH.flush()
    except Exception:
        pass


def _close_logging() -> None:
    """Flush + copy the run log to the stable install-last.log path."""
    global _LOG_FH
    try:
        if _LOG_FH:
            _LOG_FH.flush()
    except Exception:
        pass
    try:
        if _LOG_PATH and _LAST_LOG_PATH and _LOG_PATH.exists():
            shutil.copyfile(_LOG_PATH, _LAST_LOG_PATH)
    except Exception:
        pass
    try:
        if _LOG_FH:
            _LOG_FH.close()
    except Exception:
        pass
    _LOG_FH = None


def info(msg: str) -> None:
    """Record routine detail in the trace log without flooding the console."""
    _log_line("INFO", msg)


def milestone(msg: str) -> None:
    """Show one user-facing phase while retaining it in the full trace log."""
    print(f"\n==> {msg}", flush=True)
    _log_line("STEP", msg)


def warn(msg: str) -> None:
    print(f"[WARN] {msg}", flush=True)
    _log_line("WARN", msg)


def error(msg: str) -> None:
    print(f"[ERROR] {msg}", flush=True)
    _log_line("ERROR", msg)


def ok(msg: str) -> None:
    print(f"[SUCCESS] {msg}", flush=True)
    _log_line("SUCCESS", msg)


def trace(msg: str) -> None:
    """Trace-level detail: file only (keeps the console clean)."""
    _log_line("TRACE", msg)


# --------------------------------------------------------------------------
# Install progress (printed to the visible installer console)
# --------------------------------------------------------------------------
def progress(phase: str, message: str, percent: float | None = None, **extra) -> None:
    """Render one compact tqdm-style progress bar; detail stays in the log."""
    try:
        if percent is None:
            milestone(message)
            return
        pct = max(0, min(100, int(round(percent))))
        width = 30
        filled = int(round(width * pct / 100))
        bar = "#" * filled + "-" * (width - filled)
        sys.stdout.write(f"\r  {pct:3d}%|{bar}| {message:<42.42}")
        sys.stdout.flush()
        _log_line("PROGRESS", f"{pct}% {phase}: {message}")
        if pct >= 100:
            sys.stdout.write("\n")
    except Exception:
        pass


def _run(cmd, **kwargs):
    """subprocess.run wrapper that is windowless on Windows.

    Traces the command and (when captured) its output + return code to the log
    so pip/venv/schtasks failures are fully diagnosable even though the install
    runs hidden.
    """
    kwargs.setdefault("creationflags", _CF)
    try:
        printable = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    except Exception:
        printable = str(cmd)
    trace(f"run: {printable}")
    result = subprocess.run(cmd, **kwargs)
    try:
        rc = getattr(result, "returncode", None)
        if rc is not None:
            trace(f"  -> exit {rc}")
        out = getattr(result, "stdout", None)
        err = getattr(result, "stderr", None)
        if out:
            for line in str(out).splitlines():
                trace(f"  out: {line}")
        if err:
            for line in str(err).splitlines():
                trace(f"  err: {line}")
    except Exception:
        pass
    return result


def _port_free(port: int) -> bool:
    """True if nothing is listening on 127.0.0.1:port (i.e. it is free)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return False
    except Exception:
        return True


def purge_stale_packages() -> None:
    """Never reuse previously stored packages: clean them out before installing.

    The agent is always rebuilt from the bundle's wheelhouse with pip's cache
    disabled (--no-cache-dir), so no stale wheel or HTTP-cached package can be
    reused. Here we additionally remove any prior environment and transient
    model caches so a (re)install is guaranteed clean.
    """
    progress("cleaning", "Removing previously stored packages", 67)
    info("Cleaning previously stored packages for a clean install...")
    # Always rebuild the env from scratch - never reuse an old venv / lib dir.
    for d in (VENV_DIR, LIB_DIR):
        if d.exists():
            info(f"  Removing stored environment: {d}")
            shutil.rmtree(d, ignore_errors=True)
    # Transient model cache a previous (pre-API) build may have written.
    # Nothing writes here anymore, but clean it up if an old install left it.
    transient = [
        Path(tempfile.gettempdir()) / "fastembed_cache",
        INSTALL_DIR / ".cache",
    ]

    def _contains(parent: Path, child: Path) -> bool:
        """True if `child` is `parent` itself or lives somewhere under it."""
        try:
            child.resolve().relative_to(parent.resolve())
            return True
        except Exception:
            return False

    for c in transient:
        try:
            if not c.exists():
                continue
            # CRITICAL: the bundle we are installing FROM can live under
            # INSTALL_DIR/.cache (BUNDLE_DIR = .../.cache/Testing-Toolkit).
            # Never delete a directory that holds the active bundle, or we wipe
            # requirements.txt / the wheelhouse / the bundled runtime's pip
            # mid-install and every pip step then fails.
            if _contains(c, BUNDLE_DIR):
                info(f"  Skipping cache that holds the active bundle: {c}")
                continue
            info(f"  Removing transient cache: {c}")
            shutil.rmtree(c, ignore_errors=True)
        except Exception:
            pass


# --------------------------------------------------------------------------
# Platform detection
# --------------------------------------------------------------------------
def detect_platform() -> tuple[str, str]:
    """Return (os_name, arch) using the same vocabulary as runtime/ folders."""
    sysname = platform.system().lower()
    if sysname.startswith("win"):
        os_name = "windows"
    elif sysname == "darwin":
        os_name = "macos"
    else:
        os_name = "linux"

    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "x64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine or "amd64"
    return os_name, arch


# --------------------------------------------------------------------------
# Python discovery
# --------------------------------------------------------------------------
def _py_version(exe: str) -> tuple[int, int] | None:
    """Return (major, minor) for `exe`, or None if it cannot be determined."""
    try:
        out = _run(
            [exe, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    try:
        major, minor = (int(x) for x in out.stdout.strip().split("."))
    except ValueError:
        return None
    return (major, minor)


def _py_ok(exe: str) -> bool:
    """True if `exe` is a runnable Python >= MIN_PY."""
    ver = _py_version(exe)
    return ver is not None and ver >= MIN_PY


def find_bundled_python(os_name: str, arch: str) -> str | None:
    """Look for a portable Python shipped under runtime/<os>-<arch>."""
    candidates = [RUNTIME_DIR / f"{os_name}-{arch}", RUNTIME_DIR / os_name]
    for base in candidates:
        if not base.is_dir():
            continue
        for rel in ("python.exe", "bin/python3", "bin/python", "python3", "python"):
            exe = base / rel
            if exe.exists():
                return str(exe)
    return None


# Interpreter minor versions we actively probe for, newest-first within the
# range the project supports. Version-specific names (python3.12, `py -3.12`)
# let us find a wheelhouse-matching interpreter even when the default `python3`
# is a different version.
_PROBE_MINORS = (13, 12, 11, 10, 9)


def _resolve_launcher_version(py_exe: str, flag: str) -> str | None:
    """Resolve the real interpreter path behind the Windows `py` launcher for a
    given version flag (e.g. '-3.12'). Returns the path or None."""
    try:
        real = _run(
            [py_exe, flag, "-c", "import sys;print(sys.executable)"],
            capture_output=True, text=True, timeout=20,
        )
        if real.returncode == 0 and real.stdout.strip():
            return real.stdout.strip()
    except Exception:
        pass
    return None


def _candidate_system_pythons() -> list[str]:
    """Enumerate distinct, runnable system interpreters (>= MIN_PY).

    Probes generic names AND version-specific names so we can later PREFER an
    interpreter whose version matches the bundled wheelhouse. Order is not
    significant here; the caller ranks by version.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(exe: str | None) -> None:
        if not exe:
            return
        try:
            key = str(Path(exe).resolve())
        except Exception:
            key = exe
        if key in seen:
            return
        seen.add(key)
        if _py_ok(exe):
            found.append(exe)

    # Generic names.
    for name in ("python3", "python"):
        _add(shutil.which(name))

    # Version-specific POSIX names (python3.12, python3.11, ...).
    for minor in _PROBE_MINORS:
        _add(shutil.which(f"python3.{minor}"))

    # Windows `py` launcher: resolve the default and each version flag.
    if os.name == "nt":
        py = shutil.which("py")
        if py:
            _add(_resolve_launcher_version(py, "-3"))
            for minor in _PROBE_MINORS:
                _add(_resolve_launcher_version(py, f"-3.{minor}"))
    return found


def find_system_python(prefer: set[str] | None = None) -> str | None:
    """Find a venv-capable system Python, preferring a wheelhouse-matching one.

    `prefer` is a set of "major.minor" strings (the versions the bundled
    wheelhouse can satisfy offline). When provided, an interpreter whose version
    is in that set wins; otherwise we fall back to the newest runnable
    interpreter >= MIN_PY (so the online fallback can still install for it).
    """
    candidates = _candidate_system_pythons()
    if not candidates:
        return None

    versioned = [(exe, _py_version(exe)) for exe in candidates]
    versioned = [(exe, v) for exe, v in versioned if v is not None]

    if prefer:
        matches = [
            (exe, v) for exe, v in versioned if f"{v[0]}.{v[1]}" in prefer
        ]
        if matches:
            # Newest matching version wins.
            matches.sort(key=lambda t: t[1], reverse=True)
            return matches[0][0]

    # No wheelhouse-matching interpreter: return the newest available so the
    # online fallback (or an already-covered pure-python install) can proceed.
    versioned.sort(key=lambda t: t[1], reverse=True)
    return versioned[0][0]


# --------------------------------------------------------------------------
# pip helpers (always offline)
# --------------------------------------------------------------------------
# Quiet, non-interactive pip flags shared by every install command so the
# console shows a clean summary instead of a per-wheel "Processing ..." flood.
# Warnings and errors are still printed.
#
# --no-cache-dir GUARANTEES we never reuse a previously stored/cached package:
# pip neither reads from nor writes to its wheel/HTTP cache, so every install
# resolves fresh from the bundled wheelhouse (or PyPI on the online fallback).
_PIP_QUIET = [
    "--quiet",
    "--no-input",
    "--disable-pip-version-check",
    "--no-cache-dir",
]


def pip_args_offline(extra: list[str]) -> list[str]:
    # --upgrade ensures the bundled wheelhouse version always wins over any
    # older package that may already be present in the environment. Without it,
    # pip silently keeps a stale installed version even when the wheelhouse
    # contains a newer one.
    return [
        "-m",
        "pip",
        "install",
        "--upgrade",
        *_PIP_QUIET,
        "--no-index",
        f"--find-links={WHEELHOUSE}",
        *extra,
    ]


def pip_args_online(extra: list[str]) -> list[str]:
    """Online pip install, but still PREFER the bundled wheelhouse first.

    Used only as a fallback when the offline wheelhouse does not contain wheels
    for this OS/arch (e.g. macOS or Linux, or arm64). The wheelhouse is kept as
    an extra --find-links so any matching bundled wheels are still reused, and
    only the genuinely-missing ones are pulled from PyPI.
    """
    return [
        "-m",
        "pip",
        "install",
        "--upgrade",
        *_PIP_QUIET,
        f"--find-links={WHEELHOUSE}",
        *extra,
    ]


def _pkg_satisfies(python_exe: str, requirement: str) -> bool:
    """Return True if `requirement` (e.g. 'mcp>=1.0') is already satisfied
    in the Python environment at `python_exe`.

    Uses `importlib.metadata` via a subprocess so it queries the same env
    that will actually run the agent, not the installer's own environment.
    Non-fatal: any error returns False (triggers a fresh install attempt).
    """
    try:
        script = (
            "import importlib.metadata as m, sys; "
            "from importlib.metadata import requires; "
            "import re; "
            f"req = {requirement!r}; "
            "# parse name and version spec from 'pkg>=x.y' style strings; "
            "match = re.match(r'([A-Za-z0-9_.-]+)(.*)', req); "
            "name, spec = match.group(1), match.group(2).strip(); "
            "ver = m.version(name); "
            "from packaging.version import Version; "
            "from packaging.specifiers import SpecifierSet; "
            "ok = (not spec) or Version(ver) in SpecifierSet(spec); "
            "sys.exit(0 if ok else 1)"
        )
        r = _run(
            [python_exe, "-c", script],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


# Highest CPython 3.x minor we assume a stable-ABI (abi3) wheel can run on.
# abi3 wheels are forward-compatible, so a `cp39-abi3` wheel runs on 3.9..this.
_CEILING_MINOR = 14

_TAG_OS = {"windows": "win", "macos": "macosx", "linux": "linux"}
_TAG_ARCH = {"amd64": ("amd64", "x86_64"), "arm64": ("arm64", "aarch64")}


def _wheel_pyminors(name: str) -> set[int] | None:
    """Return the CPython 3.x minors a wheel supports as a BINARY constraint, or
    None when it imposes no version constraint (pure-python).

    - `...-py3-none-any` / `...-none-any` -> None (pure python)
    - `cp3XX-abi3`                        -> floor {XX .. CEILING}
    - `cp3XX-cp3XX`                       -> exact {XX}
    """
    import re

    n = name.lower()
    if "-none-any." in n or "-py3-none-" in n or "-py2.py3-none-" in n:
        return None
    m = re.search(r"cp3(\d+)-abi3", n)
    if m:
        return set(range(int(m.group(1)), _CEILING_MINOR + 1))
    m = re.search(r"cp3(\d+)-cp3\d+", n)
    if m:
        return {int(m.group(1))}
    return None


def _wheelhouse_pyversions(os_name: str, arch: str) -> set[str] | None:
    """Python versions the wheelhouse can satisfy OFFLINE for this platform.

    Returns:
      - {"*"}          : only pure-python deps -> any Python version works.
      - {"3.12", ...}  : offline works for exactly these versions.
      - set()          : platform has binary wheels but no single version can
                         satisfy every binary dep (they disagree).
      - None           : platform is not covered by any binary wheel at all
                         (offline install of binary deps is impossible here).

    The supported set is the INTERSECTION across binary packages (every binary
    dependency must have a wheel for a version to be offline-installable), where
    each package's set is the UNION across its matching-platform wheels.
    """
    if not WHEELHOUSE.is_dir():
        return None
    tag_os = _TAG_OS.get(os_name, os_name)
    tag_arch = _TAG_ARCH.get(arch, (arch,))

    per_pkg: dict[str, set[int]] = {}
    saw_platform_binary = False
    saw_any_binary = False
    for whl in WHEELHOUSE.glob("*.whl"):
        name = whl.name.lower()
        constraint = _wheel_pyminors(name)
        if constraint is None:
            continue  # pure-python; imposes no constraint
        saw_any_binary = True
        if not (tag_os in name and any(a in name for a in tag_arch)):
            continue  # binary wheel for a different platform
        saw_platform_binary = True
        pkg = name.split("-")[0]
        per_pkg.setdefault(pkg, set()).update(constraint)

    if not saw_platform_binary:
        # No binary wheels for THIS platform. If the bundle has no binary wheels
        # at all, pure-python installs anywhere; otherwise the required binary
        # deps cannot be satisfied offline here.
        return {"*"} if not saw_any_binary else None

    supported: set[int] | None = None
    for minors in per_pkg.values():
        supported = minors if supported is None else (supported & minors)
    if not supported:
        return set()
    return {f"3.{m}" for m in sorted(supported)}


def wheelhouse_supports(
    os_name: str, arch: str, py_version: tuple[int, int] | None = None
) -> bool:
    """Does the bundled wheelhouse cover an OFFLINE install for this platform
    (and, when given, this specific Python version)?

    When `py_version` is None this answers "is the platform covered for SOME
    version?"; callers that already know the interpreter pass its version so a
    Python-version/wheel-tag mismatch (e.g. wheelhouse is cp312 but the machine
    runs 3.11) correctly reports 'not covered' and enables the online fallback.
    """
    supported = _wheelhouse_pyversions(os_name, arch)
    if supported is None:
        return False
    if "*" in supported:
        return True
    if not supported:
        return False
    if py_version is None:
        return True
    return f"{py_version[0]}.{py_version[1]}" in supported


def ensure_pip(python_exe: str) -> bool:
    """Make sure `python -m pip` works; bootstrap from the wheelhouse if not."""
    try:
        r = _run(
            [python_exe, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass

    # Bootstrap by invoking a bundled pip wheel directly (no download).
    pip_wheels = sorted(WHEELHOUSE.glob("pip-*.whl"))
    if not pip_wheels:
        return False
    pip_whl = pip_wheels[-1]
    try:
        r = _run(
            [python_exe, str(pip_whl / "pip"), "install", "--no-index",
             "--no-cache-dir", f"--find-links={WHEELHOUSE}", str(pip_whl)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r.returncode == 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Install strategies
# --------------------------------------------------------------------------
def install_via_venv(base_python: str, online: bool = False) -> str | None:
    """Create a venv from a real Python and install into it.

    Offline-first: installs from the bundled wheelhouse. When `online` is True
    (used as a fallback on platforms the wheelhouse does not cover) it also
    allows pulling missing wheels from PyPI. Returns the venv's python path on
    success, else None.
    """
    info("Creating an isolated environment (venv)...")
    # Never reuse a previously created venv - always start from a clean dir.
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    try:
        _run([base_python, "-m", "venv", str(VENV_DIR)],
             check=True, capture_output=True, text=True, timeout=180)
    except Exception as exc:
        warn(f"venv creation failed: {exc}")
        return None

    venv_py = (
        VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not venv_py.exists():
        warn("venv python not found after creation.")
        return None

    if not ensure_pip(str(venv_py)):
        warn("pip is unavailable inside the venv.")
        return None

    if online:
        info("Installing packages (bundled wheels first, missing ones from PyPI)...")
        args = pip_args_online(["-r", str(REQUIREMENTS)])
    else:
        info("Installing packages offline from the bundled wheelhouse (this can take a minute)...")
        args = pip_args_offline(["-r", str(REQUIREMENTS)])
    progress("installing_deps", "Installing packages (clean, no cached wheels)", 78)
    # Capture output so the real pip failure reason is written to the trace log
    # (the install runs hidden, so streamed stdout would otherwise be lost).
    r = _run([str(venv_py), *args], text=True, capture_output=True)
    if r.returncode != 0:
        warn("pip install (venv) failed; see the installer log for details.")
        return None
    return str(venv_py)


def install_via_target(python_exe: str, online: bool = False) -> str | None:
    """Fallback: install into a plain lib dir and run with PYTHONPATH.

    Works with embeddable/portable Pythons that cannot create venvs. Offline by
    default; when `online` is True it also pulls missing wheels from PyPI.
    Returns the python path to launch with on success, else None.
    """
    info("Installing into a private library folder (portable mode)...")
    if not ensure_pip(python_exe):
        warn("Could not bootstrap pip for the bundled runtime.")
        return None

    # Never reuse a previously populated lib dir.
    if LIB_DIR.exists():
        shutil.rmtree(LIB_DIR, ignore_errors=True)
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    extra = ["--target", str(LIB_DIR), "-r", str(REQUIREMENTS)]
    args = pip_args_online(extra) if online else pip_args_offline(extra)
    progress("installing_deps", "Installing packages (clean, no cached wheels)", 78)
    # Capture output so the real pip failure reason lands in the trace log.
    r = _run([python_exe, *args], text=True, capture_output=True)
    if r.returncode != 0:
        warn("pip install (portable) failed; see the installer log for details.")
        return None
    return python_exe


# --------------------------------------------------------------------------
# Copy source
# --------------------------------------------------------------------------
def copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        warn(f"Missing bundle resource: {src}")
        return
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


# --------------------------------------------------------------------------
# Clean previous install (so a re-install never leaves a stale build behind)
# --------------------------------------------------------------------------
PID_FILE = INSTALL_DIR / "agent.pid"


def _kill_pid(pid: int) -> None:
    try:
        if os.name == "nt":
            _run(["taskkill", "/F", "/PID", str(pid)],
                 capture_output=True, text=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def stop_running_agent() -> None:
    """Best-effort: stop a previously-installed agent before overwriting files.

    On Windows, files held open by a running agent cannot be deleted, so this
    must happen before we remove the old build.
    """
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            info(f"Stopping running agent (pid {pid})...")
            _kill_pid(pid)
            time.sleep(2)
        except Exception:
            pass
        try:
            PID_FILE.unlink()
        except Exception:
            pass


def unregister_autostart(os_name: str) -> None:
    """Remove any existing login auto-start entry (best-effort)."""
    try:
        if os_name == "windows":
            _run(["schtasks", "/end", "/tn", "TestingToolkitAgent"],
                 capture_output=True, text=True)
            _run(["schtasks", "/delete", "/tn", "TestingToolkitAgent", "/f"],
                 capture_output=True, text=True)
            # Remove the shell:startup launcher too.
            vbs = _windows_startup_dir() / "TestingToolkitAgent.vbs"
            if vbs.exists():
                vbs.unlink()
        elif os_name == "macos":
            plist = Path.home() / "Library/LaunchAgents/com.testingtoolkit.agent.plist"
            _run(["launchctl", "unload", str(plist)],
                 capture_output=True, text=True)
            if plist.exists():
                plist.unlink()
        else:
            _run(
                ["systemctl", "--user", "stop", "testingtoolkit-agent.service"],
                capture_output=True, text=True)
            _run(
                ["systemctl", "--user", "disable", "testingtoolkit-agent.service"],
                capture_output=True, text=True)
            # Remove the XDG autostart entry too.
            desktop = Path.home() / ".config" / "autostart" / "testingtoolkit-agent.desktop"
            if desktop.exists():
                desktop.unlink()
    except Exception:
        pass


def write_update_config() -> None:
    """Persist the auto-update config so the running agent can fetch patches.

    The smart installer passes the repo + read-only token via env vars
    (TT_UPDATE_TOKEN / TT_UPDATE_REPO / TT_UPDATE_REF). We store them in
    ~/TestingToolkitWeb/update.json, which the agent's updater reads on every poll.
    Without this, auto-update is simply disabled (non-fatal).
    """
    token = os.environ.get("TT_UPDATE_TOKEN", "")
    # Repo/ref default to the known release location so a token alone is enough
    # to enable auto-update (the agent can reconstruct the URL and self-heal).
    repo = os.environ.get("TT_UPDATE_REPO", "") or "nrcharanvignesh/Testing-Toolkit"
    ref = os.environ.get("TT_UPDATE_REF", "") or "parts"
    if not token:
        info("Auto-update not configured (no token provided); skipping.")
        return
    manifest_url = (
        f"https://api.github.com/repos/{repo}/contents/agent-update.json?ref={ref}"
    )
    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        cfg = INSTALL_DIR / "update.json"
        # Store repo/ref too so the agent can rebuild the URL if needed later.
        cfg.write_text(json.dumps({
            "manifest_url": manifest_url,
            "token": token,
            "repo": repo,
            "ref": ref,
        }))
        try:
            os.chmod(cfg, 0o600)  # readable only by the user
        except Exception:
            pass
        info("Auto-update configured; the agent will fetch patches automatically.")
    except Exception as exc:  # noqa: BLE001
        warn(f"Could not write update config (non-fatal): {exc}")


def clean_previous_install(os_name: str) -> None:
    """Remove an earlier build before installing the new one.

    Only the program directories are removed (agent code, venv, portable lib).
    User data that lives alongside them in the single ~/TestingToolkitWeb root -
    settings.json, projects/, runs/, outputs/, logs/, ui_prefs.json - is
    intentionally preserved so a re-install keeps your configuration.
    """
    stop_running_agent()
    unregister_autostart(os_name)
    for d in (VENV_DIR, AGENT_DIR, LIB_DIR):
        if d.exists():
            info(f"Removing previous build: {d}")
            shutil.rmtree(d, ignore_errors=True)

    # Clean up the LEGACY split-tree install root (~/TestingToolkit, no "Web").
    # Older builds put the agent code/venv/lib there while the workspace + data
    # always lived in ~/TestingToolkitWeb, so this folder only ever held program
    # files - safe to remove its program dirs so no orphaned copy is left behind.
    # Skip entirely if the current install root IS the legacy path (custom
    # TT_INSTALL_DIR) so we never delete the build we are installing.
    legacy_root = (Path.home() / "TestingToolkit").expanduser()
    try:
        same = legacy_root.resolve() == INSTALL_DIR.resolve()
    except Exception:
        same = str(legacy_root) == str(INSTALL_DIR)
    if legacy_root.exists() and not same:
        for sub in ("venv", "agent", "lib"):
            d = legacy_root / sub
            if d.exists():
                info(f"Removing orphaned legacy build: {d}")
                shutil.rmtree(d, ignore_errors=True)
        # Remove the legacy root itself only if it is now empty (never nuke a
        # folder that still contains anything the user may have placed there).
        try:
            if not any(legacy_root.iterdir()):
                legacy_root.rmdir()
                info(f"Removed empty legacy folder: {legacy_root}")
        except Exception:
            pass


# --------------------------------------------------------------------------
# Autostart registration (best-effort, never fatal)
# --------------------------------------------------------------------------
def windowless_python(launch_python: str) -> str:
    """On Windows, return the pythonw.exe next to python.exe so the agent runs
    with NO console window. Falls back to the given interpreter if pythonw is
    not present (or on non-Windows). This is what prevents the black terminal
    window from flashing up on every login."""
    if os.name != "nt":
        return launch_python
    p = Path(launch_python)
    if p.name.lower() == "pythonw.exe":
        return launch_python
    pyw = p.with_name("pythonw.exe")
    return str(pyw) if pyw.exists() else launch_python


# Keep this marker in sync with agent/server.py so the running agent's
# self-heal recognises an install-time hardened task and won't re-register it.
_AUTOSTART_TASK_MARKER = "TT_AUTOSTART_V2"


def _windows_autostart_xml(pythonw: str, src_dir: str) -> str:
    """Task Scheduler XML that keeps the agent alive across cold boots / Fast
    Startup. Mirrors agent/server.py:_windows_autostart_xml (see that for the
    rationale behind StartWhenAvailable + the watchdog repetition)."""
    from xml.sax.saxutils import escape

    cmd = escape(pythonw)
    wd = escape(src_dir)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Testing Toolkit local agent - keeps the localhost bridge running for the web app. {_AUTOSTART_TASK_MARKER}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>2020-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{cmd}</Command>
      <Arguments>-m agent</Arguments>
      <WorkingDirectory>{wd}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


# --------------------------------------------------------------------------
# OS-agnostic per-user startup-folder registration (shell:startup style)
# --------------------------------------------------------------------------
# On top of the OS service (Task Scheduler / launchd / systemd, which provide
# the watchdog + restart behavior), we also drop a plain per-user "startup
# folder" launcher on every OS. This is the simplest, most portable autostart:
# no admin rights, no scheduler service, just a file in the login startup
# location - Windows Startup folder (shell:startup) via a hidden .vbs, Linux
# XDG autostart (~/.config/autostart/*.desktop), and macOS LaunchAgents (the
# plist register_autostart already writes IS the per-user startup entry).
# Running both layers is safe: the agent binds a fixed localhost port, so a
# duplicate launch simply fails to bind and exits.
_STARTUP_MARKER = "TT_STARTUP_V1"


def _windows_startup_dir() -> Path:
    """The per-user shell:startup folder."""
    base = os.environ.get("APPDATA")
    root = Path(base) if base else (Path.home() / "AppData" / "Roaming")
    return root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _windows_startup_vbs(pythonw: str, src_dir: str, pythonpath: str = "") -> str:
    """A .vbs that launches the agent fully hidden (window style 0, no console)
    and returns immediately so logon is never blocked. Sets cwd + PYTHONPATH so
    `-m agent` resolves for both venv and --target installs."""
    set_env = ""
    if pythonpath:
        set_env = f'WshShell.Environment("PROCESS")("PYTHONPATH") = "{pythonpath}"\n'
    return (
        f"' Testing Toolkit agent autostart ({_STARTUP_MARKER})\n"
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.CurrentDirectory = "{src_dir}"\n'
        f"{set_env}"
        f'WshShell.Run """{pythonw}"" -m agent", 0, False\n'
    )


def _linux_autostart_desktop(python_exe: str, src_dir: str, pythonpath: str = "") -> str:
    """An XDG autostart .desktop entry (the Linux startup-folder equivalent)."""
    env_prefix = f"env PYTHONPATH={pythonpath} " if pythonpath else ""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Testing Toolkit Agent\n"
        f"Exec={env_prefix}{python_exe} -m agent\n"
        f"Path={src_dir}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        f"Comment={_STARTUP_MARKER}\n"
    )


def register_startup_folder(os_name: str, launch_python: str, use_pythonpath: bool) -> None:
    """Write the simple per-user startup-folder launcher (complements the OS
    service registered by register_autostart). Best-effort and idempotent."""
    src_path = AGENT_DIR / "src"
    pythonpath = (
        os.pathsep.join([str(LIB_DIR), str(src_path)]) if use_pythonpath else ""
    )
    try:
        if os_name == "windows":
            startup = _windows_startup_dir()
            startup.mkdir(parents=True, exist_ok=True)
            pyw = windowless_python(launch_python)
            (startup / "TestingToolkitAgent.vbs").write_text(
                _windows_startup_vbs(pyw, str(src_path), pythonpath)
            )
        elif os_name == "linux":
            autostart = Path.home() / ".config" / "autostart"
            autostart.mkdir(parents=True, exist_ok=True)
            (autostart / "testingtoolkit-agent.desktop").write_text(
                _linux_autostart_desktop(launch_python, str(src_path), pythonpath)
            )
        # macOS: the LaunchAgents plist already provides the per-user startup
        # entry, so there is nothing extra to write here.
    except Exception as exc:  # noqa: BLE001
        warn(f"Could not register startup-folder launcher (non-fatal): {exc}")


def register_autostart(os_name: str, launch_python: str, use_pythonpath: bool) -> None:
    info("Registering auto-start on login...")
    src_path = AGENT_DIR / "src"
    try:
        if os_name == "windows":
            # Use pythonw.exe so Task Scheduler launches the agent headlessly
            # (a plain python.exe task pops a console window on every login).
            launch_pyw = windowless_python(launch_python)
            # Register from a hardened XML so the agent survives cold boot /
            # Windows Fast Startup (StartWhenAvailable + a 5-min watchdog that
            # only relaunches if it actually died). See _windows_autostart_xml.
            xml = _windows_autostart_xml(launch_pyw, str(src_path))
            registered = False
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".xml", encoding="utf-16", delete=False
                ) as tmp:
                    tmp.write(xml)
                    xml_path = tmp.name
                res = _run(
                    ["schtasks", "/create", "/tn", "TestingToolkitAgent",
                     "/xml", xml_path, "/f"],
                    capture_output=True, text=True,
                )
                registered = getattr(res, "returncode", 1) == 0
                try:
                    Path(xml_path).unlink()
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001
                warn(f"Hardened autostart XML failed ({exc}); using basic login task.")
            if not registered:
                # Fall back to the simple login task so autostart still works.
                cmd = f'"{launch_pyw}" -m agent'
                _run(
                    ["schtasks", "/create", "/tn", "TestingToolkitAgent",
                     "/tr", cmd, "/sc", "onlogon", "/rl", "limited", "/f"],
                    capture_output=True, text=True,
                )
        elif os_name == "macos":
            plist = Path.home() / "Library/LaunchAgents/com.testingtoolkit.agent.plist"
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(_macos_plist(launch_python, src_path))
            _run(["launchctl", "unload", str(plist)],
                 capture_output=True, text=True)
            _run(["launchctl", "load", str(plist)],
                 capture_output=True, text=True)
        else:  # linux
            unit_dir = Path.home() / ".config/systemd/user"
            unit_dir.mkdir(parents=True, exist_ok=True)
            (unit_dir / "testingtoolkit-agent.service").write_text(
                _linux_unit(launch_python, src_path)
            )
            _run(["systemctl", "--user", "daemon-reload"],
                 capture_output=True, text=True)
            _run(
                ["systemctl", "--user", "enable", "testingtoolkit-agent.service"],
                capture_output=True, text=True,
            )
    except Exception as exc:
        warn(f"Could not register auto-start (non-fatal): {exc}")

    # Belt-and-suspenders: also drop the simple OS-agnostic startup-folder
    # launcher so the agent still starts on login even if the scheduler
    # service above could not be registered.
    register_startup_folder(os_name, launch_python, use_pythonpath)


def _macos_plist(python_exe: str, workdir: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.testingtoolkit.agent</string>
  <key>ProgramArguments</key>
  <array><string>{python_exe}</string><string>-m</string><string>agent</string></array>
  <key>WorkingDirectory</key><string>{workdir}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
"""


def _linux_unit(python_exe: str, workdir: Path) -> str:
    return f"""[Unit]
Description=Testing Toolkit Agent
After=network.target

[Service]
Type=simple
WorkingDirectory={workdir}
ExecStart={python_exe} -m agent
Restart=on-failure

[Install]
WantedBy=default.target
"""


# --------------------------------------------------------------------------
# Launch + health check
# --------------------------------------------------------------------------
def _agent_env(use_pythonpath: bool) -> dict:
    """Environment shared by both the diagnostic self-test and the real launch."""
    env = os.environ.copy()
    env["TT_MODELS_DIR"] = str(AGENT_DIR / "models")
    env["TT_INSTALL_DIR"] = str(INSTALL_DIR)
    env["TT_LOG_DIR"] = str(LOG_DIR)
    # Force unbuffered output so the log file captures crashes in real time.
    env["PYTHONUNBUFFERED"] = "1"
    if use_pythonpath:
        extra = os.pathsep.join([str(LIB_DIR), str(AGENT_DIR / "src")])
        env["PYTHONPATH"] = (
            extra + os.pathsep + env.get("PYTHONPATH", "")
        ).rstrip(os.pathsep)
    return env


def _run_import_selftest(launch_python: str, env: dict, workdir: Path) -> str:
    """Synchronously import the agent to surface import-time crashes.

    Returns "" on success, or the captured error text on failure. This is the
    single most useful diagnostic: most "agent won't start" cases are a failed
    import (missing wheel, bad model path, syntax error) that is otherwise
    invisible because the real launch is detached.
    """
    probe = (
        "import importlib;"
        "importlib.import_module('agent');"
        "importlib.import_module('agent.server');"
        "print('IMPORT_OK')"
    )
    try:
        res = _run(
            [launch_python, "-c", probe],
            cwd=str(workdir), env=env,
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return f"self-test could not run: {exc}"
    if res.returncode == 0 and "IMPORT_OK" in (res.stdout or ""):
        return ""
    return ((res.stdout or "") + "\n" + (res.stderr or "")).strip()


def _print_log_tail(path: Path, lines: int = 40) -> None:
    try:
        if path.exists():
            content = path.read_text(errors="replace").splitlines()
            tail = content[-lines:]
            warn(f"--- last {len(tail)} lines of {path} ---")
            for ln in tail:
                print("    " + ln)
            warn("--- end of agent log ---")
    except Exception:
        pass


def start_agent(launch_python: str, use_pythonpath: bool) -> None:
    info(f"Starting agent on localhost:{AGENT_PORT}...")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "agent.log"
    env = _agent_env(use_pythonpath)
    workdir = AGENT_DIR / "src"

    # --- Diagnostic: import self-test before the detached launch -----------
    info("Running agent self-test (import check)...")
    progress("starting", "Verifying the agent", 96)
    err = _run_import_selftest(launch_python, env, workdir)
    if err:
        error("Agent self-test FAILED - the agent cannot import and will not start:")
        for ln in err.splitlines():
            print("    " + ln)
        try:
            log_path.write_text(
                "Agent import self-test failed:\n" + err + "\n"
            )
        except Exception:
            pass
        error(f"Full details saved to: {log_path}")
        progress("error", "Agent failed its self-test; see the installer log", 96)
        return
    ok("Self-test passed (agent imports cleanly).")

    # On a reinstall the OLD agent may still be holding port 7842. Wait for it
    # to actually free up so the new agent can bind it. The web app polls
    # /health for the real agent at this point.
    progress("starting", "Starting the agent", 97)
    for _ in range(20):
        if _port_free(AGENT_PORT):
            break
        time.sleep(0.5)

    # --- Launch the agent, capturing its output to the log file -----------
    try:
        log_file = open(log_path, "w", encoding="utf-8", errors="replace")  # noqa: SIM115
    except Exception:
        log_file = subprocess.DEVNULL  # type: ignore[assignment]
    try:
        kwargs = dict(
            cwd=str(workdir), env=env,
            stdout=log_file, stderr=subprocess.STDOUT,
        )
        run_python = launch_python
        if os.name == "nt":
            # CREATE_NO_WINDOW + pythonw.exe => no console window at all.
            run_python = windowless_python(launch_python)
            kwargs["creationflags"] = CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([run_python, "-m", "agent"], **kwargs)
    except Exception as exc:
        warn(f"Could not launch agent automatically: {exc}")
        progress("error", f"Could not launch the agent: {exc}", 97)
        return

    # Poll the health endpoint without requiring any extra dependency.
    import urllib.request

    for _ in range(30):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{AGENT_PORT}/health", timeout=1
            ) as resp:
                if resp.status == 200:
                    ok(f"Agent is running on localhost:{AGENT_PORT}")
                    ok("Return to your browser - it will connect automatically.")
                    progress("done", "Agent is running", 100)
                    return
        except Exception:
            time.sleep(1)
    progress("error", "Agent did not report healthy in time", 99)
    warn("Agent did not report healthy within 30s.")
    warn(
        "The agent imported fine but its HTTP server did not respond. "
        "The log below usually shows why (port in use, firewall, or a crash "
        "during startup):"
    )
    _print_log_tail(log_path)
    warn(f"Full log: {log_path}")


# --------------------------------------------------------------------------
# Optional playwright post-install (E2E browser automation)
# --------------------------------------------------------------------------
def _install_playwright_optional(launch_python: str, use_pythonpath: bool) -> None:
    """Install playwright from PyPI as a non-fatal optional step.

    Called after the core deps succeed. Any failure is logged and ignored so a
    corporate proxy, air-gapped network, or platform restriction never breaks the
    core install. The agent still boots without playwright; /e2e/* routes return
    503 until it is available.
    Skipped entirely when playwright>=1.44 is already present and up-to-date.
    """
    # Determine the pip executable for the installed environment.
    if os.name == "nt":
        pip_exe = VENV_DIR / "Scripts" / "python.exe"
    else:
        pip_exe = VENV_DIR / "bin" / "python"

    if not pip_exe.exists():
        # --target install: use the launch_python directly.
        pip_exe = Path(launch_python)

    if _pkg_satisfies(str(pip_exe), "playwright>=1.44"):
        ok("playwright>=1.44 already installed -- skipping.")
        return

    info("Installing playwright (optional, E2E automation)...")
    progress("installing_deps", "Installing playwright (optional)", 92)

    try:
        r = _run(
            [str(pip_exe), "-m", "pip", "install",
             *_PIP_QUIET, "playwright>=1.44"],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode != 0:
            warn("Optional playwright install from PyPI failed (non-fatal).")
            warn("E2E routes will return 503 until you run: pip install playwright && playwright install chromium")
            return
        ok("playwright installed.")
    except Exception as exc:  # noqa: BLE001
        warn(f"Optional playwright install raised an exception (non-fatal): {exc}")
        return

    # Install the Chromium browser binary (also optional/non-fatal).
    try:
        r = _run(
            [str(pip_exe), "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0:
            ok("Playwright Chromium browser installed.")
        else:
            warn("Playwright browser install failed (non-fatal). Run `playwright install chromium` manually.")
    except Exception as exc:  # noqa: BLE001
        warn(f"Playwright browser install raised an exception (non-fatal): {exc}")


def _install_cryptography_optional(launch_python: str, use_pythonpath: bool) -> None:
    """Install cryptography from the bundled wheelhouse (offline, non-fatal).

    cryptography (+ cffi/pycparser deps) is pre-downloaded as wheels committed
    in agent-bundle/wheelhouse/, so this works with zero network access. It is
    kept OUT of requirements.txt on purpose: it is only needed to Fernet-decrypt
    the bundled service key (core/app_config.py), the import is already guarded,
    and the app falls back to Manual Mode without it. A hard requirement would
    fail the ENTIRE offline install whenever a shipped bundle's wheelhouse
    predates the wheel (the 2.10.1 regression). Non-fatal either way.
    Skipped when cryptography>=42.0 is already present. Installs into the SAME
    place the agent imports from: the venv for the venv strategy, or LIB_DIR
    (via --target) for the portable strategy (use_pythonpath=True).
    """
    # Pick the interpreter + install target that match the chosen strategy.
    if use_pythonpath:
        # Portable/--target install: the agent runs with LIB_DIR on PYTHONPATH,
        # so cryptography must land there too (not in launch_python's site).
        pip_exe = Path(launch_python)
        target_args = ["--target", str(LIB_DIR)]
    else:
        if os.name == "nt":
            pip_exe = VENV_DIR / "Scripts" / "python.exe"
        else:
            pip_exe = VENV_DIR / "bin" / "python"
        if not pip_exe.exists():
            pip_exe = Path(launch_python)
        target_args = []

    if not target_args and _pkg_satisfies(str(pip_exe), "cryptography>=42.0"):
        ok("cryptography>=42.0 already installed -- skipping.")
        return

    info("Installing cryptography (optional, from bundled wheelhouse)...")
    progress("installing_crypto", "Installing cryptography (optional)", 91)

    wheelhouse = BUNDLE_DIR / "wheelhouse"
    try:
        r = _run(
            [str(pip_exe), "-m", "pip", "install",
             "--upgrade",
             *_PIP_QUIET,
             "--no-index", f"--find-links={wheelhouse}",
             *target_args,
             "cryptography>=42.0"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            ok("cryptography installed from wheelhouse.")
        else:
            warn("cryptography install from wheelhouse failed (non-fatal).")
            warn("The bundled service key can't be decrypted; the app will run "
                 "in Manual Mode until you enter credentials in Settings.")
            trace(f"pip stderr: {r.stderr[:1000]}")
    except Exception as exc:  # noqa: BLE001
        warn(f"cryptography install raised an exception (non-fatal): {exc}")


def _install_mcp_python_optional(launch_python: str, use_pythonpath: bool) -> None:
    """Install the mcp Python SDK from the bundled wheelhouse (offline, non-fatal).

    mcp>=1.0 and all its transitive deps are pre-downloaded as wheels and
    committed in agent-bundle/wheelhouse/ so this step works with zero
    network access.  It is kept separate from the core requirements.txt so
    a wheelhouse gap never blocks the primary pip install.
    Skipped entirely when mcp>=1.0 is already present at the required version.
    """
    # Determine the pip executable for the installed environment.
    if os.name == "nt":
        pip_exe = VENV_DIR / "Scripts" / "python.exe"
    else:
        pip_exe = VENV_DIR / "bin" / "python"
    if not pip_exe.exists():
        pip_exe = Path(launch_python)

    if _pkg_satisfies(str(pip_exe), "mcp>=1.0"):
        ok("mcp>=1.0 already installed -- skipping.")
        return

    info("Installing mcp Python SDK (optional, from bundled wheelhouse)...")
    progress("installing_mcp_sdk", "Installing mcp SDK (optional)", 91)

    wheelhouse = BUNDLE_DIR / "wheelhouse"

    try:
        r = _run(
            [str(pip_exe), "-m", "pip", "install",
             "--upgrade",
             *_PIP_QUIET,
             "--no-index", f"--find-links={wheelhouse}",
             "mcp>=1.0"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            ok("mcp SDK installed from wheelhouse.")
        else:
            warn("mcp SDK install from wheelhouse failed (non-fatal).")
            warn("MCP bridge will be unavailable until mcp is installed.")
            trace(f"pip stderr: {r.stderr[:1000]}")
    except Exception as exc:  # noqa: BLE001
        warn(f"mcp SDK install raised an exception (non-fatal): {exc}")


# --------------------------------------------------------------------------
# MCP server install (ADO, JIRA, Playwright) -- fully offline, PwC-safe
# --------------------------------------------------------------------------
# Everything required is bundled directly in the repo under
# agent-bundle/mcp_servers/.  Zero internet access needed.
#
# WHAT IS IN THE REPO (agent-bundle/mcp_servers/):
#   node_modules_bundle/
#     node_modules.tar.gz.000  }  pre-built node_modules for all 3 servers
#     node_modules.tar.gz.001  }  (~16MB compressed, ~88MB extracted)
#   node-bins/
#     win-x64/      node-win-x64.zip.{000-002}        Node.js v20 win-x64
#     linux-x64/    node-linux-x64.tar.gz.{000-004}   Node.js v20 linux-x64
#     darwin-arm64/ node-darwin-arm64.tar.gz.{000-004} Node.js v20 darwin
#   node-bins.json   sha256 + part file list per platform
#   package.json     pinned versions (@azure-devops/mcp, mcp-atlassian,
#                    @playwright/mcp) -- used only for online fallback
#   package-lock.json -- used only for online fallback
#
# INSTALL FLOW (in priority order):
#   1. Reassemble + extract node_modules_bundle directly to MCP_SERVERS_DIR
#      -- no npm, no Node.js, zero network, pure tar extraction
#   2. If Node.js not in PATH and not previously extracted: reassemble
#      node-bins parts from the repo and extract to MCP_SERVERS_DIR/node/
#      -- needed only for mcp_bridge to launch server processes at runtime
#   3. Online npm install as last resort (requires network + npm)
#   4. Verify all three entry points and report
# --------------------------------------------------------------------------

MCP_SERVERS_DIR = INSTALL_DIR / "mcp_servers"
_NODE_INSTALL_DIR = MCP_SERVERS_DIR / "node"


def _mcp_bundle_src() -> Path:
    """Locate the bundled mcp_servers/ directory that ships with the installer."""
    mei = getattr(sys, "_MEIPASS", "")
    if mei:
        p = Path(mei) / "mcp_servers"
        if p.is_dir():
            return p
    here = Path(__file__).resolve().parent
    p = here / "mcp_servers"
    if p.is_dir():
        return p
    p = Path.cwd() / "mcp_servers"
    if p.is_dir():
        return p
    return here / "mcp_servers"  # may not exist -- callers check


def _platform_key() -> str:
    """Return the node-bins.json platform key for the current OS/arch."""
    import platform as _plat
    system = _plat.system().lower()
    machine = _plat.machine().lower()
    if system == "windows":
        return "win32-x64"
    if system == "darwin":
        return "darwin-arm64" if ("arm" in machine or "aarch" in machine) else "darwin-x64"
    return "linux-x64"


def _find_node() -> str | None:
    """Find node: previously-extracted bundled node -> system PATH."""
    for candidate in (
        _NODE_INSTALL_DIR / "node.exe",          # win extracted
        _NODE_INSTALL_DIR / "bin" / "node",      # posix extracted
    ):
        if candidate.exists():
            return str(candidate)
    return shutil.which("node")


def _find_npm(node_exe: str) -> str | None:
    """Find npm co-located with node, then system PATH."""
    node_path = Path(node_exe)
    for candidate in (
        node_path.parent / "npm",
        node_path.parent / "npm.cmd",
    ):
        if candidate.exists():
            return str(candidate)
    return shutil.which("npm")


def _reassemble_parts(parts: list[str], src: Path, dest: Path) -> bool:
    """Concatenate split part files from src directory into dest file.

    parts: list of relative paths (e.g. 'node-bins/win-x64/node-win-x64.zip.000')
    src:   the mcp_servers bundle directory
    dest:  output file path
    """
    try:
        with open(dest, "wb") as out:
            for rel in parts:
                part_file = src / rel
                if not part_file.exists():
                    warn(f"  Missing part: {part_file}")
                    return False
                with open(part_file, "rb") as pf:
                    shutil.copyfileobj(pf, out)
        return True
    except Exception as exc:
        warn(f"  Part reassembly failed: {exc}")
        return False


def _extract_node_from_bundle() -> bool:
    """Reassemble and extract the bundled Node.js binary to _NODE_INSTALL_DIR.

    Returns True if node is now available, False on any failure (non-fatal).
    """
    import hashlib
    import zipfile
    import tarfile as _tarfile

    src = _mcp_bundle_src()
    manifest_path = src / "node-bins.json"
    if not manifest_path.exists():
        warn("node-bins.json not found in bundle; cannot auto-install Node.js.")
        return False

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except Exception as exc:
        warn(f"Failed to parse node-bins.json: {exc}")
        return False

    plat = _platform_key()
    plat_info = manifest.get("platforms", {}).get(plat)
    if not plat_info:
        warn(f"No Node.js binary bundled for platform '{plat}'.")
        return False

    parts       = plat_info["parts"]
    archive_name = plat_info["archive_name"]
    expected_sha = plat_info["sha256"]
    archive_type = plat_info["archive_type"]
    node_exe_rel = plat_info["node_exe_path"]
    version      = manifest.get("node_version", "20")

    info(f"Reassembling Node.js v{version} for {plat} ({len(parts)} parts)...")
    progress("extracting_node", f"Extracting bundled Node.js v{version}", 91)

    tmp_dir = Path(tempfile.mkdtemp(prefix="tt_node_"))
    try:
        archive_path = tmp_dir / archive_name
        if not _reassemble_parts(parts, src, archive_path):
            return False

        # Verify sha256
        h = hashlib.sha256()
        with open(archive_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        actual = h.hexdigest()
        if actual != expected_sha:
            warn(f"Node.js sha256 mismatch: expected {expected_sha}, got {actual}")
            return False
        ok("Node.js archive verified.")

        # Extract
        info(f"Extracting Node.js to {_NODE_INSTALL_DIR}...")
        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()
        if archive_type == "zip":
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(extract_dir)
        else:
            with _tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(extract_dir)

        _NODE_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        extracted_root = next(extract_dir.iterdir())
        for item in extracted_root.iterdir():
            dest = _NODE_INSTALL_DIR / item.name
            if dest.exists():
                shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            shutil.move(str(item), str(dest))

        # Make node executable on posix
        node_exe = _NODE_INSTALL_DIR / Path(*Path(node_exe_rel).parts[1:])
        if node_exe.exists() and os.name != "nt":
            node_exe.chmod(node_exe.stat().st_mode | 0o755)
        if not node_exe.exists():
            warn(f"Node.js exe not found at: {node_exe}")
            return False
        ok(f"Node.js ready: {node_exe}")
        return True

    except Exception as exc:
        warn(f"Node.js extraction failed (non-fatal): {exc}")
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _install_mcp_servers() -> None:
    """Deploy bundled MCP servers into INSTALL_DIR/mcp_servers/.

    Primary path: extract pre-built node_modules tarball directly.
    No npm, no network.  Node.js binary is also extracted from bundled
    parts so mcp_bridge can launch server processes at runtime.
    Falls back to online npm install if bundle assets are missing.
    Non-fatal: agent starts without MCP tools if everything fails.
    """
    progress("installing_mcp", "Installing MCP servers (ADO, JIRA, Playwright)", 92)

    src = _mcp_bundle_src()
    if not src.exists():
        warn(f"mcp_servers bundle directory not found at {src} (non-fatal).")
        return

    try:
        MCP_SERVERS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        warn(f"Could not create MCP_SERVERS_DIR: {exc}")
        return

    # -----------------------------------------------------------------------
    # Step 1: extract pre-built node_modules from bundle (PRIMARY, no npm)
    # -----------------------------------------------------------------------
    # Version-aware: we write a sentinel file .tt-nm-version next to
    # node_modules containing the bundled version string. If node_modules
    # exists AND the sentinel matches the bundle version, skip extraction
    # (already up-to-date). If the bundle is newer, re-extract so the
    # installed packages are never stale.
    nm_dir = MCP_SERVERS_DIR / "node_modules"
    sentinel = MCP_SERVERS_DIR / ".tt-nm-version"

    bundle_info: dict = {}
    manifest_path = src / "node-bins.json"
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                bundle_info = json.load(f)
        except Exception:
            pass

    bundled_nm_version: str = (
        bundle_info.get("node_modules_bundle", {}).get("version", "")
    )
    installed_nm_version: str = ""
    if sentinel.exists():
        try:
            installed_nm_version = sentinel.read_text().strip()
        except Exception:
            pass

    nm_up_to_date = (
        nm_dir.is_dir()
        and any(True for _ in nm_dir.iterdir())  # non-empty
        and bool(bundled_nm_version)              # bundle has a version
        and installed_nm_version == bundled_nm_version
    )

    if nm_up_to_date:
        ok(f"node_modules already at v{bundled_nm_version} -- skipping extraction.")
        nm_installed = True
    else:
        if nm_dir.is_dir() and installed_nm_version and installed_nm_version != bundled_nm_version:
            info(f"node_modules v{installed_nm_version} -> v{bundled_nm_version}: re-extracting...")
        nm_installed = False
        nm_parts = bundle_info.get("node_modules_bundle", {}).get("parts", [])
        if nm_parts:
            info(f"Extracting pre-built node_modules ({len(nm_parts)} parts)...")
            import tarfile as _tarfile
            tmp_dir = Path(tempfile.mkdtemp(prefix="tt_nm_"))
            try:
                archive = tmp_dir / "node_modules.tar.gz"
                if _reassemble_parts(nm_parts, src, archive):
                    with _tarfile.open(archive, "r:gz") as tf:
                        tf.extractall(MCP_SERVERS_DIR)
                    if bundled_nm_version:
                        try:
                            sentinel.write_text(bundled_nm_version)
                        except Exception:
                            pass
                    ok("node_modules extracted from bundle.")
                    nm_installed = nm_dir.is_dir()
                else:
                    warn("node_modules part reassembly failed.")
            except Exception as exc:
                warn(f"node_modules extraction failed: {exc}")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            info("No node_modules_bundle in manifest; will use npm install.")

    # -----------------------------------------------------------------------
    # Step 2: extract bundled Node.js binary (needed at runtime by mcp_bridge)
    # -----------------------------------------------------------------------
    node = _find_node()
    if node:
        info(f"Node.js: {node}")
    else:
        info("Node.js not in PATH. Extracting from bundled parts...")
        if _extract_node_from_bundle():
            node = _find_node()
        if not node:
            warn(
                "Node.js could not be found or extracted. "
                "MCP server processes cannot be launched at runtime. "
                "Install Node.js 20+ manually and re-run the installer."
            )
            # node_modules may still be usable if Step 1 succeeded

    # -----------------------------------------------------------------------
    # Step 3: online npm install -- last resort only
    # -----------------------------------------------------------------------
    if not nm_installed:
        warn("Pre-built node_modules not available. Trying online npm install...")
        if not node:
            warn("No Node.js available for npm install. MCP tools will be unavailable.")
        else:
            npm = _find_npm(node)
            if not npm:
                warn("npm not found. MCP tools will be unavailable.")
            else:
                _sp_kwargs: dict = {}
                if os.name == "nt":
                    _sp_kwargs = {"creationflags": CREATE_NO_WINDOW}
                for fname in ("package.json", "package-lock.json"):
                    sf = src / fname
                    df = MCP_SERVERS_DIR / fname
                    if sf.exists() and not df.exists():
                        shutil.copy2(str(sf), str(df))
                try:
                    r = _run(
                        [npm, "install",
                         "--prefix", str(MCP_SERVERS_DIR),
                         "--no-audit", "--no-fund", "--loglevel=error"],
                        capture_output=True, text=True, timeout=300,
                        **_sp_kwargs,
                    )
                    if r.returncode == 0:
                        ok("MCP npm install succeeded [online].")
                    else:
                        warn("npm install failed. MCP tools will be unavailable.")
                        trace(f"npm stderr: {r.stderr[:2000]}")
                except Exception as exc:
                    warn(f"npm install raised an exception: {exc}")

    # -----------------------------------------------------------------------
    # Step 4: verify entry points
    # -----------------------------------------------------------------------
    _MCP_VERIFY: list[tuple[str, str, str | None, str]] = [
        ("@azure-devops/mcp", "@azure-devops", "mcp",  "dist/index.js"),
        ("mcp-atlassian",     "mcp-atlassian",  None,  "dist/index.js"),
        ("@playwright/mcp",   "@playwright",    "mcp",  "cli.js"),
    ]
    for pkg, scope, name, dist in _MCP_VERIFY:
        entry = (
            MCP_SERVERS_DIR / "node_modules" / scope / name / dist
            if name else
            MCP_SERVERS_DIR / "node_modules" / scope / dist
        )
        if entry.exists():
            ok(f"  Verified: {pkg} -> {entry}")
        else:
            warn(f"  Not found: {entry} ({pkg} MCP server will be unavailable)")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Testing Toolkit offline installer")
    parser.add_argument("--no-start", action="store_true",
                        help="Install only; do not launch the agent.")
    parser.add_argument("--no-autostart", action="store_true",
                        help="Do not register a login auto-start entry.")
    args = parser.parse_args()

    # Set up the trace log FIRST so every subsequent step is recorded to disk.
    _setup_logging()

    os_name, arch = detect_platform()
    print("\nTesting Toolkit Agent Installer")
    print(f"Detailed log: {_LOG_PATH or LOG_DIR}")
    info(f"Platform: {os_name}-{arch}")
    info(f"Bundle:   {BUNDLE_DIR}")
    info(f"Install:  {INSTALL_DIR}")
    info(f"Logs:     {LOG_DIR}")
    trace(f"python={sys.executable}")
    trace(f"argv={sys.argv}")
    trace(f"platform={platform.platform()}")
    trace(
        "env: "
        + ", ".join(
            f"{k}={'<set>' if os.environ.get(k) else ''}"
            for k in (
                "TT_INSTALL_DIR",
                "TT_LOG_DIR",
                "TT_OFFLINE_ONLY",
                "TT_ENFORCE_DENSE",
                "TT_UPDATE_TOKEN",
            )
        )
    )

    # Validate the bundle is complete.
    missing = [p.name for p in (WHEELHOUSE, SRC_DIR, REQUIREMENTS) if not p.exists()]
    if missing:
        error(f"Bundle is incomplete, missing: {', '.join(missing)}")
        error("Make sure you run this from inside the full agent-bundle folder.")
        return 1

    # No local-model validation: dense embeddings + reranking are served by
    # the GenAI proxy API (kb/embeddings.py uses _APIEmbedder only). Nothing
    # to ship or check here.

    milestone("Preparing installation")
    progress("cleaning", "Preparing a clean install", 66)
    # Remove any previous build first (keeps user data) so re-installs are clean.
    clean_previous_install(os_name)
    # Never reuse previously stored packages / caches.
    purge_stale_packages()

    AGENT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Decide which Python + install strategy to use --------------------
    launch_python: str | None = None
    use_pythonpath = False

    # Which Python versions can the wheelhouse satisfy OFFLINE for this
    # platform? Drives interpreter SELECTION (prefer a matching version) and
    # per-interpreter online-fallback decisions.
    offline_only = os.environ.get("TT_OFFLINE_ONLY") == "1"
    supported = _wheelhouse_pyversions(os_name, arch)
    prefer = (
        None if (supported is None or "*" in supported or not supported)
        else supported
    )
    if supported is None:
        info(
            f"Bundled wheelhouse has no binary wheels for {os_name}-{arch}; "
            "an online fallback (PyPI) is required unless TT_OFFLINE_ONLY=1."
        )
    elif "*" in supported:
        info(f"Bundled wheelhouse is pure-python for {os_name}-{arch} (any Python).")
    elif not supported:
        warn(
            f"Bundled binary wheels for {os_name}-{arch} do not agree on a single "
            "Python version; an online fallback may be required."
        )
    else:
        info(
            f"Bundled wheelhouse covers {os_name}-{arch} offline for Python "
            f"{', '.join(sorted(supported))}."
        )

    # Prefer a system Python whose version the wheelhouse can satisfy offline.
    system_py = find_system_python(prefer)
    bundled_py = find_bundled_python(os_name, arch)

    def _covered(exe: str | None) -> bool:
        return exe is not None and wheelhouse_supports(
            os_name, arch, _py_version(exe)
        )

    def _allow_online(exe: str | None) -> bool:
        # An online fallback is allowed when NOT air-gapped and this specific
        # interpreter is not offline-covered (wrong OS/arch OR wrong version).
        return (not offline_only) and (not _covered(exe))

    milestone("Installing dependencies")

    if system_py:
        pv = _py_version(system_py)
        vtxt = f" (Python {pv[0]}.{pv[1]})" if pv else ""
        info(f"Found system Python: {system_py}{vtxt}")
        if not _covered(system_py) and not offline_only:
            warn(
                "This Python is not offline-covered by the bundle; missing "
                "wheels will be pulled from PyPI (needs internet)."
            )
        launch_python = install_via_venv(system_py)
        # Offline venv failed for this interpreter -> retry allowing PyPI.
        if not launch_python and _allow_online(system_py):
            info("Retrying venv install with online fallback...")
            shutil.rmtree(VENV_DIR, ignore_errors=True)
            launch_python = install_via_venv(system_py, online=True)

    if not launch_python and bundled_py:
        info(f"Using bundled Python: {bundled_py}")
        # Bundled runtimes are often embeddable -> use the --target strategy.
        launch_python = install_via_target(bundled_py)
        if not launch_python and _allow_online(bundled_py):
            info("Retrying portable install with online fallback...")
            launch_python = install_via_target(bundled_py, online=True)
        use_pythonpath = launch_python is not None

    if not launch_python and system_py:
        # venv path failed but we still have a real Python -> target install.
        info("Falling back to portable install with system Python...")
        launch_python = install_via_target(system_py)
        if not launch_python and _allow_online(system_py):
            info("Retrying portable install with online fallback...")
            launch_python = install_via_target(system_py, online=True)
        use_pythonpath = launch_python is not None

    if not launch_python:
        error("Could not install the agent.")
        have_py = system_py or bundled_py
        ver_hint = (
            f"Python {', '.join(sorted(supported))}"
            if (supported and "*" not in supported)
            else "Python 3.9+"
        )
        if offline_only and not _covered(have_py):
            if have_py and supported and "*" not in supported:
                error(
                    f"TT_OFFLINE_ONLY=1 is set but the bundle only ships offline "
                    f"wheels for {ver_hint} on {os_name}-{arch}; the available "
                    "interpreter is a different version. Install a matching Python "
                    "and re-run, or unset TT_OFFLINE_ONLY to allow a PyPI fallback."
                )
            else:
                error(
                    f"TT_OFFLINE_ONLY=1 is set but the bundle has no offline wheels "
                    f"for {os_name}-{arch}. Add runtime/{os_name}-{arch} + matching "
                    "wheels to the bundle, or unset TT_OFFLINE_ONLY for a PyPI "
                    "fallback."
                )
        elif not have_py:
            error(
                f"No suitable Python found. Install {ver_hint}"
                + (" (e.g. from the Microsoft Store)" if os_name == "windows" else "")
                + " and re-run."
            )
        else:
            error(
                f"The offline install failed on {os_name}-{arch} and the online "
                f"fallback could not reach PyPI. Ensure {ver_hint} is installed "
                "and this machine can reach the internet (or a private mirror), "
                "then re-run."
            )
        return 1

    # --- Optional: cryptography (bundled-key decryption) -----------------
    # Offline from the bundled wheelhouse. Kept out of requirements.txt so a
    # stale bundle wheelhouse never blocks the core install (2.10.1 regression).
    _install_cryptography_optional(launch_python, use_pythonpath)

    # --- Optional: playwright (E2E browser automation) -------------------
    # playwright is NOT in requirements.txt because it ships no Windows wheel
    # in the bundled wheelhouse and its browsers (~200 MB) can't be bundled.
    # We try to install it here from PyPI as a best-effort post-install step so
    # the E2E routes work out of the box. The main install already succeeded by
    # this point, so ANY failure here is logged but non-fatal: the agent starts
    # cleanly and all non-E2E routes work; only /e2e/* endpoints return 503
    # until the user manually runs `playwright install chromium`.
    if not offline_only:
        _install_playwright_optional(launch_python, use_pythonpath)
    else:
        info("Skipping optional playwright install (TT_OFFLINE_ONLY=1).")

    # --- Optional: mcp Python SDK from bundled wheelhouse ------------------
    # Wheels ship in agent-bundle/wheelhouse/ so this is always offline.
    # Kept outside requirements.txt so a wheelhouse gap never blocks the core.
    _install_mcp_python_optional(launch_python, use_pythonpath)

    # --- Optional MCP servers (ADO, JIRA, Playwright) via npm -------------
    # node_modules_bundle is committed in the repo -- no internet needed.
    # Non-fatal: agent starts without MCP tools; mcp_bridge degrades gracefully.
    _install_mcp_servers()

    milestone("Installing and verifying the agent")

    # --- Copy source -----------------------------------------------------
    # No local ML models are bundled: embeddings, reranking, OCR and audio
    # are all served by the GenAI proxy API (see kb/embeddings.py). The old
    # ONNX/fastembed model copy was removed in the API-first migration.
    progress("copying", "Installing agent files", 91)
    info("Installing agent source...")
    copy_tree(SRC_DIR, AGENT_DIR / "src")

    # --- Auto-update config ----------------------------------------------
    write_update_config()

    # --- Autostart + launch ----------------------------------------------
    if not args.no_autostart:
        register_autostart(os_name, launch_python, use_pythonpath)
    if not args.no_start:
        start_agent(launch_python, use_pythonpath)
    else:
        # Install-only mode (agent not started here).
        progress("done", "Installation complete", 100)

    print()
    ok("Installation complete.")
    info("The agent will auto-start on every login.")
    return 0


if __name__ == "__main__":
    import traceback as _tb

    try:
        rc = main()
        if rc != 0:
            error(f"Installer exited with code {rc}.")
            progress("error", "Installation failed; see the installer log", None)
        else:
            trace("installer finished successfully (exit 0)")
        _close_logging()
        sys.exit(rc)
    except KeyboardInterrupt:
        print()
        error("Interrupted.")
        progress("error", "Installation was interrupted", None)
        _close_logging()
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        error(f"Unexpected installer error: {exc}")
        # Record the full traceback so an unexpected crash is fully diagnosable.
        _log_line("FATAL", "Unhandled exception:\n" + _tb.format_exc())
        progress("error", f"Unexpected installer error: {exc}", None)
        _close_logging()
        sys.exit(1)
