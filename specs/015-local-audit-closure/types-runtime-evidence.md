# T057 runtime typing closure evidence

## Scope and first incorrect boundaries

Root expanded DATA ownership to services/terminal_service.py, workflow_service.py,
script_runner.py, turn_recovery_service.py, workflow_journal.py, then authorized
remote_terminal_service.py. Initial focused checker with existing project config
reproduced **51 errors in five files** after upstream decorator/model defaults
had already reduced the historical inventory. Source confirms dynamic function
hooks, dynamically attached record fields, untyped drive wrappers/remote replies,
Optional lifecycle IDs/providers, and mapping-shaped subprocess kwargs as roots.

## Corrections and compatibility

- Callable Protocols represent YAML attempt/replay and script recorder hooks.
  The runtime objects remain the original functions; hooks and closure state
  retain exact behavior. Targeted Protocol casts bridge Python's dynamic function
  attributes after all hook values are assigned, without casting to Any.
- RunRecord / ScriptRunRecord explicitly declare existing scoped plan owner and
  authenticated Principal fields, with None defaults and repr=False. Script
  records declare scoped_private_path and optional integer continuation_epoch.
  TYPE_CHECKING imports avoid script/workflow/security dependency cycles.
- Script drive wrapper returns WorkflowRunResult. TypedDict subprocess options
  retain the original conditional omission of pass_fds. Journal results declare
  SQL integer attempts and JSON object contracts; no SQL query text changes.
- Existing caller/PIPE/provider/result invariants are narrowed locally. Optional
  provider implementation attributes retain dynamic getattr/setattr behavior.
  Remote result fields use explicit expected scalar wire types, and public
  projection/session helpers reflect their actual optional/map/list returns.
- SQLAlchemy delete uses the mapped class as its typed target. Compiled SQL and
  bound parameters are verified identical to original table-target delete for
  remote_terminal_placements and terminals. Transactions and identity predicates
  remain unchanged.

## Verification

All commands TMPDIR=/home/felni/tmp-cao015-types-data. Focused final mypy:

`.venv/bin/mypy --follow-imports=silent --cache-dir=/home/felni/tmp-cao015-types-data/runtime-mypy-cache` plus six owned runtime files:
**exit 0, no issues in 6 source files**.

`project-composition-check "$(cat .ai/project-name)"`: **exit 0, PASS**.

isort/black six source files and `git diff --check`: **pass**. Scoped diff reviewed;
large inherited implementation changes remain preserved.

Regression command:

`.venv/bin/pytest --no-cov -q test/services/test_workflow_service.py test/services/test_script_runner.py test/services/test_turn_recovery.py test/services/test_workflow_journal_txn.py test/services/test_workflow_journal_resume.py test/services/test_workflow_journal_list.py test/services/test_workflow_journal_events.py test/services/test_workflow_journal_connection_posture.py test/services/test_step_contract.py test/services/test_workflow_step_replay.py test/services/test_terminal_service.py test/runtime_channel/test_remote_services.py test/runtime_channel/test_remote_publication_race.py`

## Composition review

Reviewed workflow engine -> recorder callback -> durable contract gate -> delivery;
script record -> subprocess FD barrier -> continuation ownership -> exit handling;
remote placement -> projection -> identity-bound deletion; and cancellation reset
-> provider initialization -> durable receipt release. Existing strong contract
hooks remain fail-closed while legacy journal writes retain best-effort behavior.
Receipt delivery ordering, stop acknowledgement, cancellation shielding,
prelaunch thread offload, session incarnation fencing, and SQL delete transactions
retain their implementation. Real SQLite and disposable script subprocesses are
used by the existing suites; external live-provider testing is not required for
these internal type-contract corrections. Root performs global integration and
final whole-repository mypy validation after all domain owners finish.

## Regression result and fixture correction

Initial broad run: **347 passed, 1 failed**, three warnings, 226.11 seconds.
The only failure was `test_script_runner.py::test_terminal_recorder_none_for_non_script_record`.
Its YAML WorkflowSpec fixture supplied an obsolete `version: "1"` key; current
preexisting strict `WorkflowSpec.model_config.extra="forbid"` rejects that key.
An isolated invocation reproduced the exact Pydantic extra_forbidden failure
before editing. Removed only that invalid fixture key, retaining the original
YAML-versus-script recorder assertion and all inherited unrelated test edits.
No production schema was relaxed.

Green regression: `.venv/bin/pytest --no-cov -q test/services/test_script_runner.py`:
**exit 0, 56 passed**, two warnings, 40.62 seconds. This reruns the whole affected
script suite, including the corrected fixture. The 347 previously passing tests
require no repeated broad run because the sole later change was this test data.

Focused typing/composition and regression verdict: **PASS**.
No commit/push or routine durable-vault save performed.

## Independent review: remote projection disappearance (resolved)

Review identified a real gap in the initial typing patch: casts/assertions assumed
that a positive remote placement or session inventory read guaranteed the next
projection still existed. Those reads are separate database snapshots; teardown
can delete the placement between them. Static casts concealed that Optional
boundary, and get_turn's assertion failed during a legitimate disappearance.

TDD reproduction in test/services/test_remote_projection_disappearance.py kept
root's four existing cases and added turn-read, vanished inventory, entirely
vanished projection, and partly vanished projection cases. Before the fix:
**5 failed, 3 passed** (terminal read returned None, turn read raised AssertionError,
and sessions dereferenced/iterated missing values).

Corrections now explicitly narrow each fresh read:

- get_terminal projection None -> existing local-style ValueError "not found";
- get_turn projection None -> existing local-style ValueError "terminal not found";
- sessions skips vanished inventory/projections, omits sessions with no surviving
  projections, and computes existing active/detached status from the survivors.

Removed the unchecked Optional projection/session casts and assertion. This is an
explicit race-handling behavior correction, preserving incarnation/ownership
validation and retaining live terminals when siblings disappear.

Fresh verification:

- `.venv/bin/pytest --no-cov -q test/services/test_remote_projection_disappearance.py test/services/test_turn_recovery.py test/services/test_terminal_service.py test/runtime_channel/test_remote_services.py test/runtime_channel/test_remote_publication_race.py`: **exit 0, 60 passed**, two deprecation warnings, 42.08 seconds.
- Focused six-file mypy: **exit 0, no issues in 6 source files**.
- `project-composition-check "$(cat .ai/project-name)"`: **exit 0, PASS**.
- isort/black affected source/tests and final `git diff --check`: **PASS**.

Composition review specifically covers stale inventory -> fresh projection,
partial deletion -> surviving session, and disappearance -> public not-found
error. Regression mocks force the timing boundary deterministically; existing
paired remote integration/publication suites exercise real SQLite transactions.
Final review resolution verdict: **PASS**; whole-repository gates remain root-owned.
