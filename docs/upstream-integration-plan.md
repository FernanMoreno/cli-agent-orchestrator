# Upstream integration inventory and verification plan

T081 / FR-024 (O05), inspected 2026-09-22. The inventories and simulations below
are historical. Current incorporation is documented separately from Git history.

## Estado actual — 2026-09-30

El candidato T085 aceptado en el clon aislado se incorporó al checkout main por
autorización posterior del usuario. Se aplicaron 505 rutas; 25 eran idénticas;
13 conflictos se revisaron preservando cambios locales posteriores. El snapshot
previo permite recuperar el estado anterior. HEAD, índice e historia no cambiaron;
no hubo commit, push ni publicación.

Los 13.491 casos del candidato mantienen su evidencia histórica. Los gates nuevos
de main y la matriz OpenCode v2 están registrados en
[spec 003](../specs/003-personal-deployment/completion-evidence.md).
[Estado vigente](../specs/001-verifiable-orchestration/acceptance-status.md).

## Local preflight update (2026-09-29)

Read-only local refs now show `HEAD` and `origin/main` at
`0d1b4ff9cf9caa88d28f16a1c9f54c22678813f4`, while the **unrefreshed**
`upstream/main` still points to `2fcc3efa6c6e70039e5b9a7308ee67cd1f024885`
(commit dated 2026-09-20). The merge base remains
`0ef89f358fa2b475b0025ccb86f8ad20bbadca38`; local divergence is 30
fork-only and 5 upstream-only commits. The current dirty tree has six tracked
path overlaps with that upstream diff: `docs/configuration.md`, API `main.py`,
CLI `launch.py`, `clients/database.py`, MCP `server.py`, and
`services/terminal_service.py`. No untracked path collides by name. These are
path overlaps, not a conflict simulation. The historical inventory below
describes the older `e04b6538` checkout and must not be used as the current
merge baseline. At that preflight stage, T085 had not fetched or integrated any
upstream change.

## Simulación del objeto local (2026-09-30)

`git merge-tree --write-tree HEAD upstream/main` salió con código 1 y detectó
12 conflictos en los commits indicados arriba. No modifica ramas, índice ni
worktree y no incluye las modificaciones locales pendientes. El inventario y
el plan concreto están en [upstream-preparation.md](../specs/001-verifiable-orchestration/upstream-preparation.md).
En esa fase de simulación aún faltaban autorización y aceptación; la preparación
y matriz posteriores completaron T085 en el clon aislado. No equivalen a un
merge de upstream en main.

## Evidence boundary

Only existing local Git objects were inspected. No remote freshness is claimed.
The configured upstream URL is `https://github.com/awslabs/cli-agent-orchestrator.git`.

| Reference | Inspected object |
| --- | --- |
| `HEAD` (`main`), `origin/main` | `e04b6538dd23050cb7f29a2eeb2c7599f980e9a1` |
| `upstream/main`, `upstream/3.0-beta1` | `2fcc3efa6c6e70039e5b9a7308ee67cd1f024885` |
| Merge base | `0ef89f358fa2b475b0025ccb86f8ad20bbadca38` |

`git rev-list --left-right --count HEAD...upstream/main` reports **9 fork-only,
5 upstream-only commits**. The upstream tip has commit date 2026-09-20; that is
not a fetch timestamp. Relative to the merge base, upstream changes **349 files,
72,770 insertions, 592 deletions**. These numbers describe committed upstream
changes, not the working tree or predicted merge conflicts.

## Divergent commits

Upstream-only commits, oldest first:

| Commit | Subject / integration surface |
| --- | --- |
| `9cecfc3b` | `build(deps): bump anyio from 4.14.1 to 4.14.2 in /examples/fleet/panel (#798)`; panel lockfile |
| `0e0e465d` | `feat(memory): use an Obsidian vault as a canonical CAO knowledge source (#644) (#674)`; vault identity, projection, migrations, memory and graph consumers |
| `9233416d` | `fix(mcp): persist handoff results durably to survive transport timeouts (#453)`; database, API, MCP and cleanup |
| `156cf1ed` | `feat(agent-plugins): agent plugins 1.0.0 support — client pipeline, CAO-as-plugin packages, MCP delivery (#573) (#584)`; agent plugin installation, provider delivery, settings, clients and packaging |
| `2fcc3efa` | `test(fifo): feed the liveness fixtures a real raw-stream buffer (#800)`; FIFO liveness regression fixtures |

Fork-only commits, newest first (including the merge commit):

