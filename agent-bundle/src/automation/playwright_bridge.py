"""
automation/playwright_bridge.py
CDP attach to the user's real Chrome/Edge browser for SSO preservation.

Instead of ephemeral profiles (which lose SSO/MFA state), this module:
1. Detects installed Chrome/Edge and user profiles.
2. Launches the browser with --remote-debugging-port on the real profile.
3. Connects Playwright via CDP so SSO cookies, extensions, MFA state persist.
4. Handles singleton lock cleanup for profiles locked by crashed sessions.

SECURITY: The user's real cookies/session are used intentionally for SSO.
Video output dir is separate from the profile. Passwords never logged.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


# -------------------------------------------------------------------
# Browser profile detection
# -------------------------------------------------------------------

_SYSTEM = platform.system()


@dataclass(slots=True)
class BrowserProfile:
    """A detected browser profile on the system."""
    browser: str       # "chrome" | "edge"
    channel: str       # playwright channel: "chrome" | "msedge"
    exe_path: Path     # full path to browser executable
    profile_dir: Path  # user-data-dir (parent of Default/)
    profile_name: str  # "Default" | "Profile 1" etc.


def detect_browser_profiles() -> list[BrowserProfile]:
    """Detect installed Chrome/Edge profiles on the system."""
    profiles: list[BrowserProfile] = []
    if _SYSTEM == "Windows":
        profiles.extend(_detect_windows_profiles())
    elif _SYSTEM == "Darwin":
        profiles.extend(_detect_macos_profiles())
    else:
        profiles.extend(_detect_linux_profiles())
    return profiles


def _detect_windows_profiles() -> list[BrowserProfile]:
    """Detect Chrome/Edge profiles on Windows."""
    results: list[BrowserProfile] = []
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    if not local_app.exists():
        return results

    # Edge
    edge_dir = local_app / "Microsoft" / "Edge" / "User Data"
    edge_exe = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not edge_exe.exists():
        edge_exe = Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
    if edge_dir.exists() and edge_exe.exists():
        for pname in _find_profile_dirs(edge_dir):
            results.append(BrowserProfile(
                browser="edge",
                channel="msedge",
                exe_path=edge_exe,
                profile_dir=edge_dir,
                profile_name=pname,
            ))

    # Chrome
    chrome_dir = local_app / "Google" / "Chrome" / "User Data"
    chrome_exe = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    if not chrome_exe.exists():
        chrome_exe = Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe")
    if chrome_dir.exists() and chrome_exe.exists():
        for pname in _find_profile_dirs(chrome_dir):
            results.append(BrowserProfile(
                browser="chrome",
                channel="chrome",
                exe_path=chrome_exe,
                profile_dir=chrome_dir,
                profile_name=pname,
            ))

    return results


def _detect_macos_profiles() -> list[BrowserProfile]:
    """Detect Chrome/Edge profiles on macOS."""
    results: list[BrowserProfile] = []
    home = Path.home()

    # Edge
    edge_dir = home / "Library/Application Support/Microsoft Edge"
    edge_exe = Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    if edge_dir.exists() and edge_exe.exists():
        for pname in _find_profile_dirs(edge_dir):
            results.append(BrowserProfile(
                browser="edge",
                channel="msedge",
                exe_path=edge_exe,
                profile_dir=edge_dir,
                profile_name=pname,
            ))

    # Chrome
    chrome_dir = home / "Library/Application Support/Google/Chrome"
    chrome_exe = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if chrome_dir.exists() and chrome_exe.exists():
        for pname in _find_profile_dirs(chrome_dir):
            results.append(BrowserProfile(
                browser="chrome",
                channel="chrome",
                exe_path=chrome_exe,
                profile_dir=chrome_dir,
                profile_name=pname,
            ))

    return results


def _detect_linux_profiles() -> list[BrowserProfile]:
    """Detect Chrome/Edge profiles on Linux."""
    results: list[BrowserProfile] = []
    home = Path.home()

    # Edge
    edge_dir = home / ".config/microsoft-edge"
    edge_exe = shutil.which("microsoft-edge") or shutil.which("microsoft-edge-stable")
    if edge_dir.exists() and edge_exe:
        for pname in _find_profile_dirs(edge_dir):
            results.append(BrowserProfile(
                browser="edge",
                channel="msedge",
                exe_path=Path(edge_exe),
                profile_dir=edge_dir,
                profile_name=pname,
            ))

    # Chrome
    chrome_dir = home / ".config/google-chrome"
    chrome_exe = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if chrome_dir.exists() and chrome_exe:
        for pname in _find_profile_dirs(chrome_dir):
            results.append(BrowserProfile(
                browser="chrome",
                channel="chrome",
                exe_path=Path(chrome_exe),
                profile_dir=chrome_dir,
                profile_name=pname,
            ))

    return results


def _find_profile_dirs(user_data_dir: Path) -> list[str]:
    """Find profile subdirectories (Default, Profile 1, etc.)."""
    names: list[str] = []
    if (user_data_dir / "Default").is_dir():
        names.append("Default")
    for d in sorted(user_data_dir.iterdir()):
        if d.is_dir() and d.name.startswith("Profile "):
            names.append(d.name)
    return names if names else ["Default"]


# -------------------------------------------------------------------
# Singleton lock cleanup
# -------------------------------------------------------------------

def _cleanup_singleton_lock(profile_dir: Path, profile_name: str) -> None:
    """Remove singleton lock files left by a crashed browser session.

    Chrome/Edge use 'SingletonLock' (Linux/Mac) or 'lockfile' (Windows)
    to prevent multiple instances on the same profile. If the browser
    crashed, this file remains and blocks new launches.
    """
    locks = ["SingletonLock", "SingletonSocket", "SingletonCookie"]
    for lock_name in locks:
        lock_path = profile_dir / lock_name
        if lock_path.exists() or lock_path.is_symlink():
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
    # Windows-specific lock
    parent_lock = profile_dir / "lockfile"
    if parent_lock.exists():
        try:
            parent_lock.unlink(missing_ok=True)
        except OSError:
            pass


# -------------------------------------------------------------------
# Port management
# -------------------------------------------------------------------

_CDP_PORT_START = 9222
_CDP_PORT_END = 9260


def _find_free_port(start: int = _CDP_PORT_START, end: int = _CDP_PORT_END) -> int:
    """Find a free TCP port in the given range for CDP."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    # ponytail: fallback to OS-assigned; proper error if range exhaustion matters
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -------------------------------------------------------------------
# CDP session file (persist across runs for reattach)
# -------------------------------------------------------------------

