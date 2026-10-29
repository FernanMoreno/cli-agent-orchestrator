# T215 · bounded private render-input capture

2026-10-29 · **Component PASS; real capture/offline replay pending.**
Base `8885aae160f5134581eb8fa8b91dc9329d47f89b`.

The source-pinned API observer has an explicit `capture_render_inputs` option.
Default config bytes and its fixed content-free stage ledger stay unchanged.
The task-query diagnostic alone enables it. Native/rendered comparison inputs
go to a separate private sibling directory, never to stage events or public
receipts. The original signature function runs first: values and exceptions
are unchanged. No production parser, equivalence rule or acceptance changes.

Limits: 8 files, 64 KiB UTF-8 per input, bounded JSON records; owned new0700
directory and0600 exclusive/no-follow files through its retained directory FD.
Thread/attempt/part ordinals correlate the two inputs. Close fences writes,
restores only the exact owned hook and seals counts/failure/overflow. Bootstrap
failure and normal Uvicorn/atexit shutdown close the capture. Private payloads
remain in the diagnostic case; only counts/seal flags may enter public receipts.

Validation: 4 initial RED; 27 observer/capture tests PASS; installed-bootstrap
control plus final focused suite6PASS25.75s. Broad regression **431 passed,
20 subtests passed, 3 existing warnings,155.57s**. Original coverage/deadlines
and thread/unraisable warning errors retained. Black/isort and composition
checks pass (5 contracts/0 broken). Claude read review
`b00ba079c5c64d4b9f4de1c665cfd3fe`: GO. No command execution by reviewer.

Optional mypy found three existing diagnostics. Exact HEAD shadow comparison
reproduces the same no-any-return and two intentional-method-patching errors
at their original lines; the new capture introduces none. No checker settings,
casts or ignores were added; no global typing PASS is claimed.

Composition: prepared bootstrap → validated module identities → private hook →
bounded FD writer → ordinary API shutdown; original return/exception, default
config, loss of hook ownership, late callback, overflow and private-stage
separation tested. No services/model used for component checks. Graphify has no
current observer node; source paths were verified and generated files preserved.

Replay must require a complete sealed pair with `failed=false` and
`overflow=false`; oversized/skipped inputs can invalidate part labels. Missing
seal means unknown completeness. Do not infer a body mismatch from counts.
R1 remains failed. T215 real diagnostic and T214 acceptance remain pending.
[Structured component receipt](validation-t215-component.json).
