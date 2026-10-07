# Strict tmux shutdown fixture evidence — 2026-10-07

Parent-authorized bounded diagnosis of the Python 3.11 matrix failure in `test/clients/test_tmux_session_exists_strict.py::TestConfirmedAnswers::test_server_shut_down_under_us_is_a_confirmed_absence`. No production code, strict lookup classification, timeout policy or matrix-copy source was modified.

The archived full-run traceback (`/home/felni/cao015-final-python/py311-baseline-pytest.log`) shows that the fixture's PID-absence wait passed, but its immediately following strict socket lookup returned `TmuxLookupError` for `list-sessions` exit 1 / `server exited unexpectedly`. The isolated original test passed, and the entire original strict-lookup file passed 27 tests. Candidate and archived fixture bytes matched before editing. This establishes a timing-sensitive fixture boundary, not a confirmed failure of the strict classifier.

Graphify query ran before mutation (`tmux-shutdown-graph-query.log`), and its structural context was checked against `_classify_session_lookup`, `_tmux_server_liveness`, the error-marker tables and actual fixture lifecycle. An unexpected server exit is UNKNOWN in the product. Broadening its classification to confirmed absence could hide live authority and was not done.

Real, bounded diagnostic runs used only disposable short `/tmp/cao-t4-*` socket paths, killed their own servers and removed their own directories. An initial 100-round native run observed confirmed absence after PID disappearance in every round. A further 300-round run with the exact Python 3.11 environment captured the shutdown boundary: **281/300 immediate post-kill queries reported `server exited unexpectedly`**, while all 300 subsequent post-PID-wait strict queries returned false. Private records: `tmux-shutdown-reproduction.json` and `tmux-shutdown-boundary-reproduction.json` under `/home/felni/cao015-final/`. The rarer archived PID-wait-then-unknown scheduling was not reproduced in these 400 rounds; its lower-level kernel/tmux interleaving is not claimed as independently proven.

The first incorrect test boundary is using PID disappearance as sufficient evidence that its separate socket query has settled. The narrow fixture correction retains the existing **five-second** wait and now requires both the exact owned PID to be absent and the strict socket query to return false. `TmuxLookupError` means the fixture is still waiting; a persistent unknown verdict fails that unchanged bound. The final independent strict-false assertion remains. Live, unlinked and unanswerable lookup cases are unchanged. No extra sleep, timeout increase or production unknown-to-false fallback was introduced.

Validation of the corrected candidate:

- Python 3.11 full strict-lookup file: **27 passed**, exit 0, 3.28s (`tmux-strict-py311-green.log`).
- Python 3.12 full strict-lookup file: **27 passed**, exit 0, 3.28s (`tmux-strict-py312-green.log`).
- Twenty additional consecutive executions of the corrected real private-socket shutdown scenario passed (`tmux-shutdown-20-repeat.log`).
- Black and isort check-only and scoped Git whitespace check passed.

The owned fixture is synchronized to candidate and original workspace. Parent owns copying it to matrix validation trees, final aggregate reruns and exact staged selection. These focused results do not replace the full matrix.
