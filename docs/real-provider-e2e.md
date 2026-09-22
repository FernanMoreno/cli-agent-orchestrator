# Real-provider native lifecycle matrix

The ordinary CI suite does not run paid or authenticated provider CLIs. The
manual **Real Provider Native Matrix** workflow is the reproducible E2E gate
for the providers configured on a protected runner.

This is a dynamic validation matrix, not a list of providers that CAO permits
in production. A job's runtime allowlist remains the authority for which
providers can collaborate. The matrix only proves that every configured
parent -> child pairing can use CAO's native lifecycle correctly.

For every selected cell the suite proves, against real CLI processes:

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
7. If a provider account pauses a live turn for quota, CAO reports
   `quota_wait`, retains the terminal as `waiting_quota`, and records a
   `reconcile` receipt without re-sending the prompt. The matrix verifies this
   contract and reports that cell as a visible skip; it is neither a pass nor
   a product failure. Re-run the cell after the provider resets to prove a
   successful turn. The response's `provider_may_resume` field is adapter
   evidence, not a guess: it is true only when that provider's exact live
   quota panel can continue the already-delivered turn itself. A false value
   means retain the receipt and wait for an explicit operator retry, model
   change, credit refill, or a fresh provider run.

## Dynamic provider manifest

Set `CAO_REAL_PROVIDER_E2E_PROVIDERS` to a JSON object. Each object key is a
CAO provider identifier; adding an enabled provider to this object makes it
eligible for every parent -> child combination without editing the workflow.
The provider adapter must already exist in CAO, and the selected runner must
have the corresponding CLI and credentials.

```json
{
  "codex": {
    "model": "gpt-6-astra",
    "binary": "codex",
    "capabilities": ["native_children"],
    "auth_env": ["OPENAI_API_KEY"],
    "auth_files": [".codex/auth.json"]
  },
  "claude_code": {
    "model": "sonnet",
    "binary": "claude",
    "capabilities": ["native_children"],
    "auth_env": ["ANTHROPIC_API_KEY"],
    "auth_files": [".claude/.credentials.json", ".claude.json"]
  },
  "opencode_cli": {
    "model": "provider/current-model",
    "binary": "opencode",
    "capabilities": ["native_children"],
    "auth_env": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
    "auth_files": [
      ".local/share/opencode/auth.json",
      ".config/opencode/auth.json"
    ]
  },
  "gemini_cli": {
    "exclude_reason": "Configure an authenticated Gemini CLI, an exact available model, and a reviewed live matrix before enabling this runner cell."
  }
}
```

For an executable cell, provide `model`, `binary`, `auth_env`, `auth_files`,
and `capabilities`. `model` must be the exact, currently available model
identifier used by that provider. This native-child suite requires the
`native_children` capability. `auth_env` lists names only, never values;
`auth_files` are paths relative to the configured auth home. Missing runtime
metadata does not abort unrelated cells: it produces a visible skipped cell
that names the missing model, binary configuration, auth probe, or capability.
CAO never fills these fields from an ambient provider default.

An entry with a non-empty `exclude_reason` is intentionally excluded. It does
not silently disappear: the matrix records the provider and reason as skipped.
Use this for a known limitation such as a missing runner installation or an
adapter that has not shipped. Do not remove an enabled provider merely to make
a release green.

The manifest is deliberately not a credential store. Put it in the protected
GitHub Actions variable `CAO_REAL_PROVIDER_E2E_PROVIDERS`, or pass it as the
manual workflow's `providers_json` input. Keep API keys and login records in
the runner or secret store. The UTF-8 JSON payload is limited to 64 KiB;
larger configuration belongs in runner provisioning, not a workflow input.

## E2E test-cell filter

`CAO_REAL_PROVIDER_E2E_TEST_FILTER` belongs only to this live test harness.
It selects which ordered parent -> child cells to exercise; it is **not** a
runtime compatibility policy and it cannot authorize or prohibit collaboration
between providers. Runtime authorization remains the job's provider allowlist.
The historical `CAO_REAL_PROVIDER_E2E_PAIRS` spelling remains accepted as a
deprecated alias for existing runners, but new scripts should use
`CAO_REAL_PROVIDER_E2E_TEST_FILTER`.

The live test requires all three explicit safety gates:

```bash
export CAO_RUN_LIVE_PROVIDER_TESTS=1
export CAO_REAL_PROVIDER_E2E_PROVIDERS='{
  "codex": {
    "model": "gpt-6-astra",
    "binary": "codex",
    "auth_env": ["OPENAI_API_KEY"],
    "auth_files": [".codex/auth.json"],
    "capabilities": ["native_children"]
  }
}'
export CAO_REAL_PROVIDER_E2E_TEST_FILTER=all
```

