# US5 local plugin trust evidence

## Scope and root causes

T046–T051 implement contract C-006. Existing source resolved Git references and
saved `resolved_ref`, but `PluginRecord` retained no content baseline, producer
status, compatibility or permission review. A loadable install immediately
projected skill instructions and delivered MCP configuration. Runtime skill
catalog/content loading also trusted an existing projection without checking
whether its installed bytes still matched approval.

Graphify query `agent_plugins installer PluginRecord projection mcp_delivery
trust` identified `PluginRecord`, installer, store and projection neighbors;
the query was truncated and the generated graph was not treated as proof.
Source review confirmed installer → store/projection, MCP delivery → mapper,
runtime skills → catalog/content, terminal launch → native projection, and
Kiro glob/OpenCode directory symlink paths. Parent owns final graph refresh
and aggregate gates; no vault write.

## Behavior

- Installation/review/listing permit unverified packages but installation is
  disabled. Explicit local approval binds SHA-256 tree content, original source
  and ref, resolved commit, version/schema, compatibility and requested perms.
- Declared manifest author is separate from producer status: publisher identity
  remains **unverified**. Local approval verifies the operator's exact content
  decision, never a publisher identity. Commit pins identify objects only.
- Missing legacy trust fields deserialize safely and fail closed for delivery.
  Explicit review/approval can establish their first local baseline.
- Changed content or changed reference evidence and incompatible schema/version
  contracts cannot be enabled. Replacement installs revoke prior approval.
- Projection election, MCP collection and fresh runtime skill catalogs/content
  all recheck exact approval. Ordinary user-owned skills retain their behavior.
- Fresh Kiro/OpenCode launch reconciles physical managed projections before
  native scans. Failed removal blocks launch. `asyncio.to_thread` keeps lifecycle
  lock waits and hashing off the shared event loop.
- Unreadable packages remain listable, disabled, with unavailable evidence and
  no approvable review ID.
- CLI review/enable/disable show policy and evidence. Exact permission approval
  admits package delivery; it does not alter provider/profile tool allowlists.
- Optional `extensions["org.cao.trust"]` declares CAO packaging-version bounds and
  additional permissions. Skill and mapped server names are always enumerated.

## Regression and checks

Initial focused commands with `-k us5 --no-cov -q` demonstrated missing
review/enable APIs and an install immediately exposing the `example` skill.
The initial four focused cases subsequently passed (4 passed, 109 deselected).
The downstream tamper regression then changed valid SKILL.md instructions after
approval and reproduced their continued presence in a fresh runtime catalog;
the consumer guard was added only after observing that failure.

Existing delivery suites stamp actual exact approval at the **test publication
boundary**. They do not bypass any production trust predicate or tool grant
check. All new `test_us5_*` regressions deliberately exclude this fixture.
Their real filesystem/Git/store/CLI assertions cover disabled installation,
missing producer evidence, exact approval and permission refusal, changed
content and reference, incompatibility, old records, and absence of auto grants.

`TMPDIR=/home/felni/tmp-us5 .venv/bin/lint-imports`: PASS, 5 kept / 0 broken,
400 files and 1673 dependencies after the runtime consumer guard.

An initial full package run encountered `/tmp` tmpfs capacity exhaustion
(SQLite `database or disk is full` during unrelated autouse test database
setup). It was stopped; no pass claim is made for it. Reruns use a dedicated
folder on the main filesystem with ample capacity. Split runs and final focused follow-up counts appear below; no single complete
final-package pass is claimed. Root owns the aggregate project composition gate.

## Composition review

Changed subsystems: plugin records/install lifecycle/trust, projection election,
MCP delivery, runtime skills, native launch, API listing and CLI. Reviewed downstream prompt/catalog and
provider configuration paths. Store lifecycle lock spans approval writes and
projection rebuild; content is checked again before approval persistence.
Revocation/enable is retryable; a failed rebuild retains truthful approval state
and a later rebuild can reconcile projection. Replacing content resets approval.
Validation parses source skills directly and avoids recursion into runtime trust
checks. Architecture contracts pass. Filesystem, local Git subprocess and
provider allowlist machinery are real dependencies; no external signer/network
service is introduced. HTTP write routes retain disabled install semantics and
cannot bypass the downstream trust gates.

Residual limits: no trusted publisher attestation verifier; producer identity is
therefore reported unverified even after local approval. This is not a sandbox
for arbitrary skill instructions or MCP processes. Already-running clients can
retain loaded snapshots and need restart for complete revocation; already-loaded
external snapshots cannot retroactively revoke execution. Fresh native launches
reconcile physical projections and block when removal fails. A hostile process
concurrently editing the same user's package files is outside the lifecycle
lock; each future CAO catalog/MCP/projection checks current content, but this
implementation is not a filesystem isolation mechanism.

Verdict: focused regression result below; architecture PASS. Composition PASS
WITH RISKS for the stated limitations. Parent owns the aggregate project gate
and reports unrelated baseline type debt.

