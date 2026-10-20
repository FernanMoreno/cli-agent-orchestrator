# Portable memory archives

The current memory importer accepts OKF directories and bounded `.tar.gz` transport
of the same format. These archives contain topic content and optional relationships;
imports pass through the current store policy. Select an explicit current destination
scope; private agent/session archive rows are refused. Use `cao memory import PATH
--scope global --dry-run` to validate before writing. Legacy CAO archive formats are
not accepted. Links, traversal, duplicate
members, devices, oversized members and excessive expansion are rejected before
any memory write. Normal imports retain conflict, secret and vault-bound gates.