| Commit | Subject |
| --- | --- |
| `e04b6538` | `merge: integrate orchestration lifecycle hardening` |
| `7c258a8c` | `feat(orchestration): harden provider lifecycle` |
| `9b6185f1` | `test: make real provider matrix dynamic` |
| `6c5c2e8d` | `fix: stabilize Claude OAuth startup for native children` |
| `c84f123a` | `test: add real provider lifecycle matrix` |
| `d9ec2d7d` | `feat(native): persist child lifecycle receipts` |
| `448ba873` | `docs: add orchestration roadmap` |
| `81478eb0` | `fix(status): probe initial rendered viewport` |
| `4e488cd1` | `feat: add opt-in at-most-once native delivery` |

## Overlap inventory

The following **28 tracked paths** have both upstream changes since the merge
base and local uncommitted changes relative to `HEAD`. Of these, **23** also
changed in the committed fork since the merge base. The five marked `dirty only`
have no committed-fork overlap. Path overlap indicates required review, not a
proven textual conflict; a conflict simulation was not performed.

| Path | Committed fork overlap |
| --- | --- |
| `README.md` | yes |
| `docs/api.md` | dirty only |
| `docs/configuration.md` | yes |
| `pyproject.toml` | dirty only |
| `src/cli_agent_orchestrator/api/main.py` | yes |
| `src/cli_agent_orchestrator/cli/commands/launch.py` | yes |
| `src/cli_agent_orchestrator/clients/database.py` | yes |
| `src/cli_agent_orchestrator/constants.py` | dirty only |
| `src/cli_agent_orchestrator/mcp_server/server.py` | yes |
| `src/cli_agent_orchestrator/providers/claude_code.py` | yes |
| `src/cli_agent_orchestrator/providers/codex.py` | yes |
| `src/cli_agent_orchestrator/providers/grok_cli.py` | yes |
| `src/cli_agent_orchestrator/services/agent_step.py` | yes |
| `src/cli_agent_orchestrator/services/cleanup_service.py` | yes |
| `src/cli_agent_orchestrator/services/config_service.py` | yes |
| `src/cli_agent_orchestrator/services/settings_service.py` | yes |
| `src/cli_agent_orchestrator/services/terminal_service.py` | yes |
| `src/cli_agent_orchestrator/utils/agent_profiles.py` | yes |
| `src/cli_agent_orchestrator/utils/orchestration.py` | yes |
| `test/providers/test_codex_provider_unit.py` | yes |
| `test/providers/test_grok_cli_unit.py` | yes |
| `test/services/test_agent_step.py` | yes |
| `test/services/test_cleanup_service.py` | yes |
| `test/services/test_settings_service.py` | yes |
| `tui/src/catalog.rs` | yes |
| `tui/src/server.rs` | yes |
| `uv.lock` | dirty only |
| `web/src/api.ts` | dirty only |

No upstream changed path collided with a local untracked file at inspection.
Untracked `.ai/`, `.specify/`, `specs/`, `graphify-out/`, architecture settings,
and test fixtures remain user work. New orchestration persistence files are being
implemented independently, so repeat this inventory before T085. Disjoint files
can still conflict semantically through shared database initialization, lifecycle,
configuration, or API contracts.

Reproduce the inventory without network access (Bash, repository root):

```bash
git status --short
git rev-parse HEAD upstream/main origin/main
git merge-base HEAD upstream/main
git rev-list --left-right --count HEAD...upstream/main
git log --reverse --format='%h %s' HEAD..upstream/main
git log --format='%h %s' upstream/main..HEAD
git diff --shortstat HEAD...upstream/main
comm -12 <(git diff --name-only HEAD...upstream/main | sort) <(git diff --name-only HEAD | sort)
comm -12 <(git diff --name-only HEAD...upstream/main | sort) <(git diff --name-only upstream/main...HEAD | sort)
comm -12 <(git diff --name-only HEAD...upstream/main | sort) <(git ls-files --others --exclude-standard | sort)
```

## Resolution plan for an explicitly authorized T085

1. Record the exact authorized upstream object and operation. Refresh remote refs
   only if requested, then recompute this inventory. Preserve the dirty tracked
   files and untracked artifacts; do not reset, auto-stash, or discard them.
2. Prepare an isolated integration workspace carrying the intended local work.
   Record baseline test results there before integration. Do not treat the clean
   committed `HEAD` as an adequate baseline for the dirty implementation.
3. Review upstream commits oldest first for dependencies. This order is a review
   aid, not authorization to cherry-pick or to select only part of upstream.
4. Resolve database/schema ownership before lifecycle callers, then API/MCP
   contracts, provider delivery/configuration, consumers, and packaging. Preserve
   both sides' invariants with regression tests; avoid whole-file ours/theirs
   replacements in shared files.
5. Run the applicable matrix below and the full local quality gates. Record
   actual pass/fail/skip evidence, unresolved limitations, and the final diff.
   Integration, history changes, publication and real-account execution remain
   subject to the user's explicit requested scope.

