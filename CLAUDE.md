@AGENTS.md

The credential-release invariants in `AGENTS.md` are mandatory. In particular,
never print Vercel project variables or reintroduce plaintext/legacy Fernet
credential loading. Use fake sentinel credentials for tests and preserve
fail-closed authenticated decryption, OS-bound rewrap, safe rotation, restrictive
permissions, and central log redaction.
