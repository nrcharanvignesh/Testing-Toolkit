# Testing Toolkit - Rule List

This file is the canonical reference for bugs, enhancements, and constraints
that MUST be checked in every Claude Code session working on this project.

---

## ARCHITECTURE RULES

1. **Streaming-first / Memory-agnostic**: All data processing must work on 4GB
   RAM. Use Polars Lazy API or chunked processing. Never load entire large
   files into memory at once.

2. **Hardware detection is fail-safe**: Every detection in `core/hardware.py`
   is wrapped in try/except with conservative fallbacks. A missing GPU, NUMA,
   or detection failure must NEVER crash the app.

3. **Same features with or without GPU**: GPU provides speed, not capability.
   Every feature must work identically on CPU-only machines (using int8/CPU
   fallbacks). Never gate a feature behind GPU presence.

4. **OS agnostic**: Code must run on Windows, macOS, and Linux. Use `pathlib`
   not string paths. Use `sys.platform` checks where platform-specific code is
   unavoidable. Builder detects platform automatically.

5. **No new dependencies for what a few lines can do**: The standing stack is
   PySide6, httpx, openpyxl, pypdf, reportlab, Pillow, python-docx, python-pptx,
   numpy, fastembed, onnxruntime. Do NOT add new packages unless truly needed.

---

## BRANDING RULES

6. **No Anthropic/Claude proper nouns in user-facing UI**: All labels, tooltips,
   error messages, and dialog text must say "LLM" or "AI". Model identifier
   strings (e.g. `claude-opus-4-6`) are acceptable since they are API IDs.

7. **Backwards-compatible aliases**: Internal code can keep `AnthropicClient`,
   `build_anthropic_client` etc for backwards compat, but must also export
   generic aliases: `LLMClient`, `build_llm_client`, `DEFAULT_LLM_BASE_URL`.

8. **Settings keys stay stable**: Internal persistence keys like
   `anthropic_base_url` must NOT be renamed (breaks existing user configs).
   They are internal, not user-facing.

---

## QUALITY RULES

9. **Type hints on every function**: Arguments and returns. No exceptions.

10. **ASCII only**: No Unicode/emojis in code, comments, logs, or UI text.
    Use `tqdm(ascii=True)`. Log prefixes: `[INFO]`, `[WARN]`, `[ERROR]`,
    `[SUCCESS]`.

11. **INPUT_PATH / OUTPUT_PATH at top**: Any script that reads/writes files
    declares paths at the absolute top.

12. **del + gc.collect() after heavy operations**: After processing large
    DataFrames, file loops, or model inference batches.

13. **Error handling at trust boundaries**: Input validation for user input,
    external API responses, and file I/O. Internal code trusts internal code.

14. **Project-scoped background preparation**: Selecting a project or changing
    its KB queues extraction, context enrichment, embeddings, vector storage,
    and context summary work in the installed agent. Closing the browser must
    not cancel this work.

15. **Durable jobs and atomic KB publication**: Safe jobs persist checkpoints
    and recover after agent restart. Readers use the last complete immutable KB
    generation until a new generation is atomically published.

16. **Truthful progress**: The status bar, Activity Bar, KB dialog, and E2E
    dialog must derive from the same job state. Never show completion for a
    partial or interrupted operation.

17. **Safe E2E interruption**: Stop at browser-action/test-case boundaries,
    retain completed cases, discard incomplete-case results, and never replay
    side-effecting partial cases automatically.

18. **Never abandon an approved implementation because a response boundary is
    near**: Persist the task list and current state, continue in the next turn,
    and report only real blockers. Never emit a generic inability statement for
    work that can be completed incrementally.

19. **Adversarial QA convergence is mandatory**: Test as a hostile senior QA,
    developer, architect, operator, accessibility reviewer, security reviewer,
    and end user. Exercise happy paths, invalid inputs, empty/partial states,
    concurrency, cancellation, restart recovery, large inputs, low resources,
    external failures, and rendered UI behavior. Fix every discovered defect,
    rerun affected and full regression gates, and repeat until a clean pass finds
    no new actionable defect or a specific blocker is proven.

20. **Evidence before completion claims**: Report exact commands, test counts,
    rendered/browser checks, deployment state, and residual risks. Compilation
    alone is never proof of functional correctness.

---

## KNOWN CONSTRAINTS & EDGE CASES

14. **os.getlogin() can raise OSError**: On headless systems. Always wrap with
    fallback to `os.environ.get("USER") or os.environ.get("USERNAME")`.

