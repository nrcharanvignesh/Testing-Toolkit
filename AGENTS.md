<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## Credential-release security

- Vercel project variables are the owner-controlled source for the agent's
  centrally managed GenAI URL/key. Seal them only with
  `agent-bundle/tools/seal_env.py --from-vercel-env`; never copy values into
  source, command arguments, fixtures, logs, screenshots, or documentation.
- Packaged agents load only the authenticated `agent-bundle/src/.env.enc`
  envelope. Do not restore plaintext `.env` fallback or the legacy portable
  Fernet format.
- Any credential-envelope change must preserve AES-256-GCM authentication,
  strict schema validation, fail-closed behavior, owner-only permissions,
  OS-bound rewrap, safe rotation, and central log redaction.
- Tests must use unmistakably fake sentinel values and scan artifacts/logs for
  leakage. Never read or print `/vercel/share/.env.project`.
- An autonomous local client cannot guarantee non-extractability against its
  administrator; do not claim otherwise. True non-extractability requires a
  remote broker or hardware-backed attestation.
