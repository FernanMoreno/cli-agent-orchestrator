# US4 — prueba, gates y publicación verificable

Scope: T038–T045; approved spec015 C-005 and FR-018. Existing dirty and untracked work was inspected and preserved. No commit, tag, push, workflow dispatch, account-backed provider run, publication, or remote mutation was performed.

## Root causes and design

Graphify query `graphify query 'release workflows dependency CI real provider matrix'` found the matrix harness, readiness, receipt and cleanup contracts. The query was truncated (226 nodes; 46 displayed); important findings were verified against source rather than treating graph coverage as complete.

Source inspection established:

- Manual release returned before tag/history, previous publication and exact-SHA workflow checks.
- Version bump plus commit after SHA validation created a different release candidate.
- Manual PyPI accepted a skipped TestPyPI smoke gate.
- CI advertised Python 3.13/3.14 without matrix legs; mypy failures were tolerated; no required-gate aggregate distinguished missing/skipped results.
- Auth source defaulted to the runner home; symlinks allowed credential renewal to mutate reusable source records; fixture teardown stopped the server but retained copied secrets.

Release now requires an already committed expected version and successful CI/cargo-deny for that exact SHA. It checks out, archives and tags that SHA without a new bump commit. Compatibility tradeoff: version/changelog preparation must happen through normal CI before manual or weekly release; an unprepared candidate blocks with an explicit diagnostic. PyPI builds and provenance use the pinned SHA and both publication entrypoints require TestPyPI smoke success.

The real-provider job requires the trusted repository/main ref and a protected environment. Both workflow and fixture reject an absent/nonexistent/relative auth home or the runner home. Selected records are copied, private, and cannot resolve outside the dedicated auth source. Fixture and workflow teardown remove temporary secrets on normal completion, failure, cancellation and startup failure.

The manifest uses stdlib only, records version/revision, artifact sizes/SHA-256, lock hashes and resolved Python/npm/Bun/Cargo inventories. Its official CLI uses Git-tracked locks and compares their bytes plus pyproject.toml against the exact commit before emitting provenance. It refuses dirty metadata/locks and external symlinks. GitHub release attaches a source archive and manifest; Python publication uploads provenance separately from dist so PyPI never receives a JSON file as a distribution.

CI now covers all advertised Python 3.10–3.14 versions; mypy, source typechecks, architecture contracts and required job outcomes block release. Root and fleet-panel Python exports accompany the recursive dependency scan. Dependabot covers root/panel/plugin Python, distributed npm environments and Rust. cargo-deny also runs on main pushes, making exact main-SHA evidence available. Contributor guide copies and their policy tests were aligned.

US5 requested the already resolved PEP 440 parser be declared directly: packaging>=23.2 was added to runtime metadata and the matching root lock metadata only; resolved packaging 25.0 was retained. No speculative runtime library or dependency upgrade was introduced.

## Red/green evidence

All provider contracts below use local doubles and `-o addopts= -m ''`; no actual provider endpoints run. Marker deselection is not counted as verification.

