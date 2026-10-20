# Source impact — T097 pidfd readiness test

## Verified graph

`test/security/test_work_bubblewrap_composition.py::test_wait_pidfd_readable_checks_ready_pidfd_with_zero_timeout`
→ `_composition_api()` (same file, lines 539–546)
→ import `cli_agent_orchestrator.services.work_bubblewrap_composition`
→ call `_wait_pidfd_readable(pidfd, timeout_seconds=0.0)` (production module, lines 1850–1866).

No production caller, schema, persistence, or process-management contract is being changed. The correction makes the test's readiness precondition explicit and makes its own child/fd ownership complete.

## Selection boundary

The exact node is present once at line 45 of `test/integration/t097/scratch-selectors.txt` (63 unique selectors). The runner partitions that manifest into 62 broker cases and one UID-1000 characterization; its overall guard remains 91. The plan adds no test node to `test_work_bubblewrap_composition.py`; any offline helper contract belongs in a separate file absent from the scratch manifest.

## Bound source facts

R6 source manifest SHA-256: `59d5701b9aa4811478def37af33f0b32375e6f4305499354d3b3f9b145c88176`.

- `test/security/test_work_bubblewrap_composition.py`: `0751a37740b26ed1bb0f376e229509b8dac76ca7b28d13778d27896581a18e75`
- `test/integration/t097/scratch-selectors.txt`: `3fa2a10acf09b993a33082120342a880b5b314b4ae1c3a156d2e326e4a5d740c`
- `.github/scripts/t097-qemu-acceptance.sh`: `d694c40092da1fc2fe3378a90bf2fb3375fde02fb4f9e3d04a6417c1a484eeac`
- `src/cli_agent_orchestrator/services/work_bubblewrap_composition.py`: `e500661f58fc86f9eadf01732e749b441c271cfec5308b1d994e8afd35b5ed8f`; source is unchanged and no product patch is proposed.

Graphify graph JSON is absent from the sealed source tree. The above direct edge list is verified against the actual test imports and function call; no structural graph refresh is needed for a test-only change.
