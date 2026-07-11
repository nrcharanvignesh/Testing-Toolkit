"""
server.py
Local compute agent for Testing Toolkit web app.
FastAPI on localhost:7842 - serves as the bridge between the Vercel
frontend and the existing Python backend modules.

Starts with: python -m agent.server
"""

from __future__ import annotations

import os
import platform
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Ensure src/ is on the path so existing modules resolve.
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.app_config import APP_VERSION, WORKSPACE, ensure_workspace
from agent.version import AGENT_VERSION, AGENT_PORT


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: ensure workspace exists and preload optional runtime models.

    Every step is best-effort: a failure here must NEVER stop the server from
    coming up, otherwise the browser can never reach /health and the app stays
    stuck on the onboarding screen even though the agent "installed".
    """
    try:
        ensure_workspace()
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] ensure_workspace failed (non-fatal): {exc}", flush=True)

    # Ensure the structured rotating log exists no matter how the agent was
    # started. main() also calls this, but when the process is launched by
    # importing `app` directly (uvicorn, a launcher, or an older entrypoint)
    # main() never runs, leaving log_path()/tail_log() at None -> the in-app
    # "Recent log" shows "(no log file configured)". init_logging() is
    # idempotent, so calling it here as well is safe.
    try:
        from core.app_logging import init_logging
        lp = init_logging()
        if lp is not None:
            print(f"[agent] log file: {lp}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] could not init structured logging (non-fatal): {exc}", flush=True)

    # Preload ONNX models in the background so a slow/failed load never blocks
    # the HTTP server (and therefore the health check) from starting.
    def _bg_preload() -> None:
        try:
            from agent.model_loader import preload_models
            preload_models()
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] model preload failed (non-fatal): {exc}", flush=True)

    threading.Thread(target=_bg_preload, daemon=True, name="preload").start()

    try:
        from agent.routes.kb import recover_interrupted_kb_jobs
        resumed = recover_interrupted_kb_jobs()
        if resumed:
            print(f"[agent] resumed {resumed} interrupted KB job(s)", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] KB recovery skipped (non-fatal): {exc}", flush=True)

    # Updates are intentionally detection-only. The browser checks the manifest
    # through GET /update/status and directs users to the installer when a newer
    # version exists; a running agent never patches or restarts itself.

    # Self-heal the Windows login task so it (a) launches headlessly (pythonw),
    # and (b) actually survives a cold boot / Windows Fast Startup. Older tasks
    # were a bare `/sc onlogon` with no catch-up or watchdog, so a missed logon
    # trigger (the usual Fast-Startup resume) left the agent down forever.
    # Re-registering here means existing users get the fix automatically after
    # the agent auto-updates - no reinstall.
    try:
        _ensure_windowless_autostart()
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] autostart self-heal skipped (non-fatal): {exc}", flush=True)

    # Also drop the simple OS-agnostic startup-folder launcher (shell:startup
    # on Windows, XDG autostart on Linux). This is the most portable autostart
    # and works even if the scheduler task could not be created. Existing users
    # get it automatically after the agent auto-updates - no reinstall.
    try:
        _ensure_startup_folder_entry()
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] startup-folder self-heal skipped (non-fatal): {exc}", flush=True)

    yield


# Bump this when the autostart XML changes so existing tasks get re-registered
# by the self-heal check below.
_AUTOSTART_TASK_MARKER = "TT_AUTOSTART_V2"


def _windows_autostart_xml(pythonw: str, src_dir: str) -> str:
    """Build a Task Scheduler XML that keeps the agent alive across cold boots.

    Key resilience settings vs. the old `schtasks /sc onlogon` one-liner:
      * StartWhenAvailable - if the logon trigger is missed (Windows Fast
        Startup resumes a hibernated session instead of firing a fresh logon),
        the task runs as soon as the scheduler notices it didn't.
      * A repeating watchdog trigger (every 5 min, indefinitely) combined with
        MultipleInstancesPolicy=IgnoreNew: while the agent is alive its task
        instance is alive so repeats are ignored; if it ever died/never started
        the next repeat relaunches it within 5 minutes.
      * RestartOnFailure and ExecutionTimeLimit=PT0S (no 3-day kill).
      * WorkingDirectory=src so `-m agent` always resolves the package.
    The marker in <Description> lets the self-heal check detect an already
    hardened task and skip re-registering.
    """
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


def _ensure_windowless_autostart() -> None:
    """On Windows, ensure the 'TestingToolkitAgent' task is headless (pythonw)
    AND resilient to cold boot / Fast Startup. Best-effort and idempotent."""
    if os.name != "nt":
        return
    import subprocess
    import tempfile

    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    launch = str(pyw) if pyw.exists() else str(exe)
    src_dir = str(_SRC_DIR)

    # Skip only when the task is BOTH headless and already the hardened version.
    try:
        existing = subprocess.run(
            ["schtasks", "/query", "/tn", "TestingToolkitAgent", "/xml"],
            capture_output=True, text=True, timeout=15,
        )
        out = (existing.stdout or "")
        if (
            existing.returncode == 0
            and "pythonw.exe" in out.lower()
            and _AUTOSTART_TASK_MARKER in out
        ):
            return  # already headless + hardened
    except Exception:
        pass

    xml = _windows_autostart_xml(launch, src_dir)
    tmp_path: Path | None = None
    try:
        # schtasks /xml wants a Unicode (UTF-16) file.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", encoding="utf-16", delete=False
        ) as tmp:
            tmp.write(xml)
            tmp_path = Path(tmp.name)
        res = subprocess.run(
            ["schtasks", "/create", "/tn", "TestingToolkitAgent",
             "/xml", str(tmp_path), "/f"],
            capture_output=True, text=True,
        )
        if res.returncode == 0:
            print("[agent] re-registered autostart task (headless + cold-boot resilient).", flush=True)
        else:
            # Fall back to the old one-liner so we never leave the user with no
            # autostart at all if the XML registration is rejected.
            subprocess.run(
                ["schtasks", "/create", "/tn", "TestingToolkitAgent",
                 "/tr", f'"{launch}" -m agent', "/sc", "onlogon",
                 "/rl", "limited", "/f"],
                capture_output=True, text=True,
            )
            print(f"[agent] hardened autostart rejected ({(res.stderr or '').strip()}); kept basic login task.", flush=True)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


# Bump when the startup-folder launcher format changes so existing entries get
# rewritten by the self-heal check below.
_STARTUP_MARKER = "TT_STARTUP_V1"


def _windows_startup_vbs(pythonw: str, src_dir: str, pythonpath: str = "") -> str:
    """A .vbs that launches the agent fully hidden and returns immediately so
    logon is never blocked. Mirrors install.py:_windows_startup_vbs."""
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
    """XDG autostart entry. Mirrors install.py:_linux_autostart_desktop."""
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


def _ensure_startup_folder_entry() -> None:
    """Ensure the per-user startup-folder launcher exists (Windows shell:startup
    .vbs / Linux XDG autostart .desktop). Best-effort and idempotent: only
    writes when the file is missing or out of date. The currently-running agent
    was launched with a correct environment, so we reuse its PYTHONPATH."""
    src_dir = str(_SRC_DIR)
    pythonpath = os.environ.get("PYTHONPATH", "")

    if os.name == "nt":
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        launch = str(pyw) if pyw.exists() else str(exe)
        base = os.environ.get("APPDATA")
        root = Path(base) if base else (Path.home() / "AppData" / "Roaming")
        startup = root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        target = startup / "TestingToolkitAgent.vbs"
        desired = _windows_startup_vbs(launch, src_dir, pythonpath)
    elif sys.platform.startswith("linux"):
        startup = Path.home() / ".config" / "autostart"
        target = startup / "testingtoolkit-agent.desktop"
        desired = _linux_autostart_desktop(sys.executable, src_dir, pythonpath)
    else:
        # macOS: the LaunchAgents plist already provides the startup entry.
        return

    try:
        if target.exists() and target.read_text() == desired:
            return  # already current
        startup.mkdir(parents=True, exist_ok=True)
        target.write_text(desired)
        print(f"[agent] wrote startup-folder launcher: {target}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] could not write startup-folder launcher (non-fatal): {exc}", flush=True)


app = FastAPI(
    title="Testing Toolkit Agent",
    version=AGENT_VERSION,
    lifespan=_lifespan,
)

# Allow the Vercel frontend (any origin) to call localhost.
#
# NOTE: with allow_credentials=True the spec forbids a literal "*" origin, so we
# echo the caller's Origin back via allow_origin_regex=".*". Without this, the
# browser drops the response and the app can never connect.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=600,
)


class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    """Make the HTTPS frontend -> http://127.0.0.1 call work in modern browsers.

    Chrome/Edge enforce Private Network Access (PNA): when a page on a public
    origin calls a loopback address, the browser sends a CORS preflight that
    includes `Access-Control-Request-Private-Network: true` and REQUIRES the
    response to carry `Access-Control-Allow-Private-Network: true`. FastAPI's
    CORSMiddleware does not emit that header, so without this the connection is
    silently blocked even though the agent is running. This is the usual cause
    of "I installed it but the site never connects" on corporate machines.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" and request.headers.get(
            "access-control-request-private-network"
        ):
            origin = request.headers.get("origin", "*")
            req_headers = request.headers.get(
                "access-control-request-headers", "*"
            )
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "*",
                    "Access-Control-Allow-Headers": req_headers,
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Allow-Private-Network": "true",
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


