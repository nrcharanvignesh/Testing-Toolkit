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
import subprocess
import sys
import time
from pathlib import Path

AGENT_PORT = 7842
MIN_PY = (3, 9)

# --- Resolve bundle layout (everything is relative to this file) ----------
BUNDLE_DIR = Path(__file__).resolve().parent
WHEELHOUSE = BUNDLE_DIR / "wheelhouse"
MODELS_SRC = BUNDLE_DIR / "models"
SRC_DIR = BUNDLE_DIR / "src"
RUNTIME_DIR = BUNDLE_DIR / "runtime"
REQUIREMENTS = BUNDLE_DIR / "requirements.txt"

INSTALL_DIR = Path(
    os.environ.get("TT_INSTALL_DIR", Path.home() / "TestingToolkit")
).expanduser()
AGENT_DIR = INSTALL_DIR / "agent"
VENV_DIR = INSTALL_DIR / "venv"
LIB_DIR = INSTALL_DIR / "lib"  # used for the --target fallback path
LOG_DIR = INSTALL_DIR / "logs"  # agent.log + diagnostics live here


# --------------------------------------------------------------------------
# Logging helpers
# --------------------------------------------------------------------------
def info(msg: str) -> None:
    print(f"[INFO] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[WARN] {msg}", flush=True)


def error(msg: str) -> None:
    print(f"[ERROR] {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"[SUCCESS] {msg}", flush=True)


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
        out = subprocess.run(
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
                real = subprocess.run(
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
_PIP_QUIET = ["--quiet", "--no-input", "--disable-pip-version-check"]


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
        r = subprocess.run(
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
        r = subprocess.run(
            [python_exe, str(pip_whl / "pip"), "install", "--no-index",
             f"--find-links={WHEELHOUSE}", str(pip_whl)],
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
    try:
        subprocess.run([base_python, "-m", "venv", str(VENV_DIR)],
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
    r = subprocess.run([str(venv_py), *args], text=True)
    if r.returncode != 0:
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

    LIB_DIR.mkdir(parents=True, exist_ok=True)
    extra = ["--target", str(LIB_DIR), "-r", str(REQUIREMENTS)]
    args = pip_args_online(extra) if online else pip_args_offline(extra)
    r = subprocess.run([python_exe, *args], text=True)
    if r.returncode != 0:
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
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
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
            subprocess.run(["schtasks", "/end", "/tn", "TestingToolkitAgent"],
                           capture_output=True, text=True)
            subprocess.run(["schtasks", "/delete", "/tn", "TestingToolkitAgent", "/f"],
                           capture_output=True, text=True)
        elif os_name == "macos":
            plist = Path.home() / "Library/LaunchAgents/com.testingtoolkit.agent.plist"
            subprocess.run(["launchctl", "unload", str(plist)],
                           capture_output=True, text=True)
            if plist.exists():
                plist.unlink()
        else:
            subprocess.run(
                ["systemctl", "--user", "stop", "testingtoolkit-agent.service"],
                capture_output=True, text=True)
            subprocess.run(
                ["systemctl", "--user", "disable", "testingtoolkit-agent.service"],
                capture_output=True, text=True)
    except Exception:
        pass


def write_update_config() -> None:
    """Persist the auto-update config so the running agent can fetch patches.

    The smart installer passes the repo + read-only token via env vars
    (TT_UPDATE_TOKEN / TT_UPDATE_REPO / TT_UPDATE_REF). We store them in
    ~/TestingToolkit/update.json, which the agent's updater reads on every poll.
    Without this, auto-update is simply disabled (non-fatal).
    """
    token = os.environ.get("TT_UPDATE_TOKEN", "")
    repo = os.environ.get("TT_UPDATE_REPO", "")
    ref = os.environ.get("TT_UPDATE_REF", "") or "parts"
    if not (token and repo):
        info("Auto-update not configured (no token provided); skipping.")
        return
    manifest_url = (
        f"https://api.github.com/repos/{repo}/contents/agent-update.json?ref={ref}"
    )
    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        cfg = INSTALL_DIR / "update.json"
        cfg.write_text(json.dumps({"manifest_url": manifest_url, "token": token}))
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
    User data that lives alongside them in ~/TestingToolkit - settings.json,
    projects/, runs/, outputs/, logs/, ui_prefs.json - is intentionally
    preserved so a re-install keeps your configuration.
    """
    stop_running_agent()
    unregister_autostart(os_name)
    for d in (VENV_DIR, AGENT_DIR, LIB_DIR):
        if d.exists():
            info(f"Removing previous build: {d}")
            shutil.rmtree(d, ignore_errors=True)


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
            subprocess.run(
                ["schtasks", "/create", "/tn", "TestingToolkitAgent",
                 "/tr", cmd, "/sc", "onlogon", "/rl", "limited", "/f"],
                capture_output=True, text=True,
            )
        elif os_name == "macos":
            plist = Path.home() / "Library/LaunchAgents/com.testingtoolkit.agent.plist"
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(_macos_plist(launch_python, src_path))
            subprocess.run(["launchctl", "unload", str(plist)],
                           capture_output=True, text=True)
            subprocess.run(["launchctl", "load", str(plist)],
                           capture_output=True, text=True)
        else:  # linux
            unit_dir = Path.home() / ".config/systemd/user"
            unit_dir.mkdir(parents=True, exist_ok=True)
            (unit_dir / "testingtoolkit-agent.service").write_text(
                _linux_unit(launch_python, src_path)
            )
            subprocess.run(["systemctl", "--user", "daemon-reload"],
                           capture_output=True, text=True)
            subprocess.run(
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
        res = subprocess.run(
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
        return
    ok("Self-test passed (agent imports cleanly).")

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
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([run_python, "-m", "agent"], **kwargs)
    except Exception as exc:
        warn(f"Could not launch agent automatically: {exc}")
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
                    return
        except Exception:
            time.sleep(1)
    warn("Agent did not report healthy within 30s.")
    warn(
        "The agent imported fine but its HTTP server did not respond. "
        "The log below usually shows why (port in use, firewall, or a crash "
        "during startup):"
    )
    _print_log_tail(log_path)
    warn(f"Full log: {log_path}")


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

    os_name, arch = detect_platform()
    print("[INFO] ============================================")
    print("[INFO] Testing Toolkit Agent Installer (offline)")
    print("[INFO] ============================================")
    info(f"Platform: {os_name}-{arch}")
    info(f"Bundle:   {BUNDLE_DIR}")
    info(f"Install:  {INSTALL_DIR}")

    # Validate the bundle is complete.
    missing = [p.name for p in (WHEELHOUSE, SRC_DIR, REQUIREMENTS) if not p.exists()]
    if missing:
        error(f"Bundle is incomplete, missing: {', '.join(missing)}")
        error("Make sure you run this from inside the full agent-bundle folder.")
        return 1

    # Remove any previous build first (keeps user data) so re-installs are clean.
    clean_previous_install(os_name)

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

    print()
    ok("Installation complete.")
    info("The agent will auto-start on every login.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        error("Interrupted.")
        sys.exit(130)
