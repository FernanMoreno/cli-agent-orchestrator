# Token-Efficient Roadmap Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` to execute this plan task-by-task. Terra is the default builder for integration and high-risk changes; Luna may produce explicitly isolated, bounded code/test increments in parallel. Every increment is reviewed and recorded in Spec Kit by Sol. Astra is reserved exclusively for exceptionally complex algorithms. Luna reflects accepted behavior in human documentation or durable memory.

**Goal:** Complete the remaining orchestration roadmap while reserving expensive reasoning for decisions that materially need it and keeping implementation, review, and documentation evidence separate.

**Architecture:** A lightweight controller selects dependency-ready work and writes bounded briefs with exclusive file ownership. Terra xhigh owns integration, persistence, authority, concurrency, correction work, and all non-algorithmic architectural decisions. Luna xhigh may implement isolated client, presentation, adapter, fixture, or test slices in parallel, each with a genuine RED/GREEN cycle. Sol high independently judges spec compliance and code quality, then owns all Spec Kit evidence, task state, and the orchestration roadmap. Astra low handles only an exceptionally complex algorithm after Terra and Sol record why normal analysis is insufficient. Luna performs supporting technical/operational documentation and durable-memory work only after the technical, Spec Kit, and roadmap gates pass.

**Tech Stack:** Python, FastAPI, SQLite, pytest, Import Linter, TypeScript/React, Rust, Spec Kit, Graphify, project composition tooling.

**Spec:** `specs/001-verifiable-orchestration/spec.md`, `specs/001-verifiable-orchestration/plan.md`, `specs/001-verifiable-orchestration/tasks.md`

## Global Constraints

- Preserve the existing dirty worktree and inspect `git status` before every increment.
- Code and fresh executable checks are the source of truth; task checkboxes and status prose may lag.
- Follow `AI_WORKFLOW.md` in order. Significant increments require Graphify impact analysis, applicable tests, `project-composition-check "$(cat .ai/project-name)"`, composition review, diff review, Graphify refresh decision, and knowledge-persistence decision.
- Use TDD for missing behavior: a genuine behavioral RED must precede implementation. Fixture/import failures are not valid RED evidence.
- Never commit, push, merge, release, rewrite history, publish, or consume real providers without explicit authorization.
- T084 and T085 remain externally gated. Mark them pending when authorization is absent.
- Do not copy generated Graphify facts into the vault. Persist only durable invariants or recurring root causes.
- Use temporary persistent SQLite for persistence claims. Never touch the operator database.
- One writer owns a file at a time. Parallel work is allowed only for independent files and state.

---

## 1. Model roster

| Role | Model and effort | Owns | Must not own |
|---|---|---|---|
| Builder | `gpt-5.6-terra`, `xhigh` | Source inspection, focused Graphify query, RED/GREEN code, fixtures, focused tests, root-cause fixes | Final acceptance, roadmap claims, vault merge |
| Evaluator, Spec Kit, and roadmap owner | `gpt-6-sol`, `high` | Spec review, diff review, composition invariants, evidence audit, severity verdict, `specs/` task/evidence/checklist updates, `docs/aipm-orchestration-roadmap.md`, Graphify refresh decision and verification record | Production implementation, supporting documentation, vault merge |
| Exceptional algorithm reviewer | `gpt-6-astra`, `low` | Only an exceptionally complex algorithm: bounded correctness invariant, complexity/risk ruling, minimal algorithmic design | Architecture, security, persistence, concurrency, routine debugging, implementation, documentation |
| Parallel bounded builder and documentation/durable-memory steward | `gpt-6-luna`, `xhigh` | Isolated code/test slices with exact source/test ownership and RED/GREEN evidence; supporting human-facing documentation and site docs, recovery/API/operator guides, release-independent explanations, durable vault proposals/transactions | Any `specs/` file, `docs/aipm-orchestration-roadmap.md`, task state, acceptance verdict, test evidence adjudication, Graphify-generated artifacts, schema/migration work, shared authority/persistence/concurrency boundaries, or a public entrypoint shared with another writer |

The controller coordinates only. It does not reread whole subsystems or reproduce worker prose. It passes paths to artifacts and receives terse verdicts.

## 2. Token budget protocol

Each task gets four small files under `/tmp/caos-exec/<task-id>/`:

- `brief.md`: exact task text, acceptance conditions, owned files, relevant source citations, commands, and global constraints.
- `builder-report.md`: root cause or design finding, changed files, RED command/result, GREEN command/result, residual risks.
- `review.diff`: scoped diff with context; never paste a repository-wide diff into an agent prompt.
- `review.md`: Sol verdict and findings, using `BLOCKER`, `IMPORTANT`, or `MINOR`.

