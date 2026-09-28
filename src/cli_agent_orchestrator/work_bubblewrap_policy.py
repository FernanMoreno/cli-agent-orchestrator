"""Shared, fail-closed Bubblewrap version policy for Work paths."""

# The composition harness and admission backend must describe the same CLI.
# This does not make the binary trusted; the backend also requires a digest in
# its reviewed allowlist, which intentionally remains empty until host evidence
# identifies an approved distribution artifact.
BUBBLEWRAP_VERSION = (0, 13, 0)
BUBBLEWRAP_VERSION_TEXT = "bubblewrap 0.13.0"
