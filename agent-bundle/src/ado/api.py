"""
ado_api.py
Light async helpers. Verification and project listing operate at the
org level (dev.azure.com/<org>/_apis/projects), avoiding the SPS
profile/accounts endpoints which are commonly blocked in enterprise
tenants.

Organization auto-discovery is therefore not provided; the user enters
the org name manually in the GUI.
"""

from __future__ import annotations

import base64
import ssl as _ssl
from typing import Any

import httpx

from core.runtime_config import API_VER_CORE, RuntimeConfig


def build_auth_header(pat: str) -> dict[str, str]:
    token = base64.b64encode(f":{pat}".encode("ascii")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
    }


def _ssl_excs() -> tuple[type[BaseException], ...]:
    return (_ssl.SSLError, _ssl.SSLCertVerificationError)


async def verify_pat_for_org(
    pat: str,
    organization: str,
    cfg: RuntimeConfig,
) -> tuple[bool, str]:
    """Verify PAT by hitting the org-level projects endpoint.

    Returns (ok, detail). Works with just 'Project and Team: Read' scope.
    """
    if not organization.strip():
        return False, "Organization is empty"
    url = (
        f"https://dev.azure.com/{organization}/_apis/projects"
        f"?api-version={API_VER_CORE}&$top=1"
    )
    try:
        headers = build_auth_header(pat)
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(cfg.http_timeout_sec),
            verify=cfg.build_ssl(),
        ) as client:
            r = await client.get(url)
            if r.status_code == 200:
                return True, ""
            body_snip: str = r.text[:300].replace("\n", " ")
            server: str = r.headers.get("server", "")
            return False, (
                f"HTTP {r.status_code} from {url}. "
                f"server={server!r} body={body_snip!r}"
            )
    except httpx.ConnectError as e:
        return False, f"ConnectError (DNS/firewall): {e!r}"
    except _ssl_excs() as e:
        return False, f"TLS error (proxy interception?): {e!r}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e!r}"


async def list_projects(
    pat: str,
    organization: str,
    cfg: RuntimeConfig,
) -> list[str]:
    """Return project names for the given organization. Paginated."""
    headers = build_auth_header(pat)
    timeout = httpx.Timeout(cfg.http_timeout_sec)
    base_url = (
        f"https://dev.azure.com/{organization}/_apis/projects"
        f"?api-version={API_VER_CORE}&$top=500"
    )
    out: list[str] = []
    async with httpx.AsyncClient(
        headers=headers, timeout=timeout, verify=cfg.build_ssl(),
    ) as client:
        next_url: str | None = base_url
        while next_url:
            r = await client.get(next_url)
            if r.status_code != 200:
                detail = (r.text or "")[:300].replace("\n", " ")
                if r.status_code == 401:
                    raise RuntimeError(
                        "HTTP 401 Unauthorized from ADO. "
                        "The PAT was sent but rejected. Common causes: "
                        "(1) the PAT is expired or has been revoked; "
                        "(2) the PAT was created for a different "
                        f"organization than '{organization}'; "
                        "(3) the PAT is missing the required scope "
                        "'Project and Team (Read)' or, for the "
                        "Packager/TC Creator, 'Work Items (Read & "
                        "Write)'. Generate a new PAT under User "
                        "Settings > Personal Access Tokens with the "
                        "correct scope for this organization and try "
                        "again."
                    )
                if r.status_code == 403:
                    raise RuntimeError(
                        f"HTTP 403 Forbidden. The PAT is valid but "
                        f"lacks the necessary scopes for "
                        f"'{organization}'. Required: "
                        f"'Project and Team (Read)'. "
                        f"Server response: {detail!r}"
                    )
                if r.status_code == 404:
                    raise RuntimeError(
                        f"HTTP 404 from ADO. The organization "
                        f"'{organization}' does not exist or is "
                        f"unreachable from this network. Check the "
                        f"spelling (should be just the slug, not the "
                        f"full URL)."
                    )
                raise RuntimeError(
                    f"HTTP {r.status_code} from {next_url}. "
                    f"body={detail!r}"
                )
            payload: dict[str, Any] = r.json()
            for prj in payload.get("value", []) or []:
                name = str(prj.get("name", "")).strip()
                if name:
                    out.append(name)
            cont = r.headers.get("x-ms-continuationtoken", "").strip()
            if cont:
                next_url = f"{base_url}&continuationToken={cont}"
            else:
                next_url = None
    out.sort(key=str.lower)
    return out
