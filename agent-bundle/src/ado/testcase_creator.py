"""
ado_testcase_creator.py
Create ADO Test Case work items, as children of given parent User
Stories, from a deterministic JSON payload produced by an LLM agent.

INPUT JSON FORMAT (strict)
--------------------------
{
  "schema_version": 1,
  "stories": [
    {
      "parent_work_item_id": 12345,
      "parent_title": "User logs in via SSO",   // optional, for logs
      "test_cases": [
        {
          "title": "TC: SSO login with valid corporate creds",
          "category": "Positive",               // see VALID_CATEGORIES
          "priority": "High",                   // Lowest / Low / Medium / High; optional
          "preconditions": "User has active AD account.",  // optional
          "tags": ["sso", "login"],             // optional
          "steps": [
            {
              "action": "Open the application URL.",
              "expected": "Login screen appears with SSO button."
            },
            {
              "action": "Click 'Sign in with SSO'.",
              "expected": "Redirected to corporate IdP login form."
            }
          ],
          "custom_fields": {
            "Custom.TestCategory": "Positive",
            "Custom.QAGenAIAutomated": "None",
            "Custom.QAGenAITool": "None"
          }
        }
      ]
    }
  ]
}

ADO TEST CASE FIELD MAPPING
---------------------------
- System.Title                          <- test_case.title
- System.WorkItemType                   <- "Test Case" (constant)
- System.AreaPath                       <- inherited from parent or specified
- System.IterationPath                  <- inherited from parent or specified
- Microsoft.VSTS.TCM.Steps              <- XML built from test_case.steps
- Microsoft.VSTS.TCM.AutomationStatus   <- "Not Automated" (constant)
- Microsoft.VSTS.Common.Priority        <- test_case.priority
- System.Tags                           <- ";"-joined test_case.tags
- <each custom_fields entry>            <- as named

Each Test Case is linked to its parent work item with a "Tested By"
relationship (Microsoft.VSTS.Common.TestedBy-Reverse on the Test Case),
which shows as "Tested By" on the parent and "Tests" on the Test Case.

PUBLIC API
----------
    validate_payload(data) -> ValidationReport
    create_test_cases(payload, org, project, pat, ...) -> CreateBatchResult
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Final

import certifi
import httpx

from ado.api import build_auth_header
from core.runtime_config import RuntimeConfig

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
VALID_CATEGORIES: Final[tuple[str, ...]] = (
    "Accessibility",
    "API Validation",
    "Browser",
    "Bug Validation",
    "Data Validation",
    "Error Handling",
    "GUI Validation",
    "Integration",
    "Mobile Platform",
    "N/A",
    "Negative",
    "Performance",
    "Positive",
    "Regression",
    "UAT",
)

VALID_PRIORITIES: Final[tuple[str, ...]] = ("Lowest", "Low", "Medium", "High")

# Map common model-chosen category synonyms to the closest VALID category,
# so a stray label coerces instead of failing validation (which previously
# forced a slow repair round-trip). Keys are lowercased.
_CATEGORY_ALIASES: Final[dict[str, str]] = {
    "boundary": "Data Validation",
    "boundary value": "Data Validation",
    "bva": "Data Validation",
    "validation": "Data Validation",
    "field validation": "Data Validation",
    "input validation": "Data Validation",
    "functional": "Positive",
    "happy path": "Positive",
    "smoke": "Positive",
    "sanity": "Positive",
    "end to end": "Integration",
    "e2e": "Integration",
    "workflow": "Integration",
    "security": "Negative",
    "authorization": "Negative",
    "authentication": "Negative",
    "permission": "Negative",
    "negative testing": "Negative",
    "error": "Error Handling",
    "exception": "Error Handling",
    "ui": "GUI Validation",
    "ux": "GUI Validation",
    "gui": "GUI Validation",
    "usability": "GUI Validation",
    "interface": "GUI Validation",
    "api": "API Validation",
    "integration testing": "Integration",
    "compatibility": "Browser",
    "cross browser": "Browser",
    "cross-browser": "Browser",
    "mobile": "Mobile Platform",
    "responsive": "Mobile Platform",
    "load": "Performance",
    "stress": "Performance",
    "a11y": "Accessibility",
    "wcag": "Accessibility",
    "regression testing": "Regression",
}
# Fast membership for the canonical names (case-insensitive resolution).
_CATEGORY_CANON: Final[dict[str, str]] = {c.lower(): c for c in VALID_CATEGORIES}


def normalize_category(cat: Any) -> str:
    """Coerce a category string to a VALID category. Exact (case-insensitive)
    matches win; otherwise a known alias maps to its canonical category;
    anything unrecognized falls back to 'Positive' (the safest default)."""
    if not isinstance(cat, str):
        return "Positive"
    key = cat.strip().lower()
    if key in _CATEGORY_CANON:
        return _CATEGORY_CANON[key]
    if key in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[key]
    return "Positive"


def _normalize_priority(pr: Any) -> str | None:
    if pr is None:
        return None
    if not isinstance(pr, str):
        return "Medium"
    key = pr.strip().lower()
    table = {"lowest": "Lowest", "low": "Low", "medium": "Medium",
             "high": "High", "critical": "High", "highest": "High",
             "1": "High", "2": "High", "3": "Medium", "4": "Low",
             "1 - critical": "High", "2 - high": "High",
             "3 - medium": "Medium", "4 - low": "Low"}
    return table.get(key, "Medium")


def normalize_payload(data: Any) -> Any:
    """Coerce every test case's category (and priority) to a valid value in
    place. Safe no-op on malformed input. Run before validate_payload so the
    model's near-miss labels don't trigger a repair round-trip."""
    try:
        stories = data.get("stories") if isinstance(data, dict) else None
        if not isinstance(stories, list):
            return data
        for story in stories:
            tcs = story.get("test_cases") if isinstance(story, dict) else None
            if not isinstance(tcs, list):
                continue
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                tc["category"] = normalize_category(tc.get("category"))
                if "priority" in tc:
                    np = _normalize_priority(tc.get("priority"))
                    if np is None:
                        tc.pop("priority", None)
                    else:
                        tc["priority"] = np
    except Exception as e:
        _log.debug("normalize_payload failed: %s", e)
    return data

# Field reference names used by PwC Digital's customized ADO process
# template. These override / replace the stock Microsoft.VSTS.* fields.
PWCD_PRIORITY_FIELD: Final[str] = "Custom.PWCDPriority"
PWCD_AUTOMATION_STATUS_FIELD: Final[str] = "Custom.PwCDAutomationStatus"

# The PwCD Priority field is a picklist that accepts the labels directly
# (not the stock 1..4 integer scale). Map keeps it explicit.
_PRIORITY_LABELS: Final[dict[str, str]] = {
    "High":   "High",
    "Medium": "Medium",
    "Low":    "Low",
    "Lowest": "Lowest",
}

# PwCD Automation Status default for newly-created TCs.
PWCD_AUTOMATION_STATUS_DEFAULT: Final[str] = "N/A"


# Regex that strips the leading "TC:" tag and any "<Category> - " segment
# the LLM may have prepended. Examples:
#   "TC: Data Validation - Closeout task trigger boundary..."
#       -> "Closeout task trigger boundary..."
#   "TC: Positive - SSO login with valid creds"
#       -> "SSO login with valid creds"
#   "Positive - Already-clean title"
#       -> "Already-clean title"
#   "Clean title with no prefix"
#       -> "Clean title with no prefix"
_TITLE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*"
    r"(?:TC\s*[:\-]\s*)?"                # optional "TC:" or "TC -"
    r"(?:(?:" + "|".join(
        re.escape(c) for c in (
            "Accessibility", "API Validation", "Browser",
            "Bug Validation", "Data Validation", "Error Handling",
            "GUI Validation", "Integration", "Mobile Platform",
            "N/A", "Negative", "Performance", "Positive",
            "Regression", "UAT",
        )
    ) + r")\s*[-\u2013\u2014:]\s*)?"     # optional "<Category> - "
    r"",
    re.IGNORECASE,
)


def clean_title(raw: str) -> str:
    """Strip 'TC:' and any '<Category> - ' prefix from a test case
    title. Returns the cleaned title, never empty - falls back to the
    original raw input if cleaning would remove everything."""
    if not raw:
        return ""
    m = _TITLE_PREFIX_RE.match(raw)
    cleaned = raw[m.end():].strip() if m else raw.strip()
    return cleaned or raw.strip()

API_VER: Final[str] = "7.1"


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------
@dataclass(slots=True)
class ValidationReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_stories: int = 0
    n_test_cases: int = 0


def validate_payload(data: Any) -> ValidationReport:
    """Validate the LLM JSON output against the strict schema. Returns
    a report; `ok=False` means do NOT proceed to creation."""
    r = ValidationReport()

    if not isinstance(data, dict):
        r.ok = False
        r.errors.append("Root must be a JSON object.")
        return r

    sv = data.get("schema_version")
    if sv != 1:
        r.warnings.append(
            f"Unexpected schema_version={sv!r}. Continuing, but the "
            f"payload may break if the schema has changed."
        )

    stories = data.get("stories")
    if not isinstance(stories, list) or not stories:
        r.ok = False
        r.errors.append("'stories' must be a non-empty list.")
        return r
    r.n_stories = len(stories)

    for si, story in enumerate(stories):
        prefix = f"stories[{si}]"
        if not isinstance(story, dict):
            r.errors.append(f"{prefix}: must be an object.")
            r.ok = False
            continue

        wid = story.get("parent_work_item_id")
        if not isinstance(wid, int) or wid <= 0:
            r.errors.append(
                f"{prefix}.parent_work_item_id must be a positive int."
            )
            r.ok = False

        tcs = story.get("test_cases")
        if not isinstance(tcs, list) or not tcs:
            r.errors.append(
                f"{prefix}.test_cases must be a non-empty list."
            )
            r.ok = False
            continue

        for ti, tc in enumerate(tcs):
            tcp = f"{prefix}.test_cases[{ti}]"
            if not isinstance(tc, dict):
                r.errors.append(f"{tcp}: must be an object.")
                r.ok = False
                continue
            title = tc.get("title")
            if not isinstance(title, str) or not title.strip():
                r.errors.append(f"{tcp}.title must be a non-empty string.")
                r.ok = False
            cat = tc.get("category")
            if not isinstance(cat, str) or cat not in VALID_CATEGORIES:
                r.errors.append(
                    f"{tcp}.category={cat!r} must be one of "
                    f"{VALID_CATEGORIES}."
                )
                r.ok = False
            pr = tc.get("priority")
            if pr is not None and (
                not isinstance(pr, str) or pr not in VALID_PRIORITIES
            ):
                r.errors.append(
                    f"{tcp}.priority={pr!r} must be one of "
                    f"{VALID_PRIORITIES} or omitted."
                )
                r.ok = False
            steps = tc.get("steps")
            if not isinstance(steps, list) or not steps:
                r.errors.append(f"{tcp}.steps must be a non-empty list.")
                r.ok = False
                continue
            for si2, st in enumerate(steps):
                if not isinstance(st, dict):
                    r.errors.append(
                        f"{tcp}.steps[{si2}]: must be an object."
                    )
                    r.ok = False
                    continue
                if not isinstance(st.get("action"), str) or \
                   not st["action"].strip():
                    r.errors.append(
                        f"{tcp}.steps[{si2}].action must be non-empty."
                    )
                    r.ok = False
                if not isinstance(st.get("expected"), str):
                    r.errors.append(
                        f"{tcp}.steps[{si2}].expected must be a string."
                    )
                    r.ok = False
            r.n_test_cases += 1
    return r


# ---------------------------------------------------------------------
# ADO Steps XML
# ---------------------------------------------------------------------
# ADO's Microsoft.VSTS.TCM.Steps field is XML at the outer level. The
# step "action" and "expected" go inside <parameterizedString> elements
# whose value is HTML - but because the surrounding container is XML,
# the inner HTML's '<' and '>' MUST be entity-encoded (so the
# parameterizedString text content is the *literal string* '<P>...</P>',
# not an XML child element). If the inner HTML is left unescaped, ADO
# silently stores the steps as empty. This is a long-standing ADO API
# quirk: the field is declared as type HTML inside an XML wrapper.

def _xml_encoded_step_html(text: str) -> str:
    """Encode user-provided step text as XML text content for embedding
    inside a <parameterizedString> element. The output is the LITERAL
    XML text content - no further encoding needed at the call site.

    Two-pass encoding (matches ADO's native export format exactly):

      Pass 1 - HTML escape user text:
          user's `<`, `>`, `&` become HTML entity references so the
          resulting HTML <P>...</P> body is well-formed.

      Pass 2 - XML escape the wrapped HTML:
          encode the whole HTML string for safe embedding inside XML.
          Note this intentionally double-encodes the `&` characters
          that pass 1 introduced (e.g. `&gt;` -> `&amp;gt;`) - this is
          correct, because the XML parser will decode one level back
          to `&gt;` which is then valid HTML for the renderer.

    Example:
        input:  "if a > b"
        pass 1: "if a &gt; b"
        wrap:   "<P>if a &gt; b</P>"
        pass 2: "&lt;P&gt;if a &amp;gt; b&lt;/P&gt;"

        ADO XML parser decodes once -> "<P>if a &gt; b</P>" (valid HTML)
        ADO HTML renderer decodes -> "if a > b" (correct display)
    """
    # Pass 1: HTML-escape user text. `&` MUST come first so the
    # ampersands we introduce in subsequent replacements don't get
    # themselves re-encoded.
    s = (text or "")
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    s = s.replace("\n", "<BR/>")
    html = f"<P>{s}</P>"
    # Pass 2: XML-escape the whole HTML string. Same ordering rule -
    # ampersand first. After this pass the `&` chars from pass 1 are
    # now `&amp;`, which is correct (XML decode gives them back).
    xml = html.replace("&", "&amp;")
    xml = xml.replace("<", "&lt;")
    xml = xml.replace(">", "&gt;")
    return xml


def build_steps_xml(steps: list[dict[str, Any]]) -> str:
    """Build the Microsoft.VSTS.TCM.Steps XML payload.

    Output shape (matches ADO's native export format):

        <steps id="0" last="N+1">
          <step id="2" type="ActionStep">
            <parameterizedString isformatted="true">
              &lt;P&gt;...action HTML...&lt;/P&gt;
            </parameterizedString>
            <parameterizedString isformatted="true">
              &lt;P&gt;...expected HTML...&lt;/P&gt;
            </parameterizedString>
            <description/>
          </step>
          <step id="3" type="ActionStep">...</step>
          ...
        </steps>

    Critical conventions enforced here:

    1. Step IDs START AT 2. id=0 is the <steps> container; id=1 is
       reserved by ADO internally. Step IDs of 1 cause some ADO
       process templates (notably PwC's customized template) to
       silently drop the Steps payload while still creating the work
       item, leading to "Test Case created but Steps are empty"
       symptoms.
    2. The `last` attribute is the HIGHEST step id used, i.e.
       (count + 1) since IDs start at 2.
    3. Type is "ActionStep" (not "ValidateStep"). ValidateStep is
       reserved for shared validate steps imported from a shared step
       library.
    4. parameterizedString inner content is the literal string of
       HTML, with `<` and `>` entity-encoded so the XML stays valid.
    """
    if not steps:
        return '<steps id="0" last="1"></steps>'

    n = len(steps)
    first_id = 2
    last_id = first_id + n - 1   # highest step id used
    parts: list[str] = []
    parts.append(f'<steps id="0" last="{last_id}">')
    for offset, st in enumerate(steps):
        step_id = first_id + offset
        action_xml = _xml_encoded_step_html(st.get("action", ""))
        expected_xml = _xml_encoded_step_html(st.get("expected", ""))
        parts.append(
            f'<step id="{step_id}" type="ActionStep">'
            f'<parameterizedString isformatted="true">'
            f'{action_xml}</parameterizedString>'
            f'<parameterizedString isformatted="true">'
            f'{expected_xml}</parameterizedString>'
            f'<description/></step>'
        )
    parts.append("</steps>")
    return "".join(parts)


# ---------------------------------------------------------------------
# ADO API client
# ---------------------------------------------------------------------
async def _get_parent(
    client: httpx.AsyncClient,
    org: str,
    parent_id: int,
) -> dict[str, Any] | None:
    """Fetch parent work item for inheriting AreaPath / IterationPath."""
    url = (
        f"https://dev.azure.com/{org}/_apis/wit/workitems/"
        f"{parent_id}?api-version={API_VER}&fields="
        f"System.AreaPath,System.IterationPath,System.Title"
    )
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _patch_body(
    title: str,
    area_path: str | None,
    iteration_path: str | None,
    steps_xml: str,
    priority: str | None,
    tags: list[str] | None,
    custom_fields: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build the JSON Patch document for creating a Test Case."""
    ops: list[dict[str, Any]] = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {
            "op": "add",
            "path": "/fields/Microsoft.VSTS.TCM.Steps",
            "value": steps_xml,
        },
        # PwC Digital custom automation status; defaults to N/A.
        {
            "op": "add",
            "path": f"/fields/{PWCD_AUTOMATION_STATUS_FIELD}",
            "value": PWCD_AUTOMATION_STATUS_DEFAULT,
        },
    ]
    if area_path:
        ops.append({"op": "add", "path": "/fields/System.AreaPath",
                    "value": area_path})
    if iteration_path:
        ops.append({"op": "add", "path": "/fields/System.IterationPath",
                    "value": iteration_path})
    if priority is not None:
        label = _PRIORITY_LABELS.get(priority)
        if label is not None:
            ops.append({
                "op": "add",
                "path": f"/fields/{PWCD_PRIORITY_FIELD}",
                "value": label,
            })
    if tags:
        ops.append({
            "op": "add", "path": "/fields/System.Tags",
            "value": "; ".join(tags),
        })
    if custom_fields:
        for k, v in custom_fields.items():
            ops.append({"op": "add", "path": f"/fields/{k}", "value": v})
    return ops


def _link_op(parent_id: int, org: str) -> dict[str, Any]:
    """Patch op that links the Test Case to its parent work item with a
    "Tested By" relationship (Microsoft.VSTS.Common.TestedBy-Reverse on the
    Test Case side, which renders as "Tested By" on the parent). No comment
    is attached - per user preference - so the link adds no visible text to
    the work-item history."""
    return {
        "op": "add",
        "path": "/relations/-",
        "value": {
            "rel": "Microsoft.VSTS.Common.TestedBy-Reverse",
            "url": f"https://dev.azure.com/{org}/_apis/wit/workItems/"
                   f"{parent_id}",
        },
    }


# ---------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------
@dataclass(slots=True)
class CreateOneResult:
    parent_id: int
    title: str
    created_id: int = 0
    created_url: str = ""
    ok: bool = True
    error: str = ""


@dataclass(slots=True)
class CreateBatchResult:
    files: list[CreateOneResult] = field(default_factory=list)
    n_ok: int = 0
    n_failed: int = 0


# ---------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------
async def _create_one(
    client: httpx.AsyncClient,
    org: str,
    project: str,
    parent_id: int,
    tc: dict[str, Any],
    area_override: str,
    iteration_override: str,
    inherit_paths: bool,
    parent_cache: dict[int, dict[str, Any]],
    on_log: Callable[[str], None] | None,
) -> CreateOneResult:
    raw_title = (tc.get("title") or "").strip()
    title = clean_title(raw_title)
    res = CreateOneResult(parent_id=parent_id, title=title)
    try:
        area = area_override
        iteration = iteration_override
        if inherit_paths and (not area or not iteration):
            if parent_id not in parent_cache:
                pinfo = await _get_parent(client, org, parent_id)
                if pinfo and "fields" in pinfo:
                    parent_cache[parent_id] = pinfo["fields"]
                else:
                    parent_cache[parent_id] = {}
            fields = parent_cache[parent_id]
            if not area:
                area = fields.get("System.AreaPath", "")
            if not iteration:
                iteration = fields.get("System.IterationPath", "")

        steps_xml = build_steps_xml(tc.get("steps") or [])
        # Merge "category" into custom_fields if a TestCategory field
        # name was passed; ado tab will inject this.
        custom = dict(tc.get("custom_fields") or {})
        ops = _patch_body(
            title=title,
            area_path=area or None,
            iteration_path=iteration or None,
            steps_xml=steps_xml,
            priority=tc.get("priority"),
            tags=tc.get("tags"),
            custom_fields=custom,
        )
        # Add parent link
        ops.append(_link_op(parent_id, org))

        url = (
            f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems/"
            f"$Test%20Case?api-version={API_VER}"
        )
        resp = await client.post(
            url,
            content=json.dumps(ops),
            headers={"Content-Type": "application/json-patch+json"},
        )
        if resp.status_code not in (200, 201):
            res.ok = False
            res.error = (
                f"HTTP {resp.status_code}: {resp.text[:400]}"
            )
            if on_log:
                on_log(f"[ERROR] '{title}': {res.error}")
            return res

        body = resp.json()
        res.created_id = int(body.get("id") or 0)
        res.created_url = (
            f"https://dev.azure.com/{org}/{project}/_workitems/edit/"
            f"{res.created_id}"
        )

        # Verify Steps actually landed. ADO can return 201 Created
        # while silently dropping the Steps field if the XML doesn't
        # conform to whatever the process template expects. Fetch the
        # work item back, look at the Steps field, and warn loudly if
        # empty.
        try:
            verify_url = (
                f"https://dev.azure.com/{org}/_apis/wit/workitems/"
                f"{res.created_id}?api-version={API_VER}"
                f"&fields=Microsoft.VSTS.TCM.Steps,System.Title"
            )
            vresp = await client.get(verify_url)
            if vresp.status_code == 200:
                vdata = vresp.json()
                steps_field = (
                    (vdata.get("fields") or {})
                    .get("Microsoft.VSTS.TCM.Steps", "")
                )
                # Steps stored as XML. Empty/missing = empty string or
                # the bare container with no <step> children.
                has_steps = bool(steps_field) and "<step " in steps_field
                if not has_steps and on_log:
                    on_log(
                        f"[WARN] TC #{res.created_id} was created but "
                        f"its Steps field is empty in ADO. The XML was "
                        f"accepted but ADO silently dropped it - this "
                        f"usually means the step IDs or XML structure "
                        f"don't match what your process template "
                        f"expects. Steps XML that was sent:"
                    )
                    on_log(f"[WARN]   {(tc.get('steps') and steps_xml) or '(empty)'}")
        except Exception as e:
            _log.debug("steps verification failed: %s", e)  # best-effort; never blocks the run

        if on_log:
            on_log(
                f"[SUCCESS] Created TC #{res.created_id} for parent "
                f"#{parent_id}: '{title}'"
            )
        return res
    except Exception as e:
        res.ok = False
        res.error = f"{type(e).__name__}: {e}"
        if on_log:
            on_log(f"[ERROR] '{title}': {res.error}")
        return res


async def create_test_cases_async(
    payload: dict[str, Any],
    org: str,
    project: str,
    pat: str,
    area_override: str = "",
    iteration_override: str = "",
    inherit_paths: bool = True,
    test_category_field: str = "",
    cfg: RuntimeConfig | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> CreateBatchResult:
    """Create every test case in the payload. test_category_field, if
    provided, names the ADO field reference (e.g.
    'Custom.TestCategory') into which each TC's `category` value will
    be written, in addition to any custom_fields the JSON specifies.

    `cfg` (RuntimeConfig) drives TLS handling. When None, a default
    config is used which combines certifi + OS-store roots (handles
    corporate TLS-intercepting proxies like Zscaler)."""
    batch = CreateBatchResult()
    stories = payload.get("stories") or []
    total = sum(len(s.get("test_cases") or []) for s in stories)
    done = 0
    if on_log:
        on_log(
            f"[INFO] Creating {total} test case(s) across {len(stories)} "
            f"story/stories in {org}/{project}"
        )

    if cfg is None:
        cfg = RuntimeConfig.from_env_defaults()
    if on_log:
        on_log(f"[INFO] TLS mode: {cfg.tls_mode}")

    headers = build_auth_header(pat)
    verify_arg = cfg.build_ssl()
    parent_cache: dict[int, dict[str, Any]] = {}

    sem = asyncio.Semaphore(max(1, cfg.concurrency))
    progress = {"n": 0}

    async with httpx.AsyncClient(
        headers=headers, verify=verify_arg,
        timeout=httpx.Timeout(cfg.http_timeout_sec),
    ) as client:
        # Pre-populate parent_cache for all unique parent IDs so
        # concurrent tasks never race on the same cache miss.
        if inherit_paths and (not area_override or not iteration_override):
            unique_parents: set[int] = set()
            for story in stories:
                pid = int(story.get("parent_work_item_id") or 0)
                if pid:
                    unique_parents.add(pid)
            _prefetch_sem = asyncio.Semaphore(8)

            async def _bounded_parent(
                _pid: int,
            ) -> tuple[int, dict[str, Any]]:
                async with _prefetch_sem:
                    try:
                        pinfo = await _get_parent(client, org, _pid)
                    except Exception:
                        return (_pid, {})
                    if pinfo and "fields" in pinfo:
                        return (_pid, pinfo["fields"])
                    return (_pid, {})

            _results: list[tuple[int, dict[str, Any]]] = (
                await asyncio.gather(
                    *(_bounded_parent(pid) for pid in unique_parents)
                )
            )
            for _pid, _fields in _results:
                parent_cache[_pid] = _fields

        # Build flat task list preserving order
        tasks: list[tuple[int, dict[str, Any], str]] = []
        for story in stories:
            parent_id = int(story.get("parent_work_item_id") or 0)
            parent_title = (story.get("parent_title") or "").strip()
            if on_log and parent_title:
                on_log(f"[INFO] Parent #{parent_id}: {parent_title}")
            for tc in story.get("test_cases") or []:
                tc_local = dict(tc)
                if test_category_field:
                    cf = dict(tc_local.get("custom_fields") or {})
                    cf.setdefault(test_category_field, tc_local.get("category"))
                    tc_local["custom_fields"] = cf
                tasks.append((parent_id, tc_local, parent_title))

        async def _run_one(
            parent_id: int, tc_local: dict[str, Any], _title: str,
        ) -> CreateOneResult:
            async with sem:
                if on_progress:
                    on_progress("create", progress["n"], total)
                r = await _create_one(
                    client=client, org=org, project=project,
                    parent_id=parent_id,
                    tc=tc_local,
                    area_override=area_override,
                    iteration_override=iteration_override,
                    inherit_paths=inherit_paths,
                    parent_cache=parent_cache,
                    on_log=on_log,
                )
                progress["n"] += 1
                return r

        results = await asyncio.gather(
            *(_run_one(pid, tc, pt) for pid, tc, pt in tasks)
        )
        for r in results:
            batch.files.append(r)
            if r.ok:
                batch.n_ok += 1
            else:
                batch.n_failed += 1

    if on_progress:
        on_progress("create", total, total)
    gc.collect()
    return batch


# ---------------------------------------------------------------------
# System prompt for the AI agent
# ---------------------------------------------------------------------
SYSTEM_PROMPT: Final[str] = r"""
You are a senior QA engineer generating Azure DevOps Test Cases for
an enterprise web application. You will be given two inputs:

  1. A requirements document (PDF/DOCX/MD/TXT). This is the source of
     truth for application behavior.
  2. An ADO Dump PDF that contains the full text of one or more User
     Stories from Azure DevOps (title, work item ID, description,
     acceptance criteria, and any embedded screenshots/notes).

YOUR DELIVERABLE
================
Emit a SINGLE JSON object that conforms exactly to the schema below.
Wrap the entire JSON in a fenced code block tagged with the word
`json`. Output nothing else - no preamble, no commentary, no trailing
text. The JSON must parse with the strictest interpretation of
RFC 8259 (no trailing commas, no comments, double-quoted strings,
no NaN/Infinity).

DETERMINISM RULES
=================
- Use a STABLE ordering: stories sorted by parent_work_item_id
  ascending; test_cases sorted within each story by their `category`
  in this exact order, then by `title` ascending:
    Positive, Negative, Data Validation, Error Handling,
    GUI Validation, Integration, API Validation, Browser, Mobile
    Platform, Accessibility, Performance, Regression, UAT,
    Bug Validation, N/A.
  Any category not in that list ranks after every listed category.
  Use ONLY the categories enumerated in the JSON SCHEMA below; do not
  invent new ones (e.g. there is no "Boundary" - use "Data Validation"
  for boundary-value checks).
- Do NOT invent acceptance criteria, error codes, field names,
  validation thresholds, or screen labels that are not stated in the
  inputs. If a criterion is implied but not explicit, omit the TC.
- Do NOT add cute prose, marketing language, or "shall" boilerplate.
  Steps and expected results are imperative, terse, technical.
- Re-running the agent on the same inputs must produce the same JSON
  byte-for-byte (no timestamps, no randomized IDs, no model
  self-references).

JSON SCHEMA
===========
{
  "schema_version": 1,
  "stories": [
    {
      "parent_work_item_id": <positive int - from ADO Dump>,
      "parent_title": <string - title of the user story>,
      "test_cases": [
        {
          "title": <string - clean human title, 12-110 chars; do NOT
                    prefix with "TC:" or "<Category> - ">,
          "category": <one of: "Accessibility", "API Validation",
                      "Browser", "Bug Validation", "Data Validation",
                      "Error Handling", "GUI Validation", "Integration",
                      "Mobile Platform", "N/A", "Negative",
                      "Performance", "Positive", "Regression", "UAT">,
          "priority": <one of: "Lowest", "Low", "Medium", "High">,
          "preconditions": <string or omit>,
          "tags": [<lowercase short tokens>],
          "steps": [
            {
              "action":   <string - imperative, single-purpose>,
              "expected": <string - observable, verifiable outcome>
            }
          ],
          "custom_fields": {
            "QA GenAI Automated": "None",
            "QA GenAI Tool":      "None"
          }
        }
      ]
    }
  ]
}

GENERATION RULES (TEST CASE QUALITY)
====================================
1. Every acceptance criterion in the source story MUST be covered by
   at least one "Positive" TC and, where preconditions allow it, by
   one "Negative" TC.
2. For each input field mentioned in the story or referenced from the
   requirements doc:
     - One "Data Validation" TC covering valid boundary values.
     - One "Negative" TC covering invalid input (out-of-range, wrong
       type, malformed format) IF the requirements specify an error
       behavior for that case. Otherwise omit.
3. For each backend call mentioned: one "API Validation" TC covering
   the happy-path payload, and (if specified) one "Error Handling"
   TC for the failure response.
4. If the story has UI elements: one "GUI Validation" TC asserting
   labels, placeholder text, default selections, and visible
   controls.
5. If the requirements doc enumerates supported browsers or mobile
   form factors that are relevant to this story: emit one "Browser"
   TC and one "Mobile Platform" TC referencing the explicit list.
6. If the story explicitly references WCAG or accessibility
   requirements: emit one "Accessibility" TC. Otherwise omit.
7. NEVER emit a TC of category "Bug Validation" unless the source
   material identifies a specific bug ID and its fix.
8. NEVER emit "Performance" TCs unless the requirements doc gives a
   measurable performance target (e.g. "<2s p95 latency").
9. NEVER duplicate logical coverage across categories. Each TC must
   verify a behavior no other TC in the same story verifies.
10. Steps:
    - TARGET 8-15 steps per TC. Minimum 5 steps, maximum 20 steps.
      Each user interaction (click, fill, select, navigate) is its
      OWN step. A test case with fewer than 5 steps is TOO HIGH-LEVEL
      and must be expanded with granular field-by-field interactions.
      Multi-screen flows MUST have one step per screen transition plus
      one step per field/control interaction on each screen.
    - CRITICAL: Every test step MUST begin with the screen/page
      context where the action takes place. Format: "On [Screen/Page
      Name], [action]" or "Navigate to [Screen/Page Name]" for
      navigation steps. Examples:
        * "Navigate to the Home Page"
        * "On the Home Page, click 'Activities' in the navigation menu"
        * "On the Activities Page, click 'Create Needs Assessment' button"
        * "On the Create Needs Assessment form, enter 'Test Assessment' in the Title field"
        * "On the Confirmation dialog, click 'Submit' button"
      Never write a step without indicating which screen, page, or
      dialog the user is currently on.
    - COMPLETE SCREEN COVERAGE: If a workflow passes through screens
      A -> B -> C, you MUST include explicit steps on EACH screen.
      Do NOT skip intermediate screens or combine multiple screens
      into one step. Each screen transition gets its own step.
    - FIELD-LEVEL SPECIFICITY: Never write generic actions like "fill
      in the form" or "enter the details". Instead, write one step per
      field interaction: "On the Registration Form, enter
      'jane.doe@company.com' in the Email field", "On the Registration
      Form, select 'Australia' from the Country dropdown".
    - EXPECTED STATE AFTER EACH ACTION: Every step's expected result
      must describe the observable UI change - what appears, what
      becomes enabled/disabled, what message is shown, or what screen
      loads next. Never leave an expected result vague like "action is
      successful".
    - First step navigates to the starting screen/page for the test.
    - Final step's "expected" must match the success criterion of
      the TC's category.
    - Use stable element references from the requirements doc when
      available (e.g. "the 'Submit' button"), otherwise descriptive
      ("the primary submit control").
11. Titles:
    - Format: a concise, human-readable description of the behavior
      under test. Do NOT prefix with "TC:" and do NOT include the
      category name in the title.
    - Example (good): "SSO login with valid corporate credentials".
    - Example (bad):  "TC: Positive - SSO login with valid corporate creds".
    - 12 to 110 characters. No emoji, no all-caps shouting.
12. Concrete test data: when a step exercises a field or value, use a
    SPECIFIC example value taken from (or consistent with) the
    requirements (e.g. a 5MB file, a 21st attachment, a duplicate GPAS
    ID) rather than a vague "enter a value". Never invent specific
    numbers/labels the requirements do not support.
13. Atomic expecteds: each step's "expected" asserts ONE observable
    outcome (a visible message, a state change, a stored value). Put the
    primary pass/fail assertion as the final step's expected.
14. Preconditions: state the exact starting state (role, record status,
    feature flags) needed to run the TC; keep it to one or two sentences.
15. Coverage balance per story: aim for a healthy spread - at least one
    Positive happy-path TC, the relevant Negative/Error Handling cases the
    requirements specify, and Data Validation for boundary values - WITHOUT
    padding. Quality and traceability beat quantity.
16. Current vs proposed (delta) coverage: requirements often describe BOTH
    the current/existing behavior ("as-is") and the proposed change
    ("to-be"). When both are present:
    - Cover the NEW/changed behavior the story introduces (the primary
      goal of the test set).
    - Add Regression TC(s) verifying that existing behavior the change is
      NOT meant to alter still works.
    - Where the source contrasts old vs new for the same function, make the
      expected result assert the PROPOSED behavior, and note the prior
      behavior in the precondition or title only when the source states it
      (e.g. precondition "Previously the field was optional"). Never invent
      a current-state detail the inputs do not provide.

ALWAYS-CONSTANT FIELDS
======================
For EVERY test case emit these exact entries inside `custom_fields`:
    "QA GenAI Automated": "None",
    "QA GenAI Tool":      "None"
These two never vary.

EDGE CASES
==========
- If a user story in the ADO Dump has no parsable acceptance
  criteria, still emit at least one "Positive" TC asserting the
  story's stated outcome, plus one "N/A" TC noting "Requires
  clarification: <specific question>" as its first step's action
  and "Requirements team responds with clarification" as its
  expected. Set priority="Lowest" on N/A TCs.
- If the ADO Dump is empty or unparseable, emit:
    {"schema_version": 1, "stories": []}
  and nothing else.
- If multiple stories share the same parent_work_item_id (should
  not happen), merge their test_cases into one stories[] entry.

EXAMPLES (calibration - match this DETAILED quality level)
===========================================================
Example 1 - Positive TC demonstrating REQUIRED step granularity (10 steps):
{
  "title": "New needs assessment created with all required fields",
  "category": "Positive",
  "priority": "High",
  "preconditions": "User is logged in as Case Manager; at least one client exists in the system with status 'Active'.",
  "tags": ["needs-assessment", "create", "happy-path"],
  "steps": [
    {"action": "Navigate to the Home Page.", "expected": "Home Page loads showing the main navigation menu and dashboard tiles."},
    {"action": "On the Home Page, click 'Assessments' in the top navigation bar.", "expected": "Assessments Landing Page loads showing the list of recent assessments and a 'Create New' button."},
    {"action": "On the Assessments Landing Page, click 'Create New Assessment' button.", "expected": "Assessment Type Selection dialog appears with options: 'Needs Assessment', 'Risk Assessment', 'Progress Review'."},
    {"action": "On the Assessment Type Selection dialog, select 'Needs Assessment' and click 'Continue'.", "expected": "Create Needs Assessment form opens with Section 1 (Client Details) visible. Client Name field is empty and marked required."},
    {"action": "On the Create Needs Assessment form (Section 1), click the Client Name search field and type 'Smith'.", "expected": "Auto-complete dropdown appears showing matching clients (e.g., 'John Smith - ID 12345', 'Jane Smith - ID 12346')."},
    {"action": "On the Create Needs Assessment form (Section 1), select 'John Smith - ID 12345' from the dropdown.", "expected": "Client Name field is populated with 'John Smith'. Date of Birth, Contact Number, and Address fields auto-fill from the client record."},
    {"action": "On the Create Needs Assessment form (Section 1), select Assessment Date as today's date from the date picker.", "expected": "Assessment Date field shows today's date. The 'Next' button becomes enabled."},
    {"action": "On the Create Needs Assessment form (Section 1), click 'Next' to proceed to Section 2.", "expected": "Section 2 (Identified Needs) loads. A table with columns 'Need Category', 'Priority', 'Description' is displayed with one empty row."},
    {"action": "On the Create Needs Assessment form (Section 2), select 'Housing' from the Need Category dropdown, set Priority to 'High', and enter 'Client requires supported accommodation' in Description.", "expected": "Row is populated. 'Add Another Need' button appears below the table."},
    {"action": "On the Create Needs Assessment form (Section 2), click 'Submit Assessment'.", "expected": "Success banner displays: 'Needs Assessment NA-2026-0042 created successfully'. Page redirects to the Assessment Detail view showing all entered data in read-only mode."}
  ],
  "custom_fields": {"QA GenAI Automated": "None", "QA GenAI Tool": "None"}
}

Example 2 - Data Validation TC with field-level detail (8 steps):
{
  "title": "Assessment Date rejects future dates beyond 7 days",
  "category": "Data Validation",
  "priority": "Medium",
  "preconditions": "User is logged in as Case Manager; on the Create Needs Assessment form Section 1 with a client selected.",
  "tags": ["needs-assessment", "date-validation", "boundary"],
  "steps": [
    {"action": "On the Create Needs Assessment form (Section 1), click the Assessment Date field to open the date picker.", "expected": "Date picker calendar opens showing the current month. Dates more than 7 days in the future are greyed out."},
    {"action": "On the date picker, attempt to click a date 8 days from today.", "expected": "The date is not selectable (greyed out). No value is entered in the field."},
    {"action": "On the Create Needs Assessment form (Section 1), manually type a date 8 days from today (format DD/MM/YYYY) in the Assessment Date field.", "expected": "Red validation error appears below the field: 'Assessment Date cannot be more than 7 days in the future'."},
    {"action": "On the Create Needs Assessment form (Section 1), clear the Assessment Date field.", "expected": "Validation error disappears. Field shows placeholder text 'DD/MM/YYYY'."},
    {"action": "On the Create Needs Assessment form (Section 1), enter a date exactly 7 days from today.", "expected": "Date is accepted. No validation error. Field shows the entered date with a green checkmark."},
    {"action": "On the Create Needs Assessment form (Section 1), clear the field and enter today's date.", "expected": "Date is accepted. No validation error. 'Next' button remains enabled."},
    {"action": "On the Create Needs Assessment form (Section 1), clear the field and enter yesterday's date.", "expected": "Date is accepted. Past dates within reason are valid for backdating assessments."},
    {"action": "On the Create Needs Assessment form (Section 1), clear the field and leave it empty, then click 'Next'.", "expected": "Red validation error appears: 'Assessment Date is required'. Form does not advance to Section 2."}
  ],
  "custom_fields": {"QA GenAI Automated": "None", "QA GenAI Tool": "None"}
}

Example 3 - Error Handling TC with recovery flow (7 steps):
{
  "title": "Assessment submission recovers gracefully after session timeout",
  "category": "Error Handling",
  "priority": "Medium",
  "preconditions": "User is on Section 2 of Create Needs Assessment with all fields filled. Session is configured to timeout after 30 minutes of inactivity.",
  "tags": ["needs-assessment", "error", "session-timeout", "recovery"],
  "steps": [
    {"action": "On the Create Needs Assessment form (Section 2), wait for the 30-minute session timeout to elapse without any interaction.", "expected": "Session Timeout Warning dialog appears: 'Your session is about to expire. Click Continue to stay logged in.'"},
    {"action": "On the Session Timeout Warning dialog, do NOT click any button and wait an additional 60 seconds.", "expected": "Dialog closes. Login Page loads indicating the session has expired."},
    {"action": "On the Login Page, enter valid credentials and click 'Sign In'.", "expected": "Home Page loads after successful authentication."},
    {"action": "On the Home Page, check for a 'Resume Draft' notification or banner.", "expected": "A notification appears: 'You have 1 unsaved draft assessment. Click to resume.' (Draft auto-save feature)."},
    {"action": "On the Home Page, click the 'Resume Draft' notification link.", "expected": "Create Needs Assessment form reloads at Section 2 with all previously entered data intact (client details from Section 1 and needs data from Section 2)."},
    {"action": "On the Create Needs Assessment form (Section 2), verify all previously entered data is present, then click 'Submit Assessment'.", "expected": "Success banner displays: 'Needs Assessment created successfully'. No data was lost from the timeout recovery."},
    {"action": "Navigate to the Assessments Landing Page and locate the newly created assessment.", "expected": "The assessment appears in the list with correct client name, date, and status 'Completed'. Created timestamp matches the submission time (not the original session start)."}
  ],
  "custom_fields": {"QA GenAI Automated": "None", "QA GenAI Tool": "None"}
}

CRITICAL: emit nothing outside the fenced ```json ... ``` block.
""".strip()
