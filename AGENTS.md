# Codex project instructions

## Operating contract

- Code is the source of truth; verify important conclusions against actual source and executable checks.
- Inspect `git status` before editing and preserve unrelated work.
- Do not commit, push, rewrite history, merge remote changes, publish, create releases, or delete remote resources unless explicitly requested.
- Read the relevant section of `AI_WORKFLOW.md` when a workflow below applies; do not blindly load the whole manual for trivial work.

## Routing

Treat work as significant when it affects multiple subsystems, shared persistence/state/events/messages/APIs/schemas/configuration, architecture/dependency direction, migrations, or meaningful upstream/downstream behavior.

For bugs/failing tests/unexpected behavior, use `systematic-debugging`, reproduce the issue, trace the first incorrect boundary, and identify root cause before changing behavior.

For significant work:
- use Spec Kit;
- use Graphify before implementation and verify it against source;
- run applicable architecture, contract, integration, and composition checks;
- use `system-composition-review`;
- use `verification-before-completion`.

## Programmatic completion checks

After code changes, run applicable project tests. For significant work, also run:

```bash
project-composition-check "$(cat .ai/project-name)"
```

Review the final diff before completion. If a required check fails, fix the root cause or report the failure accurately; never claim it passed.

`CLAUDE_OBSIDIAN_VAULT` identifies the shared durable-knowledge vault. Persist only durable conclusions that must survive agent/session switching.
