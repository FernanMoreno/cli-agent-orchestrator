# Feature 016 validation guide

This guide defines the implementation acceptance run. Results and remaining provider limitations are recorded in [evaluation.md](evaluation.md). Automated fixtures use disposable SQLite databases and isolated capabilities. Keep provider native dialogs manual-only unless the installed version has independently verified response support.

## Prerequisites

- Python environment created from the repository's pyproject/lock files and web dependencies installed from web/package-lock.json.
- CAO API configured to use a temporary database path, with a browser Principal available for human answer tests.
- Four provider CLIs available for provider evaluation: Codex, Claude Code, OpenCode and Hermes. The test profile must already grant only the bundled cao-mcp-server. Feature setup must not add tools to the profile.
- One unowned test terminal and one terminal created by an authenticated Principal. Include one server-created child assignment and, where Work tests are run, one exact managed attempt.
- Do not include credentials, access codes or full terminal transcripts in fixtures or evaluation output.

## Automated checks

Run the tests added by the implementation:

    .venv/bin/python -m pytest test/clients/test_interaction_repository.py test/services/interactions test/providers/test_interactions.py test/services/test_status_monitor_interactions.py test/services/test_terminal_interaction_capability.py test/api/test_interactions_transport.py test/api/test_interaction_websocket_input.py test/mcp_server/test_interaction_tools.py test/integration/test_interaction*.py --no-cov -q -m ''
    npm --prefix web test -- src/test/interaction-api.test.ts src/test/questions-panel.test.tsx src/test/questions-terminal-navigation.test.tsx --maxWorkers=4
    npm --prefix web test -- --maxWorkers=4
    npm --prefix web run build

For the browser journey, run from `web`:

    npx playwright test e2e/questions-inbox.spec.ts --workers=1

This starts a disposable real API/SQLite fixture. The fleet proxy and empty second node are fixtures, so this does not validate multiple running CAO instances. Chromium and its system libraries must be available.

Then run the applicable existing auth, MCP HTTP-only, provider, inbox, agent_step, Work and frontend suites. For the significant cross-subsystem change, run:

    project-composition-check "$(cat .ai/project-name)"

Review the final diff and preserve the existing Graphify work. The impact graph for this implementation is extracted from an isolated Linux copy of the source; see [graphify-impact.md](graphify-impact.md). Verify its important edges against source.

Expected results: new interaction schema is additive and independently versioned; Work schema history is unchanged; tests prove actor separation, resource content ACL, source-turn staleness, durable answer/receipt replay, no resend after uncertainty, wait budget suspension, exact Work target fencing and node-switch privacy. Web build and tests pass; composition gate reports no broken existing contract.

## Explicit same-task journey

1. Start CAO against a disposable SQLite DB and sign in as a verified browser Principal.
2. Create a terminal through the authenticated API, with a profile that already grants the bundled CAO MCP. Confirm the server binds the authenticated Principal as content owner and the provider receives a private capability-file path, not a token in argv/profile/general MCP env.
3. In that terminal's live agent task, call cao_ask_user with one required text question and one required single-choice question. Record the returned group/question IDs. Do not submit a new task.
4. In the web Questions tab, verify source, task, type, recipient, required state and expiry. Submit the text and declared option. Confirm the API returns accepted receipts and the UI distinguishes acceptance from agent observation. The required runtime wait must remain blocked until the originating source pulls its answers; acceptance alone must not settle a pre-answer result.
5. Call cao_wait_for_user_answers for the same group. Confirm the original live task receives the typed answers from its MCP call. Then call cao_acknowledge_user_answers and confirm the history shows returned and acknowledged as separate events.
6. Repeat the same API idempotency key and payload and confirm the same receipt. Use a different key with a divergent answer and confirm an explicit conflict.
   Also simulate a lost response to `cao_ask_user`: retry with the same `operation_key` and canonical request and verify CAO returns the original group; reuse that key with a changed request and verify an explicit conflict.
