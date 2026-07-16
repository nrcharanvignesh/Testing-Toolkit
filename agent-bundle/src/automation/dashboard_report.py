"""
automation/dashboard_report.py
Generates a self-contained HTML management dashboard report.

Single HTML file, all CSS/JS inline, zero external dependencies.
Opens in any browser offline.
"""

from __future__ import annotations

import html
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .e2e_runner import TestCaseResult
except ImportError:
    TestCaseResult = Any  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_dashboard(
    results: list[TestCaseResult],
    bug_summary: dict[str, Any],
    healing_history: list[dict[str, Any]],
    output_path: Path,
) -> Path:
    """Generate a self-contained HTML dashboard report.

    Args:
        results: list of TestCaseResult dataclass instances.
        bug_summary: dict with keys 'recurring_bugs', 'hotspot_stories',
                     'flaky_tests' -- each a list of dicts.
        healing_history: list of dicts with keys step_action, original_locator,
                         healed_locator, success, step_target.
        output_path: where to write the HTML file.

    Returns:
        The resolved output path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(results)
    passed = sum(1 for r in results if r.overall_status == "pass")
    failed = sum(1 for r in results if r.overall_status in ("fail", "error"))
    skipped = total - passed - failed
    pass_rate = (passed / total * 100) if total else 0

    # Story coverage: group test cases by story (tc_id prefix before last dot)
    stories: dict[str, list[Any]] = {}
    for r in results:
        story_key = r.tc_id.rsplit("-", 1)[0] if "-" in r.tc_id else r.tc_id
        stories.setdefault(story_key, []).append(r)

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    page = _HTML_HEAD
    page += _build_summary(total, passed, failed, skipped, pass_rate, len(stories), timestamp)
    page += _build_bar_chart(passed, failed, skipped, total)
    page += _build_test_table(results)
    page += _build_bug_section(bug_summary)
    page += _build_healing_section(healing_history)
    page += _build_story_health(stories)
    page += _HTML_TAIL

    output_path.write_text(page, encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# HTML building blocks
# ---------------------------------------------------------------------------

_E = html.escape


def _build_summary(
    total: int, passed: int, failed: int, skipped: int,
    pass_rate: float, story_count: int, timestamp: str,
) -> str:
    return f"""<section class="cards" aria-label="Executive summary">
<div class="card rate"><span class="big">{pass_rate:.1f}%</span><span class="label">Pass Rate</span></div>
<div class="card"><span class="big">{total}</span><span class="label">Total Tests</span></div>
<div class="card pass"><span class="big">{passed}</span><span class="label">Passed</span></div>
<div class="card fail"><span class="big">{failed}</span><span class="label">Failed</span></div>
<div class="card skip"><span class="big">{skipped}</span><span class="label">Skipped</span></div>
<div class="card"><span class="big">{story_count}</span><span class="label">Stories Covered</span></div>
</section>
<p class="meta">Report generated: {_E(timestamp)}</p>
"""


def _build_bar_chart(passed: int, failed: int, skipped: int, total: int) -> str:
    if total == 0:
        return '<section class="chart-section"><h2>Pass/Fail Breakdown</h2><p>No results.</p></section>'
    pw = passed / total * 100
    fw = failed / total * 100
    sw = skipped / total * 100
    return f"""<section class="chart-section" aria-label="Pass fail breakdown">
<h2>Pass / Fail / Skip</h2>
<div class="bar" role="img" aria-label="Bar chart: {passed} pass, {failed} fail, {skipped} skip">
<div class="bar-pass" style="width:{pw:.1f}%"></div>
<div class="bar-fail" style="width:{fw:.1f}%"></div>
<div class="bar-skip" style="width:{sw:.1f}%"></div>
</div>
<div class="bar-legend"><span class="dot-pass"></span> Pass ({passed}) <span class="dot-fail"></span> Fail ({failed}) <span class="dot-skip"></span> Skip ({skipped})</div>
</section>
"""


def _build_test_table(results: list[Any]) -> str:
    if not results:
        return '<section class="table-section"><h2>Test Cases</h2><p>No test cases.</p></section>'
    rows = ""
    for r in results:
        status_cls = {"pass": "st-pass", "fail": "st-fail", "error": "st-fail"}.get(
            r.overall_status, "st-skip"
        )
        dur_sec = r.duration_ms / 1000
        failure_reason = ""
        if r.overall_status in ("fail", "error"):
            for s in r.steps:
                if s.status in ("fail", "error"):
                    failure_reason = _E(s.actual[:200])
                    break
        detail_rows = ""
        for s in r.steps:
            s_cls = {"pass": "st-pass", "fail": "st-fail", "error": "st-fail"}.get(
                s.status, "st-skip"
            )
            detail_rows += (
                f"<tr><td>{s.step_num}</td><td>{_E(s.action)}</td>"
                f"<td class='{s_cls}'>{s.status}</td>"
                f"<td>{s.duration_ms}ms</td><td>{_E(s.actual[:120])}</td></tr>"
            )
        rows += f"""<details class="tc-row"><summary class="tc-summary">
