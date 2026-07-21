# Deployment Checklist

Steps to follow when pushing changes that affect the agent bundle.

## Pre-deploy

1. Bump `AGENT_VERSION` in `agent-bundle/src/agent/version.py`
   - Patch (X.Y.Z) for bug fixes, config changes, minor tweaks
   - Minor (X.Y.0) for new features, prompt rewrites, behavior changes
   - Major (X.0.0) for architectural overhauls (e.g. 3.0.1 -> 3.40.0)
2. Bump `REQUIRED_AGENT_VERSION` in `lib/agent-version.ts` to match
   (forces the web app to show the update screen for users on older agents)
   **CRITICAL: NEVER bump until the installer succeeds end-to-end.** Premature
   bumps block the entire app for all users.
3. Bump `"version"` in `package.json` (web app version shown in footer)
4. Commit all version bumps together

## Deploy

5. `git push origin main` (Vercel git integration auto-deploys)
6. Do NOT use `npx vercel --prod` locally (causes EBUSY errors on Windows)

## Update the agent manifest (`parts` branch)

7. Update `agent-update.json` on the `parts` branch:
   - Set `"version"` to the new AGENT_VERSION
   - Set `"ref"` to the HEAD commit SHA on main
   - Set `"generatedAt"` to current UTC timestamp
   - **Update all file `"url"` fields** to use the new ref (replace old commit SHA)
   - **Recompute `"hash"` (SHA-256)** for every source file that changed between
     the old ref and new ref. **CRITICAL: hash from git blob content (LF), NOT
     local files (CRLF on Windows).** Use:
     `git show <ref>:agent-bundle/src/<path> | python -c "import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"`
     The installer downloads from GitHub Content API which serves LF line endings.
   - Push to `origin parts`

   Quick verification: the installer reads `src/agent/version.py` from the URLs
   in the manifest and asserts `AGENT_VERSION == manifest.version`. If the URLs
   still point to the old ref, the old version.py is downloaded and the check
   fails with: `overlay version mismatch: manifest=X.Y.Z source=A.B.C`

## Post-deploy

8. Reinstall the agent locally to pick up the new version
9. Verify version in the UI log: `Agent vX.Y.Z is up to date.`
10. If context generation changed: force-regenerate project context

## How auto-update works

- On app load, the web app calls `/update/status` on the local agent
- Agent fetches `agent-update.json` from the `parts` branch (GitHub API)
- If manifest version > running version:
  - **Patch only** (same major.minor): auto-applied via source overlay, page reloads
  - **Minor+ bump**: UI shows "update required" screen, user must reinstall
- If running version < `REQUIRED_AGENT_VERSION`: web app blocks unconditionally
- Cache TTL for update checks: 300 seconds

## Common failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `overlay version mismatch` | Manifest file URLs point to old commit ref | Update all `"url"` fields to new ref SHA |
| `hash mismatch` | File changed but manifest hash not updated, OR hash computed from local CRLF instead of git LF content | Recompute SHA-256 from git blob: `git show <ref>:agent-bundle/src/<path> \| python -c "import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"` |
| `Agent vX.Y.Z is up to date` (old version) | Manifest on `parts` not updated | Push updated manifest to `origin parts` |
| No update prompt shown | `REQUIRED_AGENT_VERSION` not bumped | Bump in `lib/agent-version.ts` and redeploy |
