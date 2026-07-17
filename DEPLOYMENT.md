# Deployment Checklist

Steps to follow when pushing changes that affect the agent bundle.

## Pre-deploy

1. Bump `AGENT_VERSION` in `agent-bundle/src/agent/version.py`
   - Patch (2.x.Y) for bug fixes, config changes, minor tweaks
   - Minor (2.X.0) for new features, prompt rewrites, behavior changes
2. Bump `REQUIRED_AGENT_VERSION` in `lib/agent-version.ts` to match
   (forces the web app to show the update screen for users on older agents)
3. Bump `"version"` in `package.json` (web app version shown in footer)
4. Commit all version bumps together

## Deploy

5. `git push origin main`
6. `npx vercel --prod --yes` (deploys the web app)
7. Update `agent-update.json` on the `parts` branch:
   - Set `"version"` to the new AGENT_VERSION
   - Set `"ref"` to the HEAD commit SHA on main
   - Set `"generatedAt"` to current UTC timestamp
   - Push to `origin parts`

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
