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
      models/             <- ONNX models
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
MODELS_SRC = BUNDLE_DIR / "models"
# The two local models that dense indexing requires (Hugging Face cache folder
# names). Both must ship in the bundle so retrieval runs fully offline.
REQUIRED_MODELS = (
    "models--qdrant--bge-small-en-v1.5-onnx-q",   # dense embedder
    "models--Xenova--ms-marco-MiniLM-L-6-v2",     # cross-encoder reranker
)
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
    print(f"[INFO] {msg}", flush=True)
    _log_line("INFO", msg)


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
    """Print a single progress line to the console.

    The installer runs in a visible terminal, so progress is shown to the user
    directly on stdout (there is no install beacon / shared progress file). Any
    extra kwargs are accepted for forward-compatibility and ignored. Never
    raises: progress reporting must never break an install.
    """
    try:
        if percent is not None:
            print("  [%3d%%] %s" % (max(0, min(100, round(percent))), message))
        else:
            print("  %s" % message)
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
    # Transient model cache fastembed may have written on a previous run (the
    # old offline-failure source). The real models ship in the bundle.
    transient = [
        Path(tempfile.gettempdir()) / "fastembed_cache",
        INSTALL_DIR / ".cache",
    ]
    for c in transient:
        try:
            if c.exists():
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
def _py_ok(exe: str) -> bool:
    """True if `exe` is a runnable Python >= MIN_PY."""
    try:
        out = _run(
            [exe, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return False
    if out.returncode != 0:
        return False
    try:
        major, minor = (int(x) for x in out.stdout.strip().split("."))
    except ValueError:
        return False
    return (major, minor) >= MIN_PY


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


def find_system_python() -> str | None:
    """Find a venv-capable Python already on the machine."""
    names = ["python3", "python"]
    if os.name == "nt":
        names = ["py", "python", "python3"]
    for name in names:
        exe = shutil.which(name)
        if not exe:
            continue
        # `py` is a launcher; normalise to a real interpreter path.
        if Path(name).stem == "py":
            try:
                real = _run(
                    [exe, "-3", "-c", "import sys;print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if real.returncode == 0 and real.stdout.strip():
                    exe = real.stdout.strip()
            except Exception:
                pass
        if _py_ok(exe):
            return exe
    return None


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
    return [
        "-m",
        "pip",
        "install",
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
        *_PIP_QUIET,
        f"--find-links={WHEELHOUSE}",
        *extra,
    ]


def wheelhouse_supports(os_name: str, arch: str) -> bool:
    """Heuristic: does the bundled wheelhouse contain wheels for this platform?

    The bundle historically ships Windows/amd64 wheels only. Any binary
    (non-pure-python) wheel encodes its platform in the filename, e.g.
    `onnxruntime-1.17-cp311-cp311-win_amd64.whl`. If we find binary wheels but
    none whose platform tag matches this OS/arch, an offline install will fail,
    so callers should allow an online fallback.
    """
    if not WHEELHOUSE.is_dir():
        return False
    tag_os = {"windows": "win", "macos": "macosx", "linux": "linux"}.get(os_name, os_name)
    tag_arch = {"amd64": ("amd64", "x86_64"), "arm64": ("arm64", "aarch64")}.get(
        arch, (arch,)
    )
    binary_wheels = False
    for whl in WHEELHOUSE.glob("*.whl"):
        name = whl.name.lower()
        # Pure-python wheels (`...-py3-none-any.whl`) work everywhere; ignore.
        if name.endswith("-none-any.whl"):
            continue
        binary_wheels = True
        if tag_os in name and any(a in name for a in tag_arch):
            return True
    # If there are no binary wheels at all, pure-python deps install anywhere.
    return not binary_wheels


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
# Copy source + models
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


def register_autostart(os_name: str, launch_python: str, use_pythonpath: bool) -> None:
    info("Registering auto-start on login...")
    src_path = AGENT_DIR / "src"
    try:
        if os_name == "windows":
            # Use pythonw.exe so Task Scheduler launches the agent headlessly
            # (a plain python.exe task pops a console window on every login).
            launch_pyw = windowless_python(launch_python)
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


def _model_populated(model_dir: Path) -> bool:
    """True if a bundled HF model cache folder has at least one non-empty
    snapshot (i.e. the model files are really present, not just an empty dir)."""
    snapshots = model_dir / "snapshots"
    try:
        if not snapshots.is_dir():
            return False
        for snap in snapshots.iterdir():
            if snap.is_dir() and any(snap.iterdir()):
                return True
    except OSError:
        return False
    return False


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
    print("[INFO] ============================================")
    print("[INFO] Testing Toolkit Agent Installer (offline)")
    print("[INFO] ============================================")
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

    # Validate BOTH local models ship in the bundle. Dense indexing is enforced
    # (TT_ENFORCE_DENSE), so a model-less install would fail at index time -
    # catch it here and fail loudly up front instead. Set TT_ENFORCE_DENSE=0 to
    # allow a lexical-only install (models become optional, warn-only).
    enforce_dense = (os.environ.get("TT_ENFORCE_DENSE", "1").strip() or "1") != "0"
    missing_models = [
        name for name in REQUIRED_MODELS
        if not _model_populated(MODELS_SRC / name)
    ]
    if missing_models:
        if enforce_dense:
            error("Bundle is missing required local model(s): "
                  f"{', '.join(missing_models)}")
            error("Dense indexing is enforced and needs both bundled models. "
                  "Re-download the full installer, or set TT_ENFORCE_DENSE=0 to "
                  "install with lexical-only retrieval.")
            return 1
        warn("Local model(s) missing: " + ", ".join(missing_models) +
             ". Installing anyway (TT_ENFORCE_DENSE=0); retrieval will be "
             "lexical-only until the models are present.")

    progress("cleaning", "Preparing a clean install", 66)
    # Remove any previous build first (keeps user data) so re-installs are clean.
    clean_previous_install(os_name)
    # Never reuse previously stored packages / caches.
    purge_stale_packages()

    AGENT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Decide which Python + install strategy to use --------------------
    launch_python: str | None = None
    use_pythonpath = False

    system_py = find_system_python()
    bundled_py = find_bundled_python(os_name, arch)

    # Decide whether an online fallback is permitted. The bundle ships
    # Windows/amd64 wheels, so on other OSes/arches the offline install can't
    # satisfy binary deps; there we allow pip to pull the missing wheels from
    # PyPI (bundled wheels are still preferred). Set TT_OFFLINE_ONLY=1 to force
    # a strictly offline install (e.g. on an air-gapped network).
    offline_only = os.environ.get("TT_OFFLINE_ONLY") == "1"
    covered = wheelhouse_supports(os_name, arch)
    allow_online = (not offline_only) and (not covered)
    if covered:
        info(f"Bundled wheelhouse covers {os_name}-{arch} (offline install).")
    elif offline_only:
        warn(
            f"Wheelhouse may not cover {os_name}-{arch}, but TT_OFFLINE_ONLY=1 "
            "is set; staying strictly offline."
        )
    else:
        warn(
            f"Bundled wheels do not cover {os_name}-{arch}; will pull any "
            "missing wheels from PyPI as a fallback (needs internet)."
        )

    if system_py:
        info(f"Found system Python: {system_py}")
        launch_python = install_via_venv(system_py)
        # Offline venv failed on an uncovered platform -> retry allowing PyPI.
        if not launch_python and allow_online:
            info("Retrying venv install with online fallback...")
            shutil.rmtree(VENV_DIR, ignore_errors=True)
            launch_python = install_via_venv(system_py, online=True)

    if not launch_python and bundled_py:
        info(f"Using bundled Python: {bundled_py}")
        # Bundled runtimes are often embeddable -> use the --target strategy.
        launch_python = install_via_target(bundled_py)
        if not launch_python and allow_online:
            info("Retrying portable install with online fallback...")
            launch_python = install_via_target(bundled_py, online=True)
        use_pythonpath = launch_python is not None

    if not launch_python and system_py:
        # venv path failed but we still have a real Python -> target install.
        info("Falling back to portable install with system Python...")
        launch_python = install_via_target(system_py)
        if not launch_python and allow_online:
            info("Retrying portable install with online fallback...")
            launch_python = install_via_target(system_py, online=True)
        use_pythonpath = launch_python is not None

    if not launch_python:
        error("Could not install the agent.")
        if offline_only and not covered:
            error(
                f"TT_OFFLINE_ONLY=1 is set but the bundle has no {os_name}-{arch} "
                f"wheels. Either add runtime/{os_name}-{arch} + matching wheels to "
                "the bundle, or unset TT_OFFLINE_ONLY to allow a PyPI fallback."
            )
        elif os_name != "windows":
            error(
                f"The offline install failed on {os_name}-{arch} and the online "
                "fallback could not reach PyPI. Install a local Python 3.9+ and "
                "ensure this machine can reach the internet (or a private mirror), "
                "then re-run."
            )
        else:
            error("Install Python 3.9+ (e.g. from the Microsoft Store) and re-run.")
        return 1

    # --- Copy source + models --------------------------------------------
    progress("copying", "Installing agent files", 91)
    info("Installing agent source...")
    copy_tree(SRC_DIR, AGENT_DIR / "src")
    info("Installing ONNX models...")
    copy_tree(MODELS_SRC, AGENT_DIR / "models")

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