## Integration verification matrix

Paths described as upstream-only below exist in the inspected upstream object;
run them only after they are present in the authorized integration workspace.
These checks are a plan, not test results.

| Boundary | Resolution requirement and executable evidence |
| --- | --- |
| Database and vault identity | Upstream adds `source_kind` to memory identity and its NULL-scope unique index, plus vault projection/migration tables in `clients/database.py`. Keep fork receipt/state storage and new work persistence intact. Run `test/clients/test_database.py`, upstream-only `test/clients/test_vault_schema_migration.py`, `test/services/vault/`, and the current work-store tests. Verify repeat initialization, populated legacy DB upgrade, interrupted migration and reopen using real temporary SQLite databases. Never point migration tests at the user's database or vault. |
| Durable handoff and cleanup | Combine upstream durable handoff results with fork child/turn receipts; transport timeout must not become loss of result, duplicate delivery, or premature cleanup. Run upstream-only `test/clients/test_handoff_result_db.py`, `test/api/test_handoff_durability.py`, `test/mcp_server/test_handoff_durability.py`, plus local `test/services/test_turn_receipt_delivery.py`, `test/services/test_deferred_submit_verification.py`, `test/services/test_cleanup_service.py` and `test/api/test_aipm_native_delivery.py`. Exercise restart/reopen and timeout after persistence. |
| agent plugins, settings and provider launch | Preserve capability checks, local delivery receipts, environment precedence and provider startup handling when introducing upstream agent plugin projection/MCP delivery. Run upstream-only `test/agent_plugins/` (including no-auto-grant, launch safety, lifecycle locking and delivery tests), existing `test/providers/`, `test/services/test_settings_service.py`, `test/services/test_agent_step.py`, and configuration tests. Use mock providers for ordinary CI. |
| Memory and graph consumers | Keep canonical vault ownership, revision evidence, scope isolation and native-memory compatibility distinct. Run upstream vault reconciliation/concurrency, injection, migration, boundary-refusal and write tests plus `test/graph/` and memory API/service tests. Add composed regressions if the new local knowledge/work contracts share these paths; upstream features alone do not satisfy US3/US6. |
| FIFO and status monitoring | Retain upstream raw CRLF/SGR/redraw fixtures alongside fork initial viewport and provider lifecycle behavior. Run `test/services/test_fifo_reader.py`, `test/services/test_status_monitor.py`, `test/providers/test_codex_provider_unit.py` and `test/providers/test_grok_cli_unit.py`; cover sustained redraw and a settled output tail. |
| API, MCP, web and TUI | Preserve existing request/response compatibility, route ordering and settings/status meanings while adding upstream agent plugin/handoff surfaces. Run `test/api/`, `test/mcp_server/`, web tests/build and Rust checks below. Review `web/src/api.ts` and `tui/src/{catalog,server}.rs` against server contracts. |
| Packaging and dependency locks | Reconcile `pyproject.toml` and `uv.lock` together, keeping agent plugin resources and schema pins. Run upstream package/schema/ship-gate tests in `test/agent_plugins/` and the integrated CI packaging gates. The separate fleet-panel lockfile change requires its own project dependency validation. Do not publish artifacts. |

Baseline/full Python checks use the existing CI scope, excluding real-account E2E:

```bash
uv run pytest test/ examples/workflow/tests/ --ignore=test/providers/test_kiro_cli_integration.py --ignore=test/e2e -m 'not e2e'
uv run python scripts/validate_markdown_links.py
uv run lint-imports
project-composition-check "$(cat .ai/project-name)"
```

Include any new architecture/contract/integration tests under `tests/` in the
configured composition gate or explicitly run their relevant paths; the existing
CI `test/` selector does not automatically cover `tests/`. Check the integrated
CI configuration for additional upstream agent plugin/packaging gates before signoff.

Run `npm test` and `npm run build` from `web/`; run `cargo fmt --check`,
`cargo clippy --locked --all-targets -- -D warnings`, and `cargo test --locked`
from `tui/`. Perform a system-composition review of actual state ownership,
ordering, timeouts, restart recovery, configuration and client contracts. Use
Graphify to identify affected neighbors and verify its findings against source.

Real-provider matrix execution requires separate explicit authorization to use
real accounts. If unavailable, report that coverage as not run; mock/unit success
must not be presented as authenticated provider verification.

## Completion boundary

T081 delivers the pinned local inventory, overlap analysis and planned checks.
It does not establish that upstream is integrated or compatible. T085 remains
pending explicit authorization and measured integration results. This inventory
does not certify that the saved upstream refs are current, that paths without
overlap are safe, or that future merges will be conflict-free.