| Task | Exact command / observation | Result |
|---|---|---|
| T038/T040 | `.venv/bin/python -m pytest test/test_integration_008_release_policy.py test/test_integration_008_ci_policy.py -q -o addopts=` | RED: 13 failed, 12 passed; manual bypass, missing matrix/gates and post-gate mutation reproduced |
| T039/T041 | `.venv/bin/python -m pytest test/scripts/test_build_release_manifest.py test/test_real_provider_matrix_contract.py -k 'manifest_records or manifest_is_deterministic or manifest_rejects or manifest_does_not or auth_home_requires or auth_copy_cannot or provider_fixture_cleans' -q -o addopts=` | RED: 12 failed, 2 passed, 66 deselected; missing manifest, unsafe auth and retained homes reproduced |
| T038–T045 | `.venv/bin/python -m pytest test/test_integration_008_release_policy.py test/test_integration_008_ci_policy.py test/scripts/test_build_release_manifest.py test/test_real_provider_matrix_contract.py -q -o addopts= -m ''` | Intermediate GREEN: 112 passed |
| T045 review | `.venv/bin/python -m pytest test/scripts/test_build_release_manifest.py -k cli_refuses -q -o addopts=` | RED: dirty lock accepted before fix; 1 failed, 1 setup error, 7 deselected. Setup error was full /tmp, not a product failure |
| T042 publication review | `TMPDIR=/home/felni/tmp-cao015-us4 .venv/bin/python -m pytest test/test_integration_008_release_policy.py -k manual_pypi_requires -q -o addopts=` | RED: 1 failed, 22 deselected; manual smoke skip reproduced |
| T038–T045 + docs | `TMPDIR=/home/felni/tmp-cao015-us4 .venv/bin/python -m pytest test/test_cao_contributing_skill_accuracy.py test/test_integration_008_release_policy.py test/test_integration_008_ci_policy.py test/scripts/test_build_release_manifest.py test/test_real_provider_matrix_contract.py -q -o addopts= -m ''` | Final GREEN: 193 passed, 3 skipped (optional zsh recipe checks; zsh unavailable). Log: /home/felni/tmp-cao015-us4/final-green.log |
| Neighbor contracts | `TMPDIR=/home/felni/tmp-cao015-us4 .venv/bin/python -m pytest test/test_http_only_boundary.py test/test_enforcement_tables.py test/test_agent_plugin_pin_boundedness.py test/scripts/test_wheel_matrix.py -q -o addopts=` | GREEN: 124 passed. Log: /home/felni/tmp-cao015-us4/neighbors.log |
| Metadata | `uv lock --check --offline` | PASS: 140 packages resolved, no version upgrades |
| Composition | `TMPDIR=/home/felni/tmp-cao015-us4 project-composition-check "$(cat .ai/project-name)"` | PASS: 400 files/1669 dependencies; all 5 architecture contracts kept. Log: /home/felni/tmp-cao015-us4/composition.log |

A previous neighbor command named nonexistent test/test_wheel_matrix.py, ran zero tests, and was corrected to test/scripts/test_wheel_matrix.py. Initial contributor checks exposed assertions preserving the old tolerated-mypy policy; these assertions were updated to the approved mandatory policy. Some initial runs hit a full /tmp; overlapping stale runs were interrupted and the final runs used the dedicated TMPDIR. No unrelated temporary files were deleted.

Focused black/isort checks passed for the seven changed Python files; `git diff --check` passed for tracked US4 changes. YAML is loaded by the policy/neighbor contracts, and the actual release shell is executed against command doubles. actionlint is unavailable locally; no actionlint pass is claimed. A local parsing smoke read all 12 current tracked locks and 2177 dependency entries across Python/npm/Bun/Cargo.

## Composition review

Changed boundaries: trigger → exact-candidate preflight → immutable checkout/build/tag; build artifacts → manifest → upload/publish; protected auth configuration → disposable credential copies → server/provider cleanup; declared compatibility/dependencies → mandatory CI results.

Reviewed neighbors: prior GitHub/PyPI publication checks, wheel/platform guards, plugin regeneration/pin boundedness, existing architecture/enforcement boundaries, provider receipts and sanitized JUnit evidence. Command doubles are sufficient for policy refusal and lifecycle assertions; source locks and architecture tools are real. Provider billing/account behavior and remote GitHub/PyPI execution were intentionally not exercised.

State ownership and ordering: preflight owns the candidate SHA; subsequent builds/checkouts consume it; no post-gate version mutation occurs. Manifest verification precedes upload. Required results must equal success; absence/skip/cancel/failure block. Copies do not write through to reusable credentials. Cleanup runs under BaseException and removes homes even when setup/stop fails. Release concurrency remains serialized with no cancellation of an active release.

Residual gates: root-level mypy completed under the parent verification and failed (latest run 531 errors in 81 files; see implementation-evidence.md). This is unresolved repository type-gate debt, not a passing gate; the new workflow intentionally blocks it. The local tests used Python 3.12; complete 3.10–3.14 execution awaits CI. Remote scanner availability, protected-environment setup, genuine provider accounts, and real artifact publication remain unverified.

Verdict: US4 implementation and focused local contracts PASS WITH RISKS; full repository/release readiness must remain blocked until its mandatory gates actually succeed. Graph refresh and overall spec015 signoff remain owned by the parent task. No durable vault write is needed for these task-local/generated facts.
