@AI_WORKFLOW.md

# Claude Code project instructions

## Operating contract

1. Code is the source of truth for the current implementation.
2. Tests prove behavior. Verify important conclusions against source code and executable checks.
3. Preserve unrelated work. Never overwrite pre-existing dirty changes.
4. Never commit, push, force-push, publish, rewrite history, merge remote changes,
   create releases, or delete remote resources unless the user explicitly requests it.
5. Repository-changing work follows the enforced workflow state machine below.
6. Before implementation, invoke `/workflow-router`; do not jump directly to editing.
7. Before completion after repository changes, invoke `/workflow-completion`.
8. `AI_WORKFLOW.md` is imported above and is the authoritative workflow for this repository.
   Follow it completely for repository work; do not substitute this summary for the full workflow.

## AI_WORKFLOW authority

- The imported `AI_WORKFLOW.md` is mandatory, not advisory.
- Read and follow the complete imported workflow before deciding how to execute repository work.
- `/workflow-router`, `/workflow-completion`, hooks, and skills enforce/assist `AI_WORKFLOW.md`;
  they do not replace it.
- A skill invocation alone never proves that the workflow step is complete.
- If this file, a skill, or a hook appears inconsistent with `AI_WORKFLOW.md`, do not bypass the
  discrepancy by editing code. Resolve the workflow state first.

## Enforced workflow state machine

Workflow evidence performed out of order does not satisfy the workflow.

Every repository-changing turn begins:

`git-status -> classify/route`

Pre-implementation sequence:

- `trivial`:
  `source-inspection`
- `standard`:
  `knowledge-decision -> source-inspection -> tdd-decision`
- `significant`:
  `knowledge-decision -> speckit-specify -> speckit-plan -> speckit-tasks -> graphify-impact -> source-inspection -> tdd-decision`
- `bug`:
  `knowledge-decision -> source-inspection -> systematic-debugging -> reproduction -> root-cause -> tdd-decision`
- `bug-significant`:
  `knowledge-decision -> speckit-specify -> speckit-plan -> speckit-tasks -> graphify-impact -> source-inspection ->
   systematic-debugging -> reproduction -> root-cause -> tdd-decision`

`Edit`, `Write`, `NotebookEdit`, and mutating Bash are blocked until the applicable
pre-implementation sequence is complete.

After implementation begins, every later implementation mutation invalidates previous completion evidence.

Completion sequence:

- `trivial`:
  `diff-review -> knowledge-persist-decision -> verification`
- `standard` / `bug`:
  `tests-run -> diff-review -> knowledge-persist-decision -> verification`
- `significant` / `bug-significant`:
  `tests-run -> composition-check -> composition-review -> diff-review ->
   graphify-refresh-decision -> knowledge-persist-decision -> verification`

Only then may `/workflow-completion` create a signoff.

## Significant-change definition

A change is **significant** if any of these is true:
- more than one subsystem is meaningfully affected;
- a shared database/state/event/message/API/schema/configuration boundary changes;
- architecture, dependency direction, migration behavior, or public contracts change;
- meaningful upstream/downstream effects or integration failures are plausible;
- the change modifies a cross-subsystem workflow or failure/retry/concurrency behavior.

A bug can also be significant. Use `bug-significant` when both apply.

## Manual decision checkpoints

Conditional steps must be recorded explicitly instead of silently skipped:

- `knowledge-decision`
- `tdd-decision`
- `root-cause`
- `graphify-refresh-decision`
- `knowledge-persist-decision`

Use `workflow-step.py` as instructed by `/workflow-router` or `/workflow-completion`.
Every manual checkpoint requires a concrete reason. `not-applicable` is a decision, not a shortcut.

## Completion contract

Do not claim completion unless the state machine is complete, the current diff matches the
signoff fingerprint, executable verification passes, and no unrelated work was overwritten.

`bypassPermissions` remains enabled. It bypasses permission prompts, not workflow hooks.
