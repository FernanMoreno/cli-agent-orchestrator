# Historical specification triage

Assessment: 2026-09-22, repository HEAD `e04b6538` plus the existing working
tree. Implements T080 / FR-025 of
[verifiable orchestration](../specs/001-verifiable-orchestration/spec.md).
This is a source-based inventory, not a declaration that remote alerts or CI
are currently green. No dependencies, workflow behavior, or historical records
were changed during triage.

## Inventory and fork decisions

Discovery enumerated repository `spec.md`, `requirements.md`, `design.md`, and
`tasks.md` files and searched `docs/**/*.md` for explicit unimplemented/planned
status markers. The only historical specification with a top-level
“Specified, not implemented” marker is #568 (both requirements and design).
#345 is included because its implemented design contains deferred follow-ups.
The active `specs/001-verifiable-orchestration/` feature is not historical work.
This inventory does not treat every roadmap suggestion as an approved spec.

| Record | Source-based classification | Explicit decision for this fork |
|---|---|---|
| [#568 requirements](issues/568-js-yaml-omap-dos/requirements.md), [design](issues/568-js-yaml-omap-dos/design.md), [tasks](issues/568-js-yaml-omap-dos/tasks.md) | Implementation present; status headers stale; exact-version design superseded. Some acceptance evidence remains historical or external. | Retain the dependency/security observability behavior. Do not repeat the 4.3.1 bump, remove the newer override, or call the whole issue verified from a version comparison. Remaining verification obligations are below. |
| [#345 OKF design](issues/345-okf-export-import/design.md) | Implemented historical record, with explicitly deferred capabilities. | Retain implemented OKF import/export and graph viewing. Keep the deferred capabilities outside the orchestration implementation scope; do not silently convert them into accepted tasks. |

## #568: evidence by acceptance criterion

Local history contains commit `7f3045d9` (`fix(security): bump js-yaml to
4.3.1 and make the Trivy gate legible (#568) (#569)`). Its lockfile delta is
exactly three removed and three added lines; it does not change
`docusaurus/package.json`. It also changes seven other files for the workflow,
documentation, script, and issue records. Consequently, the literal FR-3 demand
for a *one-file commit* was not met by that combined commit, even though the
dependency delta was minimal. This distinction should not prompt a history
rewrite or reimplementation.

| Criterion | Classification and verified local evidence | Remaining evidence or disposition |
|---|---|---|
| AC-1 / FR-1: patched 4.x resolution | Present, with the exact 4.3.1 assertion superseded. [Lockfile](../docusaurus/package-lock.json) has exactly one `js-yaml` node, at 4.3.2, the matching version in its tarball URL, and SHA-512 integrity metadata. The five dependents still request `^4.1.0`. Commit `5514bb7f` records the later 4.3.1 → 4.3.2 upgrade. | Registry tarball integrity and advisory state were not checked online. Version/URL assertions prove the local resolution, not all security acceptance criteria. Preserve 4.3.2. |
| AC-2 / FR-2, FR-3: no direct dependency or unnecessary manifest change | No `js-yaml` entry exists in the current [manifest](../docusaurus/package.json) dependencies/devDependencies. The original fix left the manifest unchanged. A current `overrides.js-yaml = ^4.3.2` entry supersedes DR-1's no-override recommendation; it is not a direct dependency. | Keep the newer override. FR-3's literal one-file commit differs from actual history as described above; do not claim exact historical compliance. |
| AC-3 / FR-5: docs install, typecheck, build | Historical tasks mark all three commands passed. [.github/workflows/gh-pages.yml](../.github/workflows/gh-pages.yml) retains docs build validation. | No fresh `npm ci`, typecheck, or build was run during this offline, documentation-only task; `docusaurus/node_modules` is absent. Current build acceptance remains unverified here. |
| AC-4 / FR-3, FR-7: commit the manual fix | Commit `7f3045d9` exists locally and contains the original 4.3.0 → 4.3.1 fix and matching integrity change. This is already delivered work, not an outstanding manual bump. | No new commit or remote action is needed for this inventory. The one-file-commit caveat remains recorded. |
| AC-5 / FR-4: unrestricted scan clean and alert #201 closed | [CI security job](../.github/workflows/ci.yml) retains an exit-code-1 SARIF scan and does not enable `limit-severities-for-sarif`. Historical tasks record a zero-finding unrestricted scan. | **Unverified currently:** a fresh whole-repository scan with severity unset and default scanners, and remote alert #201 state after the scan. Trivy is unavailable locally; no network or GitHub query was made. The lockfile version cannot establish either outcome. |
| AC-6 / FR-8, FR-9: readable failure output and preserved SARIF | Parsed current CI YAML: exactly two Trivy actions share SHA `57a97c7e7821a5776cebc9bb87c984fa69cba8f1`. Order is SARIF gate → `if: failure()` table (`exit-code: '0'`, no severity/scanner filter) → `upload-sarif` with `if: always()` and the same report path. | Implementation and structure verified; live failing-job table contents and successful upload were not exercised. Preserve these steps, rather than adding duplicates. |

Additional requirements and design assumptions not exhausted by AC-1–AC-6:

| Requirement / design decision | Current source evidence | Disposition |
|---|---|---|
| FR-6 / C-2–C-4 / DR-5, DR-7: local gate matches CI | [Wrapper](../scripts/security-scan.sh) `run_trivy` omits severity/scanner flags and keeps `--ignore-unfixed --exit-code 1`; [SECURITY.md](../SECURITY.md#running-security-scans-locally) and [DEVELOPMENT.md](../DEVELOPMENT.md) describe all severities. | Original correction exists. **Residual caveat:** neither the wrapper nor the documented command explicitly unsets inherited `TRIVY_SEVERITY` or `TRIVY_SCANNERS`; a caller's environment can narrow the local scan. Gate parity is conditional on an unrestricted environment. Record for a separate focused regression/fix; this triage does not change behavior. |
| NFR-1: no dependency-count increase | Original `7f3045d9` lockfile diff only changes `version`, `resolved`, and `integrity`; no nodes or edges added. | Satisfied for the original patch's scope. Today's larger dependency tree need not match a historical absolute count. |
| NFR-2 / DR-2: at most one extra scan, reuse cache | Exactly one failure-only table scan uses the same action pin. No distinct cache override appears in those steps. | One-extra-step structure verified. Actual cache reuse, DB downloads, and elapsed time require a failing CI execution; not proven by local YAML alone. |
| NFR-3 / C-5: verification must not rewrite `uv.lock` | Local wrapper and documented export use `uv export --frozen`. Existing `uv.lock` changes predate this task. | Source mitigation present; scan wrapper was not executed, so runtime non-mutation was not retested. Existing changes were preserved; never use the historical suggested `git checkout -- uv.lock` cleanup in this dirty workspace. |
| C-1 / DR-6: count all copies | Executable JSON traversal found one `node_modules/js-yaml` and all five `^4.1.0` dependents. | Verified; a top-level version grep alone would not have been sufficient. |
| OQ-1–OQ-4 and DR-4 | Historical policy questions include severity narrowing, Dependabot coverage, non-frozen CI export, override policy, and a 5.x upgrade. Current CI still uses plain `uv export`; the later override exists. | Severity narrowing and 5.x migration remain outside this fork task. Do not re-open the fulfilled bump to implement unrelated policy/dependency changes. Dependabot behavior and remote advisory state remain unassessed. |

Fork ownership: retaining and verifying the security gate belongs to this fork.
Fresh build/scan/remote-alert evidence and the inherited-environment caveat are
remaining security-maintenance obligations, not blockers to writing this
inventory and not evidence that the original implementation is absent.

## #345: deferred work versus implemented outcomes

The design already labels itself “Implemented historical design record.” Source
inspection confirms the [archive registry](../src/cli_agent_orchestrator/services/memory_archive/__init__.py),
[OKF backend](../src/cli_agent_orchestrator/services/memory_archive/okf.py),
[CLI](../src/cli_agent_orchestrator/cli/commands/memory.py), and
[read-only export API](../src/cli_agent_orchestrator/api/main.py). These are
source checks, not a fresh execution of the archive test suite.

| Design follow-up | Source evidence | Fork disposition |
|---|---|---|
| Continuous `cao memory sync <dir>` | Memory CLI declares `export` and `import` commands but no `sync` command. | Deferred; not required by the approved orchestration feature. Existing export is not proof of continuous sync. |
| Graph viewing / sinks | [GraphView](../src/cli_agent_orchestrator/graph/models.py), [memory graph provider](../src/cli_agent_orchestrator/graph/providers/memory.py), and [OKF](../src/cli_agent_orchestrator/graph/sinks/okf.py), [Obsidian](../src/cli_agent_orchestrator/graph/sinks/obsidian.py), and [GraphML](../src/cli_agent_orchestrator/graph/sinks/graphml.py) sinks exist. | Retain the implemented viewing outcome; no duplicate graph implementation. It does not satisfy continuous sync. |
| POST `/memory/import` | API source declares GET `/memory/export`; no `/memory/import` route was found. | Deferred; requires separate authorization/threat-model design before becoming fork implementation scope. |
| Bidirectional editing | Design D4 explicitly rejects a second writable mirror; importer consumes a bundle through the memory service. | Intentional non-goal, not missing implementation. |
| `cao` archive backend | Registry registers `OkfArchiveBackend`; no `cao` backend registration exists. `export_bundle_to_tar` in `okf.py` is an OKF tar export, not the proposed full-fidelity CAO format. | Deferred; do not equate existing tar packaging with this unbuilt backend. |
| See-Also ingestion on import | `OkfArchiveBackend._import_one_topic` strips the block via `_strip_see_also_block` and increments `report.see_also_dropped`. | Deferred; source still implements the documented lossy import. No orchestration requirement authorizes changing that contract here. |

## Checks performed and limits

- Enumerated specification artifacts and searched historical status markers.
- Read original requirements/design/tasks and compared local git history with
  current lockfile, manifest, CI, security docs, wrapper, and memory sources.
- Ran Node JSON assertions: one 4.3.2 node, matching tarball URL, SHA-512
  metadata present, no direct dependency, five `^4.1.0` consumers.
- Parsed CI with PyYAML and asserted action counts/pins, formats, exit codes,
  failure/always conditions, ordering, and matching SARIF output/upload path.
- Ran `bash -n scripts/security-scan.sh` successfully.
- Reviewed this document, verified all local links, and ran
  `git diff --no-index --check /dev/null docs/historical-spec-triage.md` for it. No application
  behavior changed; application/composition gates are owned by the broader
  implementation task, not claimed as executed by this documentary triage.

No package install, build, network access, remote alert inspection, Trivy scan,
commit, or dependency modification was performed. Historical checked boxes
remain historical evidence; missing current execution evidence stays explicit.
