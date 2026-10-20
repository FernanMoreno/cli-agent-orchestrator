# Validation: Generic Message API Restart Acceptance

## Current result

**Latest generic-message live results are bound to R17, not the current main tree.** Codex R17 and OpenCode R17 passed their selected `send_message` journeys; each observed two completed calls, persisted rows, redacted events, one API replacement, and clean owned-resource cleanup. Claude R17 failed at `observer_audit` after its two calls, rows, events, and API replacement because the genuine MCP `.complete` seal was not observed. The retained evidence does not identify why the producer did not close normally. T023 and T024 remain open until all required lanes are rebound to a fresh final source and pass.

The earlier OpenCode R20 attempt exited at `native_terminals` before any provider call. Source tracing and an independent offline reproduction found the harness created the receiver before installing its native profile. The reviewed ordering fix is integrated in main, and a later R17 OpenCode generic-message run passed. This does not reproduce the separate historical 120-second CAO initialization timeout or reconcile that external job. The earlier Codex R20 pass remains historical evidence and is superseded by the later R17 run for recency, not by a current-tree pass.

The latest workspace-reported OpenCode live lane on 2026-10-19 failed in `phase_a`; its summary reported cleanup `pass` and zero calls/events. It is not a live acceptance, and it does not establish success for either Spec023's generic `send_message` or Spec024's Work message operation. The lane was not repeated during the source-binding audit.

The separate Claude question diagnostic emitted `authentication_failed` before creating questions. The historical non-ASCII `/run-step` failure has a source-level candidate: a malformed non-ASCII answer receipt could reach `hmac.compare_digest` and ASCII hashing. `AnswerAck` now rejects non-ASCII receipts at validation, and focused offline API/service regressions pass; this proves the source defect is fixed, not that the old external CAO job used that exact call site. No administrative reconciliation or global reinstall was performed.

The attempts below are historical and do not supersede the current results above.

| Attempt | Sanitized result | Acceptance meaning |
| --- | --- | --- |
| R3 preflight | Failed before API/provider startup because `CAO_REAL_PROVIDER_E2E_TEST_FILTER` was absent. The source-bound import reached `test_real_provider_matrix._pair_selection()` while `CAO_RUN_LIVE_PROVIDER_TESTS=1`. | Preflight failure; no provider-call evidence. |
| R4 preflight | The selected self-pair filter allowed source-binding verification to complete. | Preflight only; not a live acceptance pass. |
| First post-fix Codex attempt | The bounded controller reported `process_task_inventory_raced` and incomplete cleanup. No MCP `send_message/completed` record was observed and no inner acceptance receipt was produced. | Failed run; provider completion is unverified, and there is no Spec023 pass. |

The controller's sanitized summary for that attempt was: 8 owned process groups observed, 0 known groups remaining in the controller receipt, inventory unreadable, cleanup incomplete, and 0 completed MCP records observed. The reported count does not prove that no process tree remained or establish that historical cleanup completed. No resource IDs, message bodies, authentication values, or service-metadata contents belong in this report.

## Preflight invariant

Every independent child environment must set `CAO_REAL_PROVIDER_E2E_TEST_FILTER` to the exact selected-provider self-pair, such as `codex->codex`, `claude_code->claude_code`, or `opencode_cli->opencode_cli`. The filter must be derived from the single selected profile rather than inherited from the operator environment. `CAO_RUN_LIVE_PROVIDER_TESTS=1` remains required by the source-bound provider authorization helper; therefore, removing that general gate is not a valid workaround.

The filter is necessary because source-binding verification imports `test.e2e.test_real_provider_matrix`, whose module-level parametrization evaluates `_matrix_cells()` immediately. With the general provider gate enabled, `_pair_selection()` requires the filter during import. This preflight path checks source/configuration and does not itself prove that the selected Spec023 provider call, API persistence, AG-UI event, or cleanup succeeded. The live pytest target remains the Spec023 acceptance test only.

## Offline Diagnostic Follow-up — 2026-10-16

The focused offline Spec023 stream/observer selection passed **64 tests, zero skips** from the canonical resolved checkout path. The observer writes only the fixed `tool`, `status`, and `outcome` fields; a returned FastMCP `ToolResult` with `success: false` is now distinguishable from a dispatcher exception, and acceptance rejects every outcome except `success`. The stream-reader regression proves that explicit `close()` releases a blocked iterator and keeps unexpected iterator/non-iterator `AttributeError`s as failures. Black, isort, a scoped whitespace scan, and the project composition check passed; independent ECC review approved these contracts. A separate Unicode receipt/OpenCode profile selection passed 161 tests with five dependency deprecation warnings. These are offline checks only: no provider, API worker, network, PostgreSQL, or Docker ran, and the new source bytes require a fresh freeze before any live attempt. The exact cause of the old phase-A failure and both external CAO job outcomes remain unresolved.

## Passing evidence required

A provider lane is accepted only after the independent launcher validates the complete sanitized inner receipt and outer run result, including exactly two completed `send_message` audit records plus the positive completion seal, the expected persisted rows across API replacement, one redacted live AG-UI event for each action, unchanged original native sender/receiver panes, and verified owned cleanup. Any controller inventory race, missing inner receipt, incomplete cleanup, missing audit completion, or absent provider completion is a failure. Do not infer success from a source-preflight pass, generic matrix selection, or a started task.

Keep provider lanes serial and record each provider separately. Until all required lane evidence and final review are complete, leave T023 and T024 unchecked and report the feature as pending.

## Offline Source-Binding Closure — 2026-10-19

Source tracing found that `_SOURCE_BINDING_FILES` did not hash several production owners on the generic `send_message` persistence, delivery, and event path. The inventory now includes database CRUD, inbox/terminal models and services, receiver status checks, provider/backend selection, plugin dispatch, and event-log storage. An offline contract requires the complete 19-file production source set and exercises missing, symlinked, and post-snapshot mutations before auth, port selection, API startup, or provider startup. The TDD regression failed before the inventory expansion and passed afterward.

The complete offline `test/live_provider/test_message_api_restart.py` module passed **112 tests** with provider/API/network/PostgreSQL/credential gates disabled (`--noconftest`, plugin autoload disabled). Black and isort passed. `project-composition-check caos` passed for 403 files and 1,789 dependencies, keeping five contracts with zero broken. The one pytest configuration warning is expected because `asyncio_mode` is declared in project configuration while plugin autoload is disabled. No provider, API worker, HTTP/network, PostgreSQL, credential, or QEMU activity was started for this correction.

This changes the runner's source inventory and offline tests, so it does not bind any previous provider receipt to current source. T023, T024, and T044 remain open pending a fresh full-tree source freeze and separately reviewed live launcher. The reported 2026-10-19 OpenCode `phase_a` failure was not repeated and is not acceptance evidence.
