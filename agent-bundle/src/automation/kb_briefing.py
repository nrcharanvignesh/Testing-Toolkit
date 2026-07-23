"""
automation/kb_briefing.py
KB Briefing Engine — "Study the app before testing it."

Given a user story (title + description + acceptance criteria), produces a
TestBrief containing:
- Relevant screens and their navigation paths
- Preconditions (data state, permissions, prior flows)
- Business rules that apply to the story
- Expected UI elements and their known locators
- Navigation sequence from login to target feature

The engine queries two sources:
1. HybridRetriever: full-text chunk retrieval from indexed KB documents
2. ProjectContext: structured category lookups (screens, workflows, rules)

The brief is consumed by the autonomous E2E agent to navigate without
guessing and validate against known business rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]


@dataclass(slots=True)
class ScreenInfo:
    """A known screen/page from the KB."""

    name: str
    url_fragment: str = ""
    fields: list[str] = field(default_factory=list)
    description: str = ""


@dataclass(slots=True)
class Precondition:
    """A prerequisite for the test to be valid."""

    description: str
    category: str = ""  # "data" | "permission" | "flow" | "config"


@dataclass(slots=True)
class BusinessRule:
    """A validation/business rule relevant to the story."""

    name: str
    description: str
    category: str = ""


@dataclass(slots=True)
class TestBrief:
    """Structured briefing for one work item's E2E execution."""

    wi_id: str
    title: str
    screens: list[ScreenInfo] = field(default_factory=list)
    preconditions: list[Precondition] = field(default_factory=list)
    business_rules: list[BusinessRule] = field(default_factory=list)
    navigation_hints: list[str] = field(default_factory=list)
    raw_context: str = ""  # Full text for LLM consumption
    retrieval_chunks: int = 0

    def to_prompt_section(self, budget_chars: int = 175_000) -> str:
        """Render the brief as a prompt section for the E2E agent."""
        parts: list[str] = []
        parts.append("=== TEST BRIEF (from Knowledge Base) ===")
        if self.screens:
            parts.append("\n## SCREENS")
            for s in self.screens:
                line = f"- {s.name}"
                if s.url_fragment:
                    line += f" (URL: {s.url_fragment})"
                if s.description:
                    line += f": {s.description}"
                parts.append(line)
                if s.fields:
                    parts.append(f"  Fields: {', '.join(s.fields)}")
        if self.navigation_hints:
            parts.append("\n## NAVIGATION")
            for h in self.navigation_hints:
                parts.append(f"- {h}")
        if self.preconditions:
            parts.append("\n## PRECONDITIONS")
            for p in self.preconditions:
                parts.append(f"- [{p.category}] {p.description}")
        if self.business_rules:
            parts.append("\n## BUSINESS RULES")
            for r in self.business_rules:
                parts.append(f"- {r.name}: {r.description}")
        if self.raw_context:
            parts.append("\n## RAW KB CONTEXT")
            parts.append(self.raw_context[:budget_chars])
        parts.append("=== END TEST BRIEF ===")
        return "\n".join(parts)


