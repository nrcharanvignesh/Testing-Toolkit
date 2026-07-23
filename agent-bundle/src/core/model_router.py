"""
model_router.py
Multi-provider request router - selects the optimal model per task from
the GenAI LiteLLM proxy catalog. Not restricted to Anthropic; uses the
best model for each use case to maximize quality while minimizing cost.

Strategy:
  - Task-specific model overrides (MODEL_RERANK, MODEL_GENERATE, etc.)
    take highest priority when configured in .env.
  - Tier fallback: FRONTIER/MEDIUM/SMALL map to MODEL_LARGE/MEDIUM/SMALL.
  - All models accessed via the same LiteLLM proxy endpoint.

Default assignments (GenAI proxy catalog, Jul 2025):
  - Opus ($15/$75/M): TC generation, coverage verification, decomposition
    -- where reasoning depth directly determines output quality
  - Sonnet ($3/$15/M): chat streaming, navigation, defect parsing
    -- fast + capable, ideal for interactive and structured tasks
  - GPT-4o-mini ($0.15/$0.60/M): reranking, contextualization
    -- 100x cheaper than Opus for trivial classification tasks
  - GPT-4o ($2.50/$10/M): extraction
    -- strong structured output, cheaper than Sonnet for JSON extraction

The proxy handles auth/routing; we just pass the model ID string.
"""
from __future__ import annotations

from enum import Enum
from typing import Final

from core.app_config import (
    MODEL_LARGE,
    MODEL_MEDIUM,
    MODEL_SMALL,
    MODEL_RERANK,
    MODEL_CONTEXTUALIZE,
    MODEL_EXTRACT,
    MODEL_GENERATE,
    MODEL_CHAT,
    MODEL_OCR,
)

# Legacy aliases kept for any external imports
DEFAULT_MODEL = MODEL_LARGE
DEFAULT_FAST_MODEL = MODEL_MEDIUM
DEFAULT_FALLBACK_MODEL = MODEL_SMALL


class Tier(Enum):
    """Model capability tier."""
    SMALL = "small"
    MEDIUM = "medium"
    FRONTIER = "frontier"


# Tier -> model ID mapping (loaded from .env via app_config)
_TIER_MAP: Final[dict[Tier, str]] = {
    Tier.FRONTIER: MODEL_LARGE,
    Tier.MEDIUM: MODEL_MEDIUM,
    Tier.SMALL: MODEL_SMALL,
}


def model_for_tier(tier: Tier) -> str:
    """Return the model ID for the given tier."""
    return _TIER_MAP[tier]


# --- Task-based routing (convenience) ---

class Task(Enum):
    """Declared task intent. Each maps to a tier + optional override."""
    # Frontier (Opus) - complex reasoning, extended thinking
    GENERATE_TEST_CASES = "generate_tc"
    VERIFY_COVERAGE = "verify_coverage"
    TEMPLATE_ANALYSIS = "template_analysis"
    DECOMPOSE_REQUIREMENTS = "decompose"

    # Medium (Sonnet) - structured extraction, moderate complexity, streaming
    CHAT_STREAMING = "chat_streaming"
    MAP_EXTRACT = "map_extract"
    DEFECT_PARSE = "defect_parse"
    NAVIGATE_CHUNKS = "navigate"

    # Small (GPT-4o-mini / Haiku) - trivial classification, high concurrency
    CONTEXTUALIZE_CHUNK = "contextualize"
    LLM_RERANK = "rerank"

    # OCR / document extraction - needs large context window + vision
    OCR_EXTRACT = "ocr_extract"

    # Agentic E2E - LLM-in-the-loop browser automation
    E2E_AGENTIC = "e2e_agentic"
    E2E_AGENTIC_FALLBACK = "e2e_agentic_fallback"

    # Sub-agents for multi-agent E2E orchestration
    E2E_PLANNER = "e2e_planner"
    E2E_KB_CONSULTANT = "e2e_kb_consultant"
    E2E_REPORT_SYNTH = "e2e_report_synth"


_TASK_TIER: Final[dict[Task, Tier]] = {
    # Frontier - where quality directly determines output correctness
    Task.GENERATE_TEST_CASES: Tier.FRONTIER,
    Task.VERIFY_COVERAGE: Tier.FRONTIER,
    Task.TEMPLATE_ANALYSIS: Tier.FRONTIER,
    Task.DECOMPOSE_REQUIREMENTS: Tier.FRONTIER,
    # Medium - structured work, speed+quality balance
    Task.CHAT_STREAMING: Tier.MEDIUM,
    Task.MAP_EXTRACT: Tier.MEDIUM,
    Task.DEFECT_PARSE: Tier.MEDIUM,
    Task.NAVIGATE_CHUNKS: Tier.MEDIUM,
    # Small - trivial, high concurrency, no downstream quality impact
    Task.CONTEXTUALIZE_CHUNK: Tier.SMALL,
    Task.LLM_RERANK: Tier.SMALL,
    # OCR - 128K context, vision-capable
    Task.OCR_EXTRACT: Tier.MEDIUM,
    # Agentic E2E - Sonnet for speed, Opus fallback for reasoning
    Task.E2E_AGENTIC: Tier.MEDIUM,
    Task.E2E_AGENTIC_FALLBACK: Tier.FRONTIER,
    # Sub-agents - small for cheap/fast, medium for reasoning
    Task.E2E_PLANNER: Tier.MEDIUM,
    Task.E2E_KB_CONSULTANT: Tier.MEDIUM,
    Task.E2E_REPORT_SYNTH: Tier.MEDIUM,
}

# Task-specific model overrides: if configured in .env, bypass tier routing.
# Allows pinning specific tasks to non-Anthropic models for cost/quality.
_TASK_OVERRIDE: Final[dict[Task, str]] = {
    k: v for k, v in {
        Task.LLM_RERANK: MODEL_RERANK,
        Task.CONTEXTUALIZE_CHUNK: MODEL_CONTEXTUALIZE,
        Task.MAP_EXTRACT: MODEL_EXTRACT,
        Task.GENERATE_TEST_CASES: MODEL_GENERATE,
        Task.VERIFY_COVERAGE: MODEL_GENERATE,
        Task.TEMPLATE_ANALYSIS: MODEL_GENERATE,
        Task.DECOMPOSE_REQUIREMENTS: MODEL_GENERATE,
        Task.CHAT_STREAMING: MODEL_CHAT,
        Task.DEFECT_PARSE: MODEL_EXTRACT,
        Task.NAVIGATE_CHUNKS: MODEL_CHAT,
        Task.OCR_EXTRACT: MODEL_OCR,
        Task.E2E_AGENTIC: MODEL_CHAT,
        Task.E2E_AGENTIC_FALLBACK: MODEL_GENERATE,
        Task.E2E_PLANNER: MODEL_CHAT,
        Task.E2E_KB_CONSULTANT: MODEL_CHAT,
        Task.E2E_REPORT_SYNTH: MODEL_CHAT,
    }.items() if v  # only include non-empty overrides
}


def route(task: Task) -> str:
    """Route a task to the optimal model ID.
    Priority: task-specific override > tier fallback."""
    override = _TASK_OVERRIDE.get(task)
    if override:
        return override
    return model_for_tier(_TASK_TIER[task])


def tier_for_task(task: Task) -> Tier:
    """Get the tier for a task (useful for logging)."""
    return _TASK_TIER[task]
