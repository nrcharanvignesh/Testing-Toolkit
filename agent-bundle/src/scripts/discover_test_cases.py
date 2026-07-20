"""
discover_test_cases.py
Standalone diagnostic that shows HOW a tracker links test cases to work items,
and what the Testing Toolkit's versatile discovery counts for each item.

It reuses the SAME discovery logic the board uses (ado.boards / jira.boards),
so its output is an exact preview of the "Generated Tests" column -- useful to
confirm a tenant's linking convention or to debug a case that shows 0.

Credentials come from the agent's stored settings (no secrets on the command
line). ASCII-only output.

Usage (run from the agent's install dir, i.e. the folder that contains this
`scripts/` package's parent `src/`):

    python -m scripts.discover_test_cases --tracker ado  --project "My Project" --ids 1536967,1536939
    python -m scripts.discover_test_cases --tracker jira --keys PROJ-1,PROJ-2
    python -m scripts.discover_test_cases --tracker ado  --project "My Project" --ids 1536967 --json

Exit code 0 on success, 1 on a usage/credential error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running as a plain file (python scripts/discover_test_cases.py) by
# putting the agent's src/ dir on the import path.
_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _log(prefix: str, msg: str) -> None:
    print(f"[{prefix}] {msg}")


# ------------------------------------------------------------------ ADO
async def _discover_ado(project: str, ids: list[int], as_json: bool) -> int:
    from ado import boards
    from core.runtime_config import RuntimeConfig
    from core.settings_store import KEY_ORG, get_setting, load_pat_value

    org = get_setting(KEY_ORG).strip()
    pat = load_pat_value().strip()
    if not org or not pat:
        _log("ERROR", "Azure DevOps is not configured (missing PAT/org). "
                      "Connect ADO in the app first.")
        return 1

    cfg = RuntimeConfig.from_env_defaults()
    cfg.pat = pat
    cfg.organization = org

    async with boards._client(cfg) as client:
        relations_by_wi = await boards._fetch_relations(
            client, org, project, ids, cfg
        )
        # Type-check every link target so we can label parent/child/related
        # test cases too.
        link_targets: set[int] = set()
        for rels in relations_by_wi.values():
            for rel in rels:
                tid = boards._rel_target_id(rel)
                if tid and boards._is_link_relation(rel):
                    link_targets.add(tid)
        type_map = (
            await boards._fetch_work_item_types(
                client, org, project, sorted(link_targets), cfg
            ) if link_targets else {}
        )
        counts = await boards._fetch_test_case_counts(
            client, org, project, ids, cfg
        )

    report: dict[str, object] = {"tracker": "ado", "project": project,
                                 "items": {}}
    for wid in ids:
        rels = relations_by_wi.get(wid, [])
        entries = []
        for rel in rels:
            tid = boards._rel_target_id(rel)
            ttype = type_map.get(tid, "")
            is_test_rel = boards._is_test_relation(rel)
            is_test_type = bool(
                ttype and any(tok in boards._norm(ttype) for tok in boards._TEST_TYPE_TOKENS)
            )
            reason = ("test-relation" if is_test_rel
                      else "test-type-target" if is_test_type
                      else "-")
            entries.append({
                "rel": str(rel.get("rel", "")),
                "name": str((rel.get("attributes") or {}).get("name", "")),
                "target_id": tid,
                "target_type": ttype,
                "counted_as_test": is_test_rel or is_test_type,
                "reason": reason,
            })
        report["items"][str(wid)] = {
            "relation_count": len(rels),
            "test_case_count": counts.get(wid, 0),
            "relations": entries,
        }

    _print_report(report, as_json)
    return 0


# ------------------------------------------------------------------ JIRA
async def _discover_jira(keys: list[str], as_json: bool) -> int:
    from core.http_retry import request_with_retry
    from core.runtime_config import RuntimeConfig
    from core.settings_store import (KEY_JIRA_URL, KEY_JIRA_USER, get_setting,
                                     load_jira_pat)
    from jira import boards

    url = get_setting(KEY_JIRA_URL).strip()
    user = get_setting(KEY_JIRA_USER).strip()
    pat = load_jira_pat().strip()
    if not url or not user or not pat:
        _log("ERROR", "JIRA is not configured (missing URL/user/token). "
                      "Connect JIRA in the app first.")
        return 1

    cfg = RuntimeConfig.from_env_defaults()
    fields = "summary,issuetype,status,issuelinks,subtasks"
    report: dict[str, object] = {"tracker": "jira", "items": {}}

    async with boards._client(url, user, pat, cfg) as client:
        for key in keys:
            r = await request_with_retry(
                client, "GET", f"/rest/api/2/issue/{key}",
                params={"fields": fields},
            )
            if r.status_code != 200:
                report["items"][key] = {"error": f"HTTP {r.status_code}"}
                continue
            raw = r.json()
            issue = boards._parse_issue(raw)
            f = raw.get("fields", {}) or {}
            links = []
            for link in f.get("issuelinks") or []:
                lt = str((link.get("type") or {}).get("name", ""))
                linked = link.get("inwardIssue") or link.get("outwardIssue") or {}
                itype = str(
                    ((linked.get("fields") or {}).get("issuetype") or {})
                    .get("name", "")
                )
                is_test = "test" in lt.lower() or "test" in itype.lower()
                links.append({"link_type": lt,
                              "linked_key": str(linked.get("key", "")),
                              "linked_type": itype,
                              "counted_as_test": is_test})
            subs = []
            for sub in f.get("subtasks") or []:
                itype = str(
                    ((sub.get("fields") or {}).get("issuetype") or {})
                    .get("name", "")
                )
                subs.append({"key": str(sub.get("key", "")),
                             "type": itype,
                             "counted_as_test": "test" in itype.lower()})
            report["items"][key] = {
                "issue_type": issue.issue_type,
                "test_case_count": issue.test_case_count,
                "links": links,
                "subtasks": subs,
            }

    _print_report(report, as_json)
    return 0


# ------------------------------------------------------------------ output
def _print_report(report: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return
    items = report.get("items", {}) or {}
    _log("INFO", f"tracker={report.get('tracker')} items={len(items)}")
    for key, data in items.items():
        if not isinstance(data, dict):
            continue
        if "error" in data:
            _log("WARN", f"{key}: {data['error']}")
            continue
        n = data.get("test_case_count", 0)
        _log("SUCCESS" if n else "INFO", f"{key}: {n} test case(s) detected")
        for rel in data.get("relations", []) or []:
            mark = "*" if rel.get("counted_as_test") else " "
            print(f"    [{mark}] {rel.get('rel')} "
                  f"name='{rel.get('name')}' -> #{rel.get('target_id')} "
                  f"({rel.get('target_type') or '?'}) [{rel.get('reason')}]")
        for lk in data.get("links", []) or []:
            mark = "*" if lk.get("counted_as_test") else " "
            print(f"    [{mark}] link '{lk.get('link_type')}' -> "
                  f"{lk.get('linked_key')} ({lk.get('linked_type')})")
        for sub in data.get("subtasks", []) or []:
            mark = "*" if sub.get("counted_as_test") else " "
            print(f"    [{mark}] subtask {sub.get('key')} ({sub.get('type')})")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Diagnose linked test-case discovery for a tracker.")
    p.add_argument("--tracker", required=True, choices=("ado", "jira"))
    p.add_argument("--project", default="",
                   help="ADO project name (required for --tracker ado)")
    p.add_argument("--ids", default="",
                   help="ADO work item ids, comma-separated")
    p.add_argument("--keys", default="",
                   help="JIRA issue keys, comma-separated")
    p.add_argument("--json", action="store_true", help="emit JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.tracker == "ado":
        if not args.project or not args.ids:
            _log("ERROR", "--project and --ids are required for ADO")
            return 1
        try:
            ids = [int(x) for x in args.ids.split(",") if x.strip()]
        except ValueError:
            _log("ERROR", "--ids must be comma-separated integers")
            return 1
        return asyncio.run(_discover_ado(args.project, ids, args.json))
    # jira
    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    if not keys:
        _log("ERROR", "--keys is required for JIRA")
        return 1
    return asyncio.run(_discover_jira(keys, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
