# Managed server profile isolation — 2026-10-07

The five full Python diagnostic runs found the same real-HTTP admission
fixture failure. Registered-project escape rejection returned the expected
400 responses and created no sessions or tmux server, but the fixture's
reported database path did not exist.

`_subprocess_env` inherited the runner's explicit `CAO_HOME_DIR` while
replacing `HOME`. Production constants correctly prioritize `CAO_HOME_DIR`,
so the child initialized the runner profile rather than its managed profile.
The first incorrect boundary was the fixture environment, not production
profile selection or the exclusive-owner guard.

## Regression and correction

A real child process imports production constants and reports `DB_DIR`.
Before correction, the inherited-profile case failed at the expected path
assertion; the explicit-override control passed (one failure, one pass).
The fixture now sets `CAO_HOME_DIR` to its own HOME-derived managed profile
alongside `HOME`, then applies explicit extra overrides as before.

Both child-process regression cases passed after correction. Together with
`test_registered_project_admission_rejects_escape_over_real_http`, all three
passed in 3.47 seconds with an explicit runner profile in the parent.
The real HTTP test retained its path rejection, empty-session, absent-tmux,
and database-initialization assertions. Production sources are unchanged. An independent review found no blocker
and reran both child-process cases successfully in Python 3.12 (0.66 seconds).

The five original whole runs continue unchanged as diagnostic evidence.
Their failed results cannot satisfy the final matrix; full acceptance will
use fresh reviewed snapshots including this regression. Provider runs remain
evidence for the same production bytes, while their recorded source manifest
predates this fixture-only correction.
