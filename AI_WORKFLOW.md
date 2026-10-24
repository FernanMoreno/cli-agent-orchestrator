# AI Working Rules

This is the authoritative repository workflow for Claude and Codex. Follow it in order for repository changes. Hooks and skills enforce or assist it; they do not replace it.

## 1. Sources of truth

- **Code**: current implementation.
- **Tests**: executable behavior.
- **Graphify**: structure, dependencies, call paths, inheritance, impact, and neighbors.
- **Spec Kit**: requirements, specification, planning, tasks, analysis, implementation, and convergence for significant work.
- **Obsidian**: durable knowledge across sessions and agents.
- **Agent Skills**: repeatable engineering procedures.
- **`.ai/composition/`**: project composition-review expectations.

Verify important conclusions against source and executable checks.

## 2. Classify before editing

Start with `git status`; identify and preserve unrelated work.

Classify the task as:

- `trivial`: typo/comment/mechanical low-risk change; no behavioral or boundary effect.
- `standard`: bounded single-subsystem change.
- `significant`: any significance condition below applies.
- `bug`: bug/failing test/unexpected behavior without significant cross-subsystem impact.
- `bug-significant`: both bug and significant.

A change is **significant** if it affects any of:

- multiple subsystems;
- shared DB/state/event/message/API/schema/config boundaries;
- architecture or dependency direction;
- migrations or shared persistence invariants;
- meaningful upstream/downstream behavior;
- plausible integration/composition failures;
- cross-boundary retries, duplicates, idempotency, timeout/cancellation, cache/stale state,
  concurrency/locking, resource lifecycle, partial failure, or compatibility.

Do not require Spec Kit/composition machinery for truly trivial work.
## 3. Mandatory execution order

Evidence performed out of order does not count. Conditional steps may be `not-applicable` only with a concrete reason.

### Before implementation

Every repository-changing task starts:

`git status -> classify/route`

Then:

- `trivial`: `source-inspection`
- `standard`: `knowledge-decision -> source-inspection -> tdd-decision`
- `significant`: `knowledge-decision -> speckit-specify -> speckit-plan -> speckit-tasks -> graphify-impact -> source-inspection -> tdd-decision`
- `bug`: `knowledge-decision -> source-inspection -> systematic-debugging -> reproduction -> root-cause -> tdd-decision`
- `bug-significant`: `knowledge-decision -> speckit-specify -> speckit-plan -> speckit-tasks -> graphify-impact -> source-inspection -> systematic-debugging -> reproduction -> root-cause -> tdd-decision`

Do not mutate implementation until the applicable sequence is complete.
### After implementation

Every implementation mutation invalidates previous completion evidence.

Completion order:

- `trivial`: `diff-review -> knowledge-persist-decision -> verification`
- `standard` / `bug`: `tests-run -> diff-review -> knowledge-persist-decision -> verification`
- `significant` / `bug-significant`: `tests-run -> composition-check -> composition-review -> diff-review -> graphify-refresh-decision -> knowledge-persist-decision -> verification`

Only then may workflow signoff be created.
## 4. Spec Kit

Required for `significant` and `bug-significant` work.

Use the real artifact sequence:

`spec.md -> plan.md -> tasks.md`

The spec/plan must cover relevant subsystem boundaries and cross-subsystem acceptance scenarios.

Invoking a Spec Kit skill is not completion evidence; artifacts must exist.
## 5. Graphify

Use Graphify for structural facts:

- architecture/dependency direction;
- call paths/inheritance;
- impact analysis;
- upstream/downstream neighbors.

Use it before implementation for significant work and refresh it after significant structural changes when needed.

Graphify is generated repository knowledge. Do not copy generated facts into Obsidian. Verify important findings against source code.

## 6. Bugs and TDD

For bugs, failing tests, integration failures, or unexpected behavior:

1. use `systematic-debugging`;
2. reproduce the failure;
3. inspect relevant boundaries and data/state flow;
4. identify the first incorrect boundary/root cause;
5. decide and record the TDD approach;
6. only then implement the fix.

Prefer test-driven development for new behavior and regressions when practical.

Use `verification-before-completion` before substantial completion.

### 6.1 Focused native/provider diagnostics

- State the first unobserved boundary and the one observation that will distinguish the current hypotheses. Prefer instrumenting an existing working real journey over constructing a surrogate journey.
- Reuse production configuration, startup/readiness gates and protocol schemas. Before a real run, exercise the actual controller-to-service initialization boundary and verify the required tool is discovered/offered; configuration declarations and isolated packet-builder tests are insufficient.
- Separate harness defects, offline evidence, actual diagnostics and final acceptance. After a harness failure, reproduce and fix its first incorrect boundary before another expensive run; preserve the failed receipt and use fresh source-bound launchers.
- Keep reviews scoped to the observation needed. Optional diagnostics, generic hardening and unrelated refactors do not block progress unless they affect that observation's correctness, privacy or cleanup. Do not add fields or review rounds merely to improve an otherwise adequate control.
- Preserve acceptance criteria and coverage. Record durable causes and prevention rules in Obsidian; keep raw logs and temporary execution details in the repository/private receipts instead.