15. **OCR init can fail permanently**: Once `_ocr_init_failed` is set True,
    don't retry. Avoids repeated slow failures on machines without OCR deps.

16. **LanceDB cosine distance is [0, 2] not [0, 1]**: The similarity formula
    must be `max(0.0, 1.0 - dist)` to correctly convert to [0, 1] similarity.

17. **Window geometry from settings can be non-numeric**: Always wrap
    `int(get_ui_pref(...))` in try/except (ValueError, TypeError).

18. **File can disappear between glob and stat**: Always catch OSError when
    calling `.stat()` on globbed results.

19. **Embedding batch size is memory-aware**: 32 (<=4GB), 64 (<=8GB),
    128 (>8GB). Never hardcode a single value.

20. **Whisper model loading**: Use `device="cuda", compute_type="float16"`
    when GPU available, else `device="cpu", compute_type="int8"`.

21. **Frame dedup in multimedia**: Single-word frames must not be dropped.
    Empty word sets must be skipped in dedup comparison.

22. **python312._pth controls embedded Python's sys.path**: The `.` entry
    means `python-embed/` dir (NOT cwd). `Lib\site-packages` is relative to
    that dir. `..\src` reaches the source tree. `import site` enables pip.
    All four lines plus `python312.zip` are required.

23. **Qt/PySide6 cannot run in Git Bash mintty**: Any PySide6 import in a
    mintty terminal causes a SEGFAULT. The launcher MUST be native cmd.exe
    on Windows. This applies to install, build, AND app launch.

24. **Wheelhouse Python version must match python-embed**: Wheels tagged
    `cp312` only work in Python 3.12. If `python-embed/` uses 3.12, the
    wheelhouse MUST be built with `--pyver 3.12`. No cross-version.

---

## BUILD RULES

22. **Single command build**: `python build.py` is the ONLY command needed.
    It auto-cleans old artifacts, installs deps, runs preflight, auto-fixes
    issues, and builds. No flags needed for normal builds.

23. **Build never fails on optional backends**: If fastembed, onnxruntime,
    rapidocr, or PyMuPDF are missing, build proceeds and those features are
    simply inactive at runtime. Build failures are reserved for true errors.

24. **Preflight auto-resolves**: If doctor.py reports missing packages, the
    build auto-installs them and retries. Manual intervention only needed for
    system-level issues (missing OS packages, GPU drivers).

---

## DEPLOYMENT RULES (PORTABLE)

25. **install.cmd is native cmd.exe ONLY**: No Git Bash, no mintty, no
    bash.exe dependency. PySide6/Qt SEGFAULTS in Git Bash's mintty terminal
    (exit code 0xC0000005). This is non-negotiable.

26. **python312._pth must be PATCHED, never deleted**: Deleting it breaks
    pip (site-packages becomes unreachable). Correct content is exactly:
    `python312.zip`, `.`, `..\src`, `Lib\site-packages`, `import site`.
    The `..\src` entry is relative to `python-embed/` and puts `src/` on
    sys.path so `from core.xxx` works.

27. **No sentinel / skip logic in install scripts**: Every double-click
    must do a full clean-install-build-launch cycle. Users expect guaranteed
    fresh install every run. Sentinel files cause confusion when stale state
    persists.

28. **Wheels must match Python version exactly**: cp312 wheels do NOT work
    on Python 3.13. The wheelhouse must be built with the same Python minor
    version as `python-embed/`. Cross-version is NOT supported.

29. **PyInstaller must be in the wheelhouse**: It is a build-time dependency.
    `install.cmd` installs it from wheelhouse before running `build.py`.

30. **build.py --quiet for launcher use**: When called from install.cmd/sh,
    pass --quiet to suppress verbose output. Only progress bar and errors
    are shown to the user.

31. **Console must stay visible on error**: Use `pause` after failures so
    the user can read the error before the window closes. Only detach
    (via `start ""`) for the final successful .exe launch.

32. **pythonw.exe hides ALL errors**: Never use `pythonw.exe` for debugging
    or initial runs. It silently swallows crashes. Use `python.exe` during
    build/install; only the final built .exe runs windowless.

---

## TEST REQUIREMENTS

25. **E2E test suite must pass**: Run `python tests/test_full_e2e.py` before
    any release. All 57+ checks must be green.

26. **Compile check (all .py files)**: Run `python -m py_compile` on every
    file in src/. Zero failures allowed.

27. **No import errors**: Every module must import cleanly with no missing
    dependencies in the installed environment.