Rules that save context:

1. Spawn workers with `fork_turns="none"`; their prompt names the four artifacts and required source files.
2. Use `rg`, Graphify queries, and exact line ranges. Do not send conversation history.
3. Terra returns at most 12 lines plus the report path. Sol returns findings and Spec Kit mutations. Astra returns one ruling with consequences. Luna returns changed documentation paths and durable-memory actions.
4. Keep raw pytest output in `/tmp`; reports contain command, exit code, counts, and first incorrect boundary.
5. Run focused tests during implementation. Run the broader affected suite once after the diff is stable. Do not repeatedly run the full repository suite.
6. Reuse a worker only for one fix loop while its context is useful. Start a fresh reviewer for acceptance.

## 3. Standard execution loop

### Stage A — controller prepares the task

- [ ] Read only the task row, its FR/SC references, relevant contract, current status entry, and affected source/test paths.
- [ ] Query Graphify for callers, callees, and state owners; verify load-bearing findings against source.
- [ ] Record the pre-task `git status` and file ownership in `brief.md`.
- [ ] Define the narrow test command and the broader affected-suite command before dispatch.

### Stage B — Terra xhigh or Luna xhigh implements

- [ ] Reconcile the brief against current code; report already-complete behavior instead of rewriting it.
- [ ] Reproduce the missing behavior with a focused RED test.
- [ ] Identify the first incorrect boundary before changing production code.
- [ ] Implement the smallest complete behavior and run focused GREEN tests.
- [ ] Review its scoped diff and write `builder-report.md`.
- [ ] Do not mark the roadmap task complete.

Terra is mandatory for an increment that changes schemas, migrations, authority,
persistence, concurrency, recovery, security, shared public entrypoints, or a
cross-subsystem contract. Luna may build only a dependency-ready slice whose
brief gives it exclusive source and test paths; a Luna slice has the same
RED/GREEN, diff review, and executable-gate requirements as Terra.

Dispatch shape:

```text
Model: gpt-5.6-terra (integration/high-risk) or gpt-6-luna (isolated slice)
Reasoning: xhigh
Read: <brief>, named spec/contract sections, named source files only.
Write ownership: <exact paths>.
Deliver: RED/GREEN implementation and <builder-report>.
Return: <=12 lines; no full diff, no generic summary.
```

### Stage C — Sol high evaluates

- [ ] Read `brief.md`, `builder-report.md`, `review.diff`, and current source at changed boundaries.
- [ ] Judge spec compliance and code quality separately.
- [ ] Inspect authority, persistence, ordering, restart, duplicates, cancellation, partial failure, cache, configuration, and compatibility where applicable.
- [ ] Accept test evidence only when commands, exit codes, and behavioral coverage are explicit.
- [ ] Return `PASS`, `FAIL`, or `ESCALATE`, with exact `path:line` findings.
- [ ] After PASS and executable gates, update `specs/001-verifiable-orchestration/tasks.md`, `workflow-status.md`, relevant checklists, and traceability. Sol alone owns these Spec Kit mutations.
- [ ] Reconcile `docs/aipm-orchestration-roadmap.md` with demonstrated guarantees and preserved limitations. Sol alone owns the roadmap.
- [ ] Record the Graphify refresh decision and verified graph evidence in Spec Kit; execution of the local refresh may be delegated to Terra when it is mechanical.

Dispatch shape:

```text
Model: gpt-6-sol
Reasoning: high
Mode: read-only evaluator.
Inputs: <brief>, <builder-report>, <review.diff>, fresh test log paths.
Output: verdict plus actionable findings only; do not rerun already-fresh tests.
```

### Stage D — fix or escalate

The original builder gets one normal fix loop for concrete Sol findings.
Luna fix work remains limited to its bounded-builder ownership; otherwise Terra
receives the correction. Terra and Sol resolve architecture, security,
persistence, concurrency, recovery, repeated failures, and cross-subsystem
ownership. Escalate to Astra only when all apply:

- the unresolved unit is an algorithm, not a policy or integration boundary;
- Terra and Sol have recorded a concrete correctness or complexity ambiguity;
- a bounded invariant and adversarial inputs can be supplied without moving
  authority, persistence, security, or concurrency ownership to Astra.

Astra receives only the algorithm brief, competing invariants/counterexamples,
relevant source ranges, and Sol findings. Its response must contain:

```text
Ruling: <one chosen invariant/design>
Evidence: <source/test facts>
Rejected alternatives: <at most two>
Required patch boundary: <exact owners/interfaces>
Failure cost: <what breaks if ruling is wrong>
```

