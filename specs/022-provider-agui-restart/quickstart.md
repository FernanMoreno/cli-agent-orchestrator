# Quickstart: Real-Provider AG-UI Restart Acceptance

## Scope

This command is an opt-in live acceptance. It uses an authenticated provider CLI, an isolated PostgreSQL target, and loopback API worker processes. It is not an offline test or a proof of browser-login behavior.

The API worker runs in local auth-off mode on loopback. Production agent-capability checks remain active; the human answer call uses the local operator identity. Browser-session login is outside this acceptance.

## Prerequisites

- Run from the frozen candidate source tree with project dependencies installed.
- The requested Codex subscription profile or Claude account is already available to the existing private auth-staging helpers.
- The Codex AG-UI journey pins `gpt-6-luna`; the Claude AG-UI journey pins `sonnet`. Both run through the existing protected execution path. Ordinary non-AG-UI Claude journeys retain their unpinned profile.
- A healthy owned PostgreSQL service is available to the native `isolated_live_main_target` fixture.
- No provider API key fallback is set or passed to child processes.

## Commands

First run the offline harness contract tests:

```bash
python -m pytest -o addopts= -m offline_database test/live_provider/test_interaction_agui_restart.py
```

Run the combined acceptance serially for each already supported provider:

```bash
python test/live_provider/interaction_cli_journey.py codex --agui-restart
python test/live_provider/interaction_cli_journey.py claude_code --agui-restart
```

## Expected result

Each provider run must emit a sanitized success receipt only after it proves:

1. two provider-created questions are visible in the real pre-restart AG-UI snapshot;
2. the first API process is stopped and a fresh process starts against the same target;
3. the post-restart stream reports the same two pending questions and one durable group;
4. the provider/MCP invocation retrieves both human answers and acknowledges the exact returned receipts;
5. after the provider/MCP process retrieves and acknowledges both returned receipts, a fresh real AG-UI SSE request projects the same two question IDs as `agent_acknowledged` without private answer data;
6. all owned processes stop and private temporary data is cleaned.

Any provider startup/setup issue, restart failure, unknown interaction-projection field, missing projection, provider timeout, response mismatch, receipt mismatch, model-command mismatch, or cleanup failure is a failure for that provider. A synthetic question or harness-generated MCP acknowledgement is never a substitute. The successful Claude opt-in receipt reports `requested_model: "sonnet"`; it does not describe the ordinary Claude journey's model selection.

The journey reads a one-shot SSE snapshot before restart, after restart, and after acknowledgement. The wait transport phase at API replacement is unobserved and may overlap the replacement. The acceptance covers the same two pending questions persisting across API process replacement and the same provider invocation's subsequent answer delivery and acknowledgement; interruption, retry, or recovery of a wait across replacement is not proven. Actual provider/API errors fail the run.
