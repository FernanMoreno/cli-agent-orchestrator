# T033 — refuse silently discarded native policy

## Reproduced boundary and correction

Claude's `native_agent` route deliberately bypasses CAO's full MCP serializer. An explicit `cao_tool_profile` was therefore discarded while the builder returned a native command. The new guard checks resolved mapping and Pydantic server entries by field presence, rejecting known, invalid and null selections before returning the command or generating MCP/prompt files. The fixed error neither echoes server names nor policy values. Native grants cannot bypass it.

The pre-correction selection reproduced **30 failures and six passing compatibility cases** in 31.72 seconds. Two initialization regressions used a fake backend that raised as soon as the unsupported command was sent, proving the failure reached that boundary without launching a real CLI. The corrected guard prevents both backend input and the monitor's input-sent notification. Existing shell-wait/settings initialization order remains unchanged; this is not a claim of rollback over all initialization side effects.

Unprofiled native wrappers, missing-CAO-profile fallback, native model handling and the full CAO serializer retain their existing routing. The guard does not inspect or rewrite the native-agent store or credential/login files. Tests used isolated configuration and mocked initialization dependencies.

## Executable verification

The three-module selection passed **243 tests, zero failures/errors/skips in 173.49 seconds**, including all **36 new cases**, the full `test_claude_code_unit.py` and `test_claude_code_coverage.py` modules. It used the unchanged project coverage/marker options, four workers with loadfile scheduling and private HOME/XDG/temporary directories. Initial available memory was **7,050 MiB**, above the unchanged 6,144 MiB floor. Coverage is scoped evidence and does not establish a global floor.

Codex's final test review replaced an instance-method monkeypatch with a context-managed mock so the shared monitor retains its original attribute ownership. Only that two-parameter test body and its standard-library import changed; an inverse-edit SHA-256 comparison proves every other test body and the product file remained identical to the 243-test run. A requested full repeat was refused before pytest by its unchanged 6,144 MiB memory preflight and remains recorded as such. The two changed unit cases were then verified serially, with fake backend/settings dependencies and unchanged project coverage/markers: **two passed in 30.73 seconds**, with 5,434 MiB initially available. This lightweight recheck launches no vendor CLI or MCP child and does not substitute for, lower the floor of, or resume either expanded selection. These are two repeated cases within the original 243, not 245 distinct tests.

Canonical mypy passed **404 source files**. Black/isort passed on the two changed Python files. Composition preserved **five contracts with zero broken**. The existing PostgreSQL container's exact identity/image and healthy state were observed during verification; automatic provisioning remained disabled. No database-dependent behavior is accepted by this provider-configuration correction.

Private evidence in the existing publication cache includes `t033-red`, `t033-contracts` and `t033-isolation-check` logs/JUnit/runner receipts, typing/style/composition observations, source hashes and Graphify's bounded AST receipt. Earlier failures are retained and not relabeled green. The deterministic private graph refresh captured the affected provider and guard-test structure, preserving the main graph and using no semantic-model tokens. The initial broad query was truncated and had stale provider locations; direct source inspection established the actual call path.

## Codex-only composition review

Changed boundary: resolved profile → routing choice → command builder → initialization/backend input. Neighbors reviewed include plugin MCP merging, Pydantic/mapping representation, full CAO protected-filter serialization, native grants/model selection and status notification. Tests cover negative policy selections, permissive grants, input immutability, artifact absence and the supported legacy/full-profile paths. The guard introduces no persistent state, transaction, migration, retry, timeout, message/event, cache or locking change. It runs once during configuration and scales with configured MCP entries; dispatch remains unchanged.

Real provider, authentication, terminal cleanup and native-store acceptance would require their separate gates. Mocked backend/settings handling is appropriate here because the intended guarantee is refusal before a command is sent. No new external wire contract or independently deployed consumer was introduced, so no new Pact contract is needed. This review was performed by Codex only; no peer consultation, delegated agent or real vendor CLI was used for T033.

Verdict: **PASS WITH RISKS** for T033's guard. T033 is complete. T032's deferred **16-module selection and two stdio cases were not resumed**. Full T007, native-policy support, expanded provider acceptance and the historical/global NFR/coverage gates remain open. The historical Claude 504 is not explained or closed by this correction.
