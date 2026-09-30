# Data model

The deployment adds no database schema or competing Work state machine.
`deployment.json` stores loopback ports, immutable Docker image ID and issuer
coordinates. `issuer.pem` is the stable private signing key; `client.env` is an
expiring private derivative. Existing SQLite schemas own jobs, grants,
provisions, scheduler policy, attempts and provider-child receipts.

OpenCode v2 uses per-terminal private JSON and an environment file derived from
the selected installed agent, existing MCP declarations and Go login. Secrets
are not serialized into terminal launch commands or public acceptance evidence.
Backup uses SQLite's API and private atomic directory publication. Restore
preserves identity/state, renews client credentials and refuses overwrites.