7. Create an optional question, continue the task, and verify it remains visible without creating a blocking wait edge. Verify a second independent task remains eligible while the required question is waiting.
8. Restart CAO with one pending question and one returned-but-unacknowledged answer. Confirm snapshot recovery preserves IDs/status and the answer can be retrieved by the same source capability without duplicating the initial task.

## Identity, privacy and Work checks

1. Query list as an observer with cao:read: metadata only. Detail and answer must be denied unless that Principal owns the source or has an exact current resource grant.
2. Query as a browser owner, unrelated agent capability, source capability, and a supervisor with/without a server-recorded live delegation. A body actor/sender/terminal ID must never change the result.
3. Revoke a Principal's Work grant or change the exact attempt/contract after opening the question. Verify acceptance and any result/effect path reject the stale authorization.
4. Attempt generic terminal input, eager Inbox delivery, ordinary assignment, and legacy answer_user_prompt against a Work-owned target and an open required question. Confirm no path injects arbitrary text or bypasses Work authority.
5. Attempt to access an unowned source. It may appear as safe metadata, but content and answer controls remain unavailable. Use the explicit cao:admin self-designation path to bind the authenticated operator to that exact incarnation; verify audit history.
6. Verify a Work V2 process worker lacking CAO MCP reports unsupported and gets no network, PTY, local terminal fallback or additional tools. Verify Work V1 participates only when the exact active contract, attempt, grant and profile capability allow it.
7. Send text that resembles an approval or includes a password/code. No string grants authority or becomes a credential form. Work execution still requires its exact WorkDecisions receipt.

## Native provider evaluation

Run the following for each provider/version in the same isolated repository and with a profile that does not broaden permissions:

| Provider | Explicit ask/result journey | Native observation journey | Release record |
| --- | --- | --- | --- |
| Codex CLI | Required text and declared choice round-trip via the source MCP task | Trigger one safe native dialog if available; verify exact active fingerprint, real options and a non-destructive response route | Mark each separately supported or manual-only with reason |
| Claude Code | Same explicit journey | Same evidence bar; do not treat a footer/status signal as a typed dialog | Mark each separately supported or manual-only with reason |
| OpenCode | Same explicit journey without enabling its denied question tool | Observe a real permission/question panel only if the profile already permits it; preserve deny policy | Mark each separately supported or manual-only with reason |
| Hermes | Pending: no proven safe per-launch private overlay in current implementation; installed launcher is unavailable | Verify active dialog identity and exact current selector before any response; do not infer cursor navigation from status | Mark each separately supported or manual-only with reason |

For every native claim, verify that cited options come from provider evidence, a redrawn/replaced prompt gets a different version, stale replies are rejected before I/O, and an uncertain/partial effect is not resent. If any part cannot be demonstrated for an installed provider/version, record that type as manual-only and provide the exact terminal action link. Do not use transcript regex, generic Enter/y/n, or invented approve/deny choices.

## Wait, recovery and concurrency checks

- Pause one required question and confirm the same task/attempt resumes with no duplicate initial task and no duplicate verified result. Confirm human wait time does not extend a Work lease/grant.
- Expire/cancel/revoke the source while waiting; submit a late answer and confirm stale rejection. Confirm independent siblings continue.
- Create a server-derived wait cycle. Confirm wait graph names participants, operator cancel is audited and no answer is synthesized.
- Race identical and divergent answers. At most one effective answer exists per question/version; all replays resolve to its receipt or an explicit conflict.
- Crash before answer commit, after commit/before result retrieval, after retrieval/before acknowledgement, and after an external claim. Recovery preserves durable state; only a proven no-effect claim can be reconciled for a retry.
- Drop and duplicate event delivery. Reload the snapshot; the list has no duplicates and state matches the ledger.
- Switch browser node/session with a detail fetch in flight. The old content is cleared and cannot render in the new node context.

Append provider/version support results and all omitted scenarios to specs/016-agent-interactions/evaluation.md after actually running the checks. Do not record a planned or mocked case as a provider pass.