_SESSION_DIR = Path.home() / ".testing_toolkit"
_SESSION_FILE = _SESSION_DIR / ".cdp_session.json"


@dataclass(slots=True)
class CdpSession:
    """Persisted CDP session info for reattach."""
    pid: int
    port: int
    ws_endpoint: str
    browser: str
    profile_name: str


def _save_session(session: CdpSession) -> None:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _SESSION_FILE.write_text(json.dumps({
        "pid": session.pid,
        "port": session.port,
        "ws_endpoint": session.ws_endpoint,
        "browser": session.browser,
        "profile_name": session.profile_name,
    }), encoding="utf-8")


def _load_session() -> CdpSession | None:
    if not _SESSION_FILE.exists():
        return None
    try:
        data = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        return CdpSession(
            pid=data["pid"],
            port=data["port"],
            ws_endpoint=data["ws_endpoint"],
            browser=data["browser"],
            profile_name=data["profile_name"],
        )
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _clear_session() -> None:
    _SESSION_FILE.unlink(missing_ok=True)


def _is_port_open(port: int) -> bool:
    """Check if a port is accepting connections (browser still alive)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except (OSError, ConnectionRefusedError):
            return False


# -------------------------------------------------------------------
# Browser launch + CDP connect
# -------------------------------------------------------------------

def _launch_browser(profile: BrowserProfile, port: int) -> subprocess.Popen:
    """Launch Chrome/Edge with remote debugging on the user's real profile."""
    _cleanup_singleton_lock(profile.profile_dir, profile.profile_name)

    args = [
        str(profile.exe_path),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile.profile_dir}",
        f"--profile-directory={profile.profile_name}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-service-autorun",
    ]

    kwargs: dict = {}
    if _SYSTEM == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs
    )
    return proc


