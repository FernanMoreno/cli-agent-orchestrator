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
