"""Automation: credential vault (stdlib) + lazy E2E symbols.

Only the stdlib-only credential vault is imported eagerly so that the vault is
always accessible even when Playwright / openpyxl are not installed. Every
Playwright- or artifact-dependent symbol is resolved lazily on first access,
so importing `automation.credential_vault` never drags in heavy deps.
"""

from .credential_vault import CredentialVault, TestCredential


def __getattr__(name: str):
    """Lazy-load heavier symbols on first access."""
    if name == "ArtifactCollector":
        from .artifact_collector import ArtifactCollector

        return ArtifactCollector
    if name in ("StepResult", "TestCaseResult", "run_e2e_tests"):
        from . import e2e_runner

        return getattr(e2e_runner, name)
    if name in (
        "BrowserProfile",
        "browser_session",
        "detect_browser_profiles",
        "get_default_profile",
    ):
        from . import playwright_bridge

        return getattr(playwright_bridge, name)
    if name == "write_e2e_report":
        from .report_excel import write_e2e_report

        return write_e2e_report
    if name == "annotate_screenshot":
        from .screenshot_annotator import annotate_screenshot

        return annotate_screenshot
    if name == "generate_playwright_script":
        from .script_generator import generate_playwright_script

        return generate_playwright_script
    raise AttributeError(f"module 'automation' has no attribute {name!r}")


__all__ = [
    "ArtifactCollector",
    "BrowserProfile",
    "CredentialVault",
    "StepResult",
    "TestCaseResult",
    "TestCredential",
    "annotate_screenshot",
    "browser_session",
    "detect_browser_profiles",
    "generate_playwright_script",
    "get_default_profile",
    "run_e2e_tests",
    "write_e2e_report",
]
