# Python 3.10 plugin cancellation test compatibility — 2026-10-07

Bounded parent-authorized diagnosis of two actual matrix failures in `test/plugins/test_registry.py`: setup cancellation during repeated cleanup cancellation, and teardown cancellation between creating a child and its first step. **Production registry behavior was not modified.** No matrix-copy source, cancellation guard, task private field or timeout was changed.

## Root cause and red evidence

Focused execution under the exact Python 3.10 environment reproduced **two failed tests** (`/home/felni/cao015-final/plugin-cancellation-py310-red.log`). Both failed only because the `pytest.raises` regex expected an original cancellation message on the outer Task awaiter and received an empty string. Cleanup assertions were not reached because that assertion failed first.

Graphify query ran before editing (`plugin-cancellation-graph-query.log`); its structural context was verified against registry setup cleanup, shielded child ownership, repeated cancellation, subsequent plugin teardown and final caller cancellation rethrow. There are no `Task.uncancel()` or `.cancelling()` calls in the affected implementation.

An independent reproducer imports only stdlib asyncio: a coroutine raises `CancelledError('explicit-message')`, then another coroutine awaits it as a Task. Both the C and Python Task implementations produce an empty outer message on Python **3.10.20**, while both preserve `explicit-message` on **3.12.13**; every task remains cancelled. This isolates the first incorrect test boundary to the stdlib Task-to-awaiter message contract rather than registry cleanup. Reproducer and output are retained privately as `plugin-task-message-reproducer.py`, `plugin-task-message-py310.json` and `plugin-task-message-py312.json` under `/home/felni/cao015-final/`.

The [official Python asyncio Task.cancel documentation](https://docs.python.org/3.11/library/asyncio-task.html#asyncio.Task.cancel) records propagation of the cancellation message to the Task awaiter as a Python 3.11 change. The repository supports Python 3.10, so an unconditional outer-awaiter message assertion does not represent a portable product contract.

## Narrow test correction and proof

Each owned test now wraps the registry coroutine with a recording coroutine. It observes the original cancellation message immediately after the registry rethrows and before crossing the stdlib Task boundary, then immediately rethrows again. The tests assert the exact original message on every supported runtime, still require `CancelledError` at the outer awaiter, and explicitly require that outer Task to be cancelled. Setup cleanup's complete lifecycle, absent registration/dispatch state, teardown child-start/drain/later-plugin ordering and all-child-task completion assertions remain unchanged. This preserves message and cleanup proof without suppressing cancellation or weakening production behavior.

Validation, all exit 0:

- Entire registry test file, Python 3.10: **18 passed**, 1.67s (`plugin-registry-py310-green.log`).
- Entire registry test file, Python 3.12: **18 passed**, 1.51s (`plugin-registry-py312-green.log`).
- All plugin neighboring tests, Python 3.10: **110 passed**, 9.89s (`plugin-neighbors-py310-green.log`).
- Black/isort check-only and scoped Git whitespace checks passed; final test diff reviewed.

The owned test and this report are synchronized to candidate and original workspace. Parent owns copying test changes into matrix validation trees, final full-matrix reruns and exact staged selection. **Verdict: PASS for the focused compatibility correction; aggregate matrix approval remains separate.**
