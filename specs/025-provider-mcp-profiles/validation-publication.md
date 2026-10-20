# Publication verification and Python compatibility

Date: 2026-10-20. The user explicitly authorized commits and push of the accumulated work on main. Publication is distinct from final provider and scalability acceptance.

## Preserved failed attempts

The local Husky gate is `cao_mcp_apps/.husky/pre-push`. Its first step runs the normal project pytest selection with coverage; JIT and bundle-size checks follow only if Python succeeds. No hook was bypassed.

- Attempt 1 was interrupted after GitHub closed the waiting SSH connection: 370 tests passed before interruption. It is not a completed suite. Subsequent attempts used SSH keepalive.
- Attempt 2 completed with 1,540 passed, one failed, 36 skipped and 16,762 errors. Every error and the failure reported temporary-filesystem exhaustion. `/tmp` had free bytes but exhausted its inode quota. Only this attempt's verified owned pytest tree was removed; its JUnit and logs were retained.
- Attempt 3 used private Linux-disk temporaries but configured basetemp beside TMPDIR. The vault fixture correctly rejected that layout. It was interrupted for diagnosis after 6,478 passed, 64 failed and 12 skipped. Nesting basetemp under TMPDIR preserved the guard and passed both affected vault modules: 155 tests.
- Attempt 4 completed with 18,188 passed, 65 failed, 86 skipped and 71 successful subtests in 2,951.71 seconds. It used eight workers and the normal project selection and coverage. The initial private environment lacked the optional AG-UI and telemetry SDKs that CI installs with `uv sync --locked --all-extras --dev`. Fifty-eight AG-UI and three telemetry failures included missing-SDK exceptions or unavailable-feature responses; their corrected-environment rerun is separate evidence.

The four other attempt-4 failures were a keepalive SSH override without BatchMode, an ENOMEM during a journal-restart child's source import, an inherited Path.home descriptor assumption on Python 3.13, and the unchanged strict memory benchmark taking 1.573 seconds under eight-worker load. These observations do not establish one common cause. The benchmark passed a separate cold serial invocation with coverage; that does not erase its parallel failure or close Spec021/T081.

## T026: narrow compatibility correction

The runtime privacy test assumed `Path.__dict__["home"]` existed. Python 3.13 inherits that classmethod. Reading the descriptor with `inspect.getattr_static` supports both layouts without invoking it. The test still verifies the private runtime path, mode 0700, unchanged HOME and CODEX_HOME, and exact descriptor restoration. It additionally checks that restoration preserves whether Path originally owned the method or inherited it.

The existing failure is the RED proof. The complete three-test module passed on Python 3.12.13 and 3.13.15, with project coverage enabled. Black/isort and the scoped whitespace check passed. All 65 attempt-4 failures then passed a serial corrected-environment rerun with coverage in 83.39 seconds, including the unchanged strict benchmark and journal-restart subprocess. No production provider, authorization, persistence, timeout or resource-limit behavior changed. This does not retrospectively pass the failed parallel run.

An additional Claude review could not run because the session quota was exhausted; it is not reported as completed. A GPT-6 Luna Max reviewer, using the shared ECC guidance at commit b92f181b3b748d1abbc2afd94af6aa7d8c8fca29, independently approved the scoped test/documentation diff with no actionable findings. That review did not approve an unseen validation launcher or complete any pending gate.

## Evidence and remaining gate

Private logs and JUnit records are retained under `caos-git-publish-20261020` in the operator cache. Raw captures and credentials are not publication inputs. Corrected dependency installation uses the unchanged lockfile and all declared extras. The complete publication hook remains required; the 65 focused passes do not substitute for it.

A validation checkout may be created on the Linux disk from an exact committed tree to avoid relying on the OneDrive source-import boundary that reported ENOMEM. All accepted edits and commits remain in main in the principal directory. Such validation must preserve the actual Husky wrappers, relevant built-artifact checks and tool availability, and bind its tree and runtime inputs. It does not authorize model runs, QEMU reruns, historical-job reconciliation, credential changes or package publication.

T007/T022 native profile propagation, real provider acceptance, messaging/restart and global capacity/concurrency gates remain open.

## Subsequent publication diagnostics — 2026-10-20

Attempt 5 was terminated by a turn interruption at about 7%, before a completed suite or JUnit report. Its logs remain preserved. The next runner was detached and explicitly monitored so another turn change would not terminate its owned test process.

Attempt 6 used the exact committed tree `3e05cd471d7ae12ff9b6fd13e0cb0b3ee6af07b5` on Linux disk, the locked Python 3.13.15 environment with all extras, the original coverage/selection and serial scheduling. It reproduced `test_roundtrip_runs_under_one_second` at **1.0100495500082616 seconds**, against the unchanged strict one-second limit. One SIGINT to the verified owned pytest process requested normal reporting/cleanup after that failure. Its partial result was **12,724 passed, one failed, 65 skipped, 340 deselected and 71 successful subtests in 8,868.84 seconds**. This is not a completed suite and did not publish anything.

The runner retained the previous attempt number in its JUnit filename. The resulting attempt-6 XML was preserved byte-for-byte under both its original name and the corrected attempt-6 name; its timestamp, test count and duration identify the actual run. SHA-256: `7029b9c351f603bb0a3d50843d6102382acfc6bcae5e362022eddbc3939850ac`.

The unchanged isolated test subsequently passed once normally and once under cProfile. These are diagnostics, not a fix or replacement for the failed full-suite timer. Source review rejected attributing aggregate SQL-builder time to the timed operation: fixture setup is included in that profile and the existing bounded LRU already reuses statement text. No product SQL, authorization, audit commit, benchmark body, timer, threshold or GC setting was changed. See [the T081 diagnostic](../021-postgresql-persistence/publication-nfr-20261020.md).

The four preserved Apps bundles passed the JIT scan and size preflight. Another Claude diagnosis failed on session quota and is not recorded as completed. Luna's read-only review identified measurement limitations and did not approve a performance change. At this documentation freeze, the complete eight-worker comparison required by Spec021/T083 had not run on the next sealed tree; no pending gate is closed by this paragraph.

The next publication comparison preserves the normal hook marker exclusions (`e2e` and `integration`) with eight workers. It cannot be presented as unfiltered T083 acceptance, and T083/T076 remain open.

## Completed attempt 7 — normal pre-push selection

The sealed tree `d0dd2e3a0240049af5070a144c092e0c8156f15a` completed the normal eight-worker backend selection in 3,020.04 seconds: **18,263 passed, one failed, 83 skipped, 120 warnings and 71 successful subtests**. The sole failure remained `test_roundtrip_runs_under_one_second`, at **2.200422219990287 seconds** against `< 1.0`. Initial available memory was 8,270 MiB. The source, assertions, coverage options and project marker exclusions were unchanged. This is a completed publication-hook comparison, not unfiltered T083 or provider acceptance.

The displayed 87% coverage is rounded; the statement counts yield 86.913605%, which does not establish the separate global 87% floor. JUnit SHA-256: `0cbf7f0e8cebd38d3bf3e0191f4eaca19d7097271977cf2e1313b41fb87a87b7`. Husky rejected the actual push with exit 1 before JIT/size execution. No bypass, successful push, demonstrated performance cause or performance correction is recorded. The preserved serial and parallel failures remain open evidence; see [the updated NFR record](../021-postgresql-persistence/publication-nfr-20261020.md).
