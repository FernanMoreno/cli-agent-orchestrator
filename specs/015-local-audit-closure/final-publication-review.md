# Final publication scope review — 2026-10-07

Review target: native candidate workspace at HEAD `87e2d5f3`, before staging. No source edits, commits, pushes, provider execution or remote publication were performed by this reviewer. Applied requesting-code-review and system-composition-review; read project AGENTS.md and AI_WORKFLOW.md composition requirements. This is a targeted publication/composition review, not an assertion that every line of the large accumulated diff received independent review.

## Publication-scope finding — resolved

**P2 — Excluding raw evidence can break newly maintained specification links.** `specs/008-integrate-fork-improvements/verification.md:28` and `:211` link local audit evidence JSON; `:38` links the old spec008 raw Graphify JSON. Lines 36, 44, 202 and 211 link audit summary Markdown. `specs/015-local-audit-closure/spec.md:164` links the originating audit summary. These documents are currently untracked, so the current tracked-file Markdown gate does not prove they remain valid after staging. CI calls `scripts/validate_markdown_links.py`; `discover_markdown_files` in `src/cli_agent_orchestrator/utils/markdown_links.py:67` enumerates tracked Markdown. Including those specifications while omitting their targets therefore creates broken maintained links in the committed checkout.

Recommended closure: retain sanitized maintained audit summaries; convert hyperlinks to intentionally private raw dumps/old graph JSON into accurate prose artifact references, or omit unrelated untracked specification evidence when it is outside the approved scope. Validate links after staging, from the selected publication tree. Do not publish raw dumps merely to satisfy links.

Parent-authorized Markdown correction converted unpublished raw-evidence/old-graph hyperlinks in spec008 verification and six maintained audit summaries into explicit code-formatted local artifact references. Historical conclusions were preserved. Parent-authorized follow-up converted invalid source-line fragments into working file links plus explicit historical line numbers, corrected repository-escaping audit references, and converted two unavailable local /tmp artifacts in spec007 acceptance to prose references. The project actual Markdown validator then passed the predicted publication scope: **457 Markdown files, 0 errors, exit 0**, with omitted audit evidence/raw Graphify targets modeled as absent. This is predictive candidate validation, not the exact staged-tree check; the parent must run that after selection.

No additional verified P1/P2 runtime defect was found in the inspected spec015 boundary implementations. Prior independent runtime, release/plugin and recovery reviews describe resolved defects; their source paths were sampled against the current implementation.

## Security review

Independently classified all 11 source/test findings from the redacted Gitleaks 8.30.1 candidate report. No genuine sensitive credential was found in this set. The remaining whole-directory findings in raw logs/caches are outside this classification and must remain excluded unless separately reviewed.

Five source generic-api-key matches are syntax, not embedded credential material: `services/local_peer_identity.py:86` (private-key type annotation and runtime lookup) and `services/work_service.py:938,941`, `services/work_origin.py:1261,1264` (transaction authentication argument/unpacking syntax).

Six test matches are explicit synthetic negative-test inputs: `test/services/test_memory_reconciliation.py:1100`; `test/services/test_wiki_lint.py:1429,1443`; `test/services/test_recovery_bundle.py:1998,2023`; `test/services/test_work_process_supervisor.py:1820`. Their surrounding assertions prove redaction, rejection without publication, or exclusion of server environment from a worker. Exact local annotations or syntax correction are justified; broad rule/path exclusions are not. The parent added 11 exact `gitleaks:allow` annotations; `/home/felni/cao015-final/scanner-annotation-proof.json` records identical ASTs and source positions for the seven edited files. The parent owns the final staged-tree scan. Secret values are intentionally omitted from this report.

## Reviewable commit scope

Include current project source, tests, required fixtures, examples, web/MCP Apps/TUI, scripts, locks/configuration/workflows, generated product packages required by drift checks, maintained documentation, and relevant specifications. Retain the two spec015 Graphify reports and source manifests needed to explain structural evidence; exclude the large old graph/cache rebuild and raw symbol dumps.

Preserve `test/providers/fixtures/bin/mock_cli` and all referenced fixture/program assets: `test/conftest.py:28` adds the mock CLI fixture directory to PATH, and `test/integration/test_local_peer_two_process.py` runs real mock workers. Searches of tests, scripts and workflows found no runtime/test dependence on `docs/audits` or raw Graphify JSON. `scripts/build_release_manifest.py` explicitly excludes Graphify from dependency-lock inventory. Markdown links are the material omission risk above.

## Composition Review Report

- Changed subsystems reviewed: local peer identity/auth/task receipt/runtime cleanup, workflow/event consumers, publication preflight/build/provenance, provider-isolation workflow, plugin trust/native projection and documentation selection.
- Neighboring consumers: CI required-gate aggregate, committed-source manifests, Markdown validator, real mock-worker fixtures, API/SQLite ownership and native provider startup.
- Contracts checked: C-001 terminal/receipt agreement and confirmed cleanup; C-002 identity/action/project authorization; C-003 authorized event replay; C-004 bounded recovery/owned-task cleanup; C-005 exact committed candidate and failed/skipped-gate refusal; C-006 current-content approval; C-007 honest evidence limitations.
- Source confirms release and PyPI preflight select CI/cargo-deny results for the exact candidate SHA; build/tag/archive checkouts use that SHA; production publish requires successful TestPyPI smoke. CI aggregate rejects missing, failed, skipped and cancelled required jobs.
- Architecture evidence independently read: `/home/felni/cao015-final/composition.log` reports **5 contracts kept, 0 broken; PASS**. Type evidence independently read: `/home/felni/cao015-final/mypy.log` reports **398 source files clean**. These are parent-run checks, not reviewer reruns.
- Reviewer executable check: scoped `git diff --check` for workflows/scripts/source/tests/web/MCP Apps/TUI/specifications exited 0.
- Existing review artifacts record real SQLite/filesystem/Git/HTTP and two-process local coordination scenarios. No additional broad integration suite was launched because the parent is running the final native matrix; its completion is required independently.
- External publication, protected-environment behavior, all remote Python-matrix legs and billed providers were not exercised here. Plugin publisher identity remains explicitly unverified. The existing GITHUB_TOKEN-created release event limitation remains a documented neighboring risk.

**Verdict: PASS WITH RISKS for inspected code; publication selection remains conditional on successful final native checks, exact staged-tree Markdown validation, and a clean exact staged-tree secret scan.** No full-suite or remote-publication success is claimed by this report.
