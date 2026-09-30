# Personal runtime contracts

- API: 127.0.0.1:9889; local JWKS: 127.0.0.1:9890.
- Authentication: RS256, persistent issuer/audience/key, verified identity and
  scopes. Protected routes reject missing bearers.
- Restart: systemd user unit; failed parent/child exits restart automatically;
  normal stop exits cleanly. Every 12 hours the internal 24-hour bearer rotates.
- Work: explicit docker-local registration and immutable rootfs identity;
  verified admin provisioning, existing grants/snapshots/executable contracts.
  Static launch authority remains finite; no model result is inferred from it.
- Provider input: one payload delivery and one submitting Enter; paste settlement
  precedes submission. Uncertain receipts are reconciled rather than resent.
- Provider output: version-specific completion and answer boundaries; active
  receipts remain standalone final lines. Error overlays outrank idle chrome.
- TUI: optional configured bearer on ordinary/streaming requests; no redirects
  while authenticated, malformed tokens rejected, debug output redacted.
- Backup: service stopped; SQLite WAL included; immutable destination, private
  permissions and integrity checks. Provider login files stay in operator HOME.