<span class="tc-id">{_E(r.tc_id)}</span>
<span class="tc-title">{_E(r.title)}</span>
<span class="tc-status {status_cls}">{r.overall_status.upper()}</span>
<span class="tc-dur">{dur_sec:.2f}s</span>
<span class="tc-reason">{failure_reason}</span>
</summary>
<div class="tc-detail">
<table class="step-tbl"><thead><tr><th>#</th><th>Action</th><th>Status</th><th>Duration</th><th>Detail</th></tr></thead>
<tbody>{detail_rows}</tbody></table>
<div class="feedback"><label for="fb-{_E(r.tc_id)}">Feedback</label>
<textarea id="fb-{_E(r.tc_id)}" rows="2" placeholder="Add notes..."></textarea>
<button onclick="saveFb('{_E(r.tc_id)}')">Save</button></div>
</div></details>
"""
    return f'<section class="table-section"><h2>Test Cases</h2>{rows}</section>\n'


def _build_bug_section(bug_summary: dict[str, Any]) -> str:
    recurring = bug_summary.get("recurring_bugs", [])
    hotspots = bug_summary.get("hotspot_stories", [])
    flaky = bug_summary.get("flaky_tests", [])
    if not recurring and not hotspots and not flaky:
        return '<section class="bug-section"><h2>Bug Tracking</h2><p>No issues detected.</p></section>'
    parts = '<section class="bug-section"><h2>Bug Tracking</h2>'
    if recurring:
        parts += "<h3>Recurring Bugs</h3><ul>"
        for b in recurring:
            parts += f"<li><strong>{_E(str(b.get('id', '')))}</strong>: {_E(str(b.get('description', '')))}</li>"
        parts += "</ul>"
    if hotspots:
        parts += "<h3>Hotspot Stories</h3><ul>"
        for h in hotspots:
            parts += f"<li>{_E(str(h))}</li>"
        parts += "</ul>"
    if flaky:
        parts += '<h3>Flakiness Indicators</h3><ul class="flaky-list">'
        for f in flaky:
            parts += f"<li class='amber'>{_E(str(f))}</li>"
        parts += "</ul>"
    parts += "</section>\n"
    return parts


def _build_healing_section(healing_history: list[dict[str, Any]]) -> str:
    if not healing_history:
        return '<section class="heal-section"><h2>Self-Healing Activity</h2><p>No healing events.</p></section>'
    rows = ""
    for h in healing_history:
        success_cls = "st-pass" if h.get("success") else "st-fail"
        rows += (
            f"<tr><td>{_E(str(h.get('step_action', '')))}</td>"
            f"<td>{_E(str(h.get('step_target', '')))}</td>"
            f"<td class='mono'>{_E(str(h.get('original_locator', '')))}</td>"
            f"<td class='mono'>{_E(str(h.get('healed_locator', '')))}</td>"
            f"<td class='{success_cls}'>{('Yes' if h.get('success') else 'No')}</td></tr>"
        )
    return f"""<section class="heal-section"><h2>Self-Healing Activity</h2>
<table class="heal-tbl"><thead><tr><th>Action</th><th>Target</th><th>Original Locator</th><th>Healed Locator</th><th>Success</th></tr></thead>
<tbody>{rows}</tbody></table></section>
"""


def _build_story_health(stories: dict[str, list[Any]]) -> str:
    if not stories:
        return '<section class="story-section"><h2>Per-Story Health</h2><p>No stories.</p></section>'
    parts = '<section class="story-section"><h2>Per-Story Health</h2>'
    for story_id, cases in stories.items():
        p = sum(1 for c in cases if c.overall_status == "pass")
        f = sum(1 for c in cases if c.overall_status in ("fail", "error"))
        s = len(cases) - p - f
        agg_cls = "st-pass" if f == 0 and s == 0 else ("st-fail" if f > 0 else "st-skip")
        # ponytail: inline class lookup; template helper if >10 stories
        tc_list = ""
        for c in cases:
            cls = {"pass": "st-pass", "fail": "st-fail", "error": "st-fail"}.get(
                c.overall_status, "st-skip"
            )
            tc_list += f"<li class='{cls}'>{_E(c.tc_id)}: {c.overall_status}</li>"
        parts += f"""<details class="story-row"><summary>