class KBBriefingEngine:
    """Builds TestBrief objects from the project's KB for E2E execution.

    Usage:
        engine = KBBriefingEngine(project_name)
        brief = engine.build_brief(wi_id="12345", title="...", description="...")
    """

    def __init__(self, project: str, *, on_log: LogFn | None = None) -> None:
        self._project = project
        self._log = on_log or (lambda _: None)
        self._retriever: Any | None = None
        self._context: Any | None = None
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load retriever and context summary."""
        if self._loaded:
            return
        self._loaded = True

        from core.project_store import ProjectPaths, read_context_summary

        paths = ProjectPaths.for_name(self._project)

        # Load structured context
        self._context = read_context_summary(self._project)
        if self._context:
            self._log("[INFO] KB briefing: ProjectContext loaded.")

        # Load retriever
        try:
            from kb.retrieval import HybridRetriever
            index_dir = paths.kb_dir / "hybrid_index"
            if index_dir.exists():
                self._retriever = HybridRetriever(index_dir)
                if self._retriever.is_available():
                    self._log("[INFO] KB briefing: HybridRetriever loaded.")
                else:
                    self._retriever = None
        except Exception:  # noqa: BLE001
            self._retriever = None

    def build_brief(
        self,
        wi_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        budget_chars: int = 175_000,
    ) -> TestBrief:
        """Build a test brief from the KB for a single work item."""
        self._ensure_loaded()

        story_text = f"{title}\n{description}\n{acceptance_criteria}".strip()
        brief = TestBrief(wi_id=wi_id, title=title)

        # 1. Structured context lookup (fast, exact)
        if self._context:
            brief.screens = self._extract_screens(story_text)
            brief.preconditions = self._extract_preconditions(story_text)
            brief.business_rules = self._extract_business_rules(story_text)
            brief.navigation_hints = self._extract_navigation(story_text)

        # 2. Retriever-based chunk retrieval (aggressive: top-50)
        if self._retriever:
            chunks = self._retriever.retrieve(story_text, top_k=50)
            brief.retrieval_chunks = len(chunks)
            if chunks:
                raw_parts: list[str] = []
                char_count = 0
                for chunk in chunks:
                    header = f"[{chunk.title}]" if chunk.title else ""
                    text = f"{header}\n{chunk.text}"
                    if char_count + len(text) > budget_chars:
                        break
                    raw_parts.append(text)
                    char_count += len(text)
                brief.raw_context = "\n---\n".join(raw_parts)

        # 3. Selective context summary as fallback
        if not brief.raw_context and self._context:
            try:
                selective = self._context.to_prompt_section_selective(story_text)
                if selective:
                    brief.raw_context = selective[:budget_chars]
            except Exception:  # noqa: BLE001
                pass

        return brief

    def build_full_brief(
        self,
        wi_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        budget_chars: int = 175_000,
    ) -> TestBrief:
        """Build a comprehensive brief using the full KB budget.

        Tiered delivery:
          Tier 1: KB fits in budget -> inject ALL KB text
          Tier 2: KB exceeds budget -> structured summary + top-50 retrieval
        """
        self._ensure_loaded()
        full_kb_size = self.get_full_kb_size_chars()

        if full_kb_size > 0 and full_kb_size <= budget_chars:
            # Tier 1: inject everything
            brief = self.build_brief(
                wi_id, title, description, acceptance_criteria,
                budget_chars=budget_chars,
            )
            brief.raw_context = self._assemble_full_kb(budget_chars)
            return brief

        # Tier 2: structured context + aggressive retrieval
        brief = self.build_brief(
            wi_id, title, description, acceptance_criteria,
            budget_chars=budget_chars,
        )
        # Prepend full structured context if available
        if self._context:
            try:
                full_ctx = self._context.to_prompt_section()
                if full_ctx and brief.raw_context:
                    brief.raw_context = (
                        full_ctx[:budget_chars // 3]
                        + "\n\n--- RETRIEVAL CHUNKS ---\n\n"
                        + brief.raw_context
                    )
                elif full_ctx:
                    brief.raw_context = full_ctx[:budget_chars]
            except Exception:  # noqa: BLE001
                pass
        return brief

    def get_full_kb_size_chars(self) -> int:
        """Return total character count of all indexed KB chunks."""
        self._ensure_loaded()
        if not self._retriever:
            return 0
        try:
            chunks = self._retriever._chunks
            return sum(len(c.text) for c in chunks.values()) if chunks else 0
        except Exception:  # noqa: BLE001
            return 0

    def _assemble_full_kb(self, budget_chars: int) -> str:
        """Assemble all KB chunks grouped by source document."""
        if not self._retriever:
            return ""
        try:
            chunks = self._retriever._chunks.values()
            if not chunks:
                return ""
            by_source: dict[str, list[str]] = {}
            for chunk in chunks:
                src = chunk.title or "(untitled)"
                by_source.setdefault(src, []).append(chunk.text)
            parts: list[str] = []
            char_count = 0
            for src, texts in by_source.items():
                section = f"## {src}\n" + "\n".join(texts)
                if char_count + len(section) > budget_chars:
                    remaining = budget_chars - char_count
                    if remaining > 200:
                        parts.append(section[:remaining])
                    break
                parts.append(section)
                char_count += len(section)
            return "\n\n".join(parts)
        except Exception:  # noqa: BLE001
            return ""

    def _extract_screens(self, story_text: str) -> list[ScreenInfo]:
        """Find screens/pages relevant to the story from ProjectContext."""
        if not self._context:
            return []
        results: list[ScreenInfo] = []
        story_lower = story_text.lower()
        story_tokens = frozenset(re.sub(r"[^a-z0-9]+", " ", story_lower).split())

        for item in getattr(self._context, "screens", []):
            name_tokens = frozenset(
                re.sub(r"[^a-z0-9]+", " ", item.name.lower()).split()
            )
            # Match if any token from screen name appears in story
            if name_tokens & story_tokens or item.name.lower() in story_lower:
                url_frag = ""
                desc_lower = item.description.lower()
                url_match = re.search(r"(?:url|path|route)[:\s]+([/\w\-?.#]+)", desc_lower)
                if url_match:
                    url_frag = url_match.group(1)
                fields: list[str] = []
                field_match = re.search(
                    r"fields?[:\s]+([^.]+)", item.description, re.IGNORECASE
                )
                if field_match:
                    fields = [f.strip() for f in field_match.group(1).split(",")]
                results.append(ScreenInfo(
                    name=item.name,
                    url_fragment=url_frag,
                    fields=fields,
                    description=item.description,
                ))
        return results

    def _extract_preconditions(self, story_text: str) -> list[Precondition]:
        """Extract preconditions from test_data_needs and workflows."""
        if not self._context:
            return []
        results: list[Precondition] = []
        story_lower = story_text.lower()
        story_tokens = frozenset(re.sub(r"[^a-z0-9]+", " ", story_lower).split())

        # From test_data_needs
        for item in getattr(self._context, "test_data_needs", []):
            name_tokens = frozenset(
                re.sub(r"[^a-z0-9]+", " ", item.name.lower()).split()
            )
            if name_tokens & story_tokens:
                results.append(Precondition(
                    description=f"{item.name}: {item.description}",
                    category="data",
                ))

        # From actors (permission preconditions)
        for item in getattr(self._context, "actors", []):
            if item.name.lower() in story_lower:
                results.append(Precondition(
                    description=f"Requires role: {item.name} - {item.description}",
                    category="permission",
                ))

        return results

    def _extract_business_rules(self, story_text: str) -> list[BusinessRule]:
        """Find business rules relevant to the story."""
        if not self._context:
            return []
        results: list[BusinessRule] = []
        story_lower = story_text.lower()
        story_tokens = frozenset(re.sub(r"[^a-z0-9]+", " ", story_lower).split())

        for item in getattr(self._context, "business_rules", []):
            name_tokens = frozenset(
                re.sub(r"[^a-z0-9]+", " ", item.name.lower()).split()
            )
            if name_tokens & story_tokens or item.name.lower() in story_lower:
                results.append(BusinessRule(
                    name=item.name,
                    description=item.description,
                    category="validation",
                ))

        # Also check edge_cases
        for item in getattr(self._context, "edge_cases", []):
            name_tokens = frozenset(
                re.sub(r"[^a-z0-9]+", " ", item.name.lower()).split()
            )
            if name_tokens & story_tokens:
                results.append(BusinessRule(
                    name=item.name,
                    description=item.description,
                    category="edge_case",
                ))

        return results

    def query_for_stuck_agent(
        self,
        failure_query: str,
        top_k: int = 20,
        max_chunk_chars: int = 0,
    ) -> str:
        """Direct KB query for the KB Consultant sub-agent.

        Returns formatted KB chunks relevant to the failure context.
        Used when the executor is stuck and needs additional guidance.
        max_chunk_chars=0 means uncapped (full chunk text).
        """
        self._ensure_loaded()
        if not self._retriever:
            return ""
        try:
            chunks = self._retriever.retrieve(failure_query, top_k=top_k)
            if not chunks:
                return ""
            parts: list[str] = []
            for chunk in chunks:
                header = f"[{chunk.title}]" if chunk.title else "[KB]"
                text = chunk.text if max_chunk_chars == 0 else chunk.text[:max_chunk_chars]
                parts.append(f"{header}\n{text}")
            return "\n---\n".join(parts)
        except Exception:  # noqa: BLE001
            return ""

    def _extract_navigation(self, story_text: str) -> list[str]:
        """Extract navigation hints from workflows."""
        if not self._context:
            return []
        results: list[str] = []
        story_lower = story_text.lower()
        story_tokens = frozenset(re.sub(r"[^a-z0-9]+", " ", story_lower).split())

        for item in getattr(self._context, "workflows", []):
            name_tokens = frozenset(
                re.sub(r"[^a-z0-9]+", " ", item.name.lower()).split()
            )
            if name_tokens & story_tokens or item.name.lower() in story_lower:
                results.append(f"{item.name}: {item.description}")

        return results