`CAO_REAL_PROVIDER_E2E_TEST_FILTER=all` generates the Cartesian product of every
manifest provider. With `codex`, `claude_code`, `opencode_cli`, and a fourth
registered provider, that is 16 cells, including same-provider children.
Excluded entries remain visible as skips with their reason; they are never
treated as a pass.

For a cheap, focused reproduction, select one or more cells explicitly:

```bash
export CAO_REAL_PROVIDER_E2E_TEST_FILTER='codex->gemini_cli,gemini_cli->claude_code'
```

Every selected name must be a manifest key. An enabled provider must have an
adapter registered in this CAO checkout. An as-yet unregistered provider is
allowed only when it has a non-empty `exclude_reason`, so its absence remains
visible. Malformed JSON, wrong field types, unknown fields, unsafe auth paths,
and an empty exclusion reason fail closed. Missing model/runtime metadata,
missing configured binary, unavailable authentication, or non-empty
`exclude_reason` becomes an explicit skipped cell with its reason; it is never
silently removed or counted as a pass. CAO never falls back to an unrequested
provider or local default model.

Set at most one of the canonical variable and its legacy alias. If both are
set, their values must match; otherwise the harness fails before launching a
provider. This prevents a test-selection typo from being mistaken for runtime
routing.

## Strict release preflight

Local diagnosis mode is the default: unavailable configured providers become
explicit skipped cells, so an operator can see every missing prerequisite
without launching a model. Set `CAO_REAL_PROVIDER_E2E_STRICT=1` for a release
gate. In strict mode, before pytest creates a CAO server or launches any
provider terminal, the harness aggregates and fails on every selected enabled
provider that lacks a reviewed model, required capability, configured/installed
binary, or reusable authentication.

An `exclude_reason` remains an intentional skip in strict mode. It records a
deliberate adapter or runner limitation rather than allowing an unavailable
enabled provider to make CI green. Strict mode only checks providers referenced
by the selected test cells: this permits a focused reproduction without requiring
every unrelated manifest entry to be ready. It also requires at least one
selected parent -> child cell whose two endpoints are fully executable. A
selection composed only of intentional exclusions therefore fails before
fixtures instead of producing an all-skipped green release workflow.

The GitHub Actions release workflow sets strict mode automatically. Keep it
unset (or set it to `0`) for local diagnostic runs. Other values are rejected
to prevent an ambiguous release gate.

## Runner requirements

Use a protected Linux self-hosted runner labelled `cao-real-e2e`, with `tmux`
and every enabled provider CLI installed. The runner must be authenticated by a
provider API key or its normal login record. Verify each configured model with
a short real invocation before enabling it: a CLI may list a retired upstream
model even though it cannot execute it.

The test starts CAO with a disposable home. It does not copy credentials into
that home or into artifacts. When no configured API-key environment variable is
present, it symlinks only the selected provider's listed auth files from
`$HOME`, or from the optional `CAO_REAL_PROVIDER_E2E_AUTH_HOME` directory. Keep
that directory private and never upload it as an artifact.

## Local execution

Run all eligible local test cells:

```bash
export CAO_RUN_LIVE_PROVIDER_TESTS=1
export CAO_REAL_PROVIDER_E2E_PROVIDERS='{
  "codex": {
    "model": "gpt-6-astra",
    "binary": "codex",
    "auth_env": ["OPENAI_API_KEY"],
    "auth_files": [".codex/auth.json"],
    "capabilities": ["native_children"]
  },
  "opencode_cli": {
    "model": "provider/current-model",
    "binary": "opencode",
    "auth_env": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
    "auth_files": [
      ".local/share/opencode/auth.json",
      ".config/opencode/auth.json"
    ],
    "capabilities": ["native_children"]
  }
}'
export CAO_REAL_PROVIDER_E2E_TEST_FILTER=all
# Use this only when a mounted/cold test environment needs longer than the
# normal 30-second CAO server startup budget.
export CAO_TEST_SERVER_HEALTH_TIMEOUT=60
uv run pytest -o addopts= -m 'e2e and live_provider' \
  test/e2e/test_real_provider_matrix.py -vv
```

The environment variables are intentionally mandatory. This prevents an
ordinary local `pytest` invocation or a pull request from accidentally
starting authenticated models or charging an account.

## Release use

Run the GitHub workflow with its test-cell filter set to `all` before a release
from the protected self-hosted runner. It obtains the manifest from the dispatch
input when one is supplied, otherwise from the protected Actions variable. Use
an explicit `parent->child` cell only to reproduce a failed test. Adding a registered
provider or changing a model happens in the manifest, not by changing workflow
choices or CAO scheduler code. `gemini_cli` is already a registered adapter;
once a protected runner has its binary, authentication, exact model, and
reviewed live validation configured, replace its explicit exclusion with the
normal endpoint fields and it automatically enters every pair.
