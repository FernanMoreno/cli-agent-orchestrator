# Final CI security gate — 2026-10-07

The initial exact CI Trivy gate failed with 11 fixable vulnerability findings. After targeted dependency overrides and regenerated lockfiles, the same gate exited 0 with zero findings. No scanner severity, ignore policy, or CI configuration was relaxed.

## Scanner and publication scope

`.github/workflows/ci.yml` pins `aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25`. Its [official action metadata](https://github.com/aquasecurity/trivy-action/blob/ed142fd0673e97e23eac54620cfb913e5ce36c25/action.yaml) selects **Trivy v0.70.0**. The downloaded official Linux binary archive was checked against the release checksums; SHA-256 `8b4376d5d6befe5c24d503f10ff136d9e0c49f9127a4279fd110b727929a5aa9`.

Although the workflow inputs say `severity: CRITICAL,HIGH`, the pinned [entrypoint](https://github.com/aquasecurity/trivy-action/blob/ed142fd0673e97e23eac54620cfb913e5ce36c25/entrypoint.sh) removes that filter for SARIF unless `limit-severities-for-sarif` is true. This workflow does not set that option. The actual gate therefore includes **all severities** with `ignore-unfixed: true`, `exit-code: 1`, filesystem scanning and SARIF output. Both runs used the exact pinned entrypoint and these inputs; scanner defaults include vulnerability and secret detection.

The initial diagnostic and remediation scans used a selected working-file snapshot: all tracked/current nonignored source files, except `docs/audits/evidence/**` and generated `graphify-out/**`; the four maintained spec015 graph reports/manifests were retained. There were 2,401 source files before generated CI requirements exports. Ignored local `.venv`, `node_modules`, caches and build output are absent because the CI security job checks out source and does not install those trees. No dependency lockfile was excluded. This selected snapshot omitted unchanged tracked Graphify baseline artifacts and is therefore superseded for publication-scope proof by the exact staged-tree scan below. Both root `uv export --locked --all-extras --format requirements-txt` and fleet panel `uv export --project examples/fleet/panel --locked --format requirements-txt` were generated, matching CI. Files and logs are under `/home/felni/cao015-final-sandbox/trivy/`.

Vulnerability database version 2 was updated at `2026-10-07T07:38:55.515026687Z` and downloaded at `2026-10-07T10:23:16.197374455Z`.

## Exact staged-tree acceptance

The final acceptance scan used `git archive` of the actual candidate index tree **`ea2d8af417d973a15d3ed453a41aea2d67a50fb7`** from `git write-tree`, with **4,160 tracked publication files (377 MiB)**. This retains all unchanged tracked baseline artifacts, including Graphify files; the scan applied **no path exclusions**. Fixed dependency overrides were verified in the exported tree. Root and fleet panel requirements were freshly exported with the exact CI locked-export commands inside the private archive checkout.

The same checksum-verified Trivy 0.70.0 and pinned action entrypoint ran with SARIF, `ignore-unfixed: true`, `exit-code: 1`, and the workflow's effective all-severity policy. **Exit 0; zero SARIF findings.** This exact-tree result supersedes the smaller selected snapshot as publication-scope evidence. Scanner database and ignore policy were unchanged. Subsequent edits to this evidence document record the immutable scanned tree identity; no dependency manifests or source behavior were changed afterward by this task.

Private archive: `/home/felni/cao015-final-sandbox/trivy/staged-checkout`; full file manifest: `staged-files.txt`; output: `staged-results.sarif`; scanner log: `staged-scan.log`; observed exit: `staged-exit-code.txt`.

## First failure and minimal remediation

All findings were transitive dependencies in Docusaurus or the two portfolio lockfiles. Docusaurus 3.10.2 and the portfolio direct dependencies remain unchanged. The failing scanner output was the red proof; rescanning the regenerated locks was the green proof.

| Package / locked version | CVE | Severity | Remediation |
| --- | --- | --- | --- |
| compression 1.8.1 | CVE-2026-87776 | HIGH | compatible override `^1.8.2`, locked 1.8.2 |
| joi 17.13.7 | CVE-2026-90771 | MEDIUM | compatible override `^17.13.8`, locked 17.13.8 |
| postcss-selector-parser 6.1.4 and 7.1.4 | CVE-2026-104844 | MEDIUM | override `^7.1.6`, all instances locked 7.1.6 |
| proxy-addr 2.0.7 | CVE-2026-90711 | CRITICAL | compatible override `^2.0.8`, locked 2.0.8 |
| shell-quote 1.10.0 | CVE-2026-102422 | CRITICAL | exact override and lock 1.11.0 |
| source-map-js 1.2.1 | CVE-2026-93749 | HIGH | compatible override `^1.2.2`, locked 1.2.2 |
| tinypool 1.1.1 | CVE-2026-104848 / CVE-2026-104849 | CRITICAL | exact override and lock 2.1.2 |
| fast-uri 3.1.7, both portfolio locks | CVE-2026-86472 | MEDIUM | existing override raised to `^3.1.8`, locked 3.1.8 |

Tinypool has no patched 1.x release. Its [2.0 release](https://github.com/tinylibs/tinypool/releases/tag/v2.0.0) drops Node 18 support; Docusaurus already requires Node >=20, and validation uses Node 24.19.0. The [maintainer run-options advisory](https://github.com/tinylibs/tinypool/security/advisories/GHSA-85c8-ppgw-ccpr) identifies 2.1.2 as fixed. Docusaurus's actual Tinypool constructor/options and `pool.run(task)` interface were inspected before the override. The [selector parser advisory](https://github.com/postcss/postcss-selector-parser/security/advisories/GHSA-rj75-hqrm-r3gf) supplies a 7.1.6 fix, requiring the remaining 6.x CSS consumers to move to 7.x. The full production CSS build is the relevant compatibility proof. [Fast URI's advisory](https://github.com/fastify/fast-uri/security/advisories/GHSA-hrr3-gc8f-f4qj) confirms the same-major 3.1.8 patch.

Pre-remediation copies of the six manifests/locks are retained in `trivy/pre-remediation/`, so rollback preserves all prior work. Source changes are restricted to those six dependency files, synchronized to the shared workspace and native candidate.

## Fresh verification

- Exact pinned action-entrypoint scan before remediation: exit 1; 11 results (4 CRITICAL, 2 HIGH, 5 MEDIUM). JSON diagnostic agrees. No other project manifests produced findings.
- Exact same action-entrypoint scan after remediation: **exit 0, zero SARIF results**, including the effective all-severity gate. Known unfixed advisories remain subject to the existing `ignore-unfixed` policy; none were newly allowlisted.
- Docusaurus `npm ci`, `npm run typecheck`, `npm run build`: **exit 0** with Node heap limited to 2 GB. Production static files generated. npm noted blocked postinstall hooks for `@swc/core` and `core-js`; the actual build completed successfully.
- Bun 1.3.10 (CI-pinned version): both portfolio roots `bun install --frozen-lockfile` succeeded after lock regeneration. Parent portfolio `bun run check` passed typechecking and **29 tests, 0 failures, 134 assertions** in 16.90 s. Those tests exercise the nested skill's scripts; the standalone nested manifest has no independent test script.
- Actual SSG worker compatibility proof: **exit 0**, external-config production build enables only `future.faster.ssgWorkerThreads`, with exactly two workers and performance logging. The log records `Create SSG thread pool - 2 threads`, workers 1 and 2 initializing and rendering all 42 routes, thread pool completion, and generated static files. Build completed in 5.28 s. Default repository config does not enable worker SSG; merely setting the thread-count environment variable is insufficient. The external config uses the installed preset absolute path because preset lookup otherwise starts at the external config directory; no repository config changed.

## Maintainer references

- CVE-2026-87776: [maintainer source](https://github.com/expressjs/compression/releases/tag/v1.8.2), [maintainer source](https://github.com/expressjs/compression/security/advisories/GHSA-vc2v-76pw-4v95).
- CVE-2026-90771: [maintainer source](https://github.com/hapijs/joi/releases/tag/v17.13.8), [maintainer source](https://github.com/hapijs/joi/releases/tag/v18.2.9).
- CVE-2026-104844: [maintainer source](https://github.com/postcss/postcss-selector-parser/releases/tag/7.1.6), [maintainer source](https://github.com/postcss/postcss-selector-parser/security/advisories/GHSA-rj75-hqrm-r3gf).
- CVE-2026-90711: [maintainer source](https://github.com/jshttp/proxy-addr/releases/tag/v2.0.8), [maintainer source](https://github.com/jshttp/proxy-addr/security/advisories/GHSA-jqcg-44mw-7w3h).
- CVE-2026-102422: [maintainer source](https://github.com/ljharb/shell-quote/security/advisories/GHSA-pqg4-j6r4-53mv).
- CVE-2026-93749: [maintainer source](https://github.com/7rulnik/source-map-js/releases/tag/v1.2.2).
- CVE-2026-104848: [maintainer source](https://github.com/tinylibs/tinypool/releases/tag/v2.1.1), [maintainer source](https://github.com/tinylibs/tinypool/security/advisories/GHSA-5gmw-xhrv-c9v3).
- CVE-2026-104849: [maintainer source](https://github.com/tinylibs/tinypool/releases/tag/v2.1.2), [maintainer source](https://github.com/tinylibs/tinypool/security/advisories/GHSA-85c8-ppgw-ccpr).
- CVE-2026-86472: [maintainer source](https://github.com/fastify/fast-uri/releases/tag/v2.4.7), [maintainer source](https://github.com/fastify/fast-uri/releases/tag/v3.1.8).

## Evidence identities

- `results.sarif`: SHA-256 `b17875aad10132c6f97aefe86b04bac2dc0ffcd54470b87bdc22cc95a3f728d1`.
- `results.json`: SHA-256 `1b6222ef7e09b52be35b6c3290f9ef200d12886c48ed0ef7e89243a109f50746`.
- `results-final.sarif`: SHA-256 `4e3eee9cbca1cdab2e7b8f7259ef2f07c79ce506f2a7bce88736c181cbd78327`.
- `scan-final.log`: SHA-256 `9798d030b66dec9c2b40197cacd339c19140de80099ebd19e76610db71b4b6cd`.
- `docusaurus-typecheck.log`: SHA-256 `2341968adc01bfe9c9cc04e3dab36502754f694192871ed282052673ab297458`.
- `docusaurus-build.log`: SHA-256 `16f239fd648bdb720b8e34ae777d2702b25641982cbadab761d540d02d8b04d1`.
- `docusaurus-worker-build.log`: SHA-256 `a5f4d03212d33dafa8435a6c8e25ff7325942843fd61d0e60a290a10cc9296d2`.
- `portfolio-check.log`: SHA-256 `e2715b52490f4bce2032fc81a92b769875ed8db89b157de49873f1285bafb07c`.
- `portfolio-lock-update.log`: SHA-256 `00cdd66d3462f7a12b5ce597e6a9098964c382b601892d013b7285294b9ce230`.
- `portfolio-skill-lock-update.log`: SHA-256 `1b27da43513e5d6c0e1737355844f05d30caa2cd2200bf44ee3b3776e580a333`.
- `snapshot-files.txt`: SHA-256 `25f6a0e1cad81dd2d87c2e742fc3980a7eae859f6e215dcc47f9192a9aaf908a`.
- `staged-results.sarif`: SHA-256 `da6bfd1aa7eb10daca3ffe8f6e47f09ed5e4a822d1a0a70ad1c2d00d2b6a02a9`.
- `staged-scan.log`: SHA-256 `e87b4364789e4b6baa8044fe8a197f3ae1f250ccb6a10813d4b79f08d84ef536`.
- `staged-files.txt`: SHA-256 `bdee3c5229dc31310c387e39524f134e4d5ae2101d442c997a005ceec50e0e38`.
- `staged-tree.txt`: SHA-256 `2efeac84946c53775eb9b0c8f26aa4142b5d160cd56094950031784cee03016d`.
- `staged-export-root.log`: SHA-256 `d9f328ae0252e90dbabd34fdce8d62f3bf432ba012c8d4f2132b391ad9a3dffb`.
- `staged-export-panel.log`: SHA-256 `ace7ba2d21026278ea917dd6dccbe1a29a4a7a32a15b95fd745639c8fe7ea63c`.

## Final frozen-source acceptance after T077/T078

A fresh immutable `git archive` of staged tree **`16b3ff707f2ab366d86d822181434fcd61e4fe15`** retained **4,169 files**, including unchanged tracked baseline artifacts and the final T077 SQLite cleanup / T078 weak-public-key guard source and tests. No source, configuration, dependency lock, or scanner ignore policy was changed for this rerun. Both locked CI requirements exports were regenerated inside the private archive.

The exact pinned Trivy 0.70.0 action entrypoint again returned **exit 0 with zero SARIF findings**, retaining effective all-severity SARIF behavior, `ignore-unfixed: true`, filesystem scan, default vulnerability/secret scanners, and `exit-code: 1`. No path exclusions were passed. This result supersedes the earlier tree scan for source acceptance.

The exported `final-source-manifest.json` has SHA-256 **`7c45d5afd054aea6f1a9865f3ffd8c934139c962d0a95f3ed9ede157d79ddf17`**. This proves the named immutable source snapshot; subsequent matrix-result and evidence-only document updates may produce a different eventual Git tree. It does not claim that an unscanned future tree hash is identical.

Private files are prefixed `frozen-16b3-` under `/home/felni/cao015-final-sandbox/trivy/`.

- `frozen-16b3-results.sarif`: SHA-256 `35541f284804e9bb59c7ec4604c46eba0defdb3a5259734e9b473b20dccd02b4`.
- `frozen-16b3-scan.log`: SHA-256 `ac89b5320156a055162c028a1a3de4787f924fe5f68e039c3a7f4843ace9cf79`.
- `frozen-16b3-files.txt`: SHA-256 `7a985949c83de46a339eb040c58af5309c096c061a459544549ca299aff3b1ce`.
- `frozen-16b3-export-root.log`: SHA-256 `d9f328ae0252e90dbabd34fdce8d62f3bf432ba012c8d4f2132b391ad9a3dffb`.
- `frozen-16b3-export-panel.log`: SHA-256 `ca4c27db08c73659948c92577874c1eec586c6fb225b30541fac592b902456ef`.

## Frozen-source acceptance after T079

A fresh immutable `git archive` of staged tree **`e0f463fb2d046e57289ed0fee05168424badeb7c`** retained **4,171 tracked publication files**, including unchanged tracked baseline artifacts and the final T079 production connection ownership fixes and tests. The archived 1,332-record source manifest has SHA-256 **`b6d13a07e3addf2aa285ad39181596898a53680f8aac962c81b1c379d0a8d88b`**, matching the supplied freeze. Both locked CI requirements exports were regenerated inside the private archive. No source, configuration, dependency lock, scanner policy, or path exclusions changed for this rerun.

The same pinned Trivy 0.70.0 action entrypoint returned **exit 0, zero SARIF findings**, with filesystem/default vulnerability and secret scanners, effective all-severity SARIF behavior, `ignore-unfixed: true`, and `exit-code: 1`. Scanner Go concurrency was limited to one to avoid competing with the Python matrix; scan policy and coverage remained unchanged. Trivy warned that some retained baseline Graphify files are large; they were not excluded. This result supersedes prior tree results for final T079 source acceptance.

This proof names the immutable scanned source snapshot. Subsequent matrix-result and evidence-only edits can change the eventual tree hash; it does not certify an unscanned future tree as identical. Private archive and evidence files use prefix `frozen-e0f4-` under `/home/felni/cao015-final-sandbox/trivy/`.

- `frozen-e0f4-results.sarif`: SHA-256 `ab3cded37bf41c8d4940ef8938d47f5fa5289ea10c091fcda293cfcad5dbae8e`.
- `frozen-e0f4-scan.log`: SHA-256 `7d604080c23fc70b229619236d83e7e0fa2d3d2b4f61a907531b74eb4fe99a38`.
- `frozen-e0f4-files.txt`: SHA-256 `c0b69f25cc2b7a17732240a87aba8f55e7cd832d18fca05935f4332673b82025`.
- `frozen-e0f4-export-root.log`: SHA-256 `d9f328ae0252e90dbabd34fdce8d62f3bf432ba012c8d4f2132b391ad9a3dffb`.
- `frozen-e0f4-export-panel.log`: SHA-256 `ca4c27db08c73659948c92577874c1eec586c6fb225b30541fac592b902456ef`.

## Frozen-source acceptance after T080

The complete immutable archive of staged tree **`497d8cf03f297f731edbc0bf06a5d205d6da0233`** contains **4,175 tracked publication files**, including unchanged baseline artifacts and T080 ownership/context/setup-error cleanup source and regression tests. The archived 1,335-record source manifest SHA-256 is **`fd1d17a9bd9159a2d6f59ce7342a60dd16408f3fb21d322036d75b2a7398a21a`**, matching the final supplied freeze. Both root and fleet-panel locked CI requirements exports were freshly generated inside this archive.

The same checksum-verified Trivy 0.70.0 and pinned action entrypoint returned **exit 0, zero SARIF findings**. Filesystem scan, default vulnerability/secret scanners, effective all-severity SARIF, `ignore-unfixed: true`, and `exit-code: 1` remained unchanged; no paths were excluded. `GOMAXPROCS=1` bounded scanner resources while the Python matrix ran. No source/configuration/dependency-lock changes were made for this verification. This supersedes earlier trees for final T080 source acceptance; subsequent matrix-result/evidence-only document edits may change the eventual tree hash and are not claimed identical to this immutable scanned tree.

Private archive/evidence prefix: `/home/felni/cao015-final-sandbox/trivy/frozen-497d-`.

- `frozen-497d-results.sarif`: SHA-256 `a74beda8d8dabc64df994ce9f8781331ce360f326921004ac90b26c8a7ddaa8f`.
- `frozen-497d-scan.log`: SHA-256 `36b712106456b92a52934ca77fe755b622052194f0f688b04c21feb23752c4a2`.
- `frozen-497d-files.txt`: SHA-256 `494bba3aaaf29c68dc99989932075cae5d2c9d62b81e46a62a20aa3fb0185ef6`.
- `frozen-497d-export-root.log`: SHA-256 `d9f328ae0252e90dbabd34fdce8d62f3bf432ba012c8d4f2132b391ad9a3dffb`.
- `frozen-497d-export-panel.log`: SHA-256 `ca4c27db08c73659948c92577874c1eec586c6fb225b30541fac592b902456ef`.

## Frozen-source acceptance after T081–T083

The actual current candidate index was frozen with `git write-tree` and exported with `git archive`: immutable tree **`61eb8353bc0082964fa5242ce05ed89f63606356`**, **4,180 tracked publication files**, retaining all unchanged tracked baseline artifacts. The archived public source/test manifest SHA-256 is **`a2a9b937a17ac6c8db7dc72c67f5793fa930cf07b989c72ce6cb06685dcf325d`**, with **1,358 records including 21 executable scripts**, matching the supplied freeze. This snapshot includes the remote async wait/cancellation/TypeVar changes, child CAO_HOME test isolation, TOML-fallback scripts and new regression tests. No source, lockfile, scanner policy, host packages or commit changed during this verification. Root and fleet-panel locked CI requirements exports were freshly generated inside the archive.

The unchanged pinned Trivy 0.70.0 action entrypoint returned **exit 0, zero SARIF findings**, with filesystem/default vulnerability and secret scanners, effective all-severity SARIF behavior, `ignore-unfixed: true`, `exit-code: 1`, and **no path exclusions**. `GOMAXPROCS=1` bounded resource use. This supersedes earlier tree results for T081–T083 source acceptance while preserving the historical T080 results. Later matrix-result/evidence-only document changes may create another eventual tree hash; no unscanned future tree is claimed identical to this immutable scanned snapshot.

Private archive/evidence prefix: `/home/felni/cao015-final-sandbox/trivy/frozen-t083-`; raw scan logs remain private.

- `frozen-t083-results.sarif`: SHA-256 `f3bb6588b7e803ab36b19cbf8841f964ee13ca2c14288e2199befe931c646d58`.
- `frozen-t083-scan.log`: SHA-256 `3e4422e3a626e3e980d14bbdb1b3d03e35e5aacf79b013be19b2e87402d75409`.
- `frozen-t083-files.txt`: SHA-256 `c3cdd089c0f00f652e26cf345e12bc363808b93afd0ebe1bf1de7b154252ae61`.
- `frozen-t083-tree.txt`: SHA-256 `cf6093425dca7aa7be8015dd266ad335babae088adc4c788dad7feae5d8ab5d5`.
- `frozen-t083-export-root.log`: SHA-256 `9f154f1e61bebe66473292ebeb6dd2b8a24f2bc81a520cf93e333023b7097afb`.
- `frozen-t083-export-panel.log`: SHA-256 `ca4c27db08c73659948c92577874c1eec586c6fb225b30541fac592b902456ef`.