Terra implements an Astra algorithm ruling unless it remains entirely within an
active Luna bounded slice. Sol performs scoped re-review. Astra does not stay
in the routine loop.

### Stage E — executable gate

- [ ] Run focused tests for the final patch.
- [ ] Run the affected integration/contract suite once.
- [ ] For significant work, run `project-composition-check "$(cat .ai/project-name)"`.
- [ ] Run `git -c core.whitespace=cr-at-eol diff --check` and inspect the scoped final diff.
- [ ] Keep failures open; never convert an omitted or stale check into PASS.

### Stage F — Sol high closes Spec Kit and roadmap

After the executable gate, Sol converts the accepted evidence into the feature's authoritative execution record.

- [ ] Update `tasks.md` only for behavior actually accepted.
- [ ] Append concise commands, exit codes, first-boundary failures, rulings, and limitations to `workflow-status.md`.
- [ ] Update `docs/aipm-orchestration-roadmap.md` only for guarantees demonstrated by the accepted increment.
- [ ] Decide Graphify refresh from structural changes; verify refreshed modules against source and record counts/warnings.
- [ ] Preserve explicit limitations: no full-suite claim, no real-provider claim, and external gates still pending.

Dispatch shape:

```text
Model: gpt-6-sol
Reasoning: high
Inputs: accepted brief/report/review, exact test and composition evidence.
Write ownership: specs/001-verifiable-orchestration/** and docs/aipm-orchestration-roadmap.md only.
Output: changed Spec Kit/roadmap paths, task-state changes, recorded limitations.
```

### Stage G — Luna xhigh updates documentation and durable memory

Luna runs only after Sol has accepted the increment and finalized Spec Kit and roadmap. It receives the accepted evidence summary and the exact claims Sol recorded, not raw logs or conversation history.

- [ ] Update recovery/API/operator guides, Docusaurus content, and other supporting human-facing documentation only when demonstrated behavior changed.
- [ ] Keep documentation claims within Sol's accepted evidence and preserve all explicit limitations.
- [ ] Decide whether a durable vault entry is warranted. Persist only an architecture rationale, recurring root cause, or long-lived operational invariant.
- [ ] Do not edit `specs/`, `docs/aipm-orchestration-roadmap.md`, task checkboxes, workflow evidence, checklists, or Graphify-generated files.
- [ ] Skip Luna entirely when the accepted increment changes neither human documentation nor durable knowledge.

Dispatch shape:

```text
Model: gpt-6-luna
Reasoning: xhigh
Inputs: accepted artifacts and exact evidence summaries.
Write ownership: human-facing documentation and explicitly approved durable-memory transaction only.
Output: changed documentation paths, claims added/removed, durable-memory action or `not applicable`.
```

## 4. Roadmap execution order

The controller follows dependency readiness rather than task number alone.

### Wave 1 — public entry integration and snapshots

1. Reconcile pending foundational T006 against the current model implementation; close it only with executable DTO/validation evidence.
2. Verify and close T091 against current delivery code; add work only for uncovered acceptance conditions.
3. T017 ordinary launch, then T018 inbox, T019 child/handoff, and T020 YAML/scripts.
4. T024/T025 test gaps, T029 authority at assignment/send, T035 API/MCP grants and reservations.
5. T023 five-entry lifecycle and T036 independent-process composition.
6. T043 snapshot-ID adapter, then T044 universal snapshot integration.

Parallelism: T018 and T043 may be investigated concurrently only when ownership does not overlap. Implementation touching `terminal_service.py`, `agent_step.py`, `api/main.py`, or `mcp_server/server.py` is serialized.

### Wave 2 — continuity, decisions, and cancellation

1. T047 and T048 tests may run in parallel.
2. T049 durable decisions.
3. T050 export, T051 import/preflight, T052 fencing and prior-attempt cessation.
4. T053 API/MCP/AG-UI decision integration.
5. T054 cancellation propagation and cleanup.
6. T055 two-adapter end-to-end continuity.

Replay, duplicate effects, fencing, and concurrent result/cancel ordering stay
with Terra/Sol; they do not trigger Astra unless the remaining issue is an
exceptionally complex algorithm under Stage D.

### Wave 3 — state, clients, and capability projection

1. T056 DTO compatibility contract.
2. T058 provider preflight descriptors and T059 non-native backend status.
3. T060 web and T061 TUI may execute in parallel.
4. T062 CLI/MCP plus generated visual tokens.
5. T063 transition fixtures and real backend evidence when locally available without an external-account gate.