<span class="story-id">{_E(story_id)}</span>
<span class="story-agg {agg_cls}">{p}P / {f}F / {s}S</span>
</summary><ul>{tc_list}</ul></details>"""
    parts += "</section>\n"
    return parts


# ---------------------------------------------------------------------------
# Static HTML shell
# ---------------------------------------------------------------------------

_HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Test Dashboard Report</title>
<style>
*,*::before,*::after{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f4f5f7;color:#1a1a1a;line-height:1.5}
header{background:#1e293b;color:#fff;padding:1.5rem 2rem;text-align:center}
header h1{margin:0;font-size:1.6rem;font-weight:600}
main{max-width:1100px;margin:0 auto;padding:1.5rem}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem;margin-bottom:1.5rem}
.card{background:#fff;border-radius:8px;padding:1.2rem;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.1)}
.card .big{display:block;font-size:2rem;font-weight:700}
.card .label{display:block;font-size:.8rem;color:#64748b;text-transform:uppercase;margin-top:.3rem}
.card.rate .big{color:#2563eb}
.card.pass .big{color:#16a34a}
.card.fail .big{color:#dc2626}
.card.skip .big{color:#6b7280}
.meta{font-size:.8rem;color:#64748b;margin-bottom:1.5rem}
h2{font-size:1.2rem;margin:1.5rem 0 .8rem;border-bottom:2px solid #e2e8f0;padding-bottom:.3rem}
h3{font-size:1rem;margin:.8rem 0 .4rem}
.chart-section .bar{display:flex;height:28px;border-radius:4px;overflow:hidden;background:#e5e7eb}
.bar-pass{background:#16a34a}.bar-fail{background:#dc2626}.bar-skip{background:#9ca3af}
.bar-legend{margin-top:.5rem;font-size:.8rem;display:flex;gap:1rem;align-items:center}
.dot-pass,.dot-fail,.dot-skip{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
.dot-pass{background:#16a34a}.dot-fail{background:#dc2626}.dot-skip{background:#9ca3af}
.tc-row{background:#fff;border-radius:6px;margin:.5rem 0;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.tc-summary{display:grid;grid-template-columns:100px 1fr 70px 60px 1fr;gap:.5rem;align-items:center;padding:.7rem 1rem;cursor:pointer;list-style:none;font-size:.85rem}
.tc-summary::-webkit-details-marker{display:none}
.tc-id{font-weight:600;color:#334155}
.tc-status{font-weight:700;text-transform:uppercase;font-size:.75rem;padding:2px 8px;border-radius:3px}
.st-pass{color:#16a34a;background:#dcfce7}.st-fail{color:#dc2626;background:#fee2e2}.st-skip{color:#6b7280;background:#f3f4f6}
.tc-dur{color:#64748b;font-size:.78rem}
.tc-reason{color:#b91c1c;font-size:.78rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tc-detail{padding:.5rem 1rem 1rem}
.step-tbl,.heal-tbl{width:100%;border-collapse:collapse;font-size:.8rem;margin-top:.5rem}
.step-tbl th,.heal-tbl th{background:#f1f5f9;text-align:left;padding:.4rem .5rem;font-weight:600}
.step-tbl td,.heal-tbl td{padding:.4rem .5rem;border-top:1px solid #e2e8f0}
.mono{font-family:monospace;font-size:.75rem}
.feedback{margin-top:.8rem}
.feedback textarea{width:100%;border:1px solid #d1d5db;border-radius:4px;padding:.4rem;font-size:.8rem;resize:vertical}
.feedback button{margin-top:.3rem;padding:.3rem .8rem;border:none;background:#2563eb;color:#fff;border-radius:4px;cursor:pointer;font-size:.8rem}
.feedback button:hover{background:#1d4ed8}
.bug-section ul,.story-section ul{padding-left:1.2rem}
.flaky-list li.amber{color:#d97706}
.story-row{background:#fff;border-radius:6px;margin:.4rem 0;padding:.5rem 1rem;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.story-row summary{cursor:pointer;display:flex;gap:1rem;align-items:center;font-size:.9rem}
.story-id{font-weight:600}
.story-agg{font-size:.8rem;padding:2px 6px;border-radius:3px}
@media(max-width:700px){.tc-summary{grid-template-columns:1fr;gap:.2rem}.cards{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<header><h1>Test Execution Dashboard</h1></header>
<main>
"""

_HTML_TAIL = """</main>
<script>
function saveFb(id){var t=document.getElementById('fb-'+id);if(t){localStorage.setItem('fb_'+id,t.value);t.style.borderColor='#16a34a';setTimeout(function(){t.style.borderColor='';},1200);}}
document.addEventListener('DOMContentLoaded',function(){document.querySelectorAll('textarea[id^="fb-"]').forEach(function(t){var k='fb_'+t.id.replace('fb-','');var v=localStorage.getItem(k);if(v)t.value=v;});});
</script>
</body>
</html>
"""
