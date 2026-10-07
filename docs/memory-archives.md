# Portable memory archives

The current memory importer accepts OKF directories and bounded `.tar.gz` transport
of the same format. It also recognizes the frozen CAO native format-v1 manifest,
checks its canonical content hash and closed row schema, and converts content
through the current import policy. Historical SQLite IDs, owners, counters and
paths do not become local authority. Select an explicit current destination scope;
private agent/session archive rows are refused. Use `cao memory import PATH
--scope global --dry-run` to validate before writing. Links, traversal, duplicate
members, devices, oversized members and excessive expansion are rejected before
any memory write. Normal imports retain conflict, secret and vault-bound gates.