# Added AFTER CORS so it wraps it (outermost) and can answer the PNA preflight.
app.add_middleware(PrivateNetworkAccessMiddleware)

# -- Register route modules --
from agent.routes.health import router as health_router
from agent.routes.settings import router as settings_router
from agent.routes.ado import router as ado_router
from agent.routes.jira import router as jira_router
from agent.routes.sources import router as sources_router
from agent.routes.kb import router as kb_router
from agent.routes.llm import router as llm_router
from agent.routes.chat import router as chat_router
from agent.routes.generate import router as generate_router
from agent.routes.defects import router as defects_router
from agent.routes.credentials import router as credentials_router
from agent.routes.e2e import router as e2e_router
from agent.routes.jobs import router as jobs_router
from agent.routes.tools import router as tools_router
from agent.routes.artifacts import router as artifacts_router
from agent.routes.update import router as update_router

app.include_router(health_router)
app.include_router(settings_router, prefix="/settings")
app.include_router(ado_router, prefix="/ado")
app.include_router(jira_router, prefix="/jira")
app.include_router(sources_router, prefix="/sources")
app.include_router(kb_router, prefix="/kb")
app.include_router(llm_router, prefix="/llm")
app.include_router(chat_router, prefix="/chat")
app.include_router(generate_router, prefix="/generate")
app.include_router(defects_router, prefix="/defects")
app.include_router(credentials_router, prefix="/credentials")
app.include_router(e2e_router, prefix="/e2e")
app.include_router(jobs_router, prefix="/jobs")
app.include_router(tools_router, prefix="/tools")
app.include_router(artifacts_router, prefix="/artifacts")
app.include_router(update_router, prefix="/update")


