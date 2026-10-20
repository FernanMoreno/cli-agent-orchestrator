# Independent G04 / T057 final operations review

Verdict: PASS for the frozen operational diagnostic slice. No Critical/Important product findings remain. Product source was read-only; reviewer added only `test/services/test_integration_008_operations_review.py`.

## Independent proof

Fresh combined gate:

`.venv/bin/python -m pytest test/services/test_integration_008_operations.py test/services/test_integration_008_operations_review.py --no-cov -p no:cacheprovider -q`

**11 passed in17.57s**; `/tmp/cao-g04-operations-final.log`. Seven implementation cases include real configured RSA/JWT API authentication/owner/admin boundaries; four independent reviewer cases do not substitute proof factories for Work or remote identities.

The independent cases exercise:

1. Actual Work admission and dispatcher prepare create held scheduler capacity and active write-path reservations. Expire only the real attempt lease, then read as a verified READ owner: exact attempt/generation, retained holds, process unknown, release/reactivation false and actionable stop/cleanup reconciliation. Full private SQLite dump digest before/after is identical, not merely one checked row. Actual blocked-restore transition retains both holds; subsequent owner diagnostic remains non-mutating and offers GET only.
2. File-backed SQLite RemoteOperationStore and actual DurableRuntimeConnection/RuntimeRegistry: inactive is disconnected; exact activated instance/epoch is connected; reconnect with SAME incarnation but NEW epoch marks old channel identity_mismatch; closed active connection is disconnected. The real operation inspection route callable resolves the advertised exact operation without sending or exposing private request payload. Dispose/reopen SQLite with a new registry retains the disconnected node and unresolved operation unchanged.
3. 102 durable runtimes exercise bounded100-node pagination and exact next cursor, two remaining nodes and terminal cursorNone. Seven unresolved operations produce exact count7 and bounded sample5/truncation. Diagnostics never change their prepared states. Boolean/zero/over100 limits refuse; verified READ cannot access admin-wide inventory.
4. Actual approved scoped coordinator start and managed Work admission, with actual distinct Work job owner and scoped controller owner: job owner can inspect its Work but sees no foreign controller or control action; controller owner cannot read the foreign job. The published inspect action resolves through the actual Work router and repository. Whole SQLite digest is unchanged.

`project-composition-check "$(cat .ai/project-name)"`: **5 kept, 0 broken**, `/tmp/cao-g04-operations-composition.log`. Reviewer file Black check passes; scoped source diff check clean. Earlier individual corrected gates3passed and1passed are superseded by the combined11-case result.

## Source boundary review

- `services/work_projection.py:61`: verified principal before owner-only verified read snapshot; job and Work ownership evaluated together. Revoked history remains an audit read, not execution authority.
- `services/work_operations.py:project_operations`: stored recovery context controls execution_allowed; lease expiry never produces stop proof; active capacity/reservations and cleanup evidence remain descriptive, release_allowed/reactivation_allowed are literalFalse. No release, tick, provision, replay or cleanup mutation occurs.
- Controller projection additionally requires exact sealed `workflow_scoped_run.principal_id` and matching owner on coordinator/driver joins. Job ownership alone does not disclose a foreign controller. Retry links include exact current generation/step attempt/Work attempt fences and require admin; blocked restore yields no POST links. Diagnostic action links do not authorize execution.
- Actual existing `/workflows/runs/{run_id}/resume` (`api/main.py:8452`) preserves verified caller/WRITE-or-ADMIN and coordinator dispatch to existing sealed owner/current admission/active Work checks. Existing managed retry endpoint (`api/main.py:8706`) independently requires verified admin, current run generation, current exact failed managed step, Work identity and existing authorization. Diagnostics add neither mutation endpoints nor alternative admission.
- `runtime_channel/registry.py:observe_runtime` atomically checks active/closed and exact incarnation+connection epoch under registry lock. Registry absence never discards durable nodes or asserts process stopped.
- `services/work_operations.py:runtime_operations`: verified ADMIN required because runtime inventory has no owner column; bounded page limit100/cursor512; unresolved samples5/node, counts include retained old incarnations with their actual incarnation coordinates. SQL read transaction does not mutate the operation journal. DTO omits request/result/transcript/prompt material and explicitly prohibits automatic replay/protected Work support.
- `api/work_routes.py:352` preserves READ/WRITE/ADMIN plus verified job owner; `runtime_channel/server.py:395` preserves verified ADMIN. Existing operation inspection path is real and excludes stored secret payloads.
- Models are closed, additive DTOs; historical WorkView contract is unchanged.

## Verification limits and harness correction

No external runtime, provider or network cluster was launched; native Work/SQLite/registry/channel lifecycle and real authentication were exercised. Full existing workflow execution/managed retry acceptance is covered by previous C05/C07 and repository gates; this review verified action routing/retained fences without starting another worker.

Initial off-workspace test placement failed to load test/conftest isolation. Existing default store initialization verified installed schema, and scheduler configuration refused existing revision before admission/effects; reviewer moved the test into test/services to load the autouse per-test SQLite. Initial same-incarnation reconnect assertion incorrectly expected prepared→reconcile; native store correctly retains prepared while epoch observation is mismatched. Neither initial harness issue was claimed as product RED. Final11-case gate uses proper isolated fixture ownership throughout.

The previously accepted Beads review remains unchanged:13passed `/tmp/cao-c05-beads-progress-review-complete13.log`, report `/tmp/cao-c05-beads-progress-review.md`.