---

## ENHANCEMENT TRACKING

| ID | Description | Status | Added |
|----|-------------|--------|-------|
| E-001 | Full hardware utilization (CPU/GPU/NUMA) | Done | 2026-06-24 |
| E-002 | Remove splashscreen | Done | 2026-06-24 |
| E-003 | Generic LLM branding | Done | 2026-06-24 |
| E-004 | OS-agnostic builder | Done | 2026-06-24 |
| E-005 | Memory-aware embed batch sizing | Done | 2026-06-24 |
| E-006 | Comprehensive E2E test suite | Done | 2026-06-24 |
| E-007 | ARM/Apple Silicon (M-series) support | Done | 2026-06-24 |
| E-008 | Sprint filter uses System.BoardLane | Done | 2026-06-24 |
| E-009 | Nav panel icon buttons (Settings/KB/Hide) | Done | 2026-06-24 |
| E-010 | Hide button in action bar for logs | Done | 2026-06-24 |
| E-011 | Portable zero-install deployment (bundled Python) | Done | 2026-06-24 |
| E-012 | VS Code-style collapsible activity bar with SVG icons | Done | 2026-06-24 |
| E-013 | Settings dialog auto-fit (no hardcoded heights) | Done | 2026-06-24 |
| E-014 | Cross-platform install.sh + install.cmd (native, no Git Bash) | Done | 2026-06-24 |
| E-015 | install.cmd builds .exe via PyInstaller (full pipeline) | Done | 2026-06-24 |
| E-016 | build.py --quiet mode (progress bar + errors only) | Done | 2026-06-24 |
| E-017 | Always-clean-install (no sentinel skip logic) | Done | 2026-06-24 |
| E-018 | Persistent resumable agent jobs | Done | 2026-07-12 |
| E-019 | Atomic immutable KB generations | Done | 2026-07-12 |
| E-020 | Project-scoped automatic KB preparation | Done | 2026-07-12 |
| E-021 | Safe Playwright run interruption | Done | 2026-07-12 |


---

## BUG TRACKING

| ID | Description | Severity | Status | Fixed |
|----|-------------|----------|--------|-------|
| B-001 | LanceDB cosine distance [0,2] not [0,1] | Critical | Fixed | 2026-06-24 |
| B-002 | os.getlogin() crash on headless | Critical | Fixed | 2026-06-24 |
| B-003 | API response type validation missing | Critical | Fixed | 2026-06-24 |
| B-004 | Window geometry ValueError on bad prefs | High | Fixed | 2026-06-24 |
| B-005 | File stat crash between glob and access | High | Fixed | 2026-06-24 |
| B-006 | OCR repeated init attempts on failure | High | Fixed | 2026-06-24 |
| B-007 | Frame dedup drops single-word frames | High | Fixed | 2026-06-24 |
| B-008 | Embedding batch hardcoded ignoring RAM | Medium | Fixed | 2026-06-24 |
| B-009 | Dedup signature too short (240 chars) | Medium | Fixed | 2026-06-24 |
| B-010 | PySide6 SEGFAULT in Git Bash mintty (0xC0000005) | Critical | Fixed | 2026-06-24 |
| B-011 | Deleting python312._pth breaks pip entirely | Critical | Fixed | 2026-06-24 |
| B-012 | ModuleNotFoundError 'core' with embedded Python | Critical | Fixed | 2026-06-24 |
| B-013 | cp312 wheels fail on Python 3.13 (version mismatch) | Critical | Fixed | 2026-06-24 |
| B-014 | Sentinel file causes install.cmd to skip package install | High | Fixed | 2026-06-24 |
| B-015 | pythonw.exe hides all crashes (window closes silently) | High | Fixed | 2026-06-24 |
| B-016 | Qt plugin path not found with embedded Python | High | Fixed | 2026-06-24 |
| B-017 | install.cmd was calling install.sh via Git Bash (Qt crash) | High | Fixed | 2026-06-24 |
| B-018 | build.py not called from install.cmd (no .exe produced) | High | Fixed | 2026-06-24 |
| B-019 | clean_old_installs verbose output floods quiet mode | Low | Fixed | 2026-06-24 |

---

## HOW TO USE THIS FILE

- **Before starting work**: Read this file to understand constraints.
- **When fixing a bug**: Add it to the Bug Tracking table above.
- **When adding a feature**: Check rules 1-5 (architecture) first.
- **Before release**: Run the tests in rules 22-24.
- **When finding a new edge case**: Add to "Known Constraints" section.