def _install_dir() -> Path:
    override = (os.environ.get("TT_INSTALL_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    # Keep the agent's runtime files (pid, update.json) alongside
    # everything else under the single TestingToolkitWeb workspace.
    return WORKSPACE


def _write_pid_file() -> None:
    """Record our PID so the installer can stop us cleanly on re-install."""
    try:
        install_dir = _install_dir()
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / "agent.pid").write_text(str(os.getpid()))
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] could not write pid file (non-fatal): {exc}", flush=True)


def _tee_output_to_logfile() -> None:
    """Mirror stdout/stderr to <install>/logs/agent.log.

    When the agent is started at login (Task Scheduler / launchd / systemd)
    there is no console attached, so without this any crash is lost. Diagnostics
    are essential for "the agent won't start" reports.
    """
    try:
        log_dir = Path(os.environ.get("TT_LOG_DIR", _install_dir() / "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "agent.log"
        f = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)

        class _Tee:
            """A minimal write-only stream that mirrors to several streams.

            It must behave enough like a real text stream for libraries such as
            uvicorn, which probe attributes like isatty()/fileno()/encoding when
            configuring logging. Missing any of these raises at import/startup.
            """

            def __init__(self, *streams):
                self._streams = [s for s in streams if s is not None]
                # The "primary" stream we delegate unknown attrs to: prefer the
                # first real stream, falling back to the log file.
                self._primary = self._streams[0] if self._streams else None

            def write(self, data):
                for s in self._streams:
                    try:
                        s.write(data)
                        s.flush()
                    except Exception:
                        pass
                return len(data) if isinstance(data, str) else None

            def writelines(self, lines):
                for line in lines:
                    self.write(line)

            def flush(self):
                for s in self._streams:
                    try:
                        s.flush()
                    except Exception:
                        pass

            # uvicorn/logging probe these; never report as a TTY (we tee to a
            # file, so colored output would corrupt the log).
            def isatty(self):
                return False

            def fileno(self):
                if self._primary is not None and hasattr(self._primary, "fileno"):
                    return self._primary.fileno()
                raise OSError("no fileno on tee stream")

            def writable(self):
                return True

            def readable(self):
                return False

            def seekable(self):
                return False

            @property
            def encoding(self):
                return getattr(self._primary, "encoding", "utf-8")

            @property
            def closed(self):
                return False

            def __getattr__(self, name):
                # Last-resort delegation for any other stream attribute.
                # Use __dict__ to avoid recursing through __getattr__ itself.
                primary = self.__dict__.get("_primary")
                if primary is not None:
                    return getattr(primary, name)
                raise AttributeError(name)

        import datetime
        f.write(
            f"\n===== agent start {datetime.datetime.now().isoformat()} "
            f"(pid {os.getpid()}, v{__import__('agent.version', fromlist=['AGENT_VERSION']).AGENT_VERSION}) =====\n"
        )
        f.flush()
        sys.stdout = _Tee(sys.__stdout__, f)
        sys.stderr = _Tee(sys.__stderr__, f)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] could not set up file logging (non-fatal): {exc}", flush=True)


def main() -> None:
    _tee_output_to_logfile()
    # Initialise the structured rotating log so the in-app "Recent log"
    # view and "Open log folder" have a real file to point at. Without
    # this, log_path()/tail_log() return None -> "(no log file configured)".
    try:
        from core.app_logging import init_logging
        lp = init_logging()
        if lp is not None:
            print(f"[agent] log file: {lp}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] could not init structured logging (non-fatal): {exc}", flush=True)
    _write_pid_file()
    print(f"[agent] starting uvicorn on 127.0.0.1:{AGENT_PORT}", flush=True)
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=AGENT_PORT,
            log_level="info",
            reload=False,
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"[agent] FATAL: uvicorn exited: {exc}", flush=True)
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
