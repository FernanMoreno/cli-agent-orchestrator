# Final sandbox and security validation — 2026-10-07

Scope: real local Docker acceptance, optional shell/secret-scanner regressions, and host sandbox capability checks. No project behavior or security guard changed. User-local tool provisioning and disposable test containers were used. Logs live in `/home/felni/cao015-final-sandbox/`.

## Tool identity

- Docker client/engine 29.8.1, Docker Desktop 4.93.0; Linux amd64 engine, kernel `6.18.33.2-microsoft-standard-WSL2`.
- Host `/usr/bin/bwrap`: 0.11.1. Basic user/PID/network namespace probe exited 0.
- Host Landlock ABI: 7, observed with `work_process_landlock._query_abi_version()`.
- Gitleaks 8.30.1: project workflow pinned release SHA-256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb` verified before execution. Binary: `/home/felni/cao015-final-sandbox/bin/gitleaks`.
- Fish 4.2.1 and zsh 5.9: Ubuntu package binaries extracted into a user-local directory. Fish wrapper supplies its extracted libpcre2 dependency even when a test deliberately replaces the child environment.
- Bubblewrap 0.13.0 source archive SHA-256 `4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765` verified. Local GCC 15.2.0 / Meson 1.12.1 build SHA-256 `a3800ca935cf30be808f29c0eca9f389ec3c4d035d33728897099311d4d92dac` differs from the explicitly trusted builds. It was relocated to `/home/felni/cao015-final-sandbox/bwrap-0.13.0-untrusted-build`; the source-detected scratch path remains absent. No digest allowlist or host restrictions changed.

## Fresh passing checks

| Command/scope | Actual result | Log |
| --- | --- | --- |
| Native candidate `pytest -o addopts= --no-cov -q test/test_gitleaks_config.py test/test_cao_contributing_skill_accuracy.py test/clients/test_tmux_send_keys.py`, user-local tools on PATH | 128 passed in 34.18 s; no skips | `native-shell-gitleaks.log` |
| Native candidate Kimi shell tests selected by `outer_command_parses_under_a_non_posix_pane_shell or inner_shell_sees_the_pane_environment or hostile_temp_dir_round_trips_under_fish` | 3 passed, 447 deselected in 1.90 s; actual fish processes | `fish-real-rerun.log` |
| Workspace `test/integration/t019/test_docker_backend.py test/integration/test_browser_auth_docker.py` with explicit immutable rootfs image and browser opt-in | 9 passed, 5 warnings in 73.31 s | `docker-runtime.log` |
| Native candidate runtime snapshot device cases in an owned disposable Docker container as root with MKNOD, readonly source mount | 2 passed, 9 deselected, 2 warnings in 1.93 s | `docker-devices.log` |

The first Docker run used source-built rootfs image `sha256:424338cf3d8cd80dd26ade8cf70a2e5d2dc10de35adaec109a20afb46e9bc30c`. Device cases use existing local immutable image ID `sha256:bee1adae3bc3c27fb753327febaf2f780150438e109299ca69d7311ca65c0af9` with pytest 8.4.2 installed only into that disposable container. Warnings concern absent asyncio plugin and readonly pytest cache; both actual character/block device cases passed.

## Host limitations, not successful acceptance

Native candidate command: `pytest -o addopts= --no-cov -q -rA test/services/test_work_process_landlock.py test/security/test_work_bubblewrap_adversarial.py test/security/test_work_bubblewrap_runtime_snapshot.py`.

Actual result: **39 passed, 12 skipped, 2 warnings in 13.53 s** (`landlock-devices.log`). One pathname socket case requires Landlock ABI >=9 (host reports 7); nine adversarial scratch tests require the approved scratch Bubblewrap; two device cases require mknod capability (subsequently passed in Docker above). These skips do not certify the T097 host profile.

## Further runner outcomes

- Source-defined `test/integration/t019/run-docker-acceptance.sh` running the pinned Ubuntu QEMU guest via its reviewed container runner. Guest image SHA-256 `6001247f681e87448e3f403b2e5ca859fb23a6cbbf0813f01a456a072b70a529` verified; guest kernel `7.3.0-5-generic`, Landlock ABI 11, canonical Bubblewrap 0.13.0 approved SHA-256 `15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250`, GCC 15.3.0-4ubuntu1 and Meson 1.10.1 observed. **Exit 0: 8 passed in 377.98 s, no skips/xfail/xpass.** The source-defined runner used its configured 45-second worker timeout under TCG and verified the eight-pass summary. The dedicated non-login broker was `cao_t019_broker`. Log: `qemu-acceptance.log`.
- Native source-defined `CAO_T020_DOCKER_ACCEPTANCE=1 CAO_T020_DOCKER_CLI=/home/felni/.local/bin/docker bash test/integration/t020/run-docker-acceptance.sh`: **exit 0, 14 passed, 5 warnings in 81.28 s**, with no skips (8 real Docker backend + 6 real workflow restart/failure cases). Log: `docker-workflow-real.log`. Rootfs immutable image ID `sha256:1b277d81f9d53c2c60bc46c99cfca8630981abb2a47f4b5f29901c78fda58f01`; worker source SHA-256 `d91bbf4c51e742e50f56827f874738d45d9d725d3508ce28afb66a43197eebd4`, static worker ELF SHA-256 `04dc7236707b12c092b69b68b09b69b1bc312156145bb51376bee7e1573765c9`. A preliminary standalone invocation with incomplete opt-in environment honestly skipped six tests and is superseded by the source-defined runner result.

Initial provisioning issues (missing Fish library, modern pytest fixture marking incompatibility, missing coverage plugin) were isolated tooling issues and corrected in the user-local tool wrapper/disposable container. No source fix was needed. All selected runner checks above now have final outcomes. The host ABI/scratch limitations remain accurately documented and are separate from the successful approved disposable-guest acceptance.

## T097 accepted scenarios

The eight passed cases cover bound launch after real host preflight; descendant execution policy; filesystem/network/IPC/FD/proc containment; revocation after ACK withholding GO; lost-result ACK reconciliation without redelivery; distinct concurrent proxy endpoints; uncertain proxy effects surviving restart without replay; and same-UID sibling process/FD isolation. The disposable runner container exited and was removed by its source-defined `--rm` lifecycle. This proves the approved guest profile, not a different production host.

## Evidence file identities

- `native-shell-gitleaks.log`: SHA-256 `277d4ae94c686115d724cbf6c7a1744bd7ac4575a2f56b99f45def6fce820c0e`.
- `fish-real-rerun.log`: SHA-256 `917e10d68723d0a62d546ca4d1af08abf2a18f3505629a4bbf6196c8da151015`.
- `docker-runtime.log`: SHA-256 `fd319c1c7b58b08c80a8d364ab348773089671befc4fffae549560d3a1a45950`.
- `docker-workflow-real.log`: SHA-256 `e2d10b7c4d9f654a36204a877432b5fe177d3cbabbc2c78611d3d17f136e8346`.
- `docker-devices.log`: SHA-256 `7864fd976ef1d60f16ff26b3a8d82af6297d7958d051e4b6c32b8b67865a14b3`.
- `landlock-devices.log`: SHA-256 `4e056a7ae10699d75d0fc1cb201f7eaf1158968b463ae48cec91ef1c4ecb1d86`.
- `qemu-acceptance.log`: SHA-256 `6387a2ace2b0e6d7c36500c5db04eb6fe2b18b82b550ac4e2915abd7f00bcd94`.

## Final source freeze after T081–T087

Temporary scanner tools were restored after the user requested removal of earlier scratch runs. Gitleaks **8.30.1** again matched the workflow-pinned archive SHA-256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`. Trivy **0.70.0**, selected by the pinned action `ed142fd0673e97e23eac54620cfb913e5ce36c25`, matched its published release archive SHA-256 `8b4376d5d6befe5c24d503f10ff136d9e0c49f9127a4279fd110b727929a5aa9`.

