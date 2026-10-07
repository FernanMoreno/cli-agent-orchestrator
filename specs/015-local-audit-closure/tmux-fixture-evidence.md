# T057 tmux send-keys fixture repair

Scope: test/clients/test_tmux_send_keys.py only; no production behavior changes.
Inspected git status before editing (test file initially clean; production tmux
client/transport retain existing dirty/untracked work), then traced the current
send_keys/version-probe subprocess boundary against source.

## Root cause and red proof

The test fixture patched clients.tmux.subprocess as a module, but production
send_keys now calls clients.tmux_transport.run_tmux, whose subprocess module is
looked up in clients.tmux_transport. The obsolete mock therefore did not intercept
load-buffer/paste-buffer calls; the unit test invoked real tmux. Its constructor
fixture also patched clients.tmux.libtmux while TmuxClient now constructs the
imported BoundedTmuxServer directly.

Before editing:

`TMPDIR=/home/felni/tmp-cao015-types-data .venv/bin/pytest --no-cov -q test/clients/test_tmux_send_keys.py`

**37 failed**, 20.90 seconds. Trace confirms the first paste failure goes through
clients.tmux_transport.run_tmux into real subprocess.run.

## Repair

- Patch the actual BoundedTmuxServer constructor used by TmuxClient.
- Patch clients.tmux_transport.subprocess to intercept the real subprocess seam.
- Keep all existing command, payload, ordering/count, paste-mode, Enter, unique
  buffer, cleanup-on-error, version parsing/cache and log-redaction assertions.
- A strict expected-call helper adds COMMAND_TIMEOUT_SECONDS to expected
  subprocess kwargs, preserving exact call comparison at the current bounded
  transport boundary. Version-probe expectation also checks that timeout.
- Existing cleanup assertions now include production's capture_output=True;
  check=False and the exact delete-buffer command remain asserted.

The fixture repair preserves transport execution instead of mocking run_tmux
itself, so the unit tests continue to inspect actual subprocess arguments.
No payload assertions were removed or relaxed.

## Green proof and review

Same focused command after repair: **exit 0, 37 passed**, 22.82 seconds.

isort/black test file: pass (black unchanged). `git diff --check` scoped to the
file: pass. Final diff reviewed; only mock boundaries and strict expected bounded
command kwargs changed. No source edits, commits, pushes, or unrelated test edits.

This is an ordinary fixture repair; global composition/full-suite checks remain
root-owned. Final verdict: **PASS**.

## Working-directory provider fixture (additional full-CI finding)

Root's running full-CI discovery recorded five failures in
`test/providers/test_tmux_working_directory.py` at 73%, logged in
`/home/felni/tmp-cao015-root/pytest-ci-complete.log`:

- successful pane working-directory retrieval;
- create_session with explicit working directory;
- create_session default working directory;
- create_session tilde expansion;
- create_window with explicit working directory.

The autouse fixture patched `clients.tmux.libtmux.Server`, but TmuxClient now
constructs the locally imported `BoundedTmuxServer`. Unlike the six previously
reviewed harmless fixtures, this fixture relied entirely on that constructor
mock. Trace shows its session creation reached the real transport rather than
`self.mock_server`.

Inspected git status (target test initially clean), source constructor and all
cwd/session/window assertions. Root authorized this exact test repair after
those failing full-CI nodes supplied RED evidence. Changed only the patch target
to `clients.tmux.BoundedTmuxServer` and its docstring. Every existing assertion,
fixture mock value and exception case remains unchanged; no production edits.

Fresh proof:

`TMPDIR=/home/felni/tmp-cao015-types-data .venv/bin/pytest --no-cov -q test/providers/test_tmux_working_directory.py`

**exit 0, 21 passed**, 12.74 seconds. isort/black unchanged; final scoped
`git diff --check` passed. Full diff is two replaced lines (mock target/docstring).
Read-only expanded search found only the six previously verified harmless old
module mocks remaining; no speculative cleanup performed. Root will rerun the
whole CI gate after all discovery fixes settle. Finding verdict: **PASS**.
