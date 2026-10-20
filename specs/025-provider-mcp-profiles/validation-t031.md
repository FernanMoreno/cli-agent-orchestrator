# T031 — completion timeout observations

## Integrated scope

Completion-timeout diagnostics now report receipt-probe calls, prompt-recovery attempts and the existing recovery-verification flag. Probes count before invocation, including exceptions and empty returns. Recovery attempts include suppressed or failed sends; `delivery_verified` only means the guarded recovery probe reported an already-working terminal. Neither proves completion. `observed_working` remains independent, and the stable receipt count remains the current streak at the deadline.

The API projects the three added scalar fields without provider content. The real-provider matrix sanitizer accepts exactly a complete legacy shape or a complete extended shape; partial, unknown and incorrectly typed fields are rejected. Existing constructors retain defaults. No deadline, retry/recovery decision, settling rule, error/quota precedence, provider permission, database schema or persistence change was made.

## Executable evidence

The RED run reproduced **11 failures and 24 passes** before implementation. The corrected selection passed **214 tests, zero failures/skips and 11 warnings in 103.92 seconds**, using the original project coverage/marker defaults, four workers and private HOME/XDG/temporaries. It includes the full agent-step unit suite, diagnostic privacy and recovery regressions, the actual FastAPI run-step exception mapping and matrix contracts. Initial available memory was 6,920 MiB. This is scoped coverage, not a global coverage-floor claim.

Black/isort passed on the four changed Python files. Canonical mypy passed **404 source files**. Composition preserved **five architecture contracts, zero broken**. Logs/JUnit/receipts are private publication-cache evidence; historical RED logs remain unchanged.

## Review and composition

Three read-only Claude bridge consultations completed: initial diagnosis, product review and final API/probe review. The product diff was approved. The final review confirmed the actual ten-key HTTP regression and identified private-probe expiry, cleanup, receipt and file-mode/configuration issues; those were corrected before execution. No peer executed tests or changed this checkout.

The composition is completion-wait local observations → typed exception → HTTP error → closed harness evidence. Unit/HTTP boundary checks suffice for these scalar observations; a real PostgreSQL/provider probe remains separate from the correction's regression evidence. No independently evolving wire consumer or event schema changed, so no new Pact contract was introduced. Existing service/HTTP contracts cover the affected path. No new lock/cache, shared policy, transaction or process lifecycle was added to production.

Graphify's bounded query was truncated and included stale/broad neighbors. Source inspection verified the entire affected path. This increment adds fields and instrumentation to existing symbols without changing dependency direction or introducing structural symbols; no generated-graph rewrite is needed. Repository specs own the result; the known vault identity conflict remains untouched.

The strict global memory NFR and global coverage/acceptance gates remain open. T006 cannot close from this offline correction alone.

## Source-bound real Claude diagnostic

The first private diagnostic (R1) reached HTTP 200 in **21.503 seconds**, but its newly authored verifier incorrectly looked for `output` instead of the run-step endpoint's `last_message`. It therefore failed its marker assertion. Its failed JUnit, observations and owner receipt remain intact; it is not relabeled as passing. Cleanup was confirmed before a separate corrected run. This was a lead-authored harness defect, not a production-provider defect.

R2 corrected that field and additionally checked the parent-owned native receipt. Its single Unicode instruction completed with **HTTP 200 in 21.909 seconds**, the exact unique marker, a `succeeded` native receipt, settlement timestamp and recorded child cleanup. The standalone pytest diagnostic passed **one test, zero failures/errors/skips**; total owner duration was **81.992 seconds** including setup and cleanup. The parent session was deleted, the fixture workspace and exact owned tmux root were removed, and no forced termination or outer timeout occurred.

The requested model was `claude-sonnet-4-6`, with the installed Claude Code **2.1.303** executable pinned by SHA-256. This records requested configuration, not an independent provider-side model attestation. Both runs used private HOME/auth copies, the same verified healthy PostgreSQL container/image and a source manifest checked unchanged afterwards. The passing run measured **6,979 MiB** available before execution and **6,915 MiB** afterwards. Operator credential metadata stayed unchanged; this is metadata evidence, not a byte-equality or remote-token-validity claim. Staged expiry was checked privately before execution to avoid an already-expired/near-expiry refresh, and no login/renewal command was issued.

Private R2 evidence lives in `/home/felni/.cache/caos-t031-claude-diagnostic-r2/`: owner receipt, source manifest, JUnit and closed observations. R1 is preserved separately. Credentials, raw logs, prompts, responses and identifiers are not publication inputs. The run kept the 180-second step deadline, set prompt redelivery false and made no automatic retry. Recovery counters therefore cannot establish delivery in this run.

**T031 is complete; T006 remains open.** The historical 504 was not reproduced, so its cause is not established and the new timeout observations were not exercised by a real failing turn. This single no-tool Unicode turn does not accept the full native matrix, questions/ACK/AG-UI/restart, expanded MCP profiles, external Work messaging, provider concurrency or global NFR/coverage gates. Composition verdict: **PASS WITH RISKS** for the observation correction, with those live/global gates retained.
