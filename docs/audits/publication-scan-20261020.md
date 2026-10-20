# Publication scan — 2026-10-20

The three accumulated-work commits were scanned with Gitleaks 8.30.1 and the unchanged project `.gitleaks.toml`, using the complete Git range `02309f3f..HEAD`. The initial scan reported 248 `generic-api-key` matches. Each source location was checked before adding an exact historical fingerprint to `.gitleaksignore`:

- 128 manifest entries map source filenames to 64-character hexadecimal SHA256 digests.
- 105 public `operation_key` fields contain eight distinct descriptive idempotency labels from the historical demos. These identify requests and do not authorize them.
- 10 recovery tests deliberately use the same sequential synthetic API-key marker to exercise redaction.
- 5 tests use public idempotency labels for operation deduplication.

The independent Claude review checked the classification and sampled the historical records. The lead then verified all 248 locations programmatically, including all eight distinct operation labels. No path, rule or whole-commit exclusion was added, and the scanner configuration was not changed. These history fingerprints apply only to the reviewed commit, path, rule and line combinations; they do not suppress new findings elsewhere or equivalent findings in directory mode.

The existing ten Gitleaks configuration regressions passed with the pinned scanner, proving that the configured real credential shapes still trigger detection. The final complete Git-range scan must be rerun after committing the fingerprint file. Raw terminal captures and local tool caches remain private and uncommitted. Historical validation reports retain their original scope; this publication does not close the open provider or global acceptance gates.
