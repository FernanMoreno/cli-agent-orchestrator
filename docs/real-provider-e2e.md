# Real-provider native lifecycle matrix

The ordinary CI suite does not run paid/authenticated provider CLIs. The
manual **Real Provider Native Matrix** workflow is the reproducible E2E gate
for Codex, Claude Code and OpenCode.

Each run covers one parent × child cell. Run all nine cells before a release:

| Parent | Children |
| --- | --- |
| `codex` | `codex`, `claude_code`, `opencode_cli` |
| `claude_code` | `codex`, `claude_code`, `opencode_cli` |
| `opencode_cli` | `codex`, `claude_code`, `opencode_cli` |

For every cell the suite proves, against real CLI processes:

1. Parent and child launch successfully.
2. A child receives a prompt and returns a required marker.
3. The step response contains the required result marker and the parent-owned
   receipt reaches `succeeded` with cleanup; the test joins this receipt
   instead of inspecting terminal UI.
4. Two live child siblings exchange an inbox message with a durable
   `delivered` record.
5. Removing unfinished siblings yields `cancelled` receipts and cleanup, never
   a false `succeeded` result.
6. A post-send deadline yields `reconcile`; cleanup keeps that uncertain state
   rather than rewriting history.

## Runner requirements

Use a protected, Linux self-hosted runner labelled `cao-real-e2e`, with `tmux`
and the selected `codex`, `claude`, and `opencode` CLIs installed. The runner
must be authenticated by a provider API key or its normal login record.

For each workflow dispatch, enter an explicit currently available model for
the parent and child provider. The matrix refuses to use a provider's local
default: a stale default model is a routing/configuration failure, not evidence
about CAO's lifecycle. Verify an OpenCode model with a real short `opencode
run --model <provider/model> ...` response before dispatching: `opencode models`
can still list a retired upstream model. A current local NVIDIA-backed example
is `nvidia/nvidia/nemotron-3.5-lightning-30b-a3b` when that provider credential
is configured.

The test starts CAO with a disposable home. It does not copy credentials into
that home or into artifacts. When no API key is present, it symlinks only the
selected provider's auth file from `$HOME`, or from the optional
`CAO_REAL_PROVIDER_E2E_AUTH_HOME` directory. Keep that directory private and
never upload it as an artifact.

## Local execution

Run exactly one explicit cell:

```bash
export CAO_RUN_LIVE_PROVIDER_TESTS=1
export CAO_REAL_PROVIDER_E2E_PARENT=codex
export CAO_REAL_PROVIDER_E2E_CHILD=opencode_cli
export CAO_REAL_PROVIDER_E2E_PARENT_MODEL=gpt-6-astra
export CAO_REAL_PROVIDER_E2E_CHILD_MODEL=nvidia/nvidia/nemotron-3.5-lightning-30b-a3b
# Use this only when the mounted/cold test environment needs longer than the
# normal 30-second CAO server startup budget.
export CAO_TEST_SERVER_HEALTH_TIMEOUT=60
uv run pytest -o addopts= -m 'e2e and live_provider' \
  test/e2e/test_real_provider_matrix.py -vv
```

The environment variables are intentionally mandatory. This prevents an
ordinary local `pytest` invocation or a pull request from accidentally
starting authenticated models or charging an account.
