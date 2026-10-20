# T081 publication diagnostic — 2026-10-20

The serial publication attempt was blocked by the unchanged strict memory round-trip assertion. This pre-comparison snapshot records observations on committed source `3e05cd471d7ae12ff9b6fd13e0cb0b3ee6af07b5`; it does not claim a performance correction or aggregate acceptance.

## Reproduction

The normal Husky pre-push hook ran the original backend selection and coverage on Python 3.13.15, with locked development dependencies and all extras. Source and private temporaries were on Linux disk. The existing verified PostgreSQL v3 container was reused, with autostart disabled. The runner measured 6,470 MiB available before launching the serial suite and verified its source tree, hook wrappers and 27 runtime files.

The first failure was `test/services/test_memory_service_index_roundtrip.py::test_roundtrip_runs_under_one_second`: ten stores and the reader took **1.0100495500082616 seconds**; the assertion remains `< 1.0`. After one targeted SIGINT and normal pytest reporting, the incomplete result was **12,724 passed, one failed, 65 skipped, 340 deselected and 71 successful subtests in 8,868.84 seconds**. There was no successful push. The preserved JUnit SHA-256 is `7029b9c351f603bb0a3d50843d6102382acfc6bcae5e362022eddbc3939850ac`.

## Bounded diagnosis

Two fresh focused processes then ran the original test with project coverage: one ordinary run and one cProfile run, each passing. The second has profiling overhead; neither supplies a numerical passing body duration or resolves the loaded failure. Logs, receipts and profiles are private publication-cache evidence, separate from public source.

In the instrumented execution, the ten async audit wrappers accumulated 0.974 s, legacy-operation contexts 0.855 s, the 21 Work verifications 0.706 s, and the ten store bodies 0.117 s. These are nested cumulative profile times, not additive costs or accepted benchmark measurements. The whole-process profile also contains fixture migrations and catalog reads. Its 20 literal-statement builds cannot be attributed to the timer; current source already has a bounded LRU with 256-table/65,536-character guards. No builder optimization was ported on that evidence.

Source and independent Luna review confirm that the full-suite fixture reuses session-scoped targets and the private audit manager between tests. That is a measurement-context difference from the required fresh-process cohort, not a demonstrated explanation for the failure. A further Claude read-mode request failed on session quota and supplied no diagnosis. No credentials were changed.

The current Graphify query returned a truncated broad neighborhood; important boundaries were checked directly against source: `MemoryService._legacy_repository`, the private legacy-audit manager, `WorkRepository._verify`, and fresh `catalog_manifest` execution. The graph does not establish runtime cost or causality.

## Constraints and next comparison

FR-044 remains unchanged: fresh verification of all nine manifest families, 21 Work checks, 20 durable audit commits, the separate read-only initialization commit, exact values/digests, authorization, identity/history/error order and caller-owned transaction semantics. The benchmark body, timer, GC settings, one-second limit and coverage/selection are unchanged. Prior rejected T082/T084/T116/T118/T119 candidates remain rejected.

At this documentation freeze, the complete unfiltered eight-worker backend comparison specified by T083 had not run on the next sealed tree. It must retain every assertion and ordinary coverage option and report failures/skips honestly. A passing comparison would not retrospectively pass this serial diagnostic, prove its cause, replace three independent cold covered runs, or close T076/T081/T083 by itself. No QEMU, historical reconciliation or live provider job is authorized by this diagnostic.

The next publication attempt uses eight workers with the normal hook selection, which excludes `e2e` and `integration` through project addopts. It is a controlled comparison of the publication gate, not the unfiltered T083 run. These are distinct requirements; T083/T076 remain open even if that hook passes.

## Completed normal-hook comparison: attempt 7

The next sealed tree was `d0dd2e3a0240049af5070a144c092e0c8156f15a`. Its normal pre-push backend selection completed with eight workers and `loadfile` scheduling, unchanged project marker exclusions and coverage, the same locked all-extras environment and the same verified PostgreSQL container. Initial available memory was 8,270 MiB. This is not the unfiltered T083 selection.

The result was **18,263 passed, one failed, 83 skipped, 120 warnings and 71 successful subtests in 3,020.04 seconds**. The sole failure was the unchanged round-trip assertion at **2.200422219990287 seconds**, against `< 1.0`. The source did not change during execution. This reproduces the loaded failure; it does not identify its cause. The whole suite completed without a diagnostic interrupt.

The coverage table displayed 87% after rounding: 79,021 statements and 10,341 misses give **86.913605%** statement coverage. That rounded display must not be used as evidence that the separate 87% global floor passed. The normal hook does not enforce that global floor. T076/T081/T083 remain open.

The preserved JUnit SHA-256 is `0cbf7f0e8cebd38d3bf3e0191f4eaca19d7097271977cf2e1313b41fb87a87b7`. The actual Git push returned 1 because Husky's Python step failed; JIT and size checks were not reached in that hook. Their prior separate preflight passes remain separate evidence. No hook was bypassed and no successful push occurred. There is no new performance correction that would justify repeating this unchanged publication attempt.