The staged-tree Gitleaks command, `gitleaks git --pre-commit --staged --redact --config .gitleaks.toml --exit-code 1`, returned **exit 0, no leaks**, scanning **6.41 MB in 7.16 seconds**. Its redacted JSON report is an empty array, SHA-256 `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`; log SHA-256 `a9bbc688835cffa553bc64d952489c756b01d34744222e12c88c409988132dc0`.

The immutable staged tree **`836e97f1ffd3a4dd9cecfd5e14acd99031ac9aab`** contains **4,185 tracked files**, including unchanged baseline artifacts. Every one of the **1,358** archived source records matched the [final source manifest](final-source-manifest.json), SHA-256 **`74c64da609f1c71cae85c9b95e9ae1269b6c1a7de2039eb3129652ea22de243c`**. Both root and fleet-panel locked requirements exports were regenerated inside the archive using the exact CI commands.

Trivy filesystem scanning returned **exit 0, zero SARIF findings in 37.75 seconds**, starting at **2026-10-07 18:04:43 UTC**. The effective SARIF policy matches CI: all severities, default vulnerability and secret scanners, `ignore-unfixed: true`, `exit-code: 1`, and no added path exclusions. `GOMAXPROCS=1` bounded scanner resources without narrowing coverage. Large retained Graphify artifacts were scanned despite size warnings. The live vulnerability database reported `UpdatedAt: 2026-10-07T07:38:55Z` and `DownloadedAt: 2026-10-07T18:01:44Z`.

