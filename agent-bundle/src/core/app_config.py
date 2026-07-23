"""
app_config.py
Hardcoded identity, workspace paths, and defaults for the Testing
Toolkit. Charan's convention: no argparse, no .env files; the constants
live near the top of this module. Change them, restart the app.

Workspace layout (created on first launch):
    ~/TestingToolkitWeb/   (override with TT_WORKSPACE_DIR)
        projects/<full_project_name>/   per-project KB + system prompt
            system_prompt.txt
            kb/                          drop requirement docs here
            kb_index.json                cached deterministic chunk index
            generated/                   payloads + review xlsx per run
        runs/                            packager work + outputs
        logs/                            rotating debug log
        ui_prefs.json                    theme + window + splitter state
        settings.json                    base url, model, org, prefix
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final


# ---------------- authenticated release credential loader ----------------
# Packaged agents receive only a versioned AES-256-GCM .env.enc envelope.
# Process environment variables remain an explicit owner/developer override.
# Plaintext .env files are deliberately never loaded by the web agent.
def _parse_env_text(text: str) -> dict[str, str]:
    """Legacy parser retained for deterministic compatibility tests only."""
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        eq = line.find("=")
        if eq < 1:
            continue
        result[line[:eq].strip()] = line[eq + 1:].strip()
    return result


_CREDENTIAL_PROTECTION_STATE = "not-loaded"
_CREDENTIAL_PROTECTION_DETAIL = ""


def _load_env() -> dict[str, str]:
    """Authenticate the release envelope and prefer OS-bound rewrapped data."""
    global _CREDENTIAL_PROTECTION_STATE, _CREDENTIAL_PROTECTION_DETAIL
    mei: str = getattr(sys, "_MEIPASS", "")
    base = Path(mei) if mei else Path(__file__).resolve().parent.parent
    envelope_path = base / ".env.enc"
    try:
        from core.credential_store import load_release_credentials

        values, state = load_release_credentials(envelope_path)
        _CREDENTIAL_PROTECTION_STATE = state
        if not values:
            # File present but empty result: record a non-secret reason so the
            # installer self-test and Doctor report say WHY, not just "missing".
            if not envelope_path.exists():
                _CREDENTIAL_PROTECTION_DETAIL = "envelope missing"
            else:
                reason = ""
                try:
                    from core.credential_store import last_envelope_error

                    reason = last_envelope_error()
                except Exception:
                    reason = ""
                suffix = f": {reason}" if reason else ""
                _CREDENTIAL_PROTECTION_DETAIL = (
                    f"loaded empty credential (state={state}) on {sys.platform}{suffix}"
                )
        return values
    except Exception as exc:
        # Never surface a raw crypto exception: third-party implementations can
        # embed input fragments. We DO record a non-secret shape (exception type
        # + our own CredentialEnvelopeError message, which is designed to be
        # secret-free) so Windows-specific failures are diagnosable from logs.
        _CREDENTIAL_PROTECTION_STATE = "unavailable"
        detail = type(exc).__name__
        try:
            from core.credential_envelope import CredentialEnvelopeError

            if isinstance(exc, CredentialEnvelopeError):
                detail = f"{detail}: {exc}"
        except Exception:
            pass
        _CREDENTIAL_PROTECTION_DETAIL = f"{detail} on {sys.platform}"
        return {}


def credential_protection_state() -> str:
    """Return a non-secret diagnostic label for credential-at-rest strength."""
    return _CREDENTIAL_PROTECTION_STATE


def credential_protection_detail() -> str:
    """Return a non-secret human explanation when credential loading degrades.

    Empty when the credential loaded cleanly. Safe to print in installer logs
    and the Doctor report: it only contains exception type names, our own
    envelope error messages, and the platform label - never key material.
    """
    return _CREDENTIAL_PROTECTION_DETAIL


_ENV: Final[dict[str, str]] = _load_env()


def _cfg(name: str, default: str = "") -> str:
    """Resolve a config value with precedence: process env > bundled .env >
    default. Keeps cloud/dev overrides working while letting the frozen agent
    read its shipped service-account credentials."""
    val = (os.environ.get(name) or "").strip()
    if val:
        return val
    val = (_ENV.get(name) or "").strip()
    return val or default


# -------------------------- IDENTITY --------------------------
APP_NAME:    Final[str] = "Testing Toolkit"
APP_SLUG:    Final[str] = "TestingToolkit"
APP_VERSION: Final[str] = "3.37.0"

# The web build keeps its workspace separate from the desktop app so the
# two can coexist on the same machine. Everything (projects, KB, runs,
# outputs/artifacts, logs, settings) lives under this single root.
WEB_WORKSPACE_SLUG: Final[str] = "TestingToolkitWeb"


def _resolve_workspace() -> Path:
    """Workspace root for the web agent.

    Defaults to ``~/TestingToolkitWeb`` (e.g. ``C:\\Users\\cnr002\\
    TestingToolkitWeb`` on Windows). Set the ``TT_WORKSPACE_DIR``
    environment variable to override it with an absolute path.
    """
    override = (os.environ.get("TT_WORKSPACE_DIR") or "").strip()
    if override:
        try:
            return Path(override).expanduser()
        except (OSError, ValueError):
            pass
    return Path.home() / WEB_WORKSPACE_SLUG

# -------------------------- WORKSPACE -------------------------
WORKSPACE:    Final[Path] = _resolve_workspace()
PROJECTS_DIR: Final[Path] = WORKSPACE / "projects"
RUNS_DIR:     Final[Path] = WORKSPACE / "runs"
OUTPUTS_DIR:  Final[Path] = WORKSPACE / "outputs"
LOGS_DIR:     Final[Path] = WORKSPACE / "logs"

# All user-facing outputs go to the unified workspace outputs directory.
EXPORTS_DIR: Final[Path] = OUTPUTS_DIR

UI_PREFS_PATH: Final[Path] = WORKSPACE / "ui_prefs.json"

# --------------------- STABLE CONFIG DIRECTORY ----------------------
# Connection settings (ADO organization/prefix, JIRA URL/user) and the
# encrypted PAT fallback files live in a dedicated directory that is
# INDEPENDENT of both the versioned install directory and the workspace.
# The installer/updater must never touch it, so credentials survive every
# agent update and reinstall. Override with TT_CONFIG_DIR (absolute path).
def _resolve_config_dir() -> Path:
    override = (os.environ.get("TT_CONFIG_DIR") or "").strip()
    if override:
        try:
            return Path(override).expanduser()
        except (OSError, ValueError):
            pass
    return Path.home() / ".testing_toolkit"


CONFIG_DIR:    Final[Path] = _resolve_config_dir()
SETTINGS_PATH: Final[Path] = CONFIG_DIR / "settings.json"
# Legacy location (pre-2.19) — used only to migrate settings once.
LEGACY_SETTINGS_PATH: Final[Path] = WORKSPACE / "settings.json"

# -------------------------- DISPLAY ---------------------------
# Projects are shown with this prefix stripped (if configured).
# Stored full name is used for every API call; only the displayed
# label is shortened.
DEFAULT_PROJECT_PREFIX: Final[str] = ""

# -------------------------- WINDOW ----------------------------
DEFAULT_WINDOW_W: Final[int] = 1480
DEFAULT_WINDOW_H: Final[int] = 920

# -------------------------- LLM API --------------------------
# base_url is configurable (an enterprise gateway / proxy may sit in
# front of the LLM API). The Messages API path is appended by the
# client, so store only the origin here.
DEFAULT_LLM_BASE_URL: Final[str] = "https://api.anthropic.com"
LLM_API_VERSION:      Final[str] = "2025-04-15"

# Primary model drives test-case generation (quality matters).
# Opus 4-8 is the newest/largest frontier Claude in the GenAI catalog
# (bedrock, 128k output tokens) -> best reasoning + coverage quality.
DEFAULT_MODEL: Final[str] = "bedrock.anthropic.claude-opus-4-8"
# Fast model for the recursive retrieval map steps (newest default Sonnet).
DEFAULT_FAST_MODEL: Final[str] = "bedrock.anthropic.claude-sonnet-4-6"
# Safety fallback model used when primary/fast fail or rate-limit.
DEFAULT_FALLBACK_MODEL: Final[str] = "bedrock.anthropic.claude-haiku-4-5"

# Backwards-compat aliases (internal imports that use old names)
DEFAULT_ANTHROPIC_BASE_URL = DEFAULT_LLM_BASE_URL
ANTHROPIC_VERSION = LLM_API_VERSION

# Centrally managed LLM endpoint + credential. These are never browser/user
# settings. Resolution precedence is process env > bundled .env(.enc) > the
# hardcoded endpoint default, allowing deployment overrides while preserving
# the packaged service configuration.
LLM_BASE_URL: Final[str] = _cfg("BASE_URL", DEFAULT_LLM_BASE_URL)
LLM_API_KEY:  Final[str] = _cfg("API_KEY")

# LLM wire protocol: "anthropic" (POST /v1/messages, Claude models) or
# "openai" (POST /chat/completions, e.g. azure.gpt-4o). The GenAI gateway
# serves both; this only selects which the client speaks.
LLM_PROVIDER_FORMAT: Final[str] = (
    _cfg("LLM_PROVIDER_FORMAT", "anthropic") or "anthropic"
).strip().lower()

# --- Model capability tiers (consumed by core.model_router) ---
# Tiers map onto the three defaults above so behavior is unchanged unless a
# deployment overrides them. LARGE=quality, MEDIUM=balanced, SMALL=cheap.
MODEL_LARGE:  Final[str] = _cfg("MODEL_LARGE", DEFAULT_MODEL)
MODEL_MEDIUM: Final[str] = _cfg("MODEL_MEDIUM", DEFAULT_FAST_MODEL)
MODEL_SMALL:  Final[str] = _cfg("MODEL_SMALL", DEFAULT_FALLBACK_MODEL)

# Optional per-task overrides. Empty by default -> model_router falls back to
# the tier model. Set these to pin a specific task to a specific model
# (e.g. a cheaper non-Anthropic model for reranking/contextualization).
MODEL_RERANK:       Final[str] = _cfg("MODEL_RERANK")
# Native cross-encoder rerank model for the gateway POST /rerank endpoint.
# Used at retrieval time; LLM-as-judge (MODEL_RERANK / tier) is the fallback.
RERANK_MODEL:       Final[str] = _cfg(
    "RERANK_MODEL", "azure.cohere-rerank-v3-english"
)
MODEL_CONTEXTUALIZE: Final[str] = _cfg("MODEL_CONTEXTUALIZE")
MODEL_EXTRACT:      Final[str] = _cfg("MODEL_EXTRACT")
MODEL_GENERATE:     Final[str] = _cfg("MODEL_GENERATE")
MODEL_CHAT:         Final[str] = _cfg("MODEL_CHAT")
MODEL_OCR:          Final[str] = _cfg("MODEL_OCR")

# --- Embedding model (consumed by kb.embeddings API embedder) ---
# API-based embeddings (matches desktop). Override for a different
# embedding model/dimension.
# text-embedding-3-large is the highest-quality embedding model in the
# catalog (native 3072 dims). Using full dimensionality maximizes retrieval
# fidelity. NOTE: changing model/dim invalidates existing vectors -> a KB
# re-index is required (handled automatically via the embedding fingerprint).
EMBED_MODEL: Final[str] = _cfg("EMBED_MODEL", "azure.text-embedding-3-large")
EMBED_DIM:   Final[int] = int(_cfg("EMBED_DIM", "3072") or "3072")

# Token budgets for the Recursive Language Model (approximate; we count
# characters at ~4 chars/token). If the whole project KB fits under
# RLM_DIRECT_CONTEXT_TOKENS it is passed in one shot; otherwise the
# recursive navigate/map/reduce path is taken.
RLM_DIRECT_CONTEXT_TOKENS: Final[int] = 150_000
RLM_MAP_CHUNK_TOKENS:      Final[int] = 6_000
RLM_GENERATE_MAX_TOKENS:   Final[int] = 16_000
RLM_NAVIGATE_MAX_TOKENS:   Final[int] = 1_500
RLM_MAP_MAX_TOKENS:        Final[int] = 2_000


# Requirement decomposition: max output tokens for the fast model to
# enumerate atomic testable requirements before generation.
RLM_DECOMPOSE_MAX_TOKENS:  Final[int] = 2_000

# Coverage verification + gap-fill pass: max output tokens for the
# post-generation verification call.
RLM_VERIFY_MAX_TOKENS:     Final[int] = 8_000

# Regeneration: maximum number of user-driven regeneration iterations
# per session per set of work items.
RLM_MAX_REGEN_ITERATIONS:  Final[int] = 10


# -------------------------- KB INDEXING -----------------------
# The first index build is the slow part of the first generation. We run it
# "full BRRRR": files are extracted/OCR'd/contextualized across a worker pool
# so a capable PC saturates its CPU and IO instead of crawling one file at a
# time. Quality is unchanged (same deterministic chunk ids, same checkpoint).
def _resolve_int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return default


# Number of files processed in parallel during indexing.
#   0 = auto -> use every logical CPU core (full utilization).
# Override with TT_KB_INDEX_WORKERS (e.g. set to 4 to cap on a small box).
KB_INDEX_MAX_WORKERS: Final[int] = _resolve_int_env("TT_KB_INDEX_WORKERS", 0)

# Max concurrent contextual-retrieval LLM calls per document. Raising this
# pushes the gateway harder; lower it if you hit rate limits.
# Override with TT_KB_CONTEXT_CONCURRENCY.
KB_CONTEXTUAL_CONCURRENCY: Final[int] = _resolve_int_env(
    "TT_KB_CONTEXT_CONCURRENCY", 8
)


def resolve_index_workers(n_items: int) -> int:
    """Effective worker count for an indexing run of ``n_items`` files."""
    configured = KB_INDEX_MAX_WORKERS or (os.cpu_count() or 4)
    if n_items <= 0:
        return 1
    return max(1, min(configured, n_items))


def ensure_workspace() -> None:
    """Create the workspace skeleton. Idempotent, never raises."""
    for d in (WORKSPACE, PROJECTS_DIR, RUNS_DIR, OUTPUTS_DIR, LOGS_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def display_project_name(full_name: str, prefix: str) -> str:
    """Strip the configured prefix for display. Case-insensitive on the
    prefix; falls back to the full name if stripping would empty it."""
    if prefix and full_name.lower().startswith(prefix.lower()):
        stripped = full_name[len(prefix):].strip()
        return stripped or full_name
    return full_name


def _base_dir() -> Path:
    """Directory that holds bundled assets. Works both from source and
    from a PyInstaller bundle (sys._MEIPASS)."""
    mei = getattr(sys, "_MEIPASS", "")
    if mei:
        return Path(mei)
    return Path(__file__).resolve().parent


def asset_path(name: str) -> Path:
    """Absolute path to a bundled asset under assets/."""
    return _base_dir() / "assets" / name


def icon_path() -> str:
    """Best available app icon path, or '' if none is present."""
    for name in ("icon.png", "icon_64.png", "icon.ico"):
        p = asset_path(name)
        if p.exists():
            return str(p)
    return ""
