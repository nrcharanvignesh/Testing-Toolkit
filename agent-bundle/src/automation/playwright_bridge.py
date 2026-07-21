"""
automation/playwright_bridge.py
Isolated Playwright Chromium session for E2E automation.

Uses Playwright's own bundled Chromium with a dedicated automation profile
directory (~/.testing_toolkit/e2e_profile/). This guarantees:
- Zero interference with the user's open Chrome/Edge windows.
- Persistent cookies/login state across E2E runs (same profile reused).
- No user-data-dir lock conflicts (automation browser is fully owned/killed).
- No risk of killing the user's real browser processes.

The user's installed Chrome/Edge binary and profile directories are detected
ONLY for informational purposes (BrowserProfile dataclass). The actual
automation always runs on Playwright's bundled Chromium at its own path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:  # playwright not installed (optional dep)
    _PLAYWRIGHT_AVAILABLE = False
    Browser = object  # type: ignore[assignment,misc]
    BrowserContext = object  # type: ignore[assignment,misc]
    Page = object  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)


def _require_playwright() -> None:
    """Raise a clear RuntimeError if playwright is not installed."""
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "playwright is not installed. Run: pip install playwright && playwright install chromium"
        )


# -------------------------------------------------------------------
# Browser profile detection (informational only -- not used for launch)
# -------------------------------------------------------------------

_SYSTEM = platform.system()


@dataclass(slots=True)
class BrowserProfile:
    """A detected browser profile on the system (informational)."""
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
# Automation profile directory (fully isolated from user's real browser)
# -------------------------------------------------------------------

_AUTOMATION_PROFILE_DIR = Path.home() / ".testing_toolkit" / "e2e_profile"


def _cleanup_singleton_lock(profile_dir: Path) -> None:
    """Remove singleton lock files left by a crashed automation session."""
    locks = ["SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile"]
    for lock_name in locks:
        lock_path = profile_dir / lock_name
        if lock_path.exists() or lock_path.is_symlink():
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def _get_playwright_chromium_path() -> Path | None:
    """Locate Playwright's bundled Chromium executable.

    Playwright stores browsers under PLAYWRIGHT_BROWSERS_PATH (env) or the
    default ms-playwright cache directory. We search for the chromium-* dir
    containing the executable.
    """
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env_path:
        candidates = [Path(env_path)]
    elif _SYSTEM == "Windows":
        candidates = [
            Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "ms-playwright",
            Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
        ]
    elif _SYSTEM == "Darwin":
        candidates = [Path.home() / "Library" / "Caches" / "ms-playwright"]
    else:
        candidates = [Path.home() / ".cache" / "ms-playwright"]

    for base in candidates:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir(), reverse=True):
            if d.is_dir() and d.name.startswith("chromium-"):
                if _SYSTEM == "Windows":
                    exe = d / "chrome-win" / "chrome.exe"
                elif _SYSTEM == "Darwin":
                    exe = d / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
                else:
                    exe = d / "chrome-linux" / "chrome"
                if exe.exists():
                    return exe
    return None


def _kill_orphaned_automation_browsers() -> None:
    """Kill any orphaned Playwright Chromium processes from prior automation runs.

    ONLY targets processes whose exe path is under the ms-playwright cache dir.
    Never touches the user's real Chrome/Edge/Firefox processes.
    """
    pw_exe = _get_playwright_chromium_path()
    if not pw_exe:
        return
    pw_dir_str = str(pw_exe.parent.parent).lower()

    if _SYSTEM == "Windows":
        try:
            # Use PowerShell Get-CimInstance (wmic removed in Win11 24H2+)
            ps_cmd = (
                "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" "
                "| Select-Object ProcessId,ExecutablePath "
                "| ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }"
            )
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            )
            for line in out.strip().splitlines():
                if "|" not in line:
                    continue
                pid_str, exe_path = line.split("|", 1)
                if pw_dir_str in exe_path.lower():
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid_str.strip()],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        _log.info("Killed orphaned automation browser PID %s", pid_str.strip())
                    except (subprocess.SubprocessError, OSError):
                        pass
        except (subprocess.SubprocessError, OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            out = subprocess.check_output(
                ["pgrep", "-af", "chrome"], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.strip().splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2 and pw_dir_str in parts[1].lower():
                    try:
                        os.kill(int(parts[0]), 9)
                        _log.info("Killed orphaned automation browser PID %s", parts[0])
                    except (ProcessLookupError, PermissionError, ValueError):
                        pass
        except (subprocess.SubprocessError, OSError):
            pass


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
    maximized: bool = True,
    reuse_session: bool = True,
) -> AsyncIterator[tuple[Browser, Page]]:
    """Launch an isolated Playwright Chromium session for E2E automation.

    Uses Playwright's bundled Chromium with a dedicated persistent profile
    at ~/.testing_toolkit/e2e_profile/. This preserves login cookies across
    runs while guaranteeing zero interference with the user's open browser
    windows.

    Yields (browser, page). On exit: browser is terminated (automation-owned).
    Video is saved to output_dir if provided.

    Args:
        profile: BrowserProfile (informational, kept for API compat).
            The actual launch always uses Playwright's bundled Chromium.
        output_dir: Directory for video recordings (optional).
        headless: Run headless if True.
        viewport_width: Viewport width for the automation context.
        viewport_height: Viewport height for the automation context.
        maximized: Launch browser window maximized (default True).
        reuse_session: Ignored (kept for API compat). Session is always fresh.
    """
    _require_playwright()
    del profile, reuse_session  # not used for isolated launch
    # When maximized, let the OS window dictate size (no_viewport=True).
    # Headless mode cannot maximize so falls back to fixed viewport.
    use_maximized = maximized and not headless

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    _AUTOMATION_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Pre-launch cleanup: kill any orphaned automation chromium + remove locks
    _kill_orphaned_automation_browsers()
    _cleanup_singleton_lock(_AUTOMATION_PROFILE_DIR)

    pw = await async_playwright().start()
    context: BrowserContext | None = None

    try:
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                ctx_opts: dict = {
                    "headless": headless,
                    "args": [
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-background-networking",
                        "--disable-client-side-phishing-detection",
                        "--disable-sync",
                        "--metrics-recording-only",
                        "--no-service-autorun",
                    ],
                }
                if use_maximized:
                    ctx_opts["args"].append("--start-maximized")
                    ctx_opts["no_viewport"] = True
                else:
                    ctx_opts["viewport"] = {
                        "width": viewport_width,
                        "height": viewport_height,
                    }
                if output_dir:
                    ctx_opts["record_video_dir"] = str(output_dir)
                    if not use_maximized:
                        ctx_opts["record_video_size"] = {
                            "width": viewport_width,
                            "height": viewport_height,
                        }

                context = await pw.chromium.launch_persistent_context(
                    str(_AUTOMATION_PROFILE_DIR),
                    **ctx_opts,
                )
                last_err = None
                break
            except Exception as e:
                last_err = e
                _log.warning(
                    "Browser launch attempt %d/3 failed: %s", attempt + 1, e
                )
                # Aggressive cleanup before retry
                _kill_orphaned_automation_browsers()
                _cleanup_singleton_lock(_AUTOMATION_PROFILE_DIR)
                if attempt < 2:
                    await asyncio.sleep(2.0 * (attempt + 1))

        if last_err is not None or context is None:
            raise RuntimeError(
                "Browser launch failed after 3 attempts. Check that Playwright "
                "Chromium is installed (playwright install chromium)."
            ) from last_err

        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        yield context.browser, page  # type: ignore[arg-type]

    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        try:
            await pw.stop()
        except Exception:
            pass


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
