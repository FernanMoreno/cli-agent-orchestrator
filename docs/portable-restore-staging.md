# Portable restore staging: private T070 operation

## Status

Stage F R2 passed. T070 is closed for the private restore of a published
`recovery-bundle-v2` bundle with profile version 26. This documents that
bounded internal operation; it does not establish a public restore workflow.

## Accepted input and path roles

The restore accepts only the published v2 manifest and its single
`sqlite-v26` object. It verifies the receipt, manifest, source inventory, and
portable SQLite contents before creating staging. Result, worktree-evidence,
and delivery references must match their SQLite rows by identifier, role,
digest, and size. Memory references must match `memory_metadata` identifiers;
their manifest object digests and sizes are verified separately against the
bundle objects. Unreferenced non-SQLite objects are rejected.

The source database identity is read from `receipt.source_database`. Its
historical inbox identity, UUID, bridge, and bindings are checked and retained
as provenance; they are not adopted as the restored installation's identity.
The source database, private staging path, and requested destination path are
distinct paths. The destination must be new, distinct from the protected bundle,
source, SQLite object, and configured database paths, and cannot be inside
those protected paths. Configured operator storage is resolved for comparison
only and is never opened.

## Staging and publication

The operation creates a private mode-0600 staging file beside the destination.
It copies from the verified SQLite object through an `O_NOFOLLOW` descriptor,
checks the copied digest and size, and fsyncs the file. Only this staging copy
is passed to T095's transactional `blocked_restore` transform. T095 validates
the portable store and source provenance, then reconciles only attempts in
`sent`, `acknowledged`, or `running`, preserving other history. It does not
relaunch attempts or invoke providers. The transformed staging database is
verified again and fsynced before publication.

Publication uses a hard link to the new destination, so an existing destination
cannot be clobbered. The destination's parent directory is fsynced after the
link. If that post-link fsync fails, the operation returns an error with an
uncertain publication result; the blocked destination is retained and must
not be reported as a successful restore. On the successful path, staging is
unlinked and the parent directory is fsynced again.

## Limits

There is no public ingress, API, CLI, or MCP entrypoint; provider integration;
operator database access; or global ingress guard. The blocked copy is not
reactivated by this operation. T071 rollback/reactivation and T072 multi-node
validation remain open. This T070 closure is limited to the private published
v2/profile-26 restore path.
