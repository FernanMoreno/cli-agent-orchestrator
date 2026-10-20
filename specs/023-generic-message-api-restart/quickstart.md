# Quickstart: Generic Message API Restart Acceptance

## Status and scope

This is the separate Spec023 real-provider acceptance for two generic `send_message` calls around one production API process replacement. It complements Spec022's question/answer journey. Offline checks and source-bound preflight do not count as real-provider acceptance. No Spec023 provider lane has passed yet; see [validation.md](validation.md).

## Before selecting a provider

Use the independent source-bound launcher with the frozen source snapshot, its matching source manifest, a single approved provider profile, private staged authentication, and the owned PostgreSQL metadata file. Keep the child `HOME` and runtime directories private. Do not place credentials, service metadata contents, or message bodies in this document or in sanitized output.

Run the bounded offline harness checks first. For a live lane, the launcher must construct a fresh allowlisted child environment with the general provider test gate enabled, exactly one Spec023 provider gate enabled, all other provider gates disabled, and PostgreSQL autostart disabled. The metadata file path is supplied as a private launcher input.

## Required provider selection environment

Set the generic matrix selection to the selected provider's exact self-pair. For example, a Codex lane uses:

```text
CAO_RUN_LIVE_PROVIDER_TESTS=1
CAO_REAL_PROVIDER_E2E_TEST_FILTER=codex->codex
CAO_RUN_MESSAGE_API_RESTART_LIVE=1
CAO_RUN_MESSAGE_API_RESTART_CODEX=1
CAO_RUN_MESSAGE_API_RESTART_CLAUDE_CODE=0
CAO_RUN_MESSAGE_API_RESTART_OPENCODE=0
CAO_TEST_POSTGRES_AUTOSTART=0
CAO_TEST_POSTGRES_METADATA_FILE=<private-owned-service-metadata-path>
```

For Claude Code or OpenCode, use `claude_code->claude_code` or `opencode_cli->opencode_cli`, enable only the matching `CAO_RUN_MESSAGE_API_RESTART_*` gate, and set the other two provider gates to `0`. The launcher must set this value itself in its allowlisted child environment; it must ignore an inherited filter such as `all` or a cross-provider pair.

The source-bound preflight imports `test.e2e.test_real_provider_matrix` to verify source bindings. Because `CAO_RUN_LIVE_PROVIDER_TESTS=1`, that module's parameter declaration evaluates `_matrix_cells()` at import, and `_pair_selection()` requires `CAO_REAL_PROVIDER_E2E_TEST_FILTER`. Omitting the filter fails preflight before API startup. A self-pair makes the selected profile's import-time matrix selection unambiguous; the acceptance runner still selects only the Spec023 test target, not the generic matrix test. This filter does not replace the separate Spec023 gate, provider profile, staged-auth, dispatch-observer, persistence, event, or cleanup checks.

## Launcher command template

Use the exact frozen source root and its matching manifest supplied to the private launcher:

```sh
<source-bound-python> <private-launcher> --preflight \
  --provider <codex|claude_code|opencode_cli> \
  --source-root <frozen-source-root> \
  --source-manifest <matching-source-manifest> \
  --source-manifest-sha256 <verified-manifest-digest>
```

After preflight succeeds, use the same source inputs and selected-provider environment with `--execute-live`. The child environment remains allowlisted; do not append operator shell environment or provider credentials to the command.

## Execution sequence

1. Verify the selected provider profile and frozen source bindings with the private launcher's preflight mode.
2. Run one selected provider lane at a time through the independent source-bound launcher and the owned PostgreSQL service. Do not invoke the generic real-provider matrix as a substitute.
3. Accept a run only when its private inner receipt, two successful MCP audit records and completion seal, persisted rows, redacted live AG-UI events, retained native panes, and owned cleanup all validate. A preflight pass alone is not a lane result.
4. Record only sanitized outcomes in [validation.md](validation.md). Omit message bodies, provider output, auth material, resource identifiers, and service-metadata contents from sanitized shared reports and the sanitized driver receipt. Detailed ownership identifiers required for cleanup may remain in private run artifacts.