This proof applies to the named immutable source snapshot; later evidence-only edits can change the publication tree hash. It preserves the earlier [security findings and fixes](final-security-evidence.md) without claiming those historical snapshots are current.

| Artifact | SHA-256 |
| --- | --- |
| Final SARIF | `570fe6903e4138d2101c97a645dfd717deeb426b3f40b4fdb8e6f2d2480c8d05` |
| Final scan log | `2ad66cfac11772e9d79c2f8879a5c20a5569b05995a04d1f5d7f0f0a3bb5431b` |
| Complete tree path inventory | `cdb7d6724f74f9efed06a4161d6713094b6666159290982a08ca2af1084ec2d7` |
| Root locked export log | `2bdc92fc427d077f818a9d0b9bb5017b36f20fafae97e7bf97f91f148412dd09` |
| Fleet-panel locked export log | `b3f6499f327afdb7fa406b5f5cf69bf7e4ac29dd3a378b36cf5ec98d0c241fb3` |

The disposable scan archive and downloaded release archives were removed after these results were recorded. The scanner binaries remain temporarily available for required publication checks; they can be removed after those checks finish. Raw reports and logs remain private until the final review consumes their recorded identities, then are removed under the user's cleanup instruction.

## Alcance nativo tras limpiar las herramientas temporales

La revisión independiente de cierre comparó 56 registros de fuentes/pruebas nativas con T083, sin diferencias. Los cinco archivos cambiados en el manifiesto completo desde T083 son el linter de scripts y cuatro archivos de test. Las pruebas actuales que requieren el binario scratch de Bubblewrap omiten casos porque ese artefacto temporal ya no existe; el host también mantiene ABI 7 y restricciones de dispositivos. Estas omisiones no se presentan como aprobaciones. La aceptación previa del huésped QEMU aprobado (8 PASS sin SKIP/XFAIL) y Docker (14 PASS) conserva su alcance histórico para la conducta nativa sin cambios; no certifica una nueva ejecución del entorno actual.

Los 23 archivos Rust comparados son idénticos entre workspace y candidato. Trece conservan además hashes verificables en T083; los diez hashes históricos individuales restantes ya no están disponibles tras la limpieza. Los resultados Rust registrados se conservan como evidencia anterior; no se atribuye una nueva comprobación independiente de esos 23 hashes históricos.

## Último control antes de integrar

El árbol Git `56bbbecc1aa395471b7def58da6855d31a8884e5` contiene 4186 archivos mantenidos y los 1358 registros de fuente congelados coinciden sin diferencias. Gitleaks 8.30.1 sobre el índice real terminó con exit 0 y sin secretos (6.46 MB examinados). Trivy 0.70.0 sobre la exportación inmutable exacta, con ambos exports bloqueados de dependencias y la política CI intacta (vuln+secret, todas las severidades, ignore-unfixed y exit-code 1), terminó con exit 0 y cero resultados SARIF en 40.704 s. SHA-256 SARIF: `69f6304d1753fc3a6e761a1d6dde9eea21b99223c2b3542d586191dd76932eec`.

El validador real de enlaces comprobó los 482 Markdown mantenidos de ese mismo árbol con cero errores. El diff final respeta los bytes intencionales de las fixtures y no incluye autenticación ni logs raw. La fuente original, candidato e índice coinciden en 1358 hashes. La exportación temporal del scanner y la del validador se retiraron después de conservar esta evidencia. Sólo la documentación de estos resultados cambia a continuación; la fuente y los locks permanecen idénticos.
