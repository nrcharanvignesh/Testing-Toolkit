"""
server.py
Local compute agent for Testing Toolkit web app.
FastAPI on localhost:7842 — serves as the bridge between the Vercel
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
    """Startup: ensure workspace exists, preload ONNX models, start updater.

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

    # Start background auto-updater (best-effort).
    try:
        from agent.updater import start_update_loop, resolve_manifest_url
        manifest_url = resolve_manifest_url()
        if manifest_url:
            start_update_loop(manifest_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] updater not started (non-fatal): {exc}", flush=True)

    # Self-heal the Windows login task so it launches headlessly (pythonw).
    # Earlier installs registered the task with python.exe, which pops a black
    # console window on every login. Re-registering here means existing users
    # get the fix automatically after the agent auto-updates - no reinstall.
    try:
        _ensure_windowless_autostart()
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] autostart self-heal skipped (non-fatal): {exc}", flush=True)

    yield


def _ensure_windowless_autostart() -> None:
    """On Windows, ensure the 'TestingToolkitAgent' login task uses pythonw.exe
    so no console window appears at startup. Best-effort and idempotent."""
    if os.name != "nt":
        return
    import subprocess

    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    launch = str(pyw) if pyw.exists() else str(exe)
    # Only the interpreter matters for the windowless behavior; if we are
    # already pointed at pythonw there is nothing to fix.
    cmd = f'"{launch}" -m agent'
    try:
        existing = subprocess.run(
            ["schtasks", "/query", "/tn", "TestingToolkitAgent", "/fo", "list", "/v"],
            capture_output=True, text=True, timeout=15,
        )
        if existing.returncode == 0 and "pythonw.exe" in (existing.stdout or "").lower():
            return  # already headless
    except Exception:
        pass
    subprocess.run(
        ["schtasks", "/create", "/tn", "TestingToolkitAgent",
         "/tr", cmd, "/sc", "onlogon", "/rl", "limited", "/f"],
        capture_output=True, text=True,
    )
    print("[agent] re-registered login task to run headlessly (pythonw).", flush=True)


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
from agent.routes.kb import router as kb_router
from agent.routes.llm import router as llm_router
from agent.routes.generate import router as generate_router
from agent.routes.defects import router as defects_router
from agent.routes.jobs import router as jobs_router
from agent.routes.tools import router as tools_router
from agent.routes.artifacts import router as artifacts_router
from agent.routes.update import router as update_router

app.include_router(health_router)
app.include_router(settings_router, prefix="/settings")
app.include_router(ado_router, prefix="/ado")
app.include_router(kb_router, prefix="/kb")
app.include_router(llm_router, prefix="/llm")
app.include_router(generate_router, prefix="/generate")
app.include_router(defects_router, prefix="/defects")
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
