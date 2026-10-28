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

Before freezing source hashes or launching a broad regression, run the commit hook's applicable format/import/lint checks on the changed files. Fix those checks first; a later formatter mutation invalidates the frozen source evidence and must not be relabeled as the tested bytes.

When overriding an attribute already patched by another fixture, make the override a dependent fixture with a nested patch scope. Verify restoration after its consumers before broad regression; teardown from an unrelated monkeypatch fixture can resurrect a stale mock and invalidate downstream checks.

Use `verification-before-completion` before substantial completion.

### 6.1 Focused native/provider diagnostics

- Pin complete native runtime packages, not only their main executable. Codex0.164 resolves its Code Mode host relative to the installed package/executable; a loose copied binary can pass version/catalog/direct-MCP controls and still fail when the model first invokes Code Mode. Verify independently frozen metadata, helper/resource inventory, hashes and layout before credential staging; exercise the real helper handshake without a model before a model/tool retry. Do not disable Code Mode or enlarge permissions to hide missing assets.
- Reuse the production terminal-escape parser for raw status patterns and a rendered pane for screen claims. A new greedy OSC regex can erase a real footer, and a quiet gap during `loading` is not startup completion. Replay the actual captured bytes offline before another native run; account bootstrap may require network even when no model turn is requested.

- Before arming a new launcher, compile its actual bytes, execute all its import statements under the exact invocation/PYTHONPATH, bind its direct helper calls to current signatures, and verify owner context-manager versus iterator protocols without acquiring services. Selected smoke imports do not validate historical fixtures' bare imports; a callable signature alone does not establish its returned ownership protocol.

- For authenticated MCP-to-API controls, carry the API's auth-enablement selectors as well as its private credential source into the MCP child. A token file alone is ignored by `get_local_bearer` in a child with auth disabled. Check the live AG-UI route is enabled before subscribing, and retain the raw native result privately before waiting for downstream effects so a rejected call cannot become an unexplained delivery timeout.

- Apply credential masks after broader filesystem binds: a later parent mount can cover an earlier mask. Before starting a native diagnostic, probe the actual sandbox namespace for credential invisibility and the required network isolation; requested flags alone are not evidence.

- An interpreter digest binds executable bytes, not its virtual environment. Validate the exact invocation through closed imports before activation; use the verified virtualenv entrypoint when its resolved binary alone lacks dependencies. A generated enabled runner is not yet its executed archive: create that separate0600 archive from independently verified bytes before launch, retain its actual hash, and reclose only operational descriptors after owner closure while preserving executed evidence unchanged.

- State the first unobserved boundary and the one observation that will distinguish the current hypotheses. Prefer instrumenting an existing working real journey over constructing a surrogate journey.
- When deriving a synthetic native-provider control, carry forward the demonstrated context/output limits and request multiplicity from the successful control for that exact binary. Auxiliary requests can share model, nonce and stream settings with the primary request; ordinal1 is not primary identity. Review these preconditions before execution and distinguish completed-result capability from pre-finish activity.
- Reuse production configuration, startup/readiness gates and protocol schemas. Before a real run, exercise the actual controller-to-service initialization boundary and verify the required tool is discovered/offered; configuration declarations and isolated packet-builder tests are insufficient.
- Before live polling, drive the installed controller through at least two observations using its real private writer and strict reader. Exclusive-create evidence needs a unique file per event; verify archive mode0600 and its executed-byte hash with the strict reader before handing it to the next stage.
- Separate harness defects, offline evidence, actual diagnostics and final acceptance. After a harness failure, reproduce and fix its first incorrect boundary before another expensive run; preserve the failed receipt and use fresh source-bound launchers.
- Keep reviews scoped to the observation needed. Optional diagnostics, generic hardening and unrelated refactors do not block progress unless they affect that observation's correctness, privacy or cleanup. Do not add fields or review rounds merely to improve an otherwise adequate control.
- Give read-only peer reviewers a prepared scoped diff and frozen file list. They cannot execute Git or tests; avoid whole-file exploration for a bounded change.
- Even offline pytest processes share coverage output by default. Serialize them, or give each invocation a distinct private COVERAGE_FILE; never combine or erase output while another writer uses it. Preserve coverage settings, thresholds and failed-run evidence. Finish a run before editing the files it imports.
- Before parallel pytest runs, inspect autouse fixtures for real databases. Use an explicit verified PostgreSQL metadata binding with autostart disabled, and keep its owner alive until every borrower exits; a unit-test filename does not establish an offline suite.
- For private launcher admission, select the top-level flag assignment with AST inspection rather than counting flag spellings that may also occur in validation strings. Validate every candidate hash before copying any file; then arm only the reviewed execution descriptors.
- Bind a generation receipt to its archived executed runner bytes and actual plugin, not expected hashes derived from that receipt itself. Keep executed and re-closed descriptor hashes distinct; verify the flag-only derivation and re-close the generation stage before building the managed stage.
- Before an expensive private HTTP diagnostic, validate the outgoing body with the production request model or the owned API's actual OpenAPI schema. Include a negative control for any previously rejected shape before invoking the provider. Similar endpoint names do not imply identical fields.
- Freeze reviewed launcher bytes after scoped edits and formatter checks. Avoid global text replacements across definitions and call sites; validate local helper call signatures before arming a private execution copy. Host processes outside the run must be excluded by proven ownership/lifetime, not by treating unreadable new processes as absent.
- Preserve acceptance criteria and coverage. Record durable causes and prevention rules in Obsidian; keep raw logs and temporary execution details in the repository/private receipts instead.
- When workspace aliases differ from editable-import paths (including OneDrive letter case), run verification from the same canonical checkout path used by imports. Verify coverage measures the executed source; fix the invocation rather than changing coverage settings or thresholds. Before launching a source-bound child, assert literal equality of Path.cwd(), the configured repository root and the imported fixture root; Path.resolve() does not canonicalize Windows case aliases. Pass the canonical workdir and repository environment binding explicitly instead of deriving them from an inherited cwd. Read full commit identities from Git or the verified manifest; never expand an abbreviated hash by hand.
- When formatting repository files together with private diagnostic files, pass the repository formatter configuration explicitly. Mixed roots can select default settings; review the diff and remove unrelated formatting before validation.
- If a subprocess fixture times out before its protocol starts, measure import startup separately before changing product behavior. On DrvFS, a private Linux `PYTHONPYCACHEPREFIX` primed from the same pinned canonical imports under the held PostgreSQL owner can reduce source-loading overhead. Preserve deadlines, actor/barrier assertions, source hashes and failed receipts; a warm-cache pass does not accept cold-start latency.
- Cancellation/reinitialization checks must exercise the real provider's receipt-aware detector as well as controlled lifecycle barriers. A fake `initialize=True` cannot establish replacement readiness while the previous task receipt remains fenced; distinguish transport startup from task completion without releasing that fence early.
- Keep preflight attempt inventories separate from managed-run inventories. Reaping the launcher does not establish descendant exit: verify retirement of the exact owned session/process identities within a bounded wait. When deriving a capture fixture, retain line indentation and row boundaries and record the producer hash and exact suffix boundary.

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
