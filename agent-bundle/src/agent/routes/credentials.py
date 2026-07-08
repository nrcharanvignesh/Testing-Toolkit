"""
agent/routes/credentials.py
Per-project encrypted test-credential CRUD for the web UI.

SECURITY: passwords are NEVER returned to the client. The list endpoint
returns a `has_password` flag only. On upsert, an empty password means
"keep the currently stored password for this environment" so the browser
never needs to hold or re-send the secret.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from automation.credential_vault import CredentialVault, TestCredential

router = APIRouter()
_VAULT = CredentialVault()


class CredIn(BaseModel):
    env: str
    login_url: str = ""
    user_id: str = ""
    password: str = ""
    login_method: str = "form"
    notes: str = ""
    ai_instructions: str = ""
    # When true and password is empty, preserve any existing stored password.
    keep_password: bool = False


def _mask(cred: TestCredential) -> dict[str, Any]:
    """Public view of a credential: everything EXCEPT the password."""
    return {
        "env": cred.env,
        "login_url": cred.login_url,
        "user_id": cred.user_id,
        "login_method": cred.login_method,
        "notes": cred.notes,
        "ai_instructions": cred.ai_instructions,
        "has_password": bool(cred.password),
    }


@router.get("/{project}")
def list_credentials(project: str) -> dict[str, Any]:
    """List masked credentials for a project (no passwords)."""
    creds = _VAULT.load(project)
    return {"credentials": [_mask(c) for c in creds]}


@router.post("/{project}")
def upsert_credential(project: str, body: CredIn) -> dict[str, Any]:
    """Add or update a credential (keyed by env, case-insensitive).

    An empty password with keep_password=True preserves the stored secret;
    an empty password otherwise clears it.
    """
    env = body.env.strip()
    if not env:
        raise HTTPException(status_code=400, detail="Environment is required.")
    if not body.login_url.strip():
        raise HTTPException(status_code=400, detail="Login URL is required.")
    if not body.user_id.strip():
        raise HTTPException(status_code=400, detail="Username is required.")

    creds = _VAULT.load(project)
    env_lower = env.lower()

    password = body.password
    if not password and body.keep_password:
        for c in creds:
            if c.env.lower().strip() == env_lower:
                password = c.password
                break

    new_cred = TestCredential(
        env=env,
        login_url=body.login_url.strip(),
        user_id=body.user_id.strip(),
        password=password,
        login_method=(body.login_method or "form").strip(),
        notes=body.notes.strip(),
        ai_instructions=body.ai_instructions.strip(),
    )

    replaced = False
    for i, c in enumerate(creds):
        if c.env.lower().strip() == env_lower:
            creds[i] = new_cred
            replaced = True
            break
    if not replaced:
        creds.append(new_cred)

    if not _VAULT.save(project, creds):
        raise HTTPException(status_code=500, detail="Failed to save credential.")
    return {"ok": True, "credentials": [_mask(c) for c in creds]}


@router.delete("/{project}/{env}")
def delete_credential(project: str, env: str) -> dict[str, Any]:
    """Remove a single credential by environment name."""
    creds = _VAULT.load(project)
    env_lower = env.strip().lower()
    remaining = [c for c in creds if c.env.lower().strip() != env_lower]
    if len(remaining) == len(creds):
        raise HTTPException(status_code=404, detail=f"No credential for '{env}'.")
    if not _VAULT.save(project, remaining):
        raise HTTPException(status_code=500, detail="Failed to delete credential.")
    return {"ok": True, "credentials": [_mask(c) for c in remaining]}


@router.post("/{project}/clear")
def clear_credentials(project: str) -> dict[str, Any]:
    """Remove all credentials for a project."""
    _VAULT.clear(project)
    return {"ok": True, "credentials": []}
