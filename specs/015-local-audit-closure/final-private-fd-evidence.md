# Private credential FD observation regression (T087)

The historical Python 3.13 diagnostic reached a live child whose first
`/proc/<pid>/environ` read returned zero bytes. The test raised before signalling
its attachment barrier; the runner reported that observation failure as a spawn
failure. Increasing the outer startup timeout would not correct that boundary.

Only [the continuation review test](../../test/services/test_integration_008_continuation_review.py)
changes. Its environment observer waits at most one second, polling every 10 ms,
only while the live child's environment is empty. A nonempty environment must
contain the private FD marker and must exclude the raw run credential. An exited
or disappeared child fails immediately; a permanently empty environment fails
at the deadline. Diagnostics do not include environment contents or stderr.

The real workflow test now runs both normally and with two initial empty reads
injected at the `/proc` observation boundary before returning actual child
environment bytes. Both cases retain the existing assertions: allocation is
recorded before attachment, no Work binding exists before attachment, the
credential barrier remains closed until verification and persisted attachment,
and the owned process is reaped afterward. No production source, outer three
second startup bound, or four second attachment release bound changes.

Four additional real child cases reject permanently empty observations, missing
FD markers, leaked synthetic credentials, and already exited children. Children
are terminated and waited for in `finally`; the temporary test resources are
removed after verification.

## Executed verification

- Python 3.12.13 baseline with the transient observation regression: **1 failed,
  1 passed**, 37.61 seconds. The failure reproduced the original first wrong
  boundary: live child, `returncode=None`, zero environment bytes; the outer
  `entered.wait(3)` then failed.
- After the test fixture correction, the entire continuation review module:
  **15 passed**, 41.09 seconds, exit 0. Five existing dependency deprecation
  warnings were reported.
- After replacing sensitive assertion expressions with explicit generic raises,
  a second full-module run reported **14 passed, 1 failed**, 67.20 seconds. All
  six private-FD cases passed. The unchanged neighbor
  `test_actual_native_accepted_history_survives_revoke_without_next_effect[grant]`
  returned a failed initial controller drive instead of running. This failed
  whole-module result is retained as a separate validation limitation and is
  not reported as acceptance.
- The unchanged failed neighbor then passed in isolation: **1 passed**, 19.74
  seconds, exit 0. Its intermittent initial-drive failure remains for the whole
  matrix to diagnose if it recurs; a focused pass does not erase the failed run.
- Black and isort formatted the changed test module.

The change adds five selected cases: one additional real workflow parameter and
four refusal controls. This targeted result does not replace the required whole
Python version matrix. Fresh Python 3.10–3.14 acceptance remains a separate gate.

## Selección completa final

Las cinco versiones Python completaron la selección de 16704 casos con cero fallos/errores; los casos reales de observación del FD y sus vecinos de autorización pasan en estas ejecuciones. Los posthash de 1361 registros no muestran diferencias. El fallo aislado histórico se conserva como diagnóstico previo, sin atribuirle una causa no demostrada; la evidencia completa final supera esas aserciones.
