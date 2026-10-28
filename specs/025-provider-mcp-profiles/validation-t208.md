# T208 · native guard shutdown transitions

Status: diagnostic implementation/offline controls reviewed; current r2 native
diagnostic COMPLETE. The shutdown acceptance itself FAILED. Production guard behavior/defaults are unchanged. T207r1 FAILED
and full T197 remains open.

## Boundary and observation contract

T207r1 records frames and initial lifespan entry, but no shutdown EOF, stop
callback or exit request. Its worker only checks retirement if an outcome exists;
therefore its missing outcome provides no earlier retirement time. It cannot
identify signal death or guard deadline. Do not infer a cause from that record.

`test/live_provider/physical_eof_observer.py` adds a diagnostic-only context
around the actual guard instance `input_eof`/`request_stop` and process-local
`os._exit`. The original operation/exception and exit function are preserved.
Fixed Pydantic schemas whitelist events, status, clock and counter state, never
packet content/IDs or credentials. Ordinary journal/snapshot locks are
nonblocking. One reserved fatal row uses raw append without locks, snapshot or
model validation and always delegates to the real exit. Private0600/no-follow,
128rows/128KiB bounds, write failures and original-hook restoration are tested.

The private worker polls UID/birth-bound process state independently of an
outcome and CLI success. It records last-running, first-absence and leader-zombie
observations, including the actual kernel wait-status field when available.
Nonzero CLI status continues polling; cutoff saves state even without retirement.
Optional phase writes cannot alter the original guard behavior. Actual time
namespace equality is checked before starting the CLI; observation clocks are
monotonic. This introduces no proxy, synthetic EOF, model turn or persistent
worker and changes no production authority.

## Validation and review

- Missing-module RED and content-echo reader-error RED are preserved privately.
  The strict reader now raises a fixed content-free error without exception
  chaining, rather than exposing invalid input through Pydantic diagnostics.
- Final selection:24PASS in81.45s (15new controls plus9existing lifecycle).
  Includes actual pipe EOF, original-exception identity, lock contention, failed
  writes/row limits/privacy, all three real process exit70 paths, actual OS
  SIGINT and physical EOF under the unchanged6s in-loop fallback, and a real
  owned child alive→leaderZ/kernel7→waitpid→absence sequence. The fallback test
  disables the OS timer only in its isolated control to exercise the independent
  watchdog; production/native criteria remain unchanged.
- Mypy:one helper source PASS. Architecture:5kept/0broken. Black/isort and final
  diff whitespace checks pass. No HTTP/schema/database change or provider
  inference is exercised; real Linux pipes/processes/signals establish this
  diagnostic boundary, so database and consumer-provider mocks are unnecessary.
- Read-only Claude review2e7cbb54/80b67187/916c161a:final GO after failure-path
  persistence/zombie/optional-write corrections. Root checked actual results.
- Preflight executes actual descriptor imports, real builder/guard env,
  two control-pipe observations and exclusive-writer/strict-reader pairs,
  normal-observer signature and actual identity adapter on own PID/birth mismatch.
  No native CLI, services or operator auth is started by preflight.

## Interpretation limits and next gate

Native r2 must be committed-source-bound with original shared6s/180s/floor6144,
actual namespace/credential masks and positive owned retirement. Require actual
EOF, SDK normal seal, CLI0, identity absence and valid transition journal for
any scoped positive result. Preserve failed r1 and executed archives separately.

A leaderZ observation is not proof that every thread exited. Polling timestamps
bound observations to about20ms, not exact death time. A zero kernel status may
be access-masked in another context; do not label it witnessed exit0 without an
in-sandbox access control. Nonzero observed kernel status is useful evidence,
but never identifies the signal sender. Missing rows remain unobserved. After
observer teardown, the interpreter/main tail is outside the hook; actual parent
identity retirement still gates acceptance. Full T197/default activation stays
separate from this zero-turn diagnosis.

## Native r2 · diagnostic complete, shutdown FAILED

[Structured result](validation-t208-native-r2.json): source
`14435b42524093d1105e3dcaa54f9d95cdcc233a`,535pins unchanged,
41.029s/min10580MiB. Zero model turns/tool calls/operator-login copies/API/PG;
actual namespace/masks and shared time namespace passed. SIGINT callback at
+1.819ms, SIGTERM at+102.269ms, both with zero pending RPCs/tails/unflushed
replies/output activity and no unsafe code. No physical EOF or guard exit
request is recorded. LeaderZ is observed at+509.128ms with actual nonzero kernel
waitstatus9, derived signal-9; identity absent at+529.597ms. Native CLI exits0.
Parent cleanup starts only after+6.22s, excluding that cleanup as the earlier
SIGKILL source. The signal sender itself remains unobserved.

The observed abrupt kill before physical EOF prevents the normal SDK seal;
it is not the guard's six-second exit70 path. Quiescence alone did not close
the client's still-open input resource. Both owned group and exact MCP identity
are positively retired; execution archive/sources are intact and operational
controller is reclosed. Do not repeat the identical physical-EOF lane. T209
will validate a distinct, real local-resource stop on a quiescent received stop
signal, without returning fake EOF or weakening T207's physical-EOF predicate.
Full T197 remains unaccepted until its original journey passes.
