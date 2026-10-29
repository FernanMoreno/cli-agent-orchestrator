# T226 — native completion bound to the active PostgreSQL receipt

**PASS for the bounded local-tmux Claude cancellation/concurrent-final scenario, 2026-10-29.** Implementation: `1ff2746562048d723409545c74c7e23d8128012e`. [Machine receipt](validation-t226.json).

The reproduced long-response failure had no assistant head left in the native viewport or full scrollback; the native Stop envelope retained complete assistant text. A shared bounded ephemeral channel now binds provider, terminal, fresh CLI epoch, first native session and a Stop newer than the active dispatch's generation/hash baseline. Claude supplies only its native-envelope adapter and live-screen grammar. No model JSON requirement or SQLite authority was introduced.

Channel text is available only through the current-viewport hook. Both the response and current viewport must contain the active standalone receipt; completion summary, boxed composer and existing error/quota/spinner/selection/background guards must pass. LAST uses two consistent observations after missing/stale extraction, pins the generation and invokes the existing PostgreSQL CAS. Conflicts, cancellation and generation changes propagate; the fallback never retries a failed CAS. Ordinary receipt detection stays fenced until persistence succeeds.

Private CLI `--settings` merges SessionStart/Stop hooks without editing operator settings, disabling their hooks, changing model/native grants, or touching login. The callback uses isolated Python `-I`, emits no decision/stdout and fails closed. One atomic snapshot is capped at1MiB; normal readers never wait, writers/retirement retry at most1s and dispatch admission250ms. Epoch rotation, partial initialization cleanup, retired-channel refusal, fixed-temp recovery and strict JSON/ownership/mode/no-follow checks are covered. Initial support is local TmuxBackend without container mapping; unsupported paths retain legacy behavior.

## Executable evidence

- Original missing/stale-head LAST tests:2 RED, then GREEN. Earlier regression348 PASS; expanded376 PASS/201.69s; final post-cleanup78 PASS/25.25s (overlapping controls, not454 distinct tests). Actual callback packets from a poisoned working directory, stale/foreign events, missing fields, active work, raw/history exclusion, two-capture races, CAS conflicts, real flock contention, rotation and fault cleanup are covered.
- Five source files type-check with0 errors; Black/isort/diff checks pass; composition/Import Linter preserves5 contracts,0 broken. Claude read review and Luna Max/ECC findings were corrected and checked.
- Fresh native preflight:59.10s, own final receipt **verified**, actual SessionStart→Stop observed,104bytes of structured assistant text.1888 source pins and exact private executed archive.
- Fresh native concurrent case:98.36s. Stale generation409; current A cancellation200 in26.580s, old A retired, replacement idle with0 requests/no resend; join/delete preserve cancelled verdict and parent remains alive.
- B retains the same native process/FIFO/generation and produces26,481 fresh bytes with a new rendered semantic marker inside the original5s observation. One primary request,0 offered built-ins,58 progress blocks. Its native Stop carries1224bytes; its own final turn becomes **verified** within the original180s wait.
- Both cases retire observed processes/wrappers/tmux and remove owned HOME. Operational wrappers are reclosed and all pinned bytes preserved. The same existing PostgreSQL container ends with0 borrower connections/0 private databases. Lowest recorded concurrent memory9349MiB, above unchanged6144MiB.

## Composition and acceptance boundary

Dispatch claim→native event→bounded snapshot→current viewport→generation/receipt witness→durable CAS→local release preserves persistence authority and cancellation ordering. No schema/migration, transcript scan, broad exception retry, receipt-only acceptance or changed deadline was used. Shared-service/Base hooks default to no native candidate for other providers. Graphify's41 incident edges were checked against source; foreign generated graph files were preserved rather than rebuilt.

This fresh case closes **T223's bounded synthetic native concurrent-final journey** and T226; earlier T223 failures remain failures. It does not close all CL-10/T013/OC-06, native MCP-bearing profiles, container/remote backend or provider/version matrices, external inference, API-restart completion recovery, or global capacity. T225 remains a diagnostic receipt, not retroactive acceptance.
