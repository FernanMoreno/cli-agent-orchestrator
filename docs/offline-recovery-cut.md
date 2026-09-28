# Internal offline recovery cut (T069)

## Status and scope

T069 provides an internal, server-owned cut for capturing a private recovery
bundle from the Work SQLite store. It records evidence about the cut and the
integrity of captured objects. It does not provide a product recovery workflow
or prove that the copied Work state is a consistent, executable snapshot.

There is currently no public API, MCP tool, or CLI command to request a cut or
capture a bundle. The implementation is available only to internal server
composition. Do not run it against a provider or an operator-managed database.

## Operator authority and writer fence

The internal caller must provide a verified, authenticated JWT principal with
the admin scope. A local fallback identity is not accepted. The lease is created
against the server-owned Work store and records its operator, owner (`work`),
scope (`registered-work-writers`), epoch, fence, expiry time, and revision. Its
TTL must be finite and positive.

The server grants a lease only when its registry has no active registered Work
writer. While the lease is live, registered writers are fenced from starting an
effect. Capture rechecks the same live lease, store identity, fence, and empty
registered-writer set at each observation phase. Expiry, revocation, a changed
fence, or a registered writer causes the capture or publication check to fail.

This registry covers only writers registered by the Work runtime. It cannot
stop or observe an unregistered writer, a separate process, or an external
provider. An unregistered mutation can occur during staging; it is not evidence
of quiescence. Consequently, T069 does not establish global quiescence.

## Capture evidence and publication

One `capture_id` binds the `before`, `after`, and `promote` observations to the
same lease and durable publication record. The observations contain a zero
registered-writer count and the same Work inventory fingerprint and profile.
The bundle manifest uses format v2 and records the lease and phase evidence.

The manifest exposes a stable one-way projection of the store identity rather
than its server-side identity value or filesystem path. Every captured object
has `integrity-only` coverage. This means its bytes, references, and inventory
can be checked; it does not mean the object is a consistent application
snapshot, can be restored, or can be executed.

Publication keeps the database fence while promoting the private bundle and
committing the publication record. Verification requires that durable record
to match the lease, `capture_id`, path, manifest digest and size, revision, and
phase observations. A directory or receipt without the matching published row
is an orphan and does not verify. A failed capture records rejection evidence;
an artifact left by a failure after filesystem promotion must not be treated as
a published bundle.

Version 1 bundles are historical, verify-only artifacts. Their verification
covers bytes and references and carries no T069 cut or publication semantics.
New cut evidence is version 2; changing a v2 bundle to version 1 does not make
it acceptable as a historical bundle.

## Operational limits

- No public API, MCP, or CLI entry point exists for creating the lease or
  capturing a bundle.
- The operator-managed database and real providers are outside this cut.
- The registry does not establish global quiescence or control unregistered
  writers and external processes.
- The bundle is not a restore source, an execution permission, or authority to
  send, retry, or acknowledge delivery.
- T070 restore and T071 operational rollback remain open.

## Maintainer verification

These are repository checks only; they do not invoke an operational capture or
use an operator database. The T070 restore test is explicitly excluded because
restore remains open.

```bash
uv run pytest -q --no-cov \
  test/services/test_recovery_bundle.py \
  test/clients/test_work_recovery_guard.py \
  test/clients/test_work_migrations.py \
  test/services/test_work_service.py \
  test/integration/test_work_admission.py \
  --deselect test/services/test_recovery_bundle.py::test_restore_stages_bundle_without_touching_operator_store_or_relaunching_active_attempt

project-composition-check "$(cat .ai/project-name)"
```

Run these commands when checking the implementation. No test or composition
result is asserted by this document.
