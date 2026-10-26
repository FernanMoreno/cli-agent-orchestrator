# Implementation Plan: Real-Provider AG-UI Restart Acceptance

**Branch**: `[022-provider-agui-restart]` | **Date**: 2026-10-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/022-provider-agui-restart/spec.md`

## Summary

2026-10-26 diagnostic increment: the failed current Codex receipt collapsed
seven waits to `unavailable` and deleted the private transcript after verified
cleanup. Reproduce that information loss with the production MCP error mapper,
then preserve only an exact fixed-detail enum through event parsing and receipt
sanitization. Cover structured and JSON text MCP results and reject arbitrary
content. No runtime retry or deadline change. Graph query has no current harness
nodes; source confirms `_safe_mcp_result_diagnostics` feeds provider summaries
and `_safe_provider_receipt` is the final privacy boundary. No graph rebuild is
needed for this helper-only change with no new subsystem dependencies.

Add an opt-in, test-only composition path to the bounded Codex, Claude, and native OpenCode interaction journeys. The existing standalone AG-UI runner keeps its reviewed 180-second journey and 25-second cleanup windows while owning the provider process tree; the acceptance process then owns its separately spawned API worker. A separately extracted generic standalone controller command retains its reviewed 900/880-second window and requires explicit validated runtime configuration. The worker serves the production interaction and AG-UI routes over the existing isolated PostgreSQL target. The journey captures real metadata-only one-shot SSE snapshots, restarts only that API process while both provider-created questions are pending, reads a fresh snapshot from the replacement process, submits versioned human answers, and lets the provider/MCP invocation own wait and exact receipt acknowledgement.

No product API, database schema, AG-UI projection, provider policy, or normal journey behavior changes are planned.

## Technical Context

**Language/Version**: Python 3.10+

**Primary Dependencies**: Existing FastAPI application/routes, Uvicorn, HTTPX, PostgreSQL driver/interaction repository, installed Codex, Claude, and OpenCode CLIs with their existing native launch/profile paths.

**Storage**: Existing disposable native PostgreSQL main target from `test/postgres/fixtures.py`; no new tables or migrations.

**Testing**: Pytest offline unit tests for worker configuration and SSE parsing; opt-in real-provider runner for the combined acceptance.

**Target Platform**: Loopback Linux test environment with private temporary directories and existing provider CLI auth staging.

**Project Type**: Test harness and live acceptance tests only.

**Performance Goals**: Preserve the existing provider time budget. API worker readiness and restart must fit within that bound; do not widen it to mask startup delay.

**Constraints**: No provider/API-key fallback; no request or response is fabricated; provider owns wait/ack; no private fixed paths in portable modules; no raw transcript in sanitized evidence; no global auth/home mutation; no product/API/schema changes. Only standalone process controllers enable subreaper semantics. The AG-UI runner retains its 180/25-second lifecycle; the generic standalone `main()` retains its separate 900/880-second lifecycle. A host-bound caller supplies the generic command's required configuration and retains the frozen source/runtime/auth pins, including equality between the configured controller-module path and actual loaded origin.

**Scale/Scope**: Two questions per provider journey; run Codex, Claude, and OpenCode serially; one API restart per journey; one isolated main database target per journey.

## Constitution Check

- **Evidence before conclusions**: Pass. Success depends on actual provider calls, production HTTP routes, a process restart, durable database state, and real AG-UI frames.
- **Preservation and scope**: Pass. Work is confined to a private candidate and test-harness paths; no commits or external publication.
- **Design before implementation**: Pass after spec, plan, tasks, Graphify, source verification, and TDD decision are completed in order.
- **Composition verification**: Pass by design. The combined run spans provider CLI/MCP, API worker process lifecycle, PostgreSQL persistence, human answer routes, and AG-UI projection.
- **Honest closure**: Pass by design. Any provider setup failure, restart failure, missing frame, process failure, or receipt mismatch remains a failed result, not a skip or substitute proof.

## Project Structure

```text
test/live_provider/
├── interaction_cli_journey.py                 # Existing journey; new opt-in dispatch only
├── interaction_api_worker.py                 # Owned subprocess loading production API app/routes
├── interaction_agui_restart.py               # Worker config, safe SSE parser, bounded orchestration helpers
├── bounded_process_controller.py             # Portable standalone outer controller and required ControllerConfig
├── test_interaction_agui_restart.py            # Existing offline tests; no provider/server/DB launch
└── test_bounded_process_controller.py          # Adapted offline controller tests; no provider/server/DB launch