Additional red/green findings:

- `test_us5_full_commit_pin_cannot_resolve_a_same_named_branch` used a real local
  repository whose branch name equalled an earlier full SHA but pointed to a
  later commit. The old resolver accepted the later content (then `DID NOT RAISE`
  under a refusal assertion). Resolver now compares full requested SHA against
  resolved HEAD before publication and refuses missing/mismatched evidence.
  Focused rerun: **1 passed, 37 deselected**.
- CLI list JSON lacked effective current trust status (`KeyError: review`). It
  now adds a current `review` while retaining the record-list shape, so listing
  cannot imply an old approval is still current.
- After the runtime consumer guard: **6 passed, 109 deselected** for the new
  scenarios in the four owned test files (before the final CLI-list assertion
  and commit-pin case were added). A full four-file run checks those additions.


## Final verification and downstream migration

Pytest commands use `CAO_HOME_DIR=/home/felni/tmp-us5/isolated-cao`,
`TMPDIR=/home/felni/tmp-us5`, `.venv/bin/python -m pytest`, and `--no-cov -q`.

- Six owned focused files: **149 passed** (`final-focused.log`).
- Composed checks: **263 passed, 2 warnings, 116.59s** (`final-composed.log`):
  `test/utils/test_skills.py`, `test/utils/test_skill_injection.py`,
  `test/cli/commands/test_skills.py`, and package `test_no_auto_grant.py`,
  `test_cli.py`, `test_resolver.py`, `test_provider_provenance.py`,
  `test_edge_contracts.py`, `test_lifecycle_lock.py`. Includes all four native
  provider × revocation success/failure cases before the event-loop assertion.
- Remaining package files: **679 passed, 2 failed** (`remaining-suite.log`).
  Root causes: denied delivery lost existing `projection.source_missing`
  diagnostic; Git cleanup mock supplied no HEAD and no staged clone tree for
  an exact-SHA request. Production preserves the companion finding; the mock
  now supplies the exact SHA and staged `.git` file. Focused follow-up below
  exercises both, without weakening pin enforcement.
- Native async RED: **2 failed, 2 passed** (synchronous reconciliation prevented
  the event-loop callback from releasing its wait). GREEN command
  `test/agent_plugins/test_no_auto_grant.py -k native`: **4 passed,
  28 deselected, 24.57s** (`native-final.log`). Actual Kiro/OpenCode launch scans
  retain user skills, exclude changed plugin content, and fail before initialize
  when stale projection removal fails.
- Unreadable CLI RED exit 1 PermissionError. Central helper GREEN command
  `test/agent_plugins/test_cli.py -k unreadable`: **1 passed, 44 deselected,
  42.55s**. Old approval cannot enable an unreadable package.
- API owner gate: **63 passed, 8 warnings, 25.94s** (43 API cases plus ticket/WS),
  including current tamper review and selective unavailable review preserving
  healthy package status; no duplicated API fallback.
- Final `TMPDIR=/home/felni/tmp-us5 .venv/bin/lint-imports`: **5 kept, 0 broken**
  (`imports-final.log`). Black applied; demo `bash -n` and `git diff --check`
  exit 0. Parent owns final whole-project gates.

Offline dogfood RED failed projection assertions because it assumed install
immediately enables content. Its local fixture explicitly reviews the exact
disabled package and five declared permissions, then enables that review ID.
Offline rerun **PASS** (`/home/felni/tmp-us5/dogfood-green.log`), including
allowlist equality, collision handling and removal. This is demo-specific
approval, never a production trust bypass. README and recorder comments now
explain review/enable. Video renderer dependencies were not installed and the
GIF was not regenerated; docs mark the checked-in capture historical. Optional
live OpenCode observation was not run in the offline demonstration.


Final affected regression command:

```bash
CAO_HOME_DIR=/home/felni/tmp-us5/isolated-cao TMPDIR=/home/felni/tmp-us5 \
.venv/bin/python -m pytest \
 test/agent_plugins/test_projection.py \
 test/agent_plugins/test_tail_branch_contracts.py \
 test/agent_plugins/test_no_auto_grant.py \
 test/agent_plugins/test_cli.py \
 test/agent_plugins/test_resolver.py \
 test/agent_plugins/test_provider_provenance.py \
 test/agent_plugins/test_edge_contracts.py \
 test/agent_plugins/test_lifecycle_lock.py --no-cov -q
```

**193 passed, 2 warnings, 120.47s**, exit 0
(`/home/felni/tmp-us5/final-regressions.log`). Includes both repaired cases,
all four native revocation/responsiveness cases, and unreadable CLI status.
Final self-review verified permission approval never updates tool grants,
producer identity remains unverified, inaccessible/altered content fails closed,
and architecture direction avoids validation/runtime recursion. No commits,
pushes, publishing, resets, or deletion of shared artifacts.
