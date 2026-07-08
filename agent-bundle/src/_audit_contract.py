"""In-process contract audit. Hits every route via Starlette TestClient and
flags 500s (server crashes = bugs) vs graceful 4xx. No real ADO/JIRA/LLM needed;
unconfigured endpoints should return 400/404/422, never 500."""
from __future__ import annotations

import json
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

sys.path.insert(0, ".")
os.environ.setdefault("TT_SKIP_MODEL_PRELOAD", "1")
# Point the LLM at a dead local port so any network call fails FAST (connection
# refused) instead of hanging on a real endpoint. Empty key -> most endpoints
# 400 before any network anyway.
os.environ["BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["API_KEY"] = ""
socket.setdefaulttimeout(6)

from fastapi.testclient import TestClient  # noqa: E402

import agent.server as s  # noqa: E402

# Minimal bodies for POSTs. project "P" is unconfigured -> expect graceful 400.
BODIES: dict[str, dict] = {
    "/ado/verify": {"project": "P"},
    "/ado/workitems": {"project": "P", "board_id": "1", "board_name": "B",
                        "team_id": "", "team_name": ""},
    "/ado/workitems/stream": {"project": "P", "board_id": "1", "board_name": "B",
                              "team_id": "", "team_name": ""},
    "/ado/tag": {"project": "P", "wi_id": 1, "tag": "x"},
    "/jira/verify": {"project": "P"},
    "/jira/workitems": {"project": "P", "board_id": "1", "board_name": "B"},
    "/sources/verify": {"project": "P"},
    "/sources/workitems": {"project": "P", "board_id": "1", "board_name": "B",
                           "team_id": "", "team_name": ""},
    "/sources/workitems/stream": {"project": "P", "board_id": "1",
                                  "board_name": "B", "team_id": "",
                                  "team_name": ""},
    "/sources/tag": {"project": "P", "wi_id": "1", "tag": "x"},
    "/settings": {"organization": "o", "pat": "p", "project": "P"},
    "/settings/system-prompt": {"project": "P", "phase": "SIT", "prompt": "x"},
    "/settings/system-prompt/reset": {"project": "P", "phase": "SIT"},
    "/settings/tour": {"seen": True},
    "/llm/complete": {"prompt": "hi"},
    "/kb/embed": {"texts": ["a", "b"]},
    "/kb/rerank": {"query": "q", "documents": ["a", "b"]},
    "/kb/retrieve": {"project": "P", "query": "q"},
    "/kb/index": {"project": "P"},
    "/chat/stream": {"project": "P", "messages": [{"role": "user",
                                                   "content": "hi"}]},
    "/defects/parse": {"project": "P", "text": "x"},
    "/defects/excel": {"project": "P", "defects": []},
    "/defects/upload": {"project": "P", "defects": []},
    "/generate/start": {"project": "P", "wi_ids": [1], "phase": "SIT"},
    "/generate/dump": {"project": "P", "wi_ids": [1]},
    "/generate/extract": {"project": "P", "wi_ids": [1]},
    "/generate/push": {"project": "P", "job_id": "x"},
    "/generate/push-xlsx": {"project": "P"},
    "/generate/load-xlsx": {"project": "P"},
    "/e2e/start": {"project": "P", "test_cases": []},
    "/artifacts/delete": {"path": "/tmp/nonexistent-xyz"},
}

# Path params: fill with dummy values.
PARAM = {"project": "P", "wi_id": "1", "issue_key": "K-1", "phase": "SIT",
         "job_id": "nojob", "env": "dev"}


def fill(path: str) -> str:
    out = path
    for k, v in PARAM.items():
        out = out.replace("{" + k + "}", v)
    return out


def main() -> int:
    with TestClient(s.app) as client:
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        crashes = []
        results = []
        pool = ThreadPoolExecutor(max_workers=1)

        def _do(method: str, url: str, body):
            if method == "get":
                return client.get(url)
            if method == "post":
                return client.post(url, json=body or {})
            if method == "put":
                return client.put(url, json=body or {})
            if method == "delete":
                return client.request("DELETE", url, json=body or {})
            return None

        for path in sorted(paths):
            for method in paths[path]:
                if method.upper() not in ("GET", "POST", "PUT", "DELETE"):
                    continue
                url = fill(path)
                body = BODIES.get(path)
                try:
                    fut = pool.submit(_do, method, url, body)
                    r = fut.result(timeout=12)
                    if r is None:
                        continue
                    code = r.status_code
                    tag = ""
                    if code >= 500:
                        tag = " <<< CRASH"
                        detail = r.text[:200].replace("\n", " ")
                        crashes.append((method.upper(), path, code, detail))
                    results.append((method.upper(), path, code, tag))
                except FTimeout:
                    results.append((method.upper(), path, "HANG",
                                    " <<< HANG (>12s)"))
                    crashes.append((method.upper(), path, "HANG",
                                    "no response in 12s"))
                except Exception as e:  # noqa: BLE001
                    crashes.append((method.upper(), path, "EXC",
                                    f"{type(e).__name__}: {e}"))
                    results.append((method.upper(), path, "EXC", " <<< EXC"))
        for m, p, c, t in results:
            print(f"{str(c):4} {m:6} {p}{t}")
        print(f"\nTotal: {len(results)} | CRASHES/EXC: {len(crashes)}")
        if crashes:
            print("\n=== CRASHES (500/EXC) ===")
            for m, p, c, d in crashes:
                print(f"  {m} {p} -> {c}: {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