Sol reviews shared DTO compatibility and updates Spec Kit before client-specific work is accepted. Luna updates user-facing docs only after all consumers agree on the contract.

### Wave 4 — distribution and recovery

1. T064 HTTP knowledge contract and T065 recovery tests may run in parallel.
2. T067 checkpoint/cursor, then T068 retention/tombstones.
3. T069 backup, T070 isolated restore, T071 rollback procedure.
4. T072 two-process partition/reconnection composition.

Terra/Sol review backup/restore format and cross-node conflict semantics. Astra
is not used unless a separate exceptionally complex algorithm remains after
that review.

### Wave 5 — maintainability, real providers, and final closure

1. T074 effective step contract and T079 owner/dependency cleanup.
2. T082 evidence-aware real-provider matrix and T083 opt-in CI guards.
3. Leave T084 pending until explicit provider/account authorization.
4. Leave T085 pending until explicit upstream-integration authorization.
5. T086 runs fresh story/regression/composition checks; T087 performs the final system-composition review; T088 reconciles the complete diff and roadmap claims; T089 refreshes Graphify and decides durable knowledge; T090 audits final FR/SC traceability. Add new tasks for discovered gaps instead of closing by task count.

## 5. Concurrency policy

The environment has seven slots including the controller, so use at most six
workers. Fill safe slots, but never trade file ownership or a dependency fence
for throughput. The default is bounded parallelism:

```text
slot 1: controller and ownership ledger
slots 2-3: Terra or Luna xhigh implementation on disjoint source/test slices
slot 4: Luna xhigh implementation or focused test preparation on another disjoint slice
slot 5: Sol read-only pre-review once a writer freezes its scoped diff
slot 6: Astra only for a qualifying exceptionally complex algorithm
slot 7: Luna documentation/durable-memory work only after a separate Sol PASS
```

Before dispatch, the controller records task, exact files, mode (`write`,
`read-only`, or `vault`), model, and stop condition in
`/tmp/caos-exec/<task-id>/brief.md`. At most one worker may write any source,
test, Spec Kit, roadmap, or supporting-documentation path. Parallel writers
must not share a schema, public entrypoint, persistence transaction, authority
decision, generated artifact, or test fixture. A read-only worker may run
tests only in a disposable snapshot and must not modify the worktree.

Do not run Sol's acceptance verdict or Spec Kit mutations while its reviewed
diff is changing. Sol may begin read-only source/evidence analysis after a
writer freezes a scoped baseline and must restart that analysis if the baseline
changes. Luna may write production code only under its bounded-builder rule;
it remains forbidden from Spec Kit, roadmap, task state, checklists, Graphify
artifacts, schema/migration, shared authority/persistence/concurrency, and
shared public-entrypoint work. Luna documentation remains forbidden before
Sol's accepted technical record. When Astra is active for an algorithm, pause
only writers of that algorithm; independent workers may continue on confirmed
disjoint ownership.

## 6. Completion contract per task

A task closes only when all are true:

- Terra or an authorized Luna bounded slice produced a genuine RED/GREEN, or proved the current implementation already satisfies the acceptance condition.
- Sol issued PASS with no unresolved blocker or important finding.
- Any exceptional-algorithm Astra ruling, if used, was implemented and re-reviewed.
- Applicable focused, integration, architecture, and composition checks passed freshly.
- The final diff was reviewed and unrelated work remained intact.
- Sol updated task state, evidence, limitations, checklists, Graphify decision, and roadmap claims.
- Luna updated human documentation and durable memory when applicable; otherwise it recorded `not applicable` without touching Spec Kit.
- No prohibited external action occurred.

## 7. Stop conditions

Stop and report rather than infer authority when work requires:

- real provider/account consumption (T084);
- upstream merge, push, release, or publication (T085 or later);
- destructive migration or deletion outside disposable fixtures;
- a plan defect where every viable implementation would guess at a security or durability invariant.

Routine test failures, hard bugs, long filesystem I/O, architecture, security,
persistence, concurrency, recovery, or a reviewer finding are not Astra
conditions. Terra diagnoses and Sol evaluates; Astra resolves only a qualifying
exceptionally complex algorithm.

## 8. Self-review result

- Coverage: pending foundational T006, all remaining roadmap waves T017–T090, and discovered T091 are routed; completed T041/T042 are not repeated.
- Type/model consistency: model names and efforts are fixed in the roster and dispatch templates.
- External gates: T084/T085 remain pending without explicit authorization.
- Workspace safety: no worktree reset, commit, push, provider use, or operator-database access is authorized by this plan.
- Placeholder scan: the controller must fill exact paths and commands in each generated task brief from current source; no implementation task may start from a generic prompt.