def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    """Wait until the CDP port is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_port_open(port):
            return True
        time.sleep(0.3)
    return False


# -------------------------------------------------------------------
# Public API: async context manager
# -------------------------------------------------------------------

@asynccontextmanager
async def browser_session(
    profile: BrowserProfile | None = None,
    output_dir: Path | None = None,
    *,
    headless: bool = False,
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    reuse_session: bool = True,
) -> AsyncIterator[tuple[Browser, Page]]:
    """Connect to the user's real browser via CDP for SSO preservation.

    Yields (browser, page). On exit: page and context are closed but the
    browser process is LEFT RUNNING (user's real browser stays open).
    Video is saved to output_dir if provided.

    Args:
        profile: BrowserProfile to use. If None, auto-detects (prefers Edge).
        output_dir: Directory for video recordings (optional).
        headless: Ignored for CDP attach (browser is always visible).
        viewport_width: Viewport width for new context.
        viewport_height: Viewport height for new context.
        reuse_session: Try to reattach to an existing CDP session first.
    """
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Auto-detect profile if not specified
    if profile is None:
        profiles = detect_browser_profiles()
        if not profiles:
            raise RuntimeError(
                "No Chrome or Edge browser detected. "
                "Install Chrome or Edge to use E2E automation."
            )
        # Prefer Edge Default, then Chrome Default
        edge_default = [p for p in profiles
                        if p.browser == "edge" and p.profile_name == "Default"]
        chrome_default = [p for p in profiles
                         if p.browser == "chrome" and p.profile_name == "Default"]
        profile = (edge_default or chrome_default or profiles)[0]

    browser: Browser | None = None
    context: BrowserContext | None = None
    proc: subprocess.Popen | None = None
    pw = await async_playwright().start()

    try:
        port: int | None = None
        connected = False

        # Try reattaching to existing session
        if reuse_session:
            existing = _load_session()
            if (existing and existing.browser == profile.browser
                    and _is_port_open(existing.port)):
                port = existing.port
                try:
                    browser = await pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{port}"
                    )
                    connected = True
                except Exception:
                    _clear_session()
                    connected = False

        # Launch fresh if not connected
        if not connected:
            port = _find_free_port()
            proc = _launch_browser(profile, port)
            if not _wait_for_port(port, timeout=20.0):
                raise RuntimeError(
                    f"Browser failed to start CDP on port {port}. "
                    "Close any existing browser windows and retry."
                )
            browser = await pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
            _save_session(CdpSession(
                pid=proc.pid,
                port=port,
                ws_endpoint=f"http://127.0.0.1:{port}",
                browser=profile.browser,
                profile_name=profile.profile_name,
            ))

        # New context for test isolation (separate from user's main tabs)
        ctx_opts: dict = {
            "viewport": {"width": viewport_width, "height": viewport_height},
        }
        if output_dir:
            ctx_opts["record_video_dir"] = str(output_dir)
            ctx_opts["record_video_size"] = {
                "width": viewport_width,
                "height": viewport_height,
            }

        context = await browser.new_context(**ctx_opts)
        page = await context.new_page()

        yield browser, page

    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await pw.stop()
        except Exception:
            pass
        # NOTE: Browser process left running - it is the user's real browser.


def get_default_profile() -> BrowserProfile | None:
    """Get the default browser profile (Edge preferred, then Chrome)."""
    profiles = detect_browser_profiles()
    if not profiles:
        return None
    edge_default = [p for p in profiles
                    if p.browser == "edge" and p.profile_name == "Default"]
    chrome_default = [p for p in profiles
                     if p.browser == "chrome" and p.profile_name == "Default"]
    return (edge_default or chrome_default or profiles)[0]
