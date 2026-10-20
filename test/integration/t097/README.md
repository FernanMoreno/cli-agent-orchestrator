# T097 disposable guest acceptance

The QEMU guest runner keeps its acceptance groups separate so each run has an
exact count and a no-skip check:

- 8 real host-acceptance cases run as the dedicated non-login Work broker.
- 63 scratch-Bubblewrap security cases run against the checksum-pinned 0.13.0
  build at `/tmp/caos-exec/T097/bubblewrap-build/bwrap`. Their exact node IDs
  are kept in `scratch-selectors.txt`: 62 run as the dedicated non-login Work
  broker, and the exact same-host-UID user-namespace sibling case runs as the
  guest `runner` at UID/GID 1000. The runner case uses its own mode-0700
  directory with mode-0600 PostgreSQL metadata and password files because the
  global PostgreSQL fixture validates ownership against the test process.
- 1 Landlock ABI-9 pathname-socket case runs after the guest ABI preflight.
- 2 device-node snapshot cases run as guest root so `mknod` exercises the
  positive character-device and block-device paths. The runner makes private
  root-owned copies of the test PostgreSQL metadata and password because the
  normal PostgreSQL fixture requires metadata ownership to match the test
  process. Those copies and the isolated root home are removed by the guest
  cleanup trap.
- 3 filesystem/recovery cases cover dead-monitor cleanup and case-variant
  vault roots. The case-variant tests receive an ext4 `casefold` filesystem
  mounted only inside the disposable guest; the runner sets the feature on an
  empty parent directory and verifies case-insensitive lookup and inheritance
  before passing a child path as pytest's `--basetemp`.
- 7 bound-content and process-identity cases exercise durable setup, proxy
  activation, uncertainty after GO, real worker execution, and fail-closed
  process-identity persistence.
- 4 T098 cases exercise real Bubblewrap worker execution, double-fork exec
  confinement, authority revocation before GO, and uncertain cleanup without
  redelivery. The other five T098 module cases remain in the unfiltered backend
  suite: `test_durable_work_contract_can_dispatch_only_its_allowed_mcp_tools`,
  `test_gateway_dispatches_v2_to_bound_process_effect_without_terminal_fallback`,
  `test_shebang_cannot_be_published_as_bound_static_executable`,
  `test_missing_process_capability_never_substitutes_terminal_backend`, and
  `test_authority_change_at_process_effect_boundary_leaves_no_worker_marker`.
  They cover test-backend gateway contracts and ELF publication rejection
  rather than guest-only Bubblewrap/runtime capabilities.

The guest selection totals 91 cases (80 existing T097 groups, 7 additional
T097 capability cases, and 4 T098 cases). It keeps the 45-second worker timeout
only for the slow QEMU TCG guest; the default host timeout remains 10 seconds.
The eight host cases remain an independent acceptance result. Each group must
report its exact passing count with no skipped, xfailed, xpassed, or deselected
cases.
