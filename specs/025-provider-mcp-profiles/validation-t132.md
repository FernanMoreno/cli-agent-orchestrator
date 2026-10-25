# T132 — content-free receipt verification stages

2026-10-25. The source7f6cdff0 real failures are preserved in the separate v1/full8 summaries. Delivery, extraction, receipt verification and public ACK are separate boundaries. A late TUI snapshot cannot establish the result seen by an earlier verification attempt. No stale-status, timeout, retry or model-causality correction is claimed.

A dependency-neutral, default-disabled callback observes existing admission and verification stages. It receives only local attempt ordinal, fixed stage and fixed outcome. Extraction, status/receipt witness, canonicalization, native binding/parts/receipt/render checks and durable CAS reuse existing reads and deadlines. Callback BaseException is contained. The disabled path remains inert; original exceptions, retry limits, CAS ordering and model deadlines remain authoritative.

RED controls caught an exception-boundary regression in the candidate canonicalizer: an early pending-receipt error was being replaced. The correction preserves the exact original exception and one state read. A fake-clock control verifies that callback latency consumes the existing native read deadline rather than extending it. Independent Claude review7245e00cffe44e8c99eda6e8819e6e59 found no blocking core issue; a previous timed-out review is not approval.

The combined controlled regression initially passed409 tests with5 warnings in194.69s, using normal configured coverage23%. A separate uncontrolled run failed during shared PostgreSQL teardown: the owner exited while another pytest process was borrowing its autouse database. The failed log is retained. Subsequent runs explicitly bind verified metadata with autostart disabled and hold the owner through every borrower. No database checks were disabled.

T133 separately covers the actual fixture bootstrap, startup handshake and shutdown/collection. Core offline evidence does not establish native acceptance or fix the post-restart failure. See validation-t133.md for final integrated checks and lifecycle findings.
