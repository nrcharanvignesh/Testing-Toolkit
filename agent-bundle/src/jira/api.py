"""
jira/api.py
Async helpers for JIRA Server/Data Center REST API.
Authentication: Basic auth with username:PAT (base64 encoded).
Base URL pattern: https://<jira-server>/rest/api/2/...
"""

from __future__ import annotations

import base64
from typing import Any, Callable

import httpx

from core.http_retry import request_with_retry, ssl_exception_types
from core.runtime_config import RuntimeConfig


def build_auth_header(user: str, pat: str) -> dict[str, str]:
    """Base64-encode user:pat for HTTP Basic auth."""
    token = base64.b64encode(f"{user}:{pat}".encode("ascii")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
    }


async def verify_connection(
    url: str, user: str, pat: str, cfg: RuntimeConfig,
) -> tuple[bool, str]:
    """Hit /rest/api/2/myself to verify credentials. Returns (ok, detail)."""
    endpoint = f"{url.rstrip('/')}/rest/api/2/myself"
    try:
        headers = build_auth_header(user, pat)
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(cfg.http_timeout_sec),
            verify=cfg.build_ssl(),
        ) as client:
            r = await request_with_retry(client, "GET", endpoint)
            if r.status_code == 200:
                data = r.json()
                display = data.get("displayName", data.get("name", ""))
                return True, display
            body_snip: str = r.text[:300].replace("\n", " ")
            return False, (
                f"HTTP {r.status_code} from {endpoint}. body={body_snip!r}"
            )
    except httpx.ConnectError as e:
        return False, f"ConnectError (DNS/firewall): {e!r}"
    except ssl_exception_types() as e:
        return False, f"TLS error (proxy interception?): {e!r}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e!r}"


async def list_projects(
    url: str, user: str, pat: str, cfg: RuntimeConfig,
) -> list[str]:
    """Fetch all project keys from JIRA. GET /rest/api/2/project"""
    endpoint = f"{url.rstrip('/')}/rest/api/2/project"
    headers = build_auth_header(user, pat)
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(cfg.http_timeout_sec),
            verify=cfg.build_ssl(),
        ) as client:
            r = await request_with_retry(client, "GET", endpoint)
            if r.status_code == 401:
                raise RuntimeError(
                    "HTTP 401 Unauthorized from JIRA. "
                    "The PAT was sent but rejected. Check username and "
                    "token validity."
                )
            if r.status_code == 403:
                raise RuntimeError(
                    "HTTP 403 Forbidden. The PAT lacks project browse "
                    "permissions."
                )
            if r.status_code != 200:
                detail = (r.text or "")[:300].replace("\n", " ")
                raise RuntimeError(
                    f"HTTP {r.status_code} from {endpoint}. body={detail!r}"
                )
            projects: list[dict[str, Any]] = r.json()
            return [
                str(p.get("key", "")).strip()
                for p in projects
                if p.get("key")
            ]
    except (RuntimeError,):
        raise
    except httpx.ConnectError as e:
        raise RuntimeError(f"ConnectError: {e!r}") from e
    except ssl_exception_types() as e:
        raise RuntimeError(f"TLS error: {e!r}") from e
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {e!r}") from e