## 7. Significant composition review

A significant change is not complete because isolated feature tests pass.

Review the affected composition surface, as applicable:

- state ownership and persistence invariants;
- transactions, rollback, compensation, and partial failure;
- events/messages, ordering, replay, duplicates, idempotency;
- timeouts, cancellation, and error propagation;
- cache invalidation/stale state;
- concurrency/locking and resource lifecycle;
- API/schema/data contracts;
- configuration/environment propagation;
- migrations and backward compatibility.

Use Graphify to identify the impact surface, then verify against source.

Use `system-composition-review` for significant cross-subsystem completion.
### Architecture / integration tools

Use the configured ecosystem gate when applicable:

- Python: Import Linter;
- JavaScript/TypeScript: dependency-cruiser;
- .NET: ArchUnitNET;
- Java: ArchUnit;
- other ecosystems: equivalent architecture tests, or document why none applies.

Do not invent architecture boundaries just to satisfy a checker.

Use Testcontainers (or equivalent) when mocks/in-memory systems could hide real dependency behavior.

Use Pact (or equivalent) when independently evolving consumers/providers communicate through APIs, messages, or services.

## 8. Durable knowledge

Use Obsidian only for information worth carrying across sessions/agents:

- architecture rationale and important decisions;
- non-obvious constraints/contracts/invariants;
- recurring root causes;
- significant research/discoveries;
- durable conventions and lessons.

Do not save routine edits, raw logs, temporary implementation details, obvious source facts, Graphify output, or copies of every Spec Kit artifact.

Claude and Codex share repository artifacts, Spec Kit/Graphify output, and the vault; not conversation history.
## 9. Documentation and documents

For current/version-specific library documentation:

- LangChain/LangGraph/LangSmith: prefer official LangChain Docs MCP.
- Other libraries/frameworks/SDKs/APIs: use Context7 when needed.
- Do not query both unless the first source is incomplete or conflicting.

For PDF/DOCX/PPTX/XLSX/HTML/images and other supported documents, use Docling/`project-doc` as the default preprocessing path.

- Treat the original document as the source artifact and generated Markdown as the agent-readable representation.
- Preserve headings, tables, lists, order, and referenced meaningful images.
- For whole-document tasks, read the generated Markdown completely; if too large, read contiguous sections in order.
- Do not silently replace required full reading with search/retrieval.
- Avoid OCR for digital PDFs; use local OCR for scanned/mixed content as needed.
- Generated artifacts live under `.project-docs/`.

## 10. Git and safety

Preserve unrelated changes.

Do not commit, push, force-push, publish, rewrite history, merge remote changes, create releases, or delete remote resources unless the user explicitly requests it.

Review the actual final diff before completion.
## 11. Completion standard

Before claiming substantial work complete:

1. final diff matches the requested change;
2. applicable tests/checks actually ran and their real results are known;
3. significant work includes applicable architecture/contract/integration/composition coverage;
4. failed composition checks are debugged to the first incorrect boundary before changing code;
5. `verification-before-completion` passes;
6. only genuinely durable knowledge is persisted;
7. no check is claimed unless it actually ran.

<!-- AI-STACK ECC WORKFLOW BEGIN -->
ECC project workflow (Codex, Claude Code and OpenCode):
Load `.agents/skills/ai-stack-ecc-project-workflow/SKILL.md` when the user explicitly requests ECC guidance.
Registered project: `caos`.
ECC upstream source: use the stack-wide Git cache at `${XDG_CACHE_HOME:-$HOME/.cache}/ai-dev-stack/ecc/ECC`; project-new and project-fix refresh the official main branch for all registered projects.
Obsidian: before non-trivial work, read `vault_context` in `.ai/ecc-prompt-workflow.json`; verify its project and repository (or repository_windows alias) match this checkout.
Use the registered `linux` path with Linux/WSL tools and `windows` with native Windows tools. These are aliases of the same vault, not separate vaults.
`CLAUDE_OBSIDIAN_VAULT` may be absent in desktop sessions; use the verified local record. If an injected path conflicts with that record, stop vault access and report the identity mismatch.
AIPM-bound attempts use only their delivered vault tools/job binding; do not switch to the source checkout's vault record.
Check access before reading `wiki/index.md` and `wiki/hot.md`, then search relevant notes. Unavailable access, permission errors or WSL failure do not prove the vault is missing.
Never create, initialize, adopt or substitute a vault unless the user explicitly requests it. Report access failures and continue with repository evidence.
The stack refreshes ECC source and this prompt only. It does not install ECC plugins or activate hooks, MCP servers, agents or scripts.
<!-- AI-STACK ECC WORKFLOW END -->
