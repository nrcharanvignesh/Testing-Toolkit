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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure src/ is on the path so existing modules resolve.
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.app_config import APP_VERSION, ensure_workspace
from agent.version import AGENT_VERSION, AGENT_PORT


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: ensure workspace exists, preload ONNX models, start updater."""
    ensure_workspace()
    # Lazy-load models in background to avoid blocking startup
    from agent.model_loader import preload_models
    preload_models()
    # Start background auto-updater
    manifest_url = os.environ.get("AGENT_MANIFEST_URL", "")
    if manifest_url:
        from agent.updater import start_update_loop
        start_update_loop(manifest_url)
    yield


app = FastAPI(
    title="Testing Toolkit Agent",
    version=AGENT_VERSION,
    lifespan=_lifespan,
)

# Allow the Vercel frontend (any origin) to call localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Register route modules --
from agent.routes.health import router as health_router
from agent.routes.settings import router as settings_router
from agent.routes.ado import router as ado_router
from agent.routes.kb import router as kb_router
from agent.routes.llm import router as llm_router

app.include_router(health_router)
app.include_router(settings_router, prefix="/settings")
app.include_router(ado_router, prefix="/ado")
app.include_router(kb_router, prefix="/kb")
app.include_router(llm_router, prefix="/llm")


def main() -> None:
    uvicorn.run(
        "agent.server:app",
        host="127.0.0.1",
        port=AGENT_PORT,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
