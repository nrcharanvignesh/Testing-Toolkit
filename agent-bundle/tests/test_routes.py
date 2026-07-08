# Route-level integration + contract tests against the real ASGI app via
# TestClient. Verifies happy paths where no external service is required and
# guards that NO route returns a 500 due to a code defect (network-dependent
# routes are allowed to return graceful 4xx/502/503).
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

import pytest
from fastapi.testclient import TestClient

import agent.server as server


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


# --------------------------------------------------------------------------
# Happy-path integration
# --------------------------------------------------------------------------
def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)


def test_version_reports_current(client):
    from agent.version import AGENT_VERSION

    r = client.get("/version")
    assert r.status_code == 200
    assert AGENT_VERSION in str(r.json())


def test_metrics_reports_process_stats_without_psutil(client):
    """Regression guard: /metrics must report this process's CPU% and resident
    memory using the dependency-free core.process_metrics module, NOT psutil
    (which is not bundled). Before this was wired up the status bar showed
    'CPU -- RAM --' forever on every real install. cpu_percent may legitimately
    be 0.0 on the first poll, but proc_mem_mb must be a positive int."""
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert "cpu_percent" in body and "proc_mem_mb" in body
    # The running agent process always has resident memory > 0.
    assert isinstance(body["proc_mem_mb"], int)
    assert body["proc_mem_mb"] > 0
    # cpu_percent is either None (unsupported platform) or a real 0-100 number,
    # but on Linux/Windows/macOS the native reader always returns a number.
    if body["cpu_percent"] is not None:
        assert 0.0 <= float(body["cpu_percent"]) <= 100.0


def test_capabilities(client):
    r = client.get("/capabilities")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_settings_roundtrip(client, tmp_install):
    # Save a couple of settings, then read them back.
    r = client.post("/settings", json={"values": {"org": "acme-corp"}})
    assert r.status_code in (200, 201), r.text
    r2 = client.get("/settings")
    assert r2.status_code == 200
    assert isinstance(r2.json(), dict)


def test_tour_roundtrip(client):
    r = client.post("/settings/tour", json={"completed": True})
    assert r.status_code == 200


def test_system_prompt_default(client):
    r = client.get("/settings/system-prompt", params={"project": "P"})
    assert r.status_code == 200
    body = r.json()
    # returns a non-empty canonical prompt
    assert isinstance(body, dict)


# --------------------------------------------------------------------------
# Regression guard: /kb/retrieve must NOT 500 (was AttributeError is_ready)
# --------------------------------------------------------------------------
def test_kb_retrieve_no_500(client):
    r = client.post("/kb/retrieve", json={"project": "Nonexistent",
                                           "query": "hello", "top_k": 3})
    # Unconfigured/empty KB -> graceful (409 or 200 empty), NEVER 500.
    assert r.status_code != 500, r.text
    assert r.status_code in (200, 400, 404, 409, 422)


def test_kb_status_no_500(client):
    r = client.get("/kb/status/SomeProject")
    assert r.status_code != 500, r.text


# --------------------------------------------------------------------------
# Full contract sweep: no route may return 500.
# --------------------------------------------------------------------------
_BODIES = {
    "/settings": {"values": {"org": "x"}},
    "/settings/tour": {"completed": True},
    "/kb/retrieve": {"project": "P", "query": "q", "top_k": 3},
    "/kb/embed": {"texts": ["a", "b"]},
    "/kb/rerank": {"query": "q", "documents": ["a"], "top_k": 1},
    "/chat/stream": {"messages": [{"role": "user", "content": "hi"}],
                     "project": "P", "use_kb": False, "use_tools": False},
}


def _fill(path: str) -> str:
    out = []
    for seg in path.split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            out.append("1" if "id" in seg.lower() else "P")
        else:
            out.append(seg)
    return "/".join(out)


def test_no_route_returns_500(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    # Network/streaming routes: allowed to be slow/graceful, excluded from the
    # strict-no-500 sweep because they depend on live ADO/JIRA/LLM services.
    network_prefixes = (
        "/ado", "/jira", "/sources", "/generate", "/chat", "/e2e",
        "/defects", "/kb/index", "/kb/embed", "/kb/rerank", "/update",
        "/tools", "/llm",
    )
    pool = ThreadPoolExecutor(max_workers=1)
    offenders = []

    def _do(method, url, body):
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
        if any(path.startswith(p) for p in network_prefixes):
            continue
        for method in paths[path]:
            if method.upper() not in ("GET", "POST", "PUT", "DELETE"):
                continue
            url = _fill(path)
            body = _BODIES.get(path)
            try:
                fut = pool.submit(_do, method, url, body)
                r = fut.result(timeout=15)
                if r is not None and r.status_code >= 500:
                    offenders.append((method.upper(), path, r.status_code,
                                      r.text[:120]))
            except FTimeout:
                offenders.append((method.upper(), path, "HANG", ">15s"))
            except Exception as e:  # noqa: BLE001
                offenders.append((method.upper(), path, "EXC",
                                  f"{type(e).__name__}: {e}"))

    assert not offenders, "routes returned 5xx/hang:\n" + "\n".join(
        f"  {m} {p} -> {c} {d}" for m, p, c, d in offenders)
