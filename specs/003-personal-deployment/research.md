# Research decisions

Use host CAO with Docker Work to preserve subscription CLIs and the accepted
no-network process contract. All-in-container subscription CLIs would require
new credential/network/workspace contracts; no hosted service is needed for
localhost-only access. OpenCode v2.0.18 supports mini --standalone --agent/--model;
v1 uses the root TUI. Official CLI: https://opencode.ai/v2/docs/cli/commands/.

A persistent RSA issuer on loopback and short-lived client bearers reuse the
existing RS256 auth boundary. SQLite backup API captures WAL consistently.