specs/022-provider-agui-restart/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/api-worker.md
├── tasks.md
├── graphify-impact.md
└── checklists/requirements.md
```

**Structure Decision**: Keep executable project changes under `test/live_provider/`. The portable `bounded_process_controller.py` supplies the generic standalone `main()` path and shared process-lifecycle helpers; it has no default host paths, credentials, launcher hashes, or fixed auth/cache roots. Its `main()` stays prepared-only without `--execute`; that flag requires a private validated `ControllerConfig` carrying runner, source-manifest, shim, runtime, private-profile, receipt, and the reviewed fixed 900/880-second inputs. Static pin verification requires the configured controller module to resolve to the executing module's `__file__` and its pinned digest to match. The AG-UI runner remains a separate standalone owner with its existing 180/25-second contract; it can consume ownership helpers but does not call generic `main()`. A host-bound caller imports a mode-0600 frozen source copy and retains reviewed source/auth/runtime pins and receipt binding. Do not fold subreaper behavior into pytest, the ordinary journey, or `ApiWorkerController`. The inner acceptance worker still loads the actual production app (`api.main.app`) with lifespan disabled, explicitly initializes an `InteractionService` over the private database config, and assigns it to app state. Parent setup registers terminal/source identity through the durable database before worker start.

## Lifecycle and Data Flow

1. The AG-UI runner verifies its source/auth/runtime pins, verifies an empty baseline kernel child inventory, and pins its own identity before launching the provider journey; its fixed deadlines remain 180 seconds for the journey and 25 seconds for cleanup. The generic portable `main()` remains a separate 900/880-second command and accepts only a required validated `ControllerConfig`.
2. The existing journey prepares its private home, staged provider auth, isolated PostgreSQL main target, terminal row, and source capability using the current code path.
3. The opt-in orchestrator starts API worker A as a child process on a loopback port. Worker A imports the source tree through the run-derived `PYTHONPATH`, loads the private config, initializes a fresh interaction service, installs it in the production app state, and runs the production routes with the app lifespan disabled. Its tmux socket directory is also private because the AG-UI stream builds a fleet snapshot before projecting interaction metadata.
4. The actual provider CLI and its MCP subprocess start once and keep their existing profile, model, tool allowlist, timeout, and auth behavior.
5. When PostgreSQL shows exactly the two expected provider-created questions, the orchestrator reads a real AG-UI `STATE_SNAPSHOT` and verifies the pending IDs and closed metadata shape.
6. The orchestrator stops only worker A, verifies that owned process exited, starts worker B on the same configured port/target, and waits for a real production route to become ready.
7. A fresh AG-UI connection verifies unchanged group/question IDs, versions, lifecycle, source incarnation, and unanswered delivery state. The ledger group count remains one.
8. The orchestrator posts the existing two fixed human answers with the exact question version, `If-Match`, and stable idempotency key. It never calls MCP wait/ack.
9. The provider/MCP process retrieves and acknowledges both returned receipts. A fresh real AG-UI SSE request then reads worker B's initial snapshot and must show the same two question IDs as `agent_acknowledged` without private answer data.
10. Codex and Claude receipt checks require natural provider exit zero, two answers sent, exact returned/ack identity, and owned cleanup. OpenCode instead requires SC-010's exact acknowledgements, a timely production-provider `IDLE`/`COMPLETED` state before cleanup, verified owned cleanup, and harness exit zero; report its natural provider exit status as null unless directly captured. Sanitized evidence contains only bounded status, identity booleans, and timings.

## TDD Decision

Use test-first development for the new pure harness helpers. Begin with failing offline tests for:

- strict parsing of actual named SSE frames, JSON data, comment/heartbeat lines, and partial/multiple data lines;
- extraction and validation of production `STATE_SNAPSHOT` and `STATE_DELTA` interaction fields without inventing alternative payload schemas;
- API-worker configuration built from run-local inputs, including source-root and private config paths, port validation, environment allowlist, and absence of API key variables;
- process helper ownership rules: only the exact spawned worker may be stopped/replaced, and ordinary journey mode does not invoke restart helpers.

Then implement the helper and worker. The real-provider acceptance is an opt-in integration gate and will not be run as part of these offline unit tests.

## Acceptance and Failure Semantics

- Preserve one allowed `delivery_states` entry per question when sanitizing the receipt. The observed two-question Codex run completed internally, but the sanitizer collapsed the two acknowledgements into one and the strict outer classifier correctly failed. Add an offline regression against the producer, change only this field's sequence projection, retain the strict two-delivery classifier, and repeat live acceptance against the newly frozen source. The original failed outer receipt remains historical evidence.
- Require exactly two questions for the expected group before restart. Zero, one, or extra questions fail.
- Restart only after the initial snapshot proves both questions are pending and before human answers are posted.
- Use the same listener port for provider/MCP config. The replacement worker must bind only after worker A is confirmed exited.
- A provider/MCP tool failure during downtime is not hidden. The journey must still complete through the provider's normal tool path within its existing budget.
- Snapshot/projection availability must be true; empty fallback state is failure.
- Check identity equality by values internally and emit only safe booleans/IDs in private acceptance evidence according to the existing receipt policy.
- If any owned child cannot be reaped or private cleanup fails, journey result is failure. Both standalone owner paths use bounded PPID census only to discover descendants after verified task-list churn; they signal only previously verified process groups and reap only exact known adopted PIDs. Final empty proof comes from the pinned controller kernel child list after known groups settle. The AG-UI path stays within its existing 180/25-second windows; the generic `main()` path stays within 900/880 seconds. Any unresolved child or inventory race preserves evidence and reports incomplete cleanup.

The harness uses separate one-shot SSE requests for snapshots. The wait transport phase at API replacement is unobserved and may overlap the replacement. The acceptance proves persistence of the same two pending questions across worker replacement and later provider-owned answer delivery/acknowledgement; it does not prove interruption, retry, or recovery of a wait across replacement. Actual provider/API errors fail the acceptance.

## Requirement Traceability

| Requirement | Design / implementation | Validation |
|---|---|---|
| FR-001, FR-002 | Existing `interaction_cli_journey.py` opt-in flag dispatches to the combined journey, retaining existing provider profile builders and adding the approved native OpenCode dispatch | Default-mode and opt-in parser/model contract tests; later serial Codex, Claude, and OpenCode runs |
| FR-003, FR-004 | `interaction_api_worker.py` uses production app/routes and configured PostgreSQL target; parent registers durable source before launch | Worker startup unit tests; cross-process identity assertions in live run |
| FR-005, FR-009 | `interaction_agui_restart.py` reads actual production SSE frames and checks metadata-only state | SSE parser unit tests plus real stream snapshots/delta in live run |
| FR-006 | Bounded restart orchestrator manages exactly API worker A/B while retaining provider/MCP and PG | Process ownership unit tests and live restart proof |
| FR-007, FR-008 | Existing human answer and provider receipt instrumentation retained; no harness wait/ack calls; OpenCode lifecycle is evaluated under SC-010 | Live exact-identity/database delivery assertions; native OpenCode status and cleanup independently verified |
| FR-010 | Run-root-derived worker environment/logs and existing private provider staging | Offline environment tests and existing auth/cleanup assertions |
| FR-011 | Pure helpers and offline unit test file | Pinned offline test command; verify no DB/server/provider calls |
| FR-012 | AG-UI standalone runner keeps 180/25-second bounds; generic controller `main()` requires validated `ControllerConfig` and keeps 900/880; both retain bounded discovery, verified group signalling, and final kernel child closure | Adapted offline lifecycle tests; source-import manifest; live acceptance remains separately gated |

## Gate Review

No unresolved design question requires a product API change. The production stream already provides a complete snapshot on every connection, and the default requested interaction page limit is 100, sufficient for this isolated two-question run. A fresh connection reads its initial snapshot rather than assuming interactions generate fleet event-log records.

The Phase 4 portable-controller source was reviewed against the workspace controller and test files. The generic command remains prepared-only unless execution is explicitly requested and retains its 900/880-second contract; the standalone AG-UI runner retains its separate 180/25-second contract. The configured frozen-module path is bound to the executing `__file__` and pinned digest. The validation interpreter passed all 90 offline tests with an empty private `PYTHONPYCACHEPREFIX` before and after; the imported-source manifest and exit status are recorded in the private external evidence bundle. A no-execution origin check confirmed workspace source and frozen module both have SHA-256 `4617d25779313db2d13225f8e282f94d0fddacaff31235bf2b83ef47eb3a83b9`. Targeted Black and isort checks passed, as did `project-composition-check "$(cat .ai/project-name)"` (399 files, 1,775 dependencies, five contracts kept, zero broken).

The existing Graphify snapshot was queried read-only but did not surface relevant controller or AG-UI journey nodes; this is a graph-coverage limitation, not evidence that dependencies are absent. Direct source review confirmed the portable controller imports only the standard library and that the change adds no production import or dependency edge. No refresh was needed for production architecture closure; the generated graph remains stale for the new test-only module. T016 and the Codex, Claude, and OpenCode live-provider acceptance lanes remain open; the private operator adapter was not executed.

The subsequent T018 review traced two raw database delivery rows through the unchanged exact-identity success gate into `_safe_provider_receipt`, where a set projection discarded duplicate states. Only the delivery-state projection now preserves sequence order and multiplicity, retaining the same allowed enum values; all other category projections and the strict outer classifier are unchanged. RED/GREEN producer tests and 214 affected harness tests passed after integration in main. Composition again reported five contracts kept and none broken; independent ECC review accepted this narrow harness delta. A Graphify query over receipt/delivery/provider/question/safe did not surface the producer in the existing graph; direct source inspection established the boundary. No new dependency or production edge requires a graph rebuild. The existing specification and private evidence retain the failure and acceptance provenance; no routine debugging details need a vault entry. This closes the offline producer fix only; a newly frozen-source live run must still pass the strict outer classifier.


## Follow-up: empty task inventory identity recheck

### Evidence and decision

The preserved R3 run failed with `process_task_inventory_empty` during owned-child inventory, later reported zero process groups while unreadable state remained true, and ended with incomplete cleanup. It produced no JUnit result, launcher receipt, or handoff evidence, and it captured no identity observation after the empty task listing. The actual branch at that moment is unknown. The private RED reproduction shows only that the copied baseline does not distinguish a confirmed process exit or changed identity from a same-pin empty task listing.

On each empty task listing, perform one immediate read of the existing pinned identity. Use the established branches only: an absent pin becomes `identity_missing_after_snapshot` and can enter the existing bounded restart; a changed pin becomes `identity_changed_after_snapshot` and fails closed; an unchanged pin stays `process_task_inventory_empty`; unreadable identity or deadline expiry fails closed. Do not infer that the process has no children from an empty task directory. Do not adopt, signal, wait on, or reap a changed PID identity.

### Implementation boundary and invariants

Keep the implementation within `test/live_provider/bounded_process_controller.py` and extend its existing portable controller test module. Make no production API, provider, database, schema, or unrelated harness changes. Reuse the existing identity reader, pin fields, error branches, and bounded owned-tree restart path. One fresh identity read follows each empty task listing; there is no loop or new retry budget.

Preserve the existing three-attempt inventory bound, the generic controller's 900-second hard and 880-second run-stop bounds, and the AG-UI runner's separate 180-second journey and 25-second cleanup bounds. Preserve exact verified-process-group signalling, exact-PID reap rules, final kernel child-inventory checks, and incomplete-cleanup reporting. Do not add a PPID-census fallback, signal/wait path, receipt telemetry, or new receipt fields. The earlier R3 artifacts remain immutable, and no live run is part of offline implementation.

### Test-first validation

Use synthetic proc trees and the existing portable tests to prove: empty plus absent pin selects the existing missing-after-snapshot branch; empty plus unchanged pin remains `process_task_inventory_empty`; empty plus changed pin selects the existing identity-change failure without adopting or signalling; recheck errors/unreadable identity and deadline expiry fail closed; and a later empty listing remains bounded. Keep existing non-empty and TID-churn behavior covered. No test in this phase may read host `/proc`, spawn a child, send a signal, or start a provider, server, or database.

After the private candidate is accepted, port only the reviewed controller/test change and these Spec 022 amendments. Run the focused controller suite and applicable formatting/type checks, review the exact diff and ownership/budget invariants, query the relevant Graphify impact and verify it against source, and run the project composition gate. Then create a new immutable source revision without replacing earlier snapshots, rebind the existing 25-file global runner to its source manifest, and run runner-integrity and offline contract checks before requesting a separate root decision for the actual global run. The full run must use its own dynamic collection, keep the exact critical/rest partition and existing bounds, and must not use a standalone collection step. A fresh scoped process/artifact check is required immediately before that run.

A passing offline candidate or global backend run does not close the preserved provider acceptance failure. A later live provider acceptance remains separately gated; the R3 cause must stay recorded as unknown unless new direct evidence establishes it.

## Addendum: native OpenCode FR-019/FR-020 and SC-010

### Intended composition and restart boundary

The OpenCode lane must compose its real `OpenCodeCliProvider` initialization and native interaction/MCP invocation with the existing owned production API worker, strict AG-UI snapshots, and persistent observer. The provider/MCP process and the same PostgreSQL target remain live; only `ApiWorkerController`'s API worker is replaced. The private response barrier is raised after the real create-group POST response is received/committed and before that same response is returned to the same native MCP call. Releasing the barrier lets the existing native invocation proceed to its ordinary ready-wait and exact receipt acknowledgement. This is a pre-wait API-worker restart. It does not claim recovery from an interrupted wait. No extra request, Enter, answer, wait, acknowledgement, or deadline is introduced.

The sourcebound child opt-in uses only the fixed `CAO_MCP_RESTART_BARRIER_DIR` environment entry. The existing source-bound `sitecustomize` hook remains byte-identical and default-off when the key is absent; the opt-in hook uses the run-private, bounded sidecar and writes only fixed markers. The OpenCode MCP environment must pass the explicit isolated-environment allowlist and serialized v2 writer; the exact generated `env -i` command prefix must be tested from a private working directory. Do not put barrier data on MCP stdout or persist response contents, IDs, receipts, URLs, headers, argv, environment dumps, or provider text. The response wrapper returns the same object or fails closed.

### Source-verified composition and limitations

- `test/live_provider/interaction_cli_journey.py` currently keeps the AG-UI restart dispatch and provider argument parser to Codex/Claude; its OpenCode profile/model helper rejects restart opt-in. The private lane must add only explicit native OpenCode dispatch while preserving ordinary default-off behavior and the already-approved native model `opencode-go/longcat-2.5-preview-free`.
- `test/live_provider/opencode_interaction_journey.py` builds the native OpenCode profile/agent and fixed-model command and runs the native question diagnostic. Its existing diagnostic builds a local test app and does not replace the production API worker or validate the production `/agui/v1/stream`; it is not acceptance evidence. The actual combined lane must use the production worker route and leave the provider/MCP invocation alive across its restart.
- `test/live_provider/interaction_api_worker.py` persists privacy-filtered create-group, ready-wait, and acknowledgement observations for the exact production invocation. The OpenCode acceptance must correlate those observations with the exact durable ledger rows and the three strict production snapshots; a missing observer item fails closed. The prior native question observer did not prove this combination, and no live OpenCode result is established by this plan.
- `test/live_provider/interaction_agui_restart.py` owns the existing bounded API worker lifecycle and snapshot parser; reuse it rather than adding a second supervisor. Retain the standalone 180-second journey and 25-second cleanup budgets. Do not call the generic 900/880-second controller path. The original OpenCode provider time limit remains unchanged.
- Immediately before owned cleanup, the existing same `OpenCodeCliProvider` receives a fresh backend history buffer through `get_status()`. Accept only `IDLE` or `COMPLETED`, before the existing absolute deadline. In tmux, this is the production provider's classification of the live pane buffer (`tmux_live_pane_via_production_provider`); do not replace it with harness text parsing or `_native_tui_stage`. Provider natural exit status is a separate nullable observation, never inferred as zero from the harness, acknowledgement, terminal removal, or cleanup. Owned cleanup and harness exit zero are independently required.

### Exact existing test impact

1. Change `test/live_provider/test_interaction_cli_journey.py::test_opencode_profile_model_is_implicit_and_not_used_for_agui_restart` to retain the ordinary implicit native selection and cover the explicit approved restart-lane model without a synthetic profile override.
2. Extend `test/live_provider/test_interaction_agui_restart.py::test_agui_restart_is_narrowly_opt_in_and_default_prompt_is_unchanged` and the existing model/receipt contract tests for explicit OpenCode opt-in while preserving default-off behavior and the exact native model receipt.
3. Extend `test/live_provider/test_opencode_native_mini_journey.py::test_real_production_source_awaits_opencode_initialize_and_builds_mini_command` or its focused neighboring contracts to compose the native provider/MCP path with the production API worker, strict snapshots, observer, and bounded cleanup; keep the diagnostic distinct from acceptance. The new `test_restart_barrier_survives_native_mcp_isolation_and_v2_writer` must cover default-off omission, overlay, isolation, v2 serialization, and harmless startup under the emitted `env -i` prefix.
4. Preserve `test/live_provider/test_provider_harness_upgrade.py::test_safe_receipt_keeps_only_the_approved_opencode_model_literal` and `::test_opencode_main_requires_cleanup_and_auth_for_success_exit`; update only the safe opt-in receipt assertions required for OpenCode's nullable natural exit status and SC-010 lifecycle fields.
5. Retain the strict snapshot allowlists, exact identity matching, observer privacy and wait/ack correlation in `test/live_provider/test_interaction_agui_restart.py`. `test/live_provider/test_message_api_restart.py` remains separate message evidence and cannot satisfy FR-019/FR-020 or SC-010.

### Bound private candidate evidence

The private implementation candidate is based on frozen R13 source manifest SHA-256 `23d1d27e10b8000d1d9960e7c89c8f3aceeadc0b69b23cef78e5380905d01b26` (3,153 entries). The final allowlist correction is limited to `CAO_MCP_RESTART_BARRIER_DIR` and the focused v2-writer/child-startup regression. Baseline R3 failure is reproduced at `_isolated_mcp_command` as `mcp_environment_not_allowlisted`; the corrected focused test passes, and four affected offline modules pass 228/228 with two existing Pydantic deprecation warnings. Black and isort pass. Receipt SHA-256 is `f3f754f1151fc7e521dbdf2cf9cdc0f2ac46c4080da1477b17ed34cc92734b67`; the full R13-to-private-candidate diff is `b43b47f5892398a3db7e46b893ec8e09242a43e3aafafe46204bb52ab2c0449d`. These are offline candidate results only: root/peer review, main integration, a new frozen source/runner binding, and the actual OpenCode acceptance are still pending.

## Private follow-up: portable stdio ownership cleanup helper

### Scope and source impact

Reuse the existing test-only ownership contract described by FR-010. The private MCP operator currently owns `NativeReceipt`, `OperatorFailure`, `receipt_rows`, `cleanup_owned`, fixed cleanup diagnostic stages/kinds, and the diagnostic projection. Extract those generic symbols into `test/live_provider/mcp_stdio_cleanup.py`; adapt the private `mcp_stdio_operator.py` to import them. Keep `read_result`, join validation, host/source/runtime pins, source binding, auth setup, stdio logging, and provider-specific orchestration in the private adapter.

The read-only Graphify query found the existing matrix module and its contract-test edges but no `cleanup_owned` node; its line locations are stale. Direct source review is authoritative for this delta: the production matrix's `_delete_terminal` remains strict (HTTP 200 required), while its completed-receipt path already verifies a non-null `cleanup_completed_at` stamp and does not delete that completed child again. The portable operator helper must skip only receipts normalized to `cleanup_completed=True`; a null stamp remains incomplete and eligible for deletion only after parent ownership validation. Do not change `_delete_terminal`, production API status semantics, receipt schemas, or any cleanup acceptance rule.

The R13 source manifest has 3,153 entries and does not contain the new portable helper or its companion test. Keep R13 pins and verification private and unchanged in this candidate; record the helper/test as unpinned candidate files. A future source revision must explicitly include and bind both files before host-adapter acceptance can claim they are source-pinned.

### Implementation and verification sequence

1. Extract only host-independent receipt/ownership-cleanup code and fixed cleanup diagnostics into the new portable helper, preserving all input checks, allowed receipt states, order, timeouts, request behavior, first-error diagnostics, and existing 25-second cleanup deadline.
2. Adapt the private host operator to import the exact helper symbols and remove the duplicate implementations. Keep all source/runtime/SDK verification and host configuration in the adapter.
3. Move or mirror the existing helper contract cases into the companion offline test module so it tests the extracted code itself, including the previously accepted completed-receipt skip regression. Add an import identity check proving that the adapter's exported symbols are the portable module objects.
4. Run the portable suite only, using the literal validation interpreter, `env -i`, private HOME/TMP/cache directories, usersite disabled, and explicit pytest plugins. Use a source-relative package import, an offline marker, and a deterministic fake clock for deadline cases while retaining the existing 25-second cleanup deadline. Verify no provider/API/PG/network behavior is reachable from the selected test module, and record exact commands/results and file hashes.
5. Format only the two newly created portable Python files. Review the candidate diff against the R2 operator baseline and confirm no pin, auth, provider, prompt, model, source-tree, or deadline behavior changed.

No live run, API call, source freeze, or main edit is part of this private extraction. Root review and a separate future source binding are required before any later acceptance.


## Addendum: exact OpenCode native deadline start point

The opt-in OpenCode AG-UI lane publishes its existing 75-second monotonic deadline from the `_create_and_send_native_terminal` phase callback for `native_send_input`. The callback runs only after `create_terminal(..., defer_init=False)` has completed provider initialization and immediately before the one normal `send_input` call. Initialization is therefore charged only to the unchanged outer 180-second journey bound. The 75 seconds cover the input call, bounded MCP response-barrier wait, question collection, answer/acknowledgement flow, and required final provider-status sample. The existing 25-second cleanup bound remains separate.

The deadline is initialized once and cannot be replaced or extended. A missing private barrier directory, an already-populated deadline, or a publication failure stops before input. A deadline absent after the native helper returns is an AG-UI failure; the harness must not create a replacement after sending. Ordinary native mode keeps its post-helper 75-second start point. Earlier references to an unchanged absolute journey deadline mean the fixed duration and outer 180/25-second windows, not that the two native modes share an identical inner-deadline start point.

The regression runs the existing native helper with an initialized fake terminal service, a test phase callback, and the real deadline publisher; source-contract checks and review verify the callback installed by the actual journey. It checks event order and send count, including initialization that outlasts a hypothetical stale pre-create deadline; failure cases prove missing barrier, an existing deadline, and marker-publication errors stop before input. It starts no provider, HTTP worker, database, PostgreSQL connection, or network request.


## Claude credential archive preservation

### Source-verified cleanup boundary

`test/live_provider/interaction_cli_journey.py::_base_cli_env` creates the isolated `root/claude-home` and copies the selected Claude credential file into that per-run home. The original configured profile is a read source for staging. `_remove_private_run_root` is the existing deletion boundary and already refuses removal unless the owned process is confirmed stopped and no cleanup failures remain. The archive must consume only the isolated per-run copy after the owner has verified process reaping and before this existing root-removal boundary deletes it.

### Integration design

- Keep credential preservation in one portable helper that accepts explicit run-root and archive-root inputs. The helper must contain no operator-specific paths, live-home defaults, provider profiles, runtime pins, or credentials. The host-bound R14 adapter must source-pin and import that helper and connect it to the existing journey cleanup boundary; it must not duplicate the archive/parser/cleanup behavior.
- Use a unique create-only archive entry outside the temporary run root. Validate private ownership, 0700 directories, 0600 regular credential file, stable source identity, source/destination path identity, and durable file plus directory writes before permitting the existing run-root deletion. Never overwrite an archive entry or copy from the original/staged profile.
- If stop proof, source identity, archive path, write, or sync is uncertain, add only a fixed safe cleanup category and leave the run root and its evidence intact through the existing failure path. Do not log credential bytes, provider text, auth paths, or stage contents. Keep auth staging, model/prompt/tool selection, provider commands, and all existing time bounds unchanged.
- Add a portable offline helper suite and a composition regression against the real `interaction_cli_journey._remove_private_run_root` path. Use synthetic run-root bytes and source/stage sentinels under temporary directories. Verify ordering, exact copy, permissions, cleanup success/failure, no overwrite, symlink/replacement refusal, and safe diagnostics. The test must not open the actual operator Claude credential file; use fake paths and prove the archive source resolves only under the synthetic per-run home.
- Run Graphify impact and verify its call-path findings against current source before selecting the host adapter import seam. The existing R14 source manifest does not bind this future helper/test/adapter change. A new source/runner freeze must bind the final files after the separate OpenCode AG-UI integration work is resolved; the existing R14 manifest must not be cited as covering them.

This change preserves the per-run credential state and protects the operator login source; it does not prove why a previous Claude login expired or guarantee that a preserved token remains valid. Offline verification is not a Claude provider acceptance, and live execution remains a separate root-approved gate.

## OpenCode observer integration boundary

Keep the observer in `test/live_provider/opencode_mcp_observer.py` and install it through the existing source-bound `sitecustomize.py` startup seam, wrapping `FastMCP.run` only for the opted-in bundled MCP entrypoint. Production `src/` must remain byte-identical to R14. Wrap the server's registered low-level list/call handlers, suppress internal list refresh with context-local state, and close the private audit around the original run. Require an integrity proof created only after successful audit closure; never infer closure from process termination. Read the safe summary after owned cleanup and project it through the existing inner/outer receipt allowlists. Preserve the existing AG-UI journey, restart, ACK, completion and timeout criteria.

Root rejected the earlier private production-instrumentation architecture; it is not integrated. The reviewed test-only successor and its tests must be ported to main and source-bound before a fresh diagnostic provider run.

## Addendum: outbound OpenCode model-tool offer observation

### Boundary and source basis

This is test-harness instrumentation for the separately gated OpenCode diagnostic. The v2.0.18 source at commit `cd9a14a` defines `SessionHttpRequest` with the resolved model reference, request kind, and web `Request` (`packages/plugin/src/promise/session.ts`, `SessionHttpRequest`); `session.hook` accepts the `http.request` callback and an optional provider filter (`packages/plugin/src/promise/registration.ts`). `SessionModelRequest` builds its `tools` array from the permission-filtered snapshot and shaped context, then invokes `session.http.request` immediately before the transport handler (`packages/core/src/session/model-request.ts`). The callback is observational and does not need to change the event or request.

The exact v2.0.18 plugin documentation discovers local plugins from the project `.opencode/plugins/` directory at startup (`packages/web/src/content/docs/plugins.mdx`). Place only the private source-pinned observer copy in the run-private OpenCode working directory there. Do not add an npm plugin, alter the model/profile/agent permissions/configuration, or create a proxy. The observer is opt-in with the OpenCode AG-UI run and a run-specific nonce. No plugin is installed globally.

### Request and evidence flow

1. Before native OpenCode starts, the private journey creates the existing working directory's private `.opencode/plugins/` directory and copies the pinned observer module with create-only semantics and mode 0600. The existing native model, profile, prompt, grants, `OPENCODE_CONFIG`, request command, and all deadlines remain unchanged; the only added child environment entry is the fresh observer nonce.
2. The plugin registers `session.hook("http.request", callback, { providerID: "opencode-go" })`. For every callback it records only a closed request kind and exact approved model-match boolean. It reads the body only for a matching primary request, from `request.clone()` through a byte-bounded reader. This is an observation of the SDK-built request at the pre-transport boundary; it does not prove the HTTP request was sent or received, or that the provider/model accepted the tools. It never reads response streams, headers, URL, prompt strings, tool schemas, or arguments.
3. A valid outgoing Chat Completions-style `tools` array is summarized in memory. Persist only array size, counts for the three exact granted names (`cao_mcp_server_cao_ask_user`, `cao_mcp_server_cao_wait_for_user_answers`, `cao_mcp_server_cao_acknowledge_user_answers`), a count of all other names, status, request kind, and the nonce. Unknown names are never retained. A valid empty array proves that this request offered zero tools; an absent `tools` field or unsupported shape is incomplete rather than inferred empty.
4. Before each hook callback resolves, it writes one nonce-bound request record using an exclusive pending file, bounded write, file `fsync`, successful close, no-overwrite atomic publication, and successful containing-directory `fsync`. The callback does not assign to the hook event or `Request`, and it returns normally after the record attempt. A clone/read/parse, size, registration, record-write, file/directory sync, close, publication, or invalid-nonce failure cannot establish a complete record; it does not suppress, rewrite, or retry the model request. The ledger is capped at 16 requests.
5. Plugin cleanup separately disposes the request hook, waits for all queued record writes, and then publishes a bounded global seal with successful file and directory sync. The seal reports hook-disposal, trace-integrity, count, and overflow facts only; it does not report CLI exit, provider response, pane completion, or journey success. A hard stop can leave a fully published request record without the seal. The reader may preserve that record as a per-request fact, but overall observer and offer status stays incomplete/unobserved. Invalid, partial, foreign-nonce, unexpected, or pending files; a missing seal; an ordinal gap/duplicate; overflow; or uncertain cleanup cannot produce aggregate completion.
6. The journey projects this evidence separately from `mcp_catalog_*`, `mcp_tool_calls`, and `observed_interaction_tools`. No catalog field, MCP readiness bit, configured grant, model metadata capability, or provider UI event may substitute for a real serialized request. A tool offer is not a tool invocation, provider acceptance, question, or AG-UI success.

### Offline verification

Use Node's built-in test runner for the plugin module and the existing Python receipt-projection tests for the final safe fields. The tests use local real `Request` objects and synthetic bodies only; after the hook returns, consume the original `Request` and assert its bytes are unchanged. Cover each supported tool name, unknown-name counting without value retention, valid empty arrays, missing/unsupported/malformed shapes, oversized/unreadable requests, non-target/auxiliary model requests, nonce mismatch, missing seal, interrupted write/sync/close, and overflow. A forced-termination fixture must kill an isolated local Node child only after the request callback has returned with its record durably published; the surviving request fact must project while the missing global seal keeps aggregate status incomplete. Sentinels in prompts, headers, authorization, descriptions, schemas, and arguments must not appear in serialized evidence. Do not spawn OpenCode, make network calls, start MCP/API/HTTP services, access PostgreSQL, or change any acceptance criterion. Root and independent review precede any main port or source freeze.

### Capability evidence limit

Root's separately executed bounded public metadata requests queried the exact v2.0.18 model-list endpoint and current model-list endpoint without credentials or inference. Both responses contained the target model but neither supplied `capabilities.tools`; their response-body SHA-256 values were equal. This establishes only that those public responses omit the field. It does not establish the pinned binary's tool capability, the request actually sent to a provider, or the tool list seen by the model. The source hook is the narrow direct observation seam; no inference/chat request, proxy, binary instrumentation, or model configuration change is proposed.


### Private implementation bounds

The OpenCode project-local plugin is installed into the run-private `.opencode/plugins/` directory with create-only semantics, byte-parity verification, and mode 0600. Its only added child environment value is a fresh 32-hex run nonce. It reads at most 1 MiB from a bounded in-memory request clone, stores no more than 16 per-request records of at most 4 KiB each, and writes one bounded global seal. Each publication requires a mode-0600 file with exact schema keys, the expected nonce, validated count/status enums, a synced and closed file, atomic no-overwrite publication, and a successful containing-directory sync. The reader accepts only owner-owned files under the run-private mode-0700 ledger directory. Unknown request kinds on the matching model remain incomplete rather than being treated as non-primary. A valid empty `tools` array is the sole zero-tools observation; absent, malformed, unsupported, oversized, unreadable, or unknown-kind input cannot establish zero.

Each record is individually evidentiary only after its full bounded write has been synced, closed successfully, atomically published without replacement, and its directory entry synced. The cleanup seal is separate: it is published after hook disposal and all queued record writes settle, and its fields cannot claim CLI exit or journey completion. A forced process stop after record publication may preserve that request fact while leaving aggregate observer and offer status incomplete because the global seal is absent. Observer incompleteness does not add a journey failure condition; the original acceptance criteria remain authoritative. This does not alter the outer 180-second journey and 25-second cleanup bounds or the native 75-second AG-UI budget.

### R2 lifecycle and durability clarification

R2 supersedes the aggregate-only persistence design above. Each callback publishes one nonce-bound per-request record under `.opencode/http-tool-offer-observation/` using an exclusive pending file, bounded write, file `fsync`, successful close, no-overwrite atomic publication, and successful directory `fsync`. The total is capped at 16 records; request bodies remain in-memory-only bounded clones. The callback returns the original hook event unchanged after its record attempt. A record is individually evidentiary only if the full record was synced, closed, published, and directory-synced.

The plugin cleanup callback separately disposes the request hook, waits for all record writes, and publishes `seal.json` with `hook_disposed`, `trace_complete`, record-count, and overflow facts. This seal describes observer lifecycle and ledger integrity only; it does not certify OpenCode CLI exit, provider response, pane completion, or journey success. The forced-termination fixture must stop a local child after its request record is published and before cleanup; the record remains an individual fact, but the missing seal keeps overall observer and offer status incomplete. A missing or malformed record, nonce mismatch, unexpected/pending file, ordinal gap/duplicate, overflow, write/file-sync/directory-sync/close/dispose error prevents aggregate completion. The reader performs bounded enumeration of the private run ledger and accepts no unchecked path or JSON field. Observer incompleteness does not add a journey failure condition; the existing journey criteria remain authoritative. This R2 contract replaces the earlier aggregate-only completion language in this plan.


## Phase 13: Correct the pinned OpenCode model-reference boundary and outer receipt forwarding

### Evidence and root cause

- The R16 safe projection contains two durable request records (one `primary`, one `title`); both have `model_match=false` and `status=model_mismatch`. No target-primary body was inspected, so the actual tool offer and the zero-question cause remain unknown.
- The R16 observer compares `event.model.modelID`. The pinned OpenCode v2.0.18 source at commit `cd9a14a` defines `Model.Ref` with `id`, `providerID`, and optional `variant` (`packages/schema/src/model.ts`); `SessionHttpRequest.model` is a `Model.Ref` (`packages/plugin/src/promise/session.ts`), and `SessionModelRequest` passes `model.ref` into the hook scope (`packages/core/src/session/model-request.ts`). Therefore the observer rejects the real contract before reading the bounded request clone. The existing offline fixtures repeat `modelID` and miss the source contract.
- The inner common provider projection already validates `opencode_http_tool_offer`, but the outer operator `_safe_journey_summary` uses an explicit field allowlist and drops that summary. The candidate must forward it through the canonical helper already bound by the selected-source manifest; a bare import from `test.live_provider` is not available in the operator's minimal `PYTHONPATH`.

### Minimal design

- Change only the JS classifier to compare `providerID` and `id` against the approved constants. Persist only `model_match`; do not persist model IDs or variants. The optional `variant` remains accepted by the pinned `Model.Ref` shape and does not change the configured exact provider/model ID match.
- Correct both Node and Python forced-child fixtures to use the pinned object shape. Add a red regression against the preserved R16 JS module before the change; then cover target ID with optional variant, wrong ID, wrong provider, and a `modelID`-only lookalike.
- In the operator runner, validate the selected source-manifest row and module origin, load the existing Python observer module from `SOURCE_ROOT/test/live_provider/opencode_http_tool_offer_observer.py` with the pinned-origin loading pattern, and call its `safe_receipt_projection`. Do not duplicate its schema or projection code. Missing/malformed evidence is passed through the canonical fail-closed projection (`incomplete`); a source-loader failure returns only fixed `unobserved` status fields. Neither outcome adds a journey gate.
- Add one representative outer projection test with only the operator runner directory on `PYTHONPATH`, plus focused absent/malformed cases. Preserve runner criteria, prompt, model, grants, input count, native deadline, outer and cleanup bounds, and all R16 artifacts.

### Validation order

1. TDD red: run the new target-shaped fixture against the immutable R16 observer and record the expected mismatch before changing code.
2. Apply the minimal JS/fixture and operator-projection edits to a new private candidate only.
3. Run the focused Node observer and Python receipt tests under isolated private `HOME`, `TMPDIR`, and bytecode cache. No OpenCode CLI, provider, API, MCP transport, network, or database process is part of this validation.
4. Verify that only the intended private source/test/runner files differ, inspect the full candidate diff, and bind the candidate's source-origin loader to the R16 selected manifest. The candidate is diagnostic-only and is not an actual-run authorization.

### Phase 13 candidate verification (2026-10-16)

- The pinned-R16 RED reproduced `model_mismatch` for the actual `Model.Ref` fixture before the JS change. The corrected Node observer suite passed 17 tests, including exact ID/provider matching, optional `variant`, wrong-provider/ID/modelID-only negatives, and no persisted model identifiers. The Python receipt suite passed 21 tests using the immutable R16 source tree for imports; the outer runner suite passed 27 tests and 8 subtests, including a runner-only `PYTHONPATH` subprocess that verified source origin and valid/missing/malformed safe projection behavior. The selected run-plan binding check passed with no provider/CLI/API/PG invocation; private bytecode-prefix listings were empty before and after.
- Targeted isort passed. Black check was run with one worker and a short private TMPDIR; it reports the two inherited operator files (`runner/run_acceptance.py` and `runner/tests/test_acceptance_runner.py`) as needing reformatting. The same two files also fail Black in the preserved R16 baseline, so no broad reformat was applied. This private candidate does not claim full integration or actual acceptance, and the original R16 source/run remain unchanged.


## Phase 14: Preserve canonical OpenCode MCP actions through the shared writer

### Root cause and source boundary

The preserved R17 `_agent_source()` used underscore-form `cao_mcp_server_...` grants, which do not match the canonical OpenCode v2.0.18 MCP actions. Correcting the profile names alone would still fail: the private R17 baseline and current main both bind `src/cli_agent_orchestrator/utils/opencode_v2.py` to SHA-256 `ba8b4841fec501f911bc8901d461501665cb73ed06867f9b42a67b70a71bc5a7`, and its `_normalize_mcp_actions()` used server and tool-suffix character classes that excluded `-`, rewriting a corrected `cao-mcp-server_...` grant before serialization. The pinned OpenCode v2.0.18 `McpTool.name()` preserves hyphens in both the server and raw tool name and joins those components with `_`; the permission filter therefore does not match the rewritten form. The error sits in the profile/writer boundary, not in the model request observer or MCP catalog.

### Minimal correction

1. Preserve `-` in the existing MCP server and tool-pattern sanitizers while retaining the prior handling of other unsupported characters, `*`/`?`, longest-server selection, non-MCP actions, and rule order.
2. Keep the AG-UI profile's wildcard deny and exact three canonical action grants. Keep `codemode: false` so those actions remain direct definitions; leave `execute` denied because enabling CodeMode also exposes its additional fetch capability.
3. Update existing writer/installer assertions, AG-UI profile generation and its effective-scope assertion, and the current catalog/observer allowlists and fixtures. Preserve prompt, model, credentials, input/restart behavior, budgets, cleanup, receipt schema, and acceptance criteria.
4. Keep all observer evidence supplementary: a configured grant or outbound offered tool is not an invocation, answer, acknowledgement, or successful journey.

### Private candidate evidence (2026-10-16)

The private candidate loaded the exact preserved R17 writer for its RED run; the source-explicit profile-contract test failed with zero effective canonical direct actions (exit 1). Its corrected writer/profile tests then passed: 28 focused Python tests and 17 Node tests, with Black and isort checks passing. The candidate was approved by root and an independent ECC reviewer. These results are private-candidate evidence only; they are not main verification, source-freeze evidence, or a live OpenCode acceptance. The accepted private source diff SHA-256 is `7cbe5a1d518f0e645444a7a3ec0b624cbee41e39cf7310fd877bc7ec6b6050de`. Its original documentation diff was based on an older feature-document baseline and is not the source of this main-port addendum.

### Main-port verification and remaining gate

The main port remains pending. Before porting, the guarded script must verify all ten source baselines against both current main and R17 and preserve each current main mode; it must create owner-only O_EXCL backups before any target write. The four documentation addenda are generated by appending to the current main documents, whose current bytes match R17 but differ from the older documentation baseline in the private candidate receipt. Do not replace existing Phase 13 or root status text, and leave all Phase 14 tasks open until main tests/review provide evidence.

After root ports the reviewed bytes, run the full affected Python modules and the existing Node observer tests with isolated HOME/TMP/cache and a direct source-origin/hash guard. Root will separately run the Node 22 check, style/composition checks, and decide source-freeze/live gates. The 91-case QEMU T097/T098 acceptance is unrelated to this config-writer path and remains bound to its immutable source; no QEMU or Docker execution is part of this Phase 14 preparation.


## Phase 15 plan

Status: design reviewed by root and independent ECC; main verification and live acceptance remain pending.

### Decision

Add one explicit recovery instruction to the existing restart-only prompt. When `cao_wait_for_user_answers` returns `state == "pending"` or a structured `error.code == "unavailable"`, repeat the same wait for the same returned `group_id` with `wait_seconds=20`, stopping on ready/terminal state or the already-existing 75-second provider-task budget. Keep ask exactly-once and ACK only after a ready wait returns the exact expected answers and receipts. Preserve the normal prompt byte-for-byte.

The `recover_api_restart=True` prompt path is used by opt-in Codex/Claude restart runs. OpenCode's native path calls the common prompt without that restart flag, so the ordinary OpenCode prompt remains unchanged. No provider profile, model, credentials, or tool permissions change.

### Source boundary and replay safety

The inspected R17 MCP mapper returns the bounded error code `unavailable` for `requests.ConnectionError`; the same code can also represent a generic unavailable request failure. The proposal therefore treats this as a safe same-group wait recovery signal, not proof that every `unavailable` code was caused by an API restart. Repetition remains under the existing provider-task budget and adds no per-retry count.

`cao_wait_for_user_answers` posts to the wait endpoint for the supplied group and does not create or mutate question content. The service computes each receipt deterministically from the capability digest, answer ID, question ID, and version. On the first ready wait, it changes a delivery from `accepted` to `returned_to_mcp` and appends the return event. A subsequent ready wait for the same group sees the already-returned delivery, skips that transition/event, and returns the same receipt and answer. Thus repeating the wait is safe both when the first request never reached the service and when the service committed but the response was lost. ACK remains a separate transition and must use the exact receipts actually returned to the provider.

The current acceptance prompt already allows repeating a wait while `state == "pending"`; this proposal adds the same-group recovery rule for a structured `unavailable` tool error, under the existing 75-second provider-task budget. It does not instruct retries for timeout, authorization, stale, expired, cancelled, or other error/state results. Other errors, terminal states, and expiry of the existing budget remain failures; no failed or partial run passes.

### Alternatives considered

- **Automatic HTTP retry in the MCP client/server:** rejected for this proposal. It changes production request behavior and could conceal the interruption boundary; the observed request already failed before service entry.
- **Wait for API readiness in the test harness before allowing the provider to wait:** rejected. That would move the provider's wait past the observed restart gap and would no longer test recovery from an interrupted wait call.
- **Repeat the ask:** rejected because the group already exists and ask replay is not needed to recover a failed wait.
- **Retry ACK:** rejected because no ACK is justified until the provider receives and observes the exact answer receipts.

### Validation scope after approval

1. Update only the restart prompt and its focused prompt assertion; assert the ordinary prompt remains unchanged.
2. Extend `test/integration/test_interaction_recovery.py::test_group_and_answer_receipts_replay_after_reopening_the_same_postgres_store`, which already checks stable ready answers/receipt and exact-receipt acknowledgement on replay. Add only the missing assertion that the repeated wait did not append another `interaction.answer_returned` event. Do not create a duplicate service-flow test. This existing service code should pass the new assertion; it is a contract confirmation, not an expected RED for a production defect.
3. Run only these affected tests, targeted formatting, and root source/diff review.
4. The prompt tests can run in an isolated offline environment. The replay test uses the existing PostgreSQL-backed integration fixture and requires a separate root-reviewed exact command before it starts. If focused validation and review pass, use a new source-bound operator and a separate root GO for one Codex restart run. Preserve all original question, acknowledgement, status, budget, and cleanup gates. No retry of a failed live run is implied.

### Non-goals

No production HTTP retry, retry framework, new endpoint, schema, tool, question, input, provider/model/auth change, deadline extension, readiness barrier, or acceptance-gate relaxation. The prompt may repeat the same wait after `unavailable` only while the original 75-second provider-task budget remains; other errors, terminal states, and budget expiry remain failures, never green results.


**Main integration (2026-10-16):** The accepted three-file delta is ported after root and independent ECC review. The ordinary prompt was compared byte-for-byte with R17 for ASCII and Unicode operation keys. Main module validation and live recovery are pending; no provider result is inferred from this port.


**Main verification update (2026-10-16):** The Phase 14 and Phase 15 source/test changes are integrated in main. The five OpenCode-related Python modules passed 239 tests, the archive module passed 22, and the remote response-loss, AG-UI restart, and PostgreSQL interaction replay modules passed 45 together; each run had zero skips. The Node 22.22.1 observer suite passed all 17 tests. Black/isort passed on 13 changed Python files, and project composition passed five contracts with zero broken. These checks do not establish live provider acceptance, a fresh Docker image, or a passing full backend suite. Final source/diff review and source binding remain pending.


## Phase 16 plan: validate opaque receipt encoding before comparison

### Reproduction and root cause

The current `AnswerAck` model accepts a receipt such as `"r" * 15 + "ñ"`. A direct source-level reproduction then passes that string to `hmac.compare_digest("A" * 43, receipt)` and raises the exact `TypeError: comparing strings with non-ASCII characters is not supported`. The accepted malformed value crosses the service boundary at `services/interactions/models.py::AnswerAck`; the API `_AckReceipt` and MCP `_InteractionAckInput` also constrain only length. `InteractionService.acknowledge_answers` compares the expected ASCII receipt with the caller value, then calls `receipt_hash`, which encodes the same value as ASCII. Valid receipts originate in `services/interactions/capability.py::answer_receipt` using URL-safe base64 and are ASCII.

This establishes a real defect capable of producing the reported exception. The preserved historical Claude `run-step` record does not identify the specific call that raised it, and the interaction API wrapper normally sanitizes service exceptions; therefore this phase does not claim retrospective proof that the old run failed in ACK. A fresh source-bound provider run is required to establish that boundary.

### Minimal implementation

1. Add explicit ASCII-only validation to the domain `AnswerAck`, MCP `_InteractionAckInput`, and MCP `_ReturnedInteractionAnswer` receipt fields. Keep each layer's existing length bounds. At the HTTP route, convert DTOs to `AnswerAck` inside a narrow `ValidationError` handler and return the fixed `InteractionInvalid` response; this avoids FastAPI's default validation detail reflecting the opaque receipt.
2. Add a service-level defensive check before opening the transaction. It raises the existing typed `InteractionInvalid` without comparing or hashing the malformed value, including for a forged/bypassed Pydantic object.
3. Leave Unicode prompt, option-label, and free-text-answer rules unchanged. Preserve the generated receipt format, exact identity comparison, event ordering, and idempotent acknowledgement behavior.
4. Keep custom errors fixed and value-free. Do not add receipt telemetry or include input values in application logs.

### Verification and composition boundary

Use test-first offline cases at the domain, HTTP, and MCP edges. The direct service defense must be tested with a forged `AnswerAck.model_construct(...)` and a transaction spy that fails if entered. Verify valid ASCII receipt shapes remain accepted and Unicode prompt/answer models remain valid. Then run the existing PostgreSQL acknowledgement/replay path and the affected API/MCP modules when the local resource gate permits. No provider, API worker, Docker, or QEMU process is part of these offline tests.

Graphify was queried read-only for the ACK/receipt call path, but the current generated snapshot returned unrelated broad nodes and no unique service/API/MCP path. This is a graph-coverage limit, not evidence that the path is absent. Direct source inspection confirmed the four DTO/service boundaries above. The fix adds no imports, modules, dependency edges, persistence fields, or schema changes, so the dirty generated graph will not be refreshed for this correction; source and executable tests remain the evidence.

### Alternatives considered

- **ASCII-encode arbitrary input and compare bytes:** rejected because invalid receipt text should be rejected as malformed, not transformed into a new token representation.
- **Catch `TypeError` around `compare_digest`:** rejected because it leaves an invalid value crossing all adapters and can still fail later in `receipt_hash`.
- **Restrict all interaction text to ASCII:** rejected because prompts and answer text are UTF-8 content, not opaque security tokens.

### Remaining live evidence

The offline regression closes this specific source defect only. It does not identify the historical Claude `run-step` call site, certify that an updated provider session is healthy, or close the source-bound Claude/OpenCode/Codex acceptance gates. Preserve old run evidence; after final source freeze, perform only fresh distinct runs and capture sanitized boundary evidence.

### Pre-endpoint ACK validation errors

ECC review found that `_AckReceipt` and `_AckBody` validation happens before `acknowledge_interaction_answers` runs. FastAPI's default 422 body can therefore reflect a too-short or too-long receipt, an incomplete receipt object, or an over-sized list before the endpoint's conversion-time `ValidationError` handler can sanitize it. The final HTTP boundary uses an `APIRoute` subclass on the interactions router to catch `RequestValidationError` only for `acknowledge_interaction_answers` and return the same fixed `_safe_problem(InteractionInvalid(...))` used inside the endpoint. This travels with the router when another app mounts it; the global `env_vars` 422 handler and unrelated route errors stay unchanged.

The regression runs against both the canonical `main.app` and a minimal FastAPI app that mounts the router directly. It covers short/long receipts, missing `question_id`, more than 20 receipts, no service call, no echoed sentinel, and the original FastAPI detail shape on a non-ACK route. This transport fix adds only imports from the existing FastAPI package; it does not change schema, persistence, or provider behavior.


## Phase 17 plan: fail closed on the outer launcher's first identity mismatch

### Reproduction and root cause

Before Phase 17, the portable outer controller's first `/proc` identity check validated UID, PID/PGID/session equality, and session separation, but omitted the direct-parent relation to the controller that just received the `Popen` object. Its cleanup branch for a missing launcher pin called `process.poll()` and returned; if that direct child remained live, the bounded supervisor could exit with incomplete cleanup while the exact child it started continued running. It correctly avoided process-group signalling and artifact deletion in this branch. Phase 17 adds the parent check and stops only the direct `Popen` child when the pin is unavailable.

### Minimal implementation

1. Require `launcher_identity.ppid == controller_pid` in the single first-pin predicate. Preserve one observation only and fail before reading stdout or constructing the selector.
2. On an unverified controller/launcher identity, poll the `Popen` child. If it is live, call `Popen.terminate()`, wait only within the existing cleanup deadline, and escalate to `Popen.kill()` only if the same `Popen` child remains live and time remains. Poll through the `Popen` object to confirm/reap its direct child. This fallback depends on the current CLI controller being the only waiter/reaper for that child; an embedding host with a concurrent reaper would need an atomic pidfd-based signal.
3. Do not retry `/proc` identity, call `killpg`, use `waitpid` for an unpinned PID, infer descendants from a later scan, or remove artifacts. Keep cleanup incomplete because the descendant tree was not identity-pinned.

### Verification boundary

Add fake-only tests before implementation for a readable first identity with the wrong parent, a live direct child with a missing first identity, and a live direct child when the controller identity is unverifiable. Keep the existing PID-reuse case, and prove that an already-exited Popen child does not cause a replacement at the same numeric PID to be touched. Run the focused controller tests, the full portable-controller module, targeted Black/isort, `git diff --check`, and project composition. No live launcher, provider, API, database, PostgreSQL, or network call is needed to validate this safety change.

Graphify's generated graph predates the current controller source and does not contain the portable supervisor node. The existing Spec 022 impact record captures its dynamic process boundary; current source inspection confirms the `Popen` → first pin → `_stop_owned_tree` path and its fake-only tests. Treat the graph gap as a limitation, not evidence that the code path is absent. No generated graph refresh is needed because the change adds no imports or dependency edges.
