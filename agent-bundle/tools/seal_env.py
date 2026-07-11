#!/usr/bin/env python3
"""Seal Vercel-managed GenAI configuration into the agent release envelope.

Reads only named process variables and never prints values. The output is
versioned authenticated ciphertext. Autonomous client decryption is a POC
hardening boundary, not protection from a determined local administrator.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.credential_envelope import (  # noqa: E402
    CredentialEnvelopeError,
    open_credentials,
    seal_credentials,
    write_envelope_atomic,
)

OUTPUT = SRC / ".env.enc"


def _pick(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _values_from_vercel_env() -> dict[str, str]:
    return {
        "BASE_URL": _pick("LLM_UPSTREAM_BASE_URL", "BASE_URL"),
        "API_KEY": _pick("LLM_UPSTREAM_API_KEY", "API_KEY"),
        "LLM_PROVIDER_FORMAT": _pick("LLM_PROVIDER_FORMAT") or "anthropic",
    }


def _metadata(path: Path) -> str:
    values = open_credentials(path.read_bytes())
    # Deliberately disclose no host, path, key prefix, lengths, or hashes.
    return f"valid envelope v2; provider={values['LLM_PROVIDER_FORMAT']}; fields=3"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seal Testing Toolkit release credentials")
    parser.add_argument("--from-vercel-env", action="store_true", help="read named process variables")
    parser.add_argument("--verify", action="store_true", help="authenticate output; print metadata only")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    try:
        if args.verify:
            print(_metadata(args.output))
            return 0
        if not args.from_vercel_env:
            parser.error("--from-vercel-env is required; plaintext files are not accepted")
        envelope = seal_credentials(_values_from_vercel_env())
        write_envelope_atomic(args.output, envelope)
        _metadata(args.output)  # verify exact persisted bytes
        print(f"sealed authenticated credential envelope: {args.output} (values not displayed)")
        return 0
    except (CredentialEnvelopeError, OSError) as exc:
        print(f"sealing failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
