# Requirements quality review — Spec 025

- [x] User scenarios distinguish diagnosis, surface policy, operation authority and real acceptance.
- [x] Requirements and success criteria are testable with explicit evidence limits.
- [x] Scope and compatibility boundaries are explicit, including OpenCode v1/v2 and legacy marker intersection.
- [x] All numbered provider pending groups have execution tasks; no live gate is inferred from offline results.
- [x] Unknown policy/configuration values fail closed; no credentials, prompts or receipts enter diagnostics.
- [x] Constraints preserve main changes, existing limits, source bindings and historical records.

Review 2026-10-20: specification-quality criteria satisfied; this checklist does not mark implementation or live acceptance complete.

Review2026-10-21, FR048/SC032: the existing-capacity Work concurrency gap has an explicit bounded preparation/delivery contract, direct-call compatibility, and cancellation/shutdown uncertainty requirements. Actual-source scheduling RED is separate from process-backed acceptance. No new grant, API retry surface, schema, multiworker assumption or model budget is introduced. All six quality checks remain satisfied; T064 implementation and live gates stay open.
