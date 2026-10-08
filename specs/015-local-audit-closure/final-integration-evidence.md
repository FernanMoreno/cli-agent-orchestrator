# Final integration and fork publication — 2026-10-08

The user authorized completion, reviewed commits and a non-force push to
`main` of `FernanMoreno/cli-agent-orchestrator`. The isolated candidate branch
is `codex/015-final-recovery`, based on `87e2d5f354374ea8acc685275e749992df51cdbd`.
No package release, remote deletion or history rewrite is authorized here.

## Candidate scope and preservation

The publication contains maintained source, tests and required fixtures,
clients, TUI, configuration, locks, workflows, scripts, examples, documentation
and specifications. New or modified raw audit evidence and generated Graphify
cache/graph files are excluded, apart from the two spec015 reports and their
source manifests. Existing tracked historical artifacts remain in the tree.
The original workspace and unrelated work are preserved. Captured provider
fixtures intentionally retain their ANSI, CRLF and trailing-space bytes.

The [current source manifest](final-source-manifest.json) binds 1,363 source/test/script and contribution-documentation files after T093, SHA-256 `b0b5836af7ff40a0ddd18d9bf0d238961cce6827130dc3cad902a313c6a7bcaa`. Its extended configuration freeze binds 1,366 files. The [T087 archive](final-source-manifest-t087.json) binds the completed prior local matrix: four later fixture/control corrections, Docker case-name metadata, a CI-only host helper/tests and contribution documentation differ; ci.yml changes its launcher. Runtime code, locks and QEMU remain unchanged. Those completed Python snapshots verified 1,361 files each, including the then-current workflow. The historical T080 diagnostic snapshots contained 1,338 files and reproduced the T081 fixture issue. Their temporary copies and raw logs are no longer available after environment cleanup. The baseline Python
attempts failed or were interrupted; they are retained as diagnostic history
and cannot substitute for successful final runs.

## Executable evidence

- Python: historical T080 diagnostics failed or were interrupted. Fresh whole acceptance runs against the T077–T087 freeze completed on all five versions with zero failures. Individual percentages and the Python 3.14 supplemental diagnostic are recorded separately; the mandatory Python 3.12/backend and frontend ratchet passed;
  see [matrix evidence](final-python-matrix-evidence.md).
- Static Python: Black verification covers 1,194 source/test/workflow files; isort passed;
  mypy reported no issues in 398 source files.
- Architecture: Import Linter and `project-composition-check caos` passed:
  400 files, 1,680 dependencies, five contracts kept, zero broken.
- Web: 470 unit cases and 17 real browser cases passed, with types/build;
  MCP Apps: 92 unit cases, seven browser cases, types/build/JIT/size passed.
  Fresh MCP Apps line coverage is 90.01%, above the unchanged 90.0% floor.
  See [frontend evidence](final-frontend-evidence.md).
- Rust: 262 unit/integration tests, formatting, clippy and cargo-deny passed;
  see [Rust evidence](final-rust-evidence.md).
- Native Docker, strict guest Bubblewrap, shell/device and isolation checks
  passed as detailed in [sandbox evidence](final-sandbox-security-evidence.md).
- The exact CI-pinned Trivy configuration passed against an exported staged
  tree after compatible lock corrections; see [security evidence](final-security-evidence.md).
- The staged Git additions secret scan found no leaks. Local Markdown links
  passed on the exported staged tree. Whitespace checks passed outside
  intentionally byte-preserved capture fixtures.

## Composition findings and bounded fixes

Profile exclusivity correctly rejects another live owner. The Python runner
now supplies independent worker HOME/CAO_HOME/tmux directories before imports.
Graph fixtures restore the real graph-build lifecycle after server shutdown.
Tmux shutdown tests wait for both process and socket settlement without
weakening production refusal of unknown state. Python 3.10 cancellation tests
check the message at the coroutine boundary where it is preserved, alongside
cleanup and cancellation outcomes. Web logout correctly constructs an empty
204 Response using a null body; real restart/logout browser checks passed.
See [tmux](final-tmux-evidence.md), [plugins](final-plugin-evidence.md),
[frontend](final-frontend-evidence.md) and [SQLite ownership](final-sqlite-evidence.md).

Peer authentication also rejects the demonstrated small-order/noncanonical
public-key encodings during enrollment and legacy-grant verification; see
[peer-key evidence](final-peer-key-evidence.md). Real generated-key signatures
and two-process paired traffic pass after this correction.

The remaining startup-owned connections identified by allocation traces also
close explicitly, with transaction and borrowed-connection regressions:
[startup ownership](final-startup-ownership-evidence.md). The external parallel
runner preserves vault containment by using TMPDIR above pytest basetemp.
The subsequent real journal/store ownership audit closed 55 operation-owned
contexts and three setup/error owners, preserving transactions and borrowed
handles; see [store ownership](final-store-ownership-evidence.md).

Independent targeted publication/composition review and the final SQLite and peer-key
reviews found no outstanding code blocker in their inspected surfaces. The
publication review does not claim exhaustive line-by-line review of the large
accumulated diff. Graphify was consulted and checked against current source;
its earlier snapshots remain historical evidence. No broad graph refresh is
required for the final standard-library connection wrapper and shared key-encoding
guard within the existing peer-authentication dependency; current source
hashes and executable architecture checks establish this boundary. These
conclusions are durable in the repository artifacts; duplicating routine
validation logs into the shared knowledge vault is unnecessary.

## External limits

Codex, Claude and OpenCode have real-provider acceptance evidence, including
the previously expired Claude login cases after reauthentication and a fresh
three-provider same-provider run. Ten additional providers lack usable accounts
or model configuration; the user confirmed they are unavailable for now.
Actual quota pause/continuation was not observed and remains unproven.
See [provider evidence](final-provider-inventory-evidence.md). No unavailable
provider, skipped scenario or host-only capability limitation is counted as a
passed acceptance.

## Publication status

El push normal HTTPS terminó con exit 0 y la lectura remota confirmó `main` en `71156b2108f71e42020581dce50e14510e5c66c1`. El hook completo aprobó 16523 casos, omitió 74 y aprobó los controles JIT/tamaño. El perfil temporal de esa ejecución se eliminó tras guardar su resultado. CI remoto está en ejecución sobre ese SHA; aún no se presenta como aprobado. El historial y los resultados definitivos se mantienen en [verificación del fork](final-fork-publication-evidence.md).

The managed server fixture also binds HOME and CAO_HOME_DIR together while
preserving explicit overrides. Real child-process regression and the original
HTTP refusal case passed, followed by the five successful whole-matrix selections. See
[fixture profile evidence](final-fixture-profile-evidence.md).

Remote-channel cancellation now survives simultaneous send/result completion
on Python 3.10/3.11. Docker TOML preflight uses the already-declared Python 3.10
parser fallback. See [runtime compatibility](final-runtime-compatibility-evidence.md).

A subsequent Python 3.14 SQLite-build fixture finding is corrected without
altering production credential redaction; see [deleted-byte fixture proof](final-sqlite-fixture-evidence.md).
The historical T083 snapshot is preserved in `final-source-manifest-t083.json`. The archived T087 manifest binds the reconstructed completed local matrix; the current manifest includes later fixture/control corrections, T091 CI controls, T092 contribution documents and T093 case metadata. Production through T090 is committed and published; T091 hosted acceptance remains pending; see the publication status above.

The Python 3.10 native parser crash is guarded before AST construction, with
intentional statement/f-string complexity limits and large benign controls;
see [parser proof](final-script-parser-evidence.md). Retry tests observe real
transaction completion before closure and enforce closure afterward; see
[retry observer proof](final-retry-observer-evidence.md). These fixes passed the final whole matrix against the unchanged source freeze.

## Recovered validation environment

The original workspace retained all maintained changes after temporary native copies and logs were removed. The reconstructed candidate uses the same baseline and filtered publication scope. The historical T087 1,358-file source freeze SHA-256 is `74c64da609f1c71cae85c9b95e9ae1269b6c1a7de2039eb3129652ea22de243c`; original and candidate had zero differences at that validation. The historical T090 fixture-only freeze and the historical T091 CI-control freeze and current T093 freeze are recorded separately. Historical recorded results remain scoped to their stated identities; missing raw files cannot be reinspected. New temporary profiles, test copies and scanner exports will be removed after results are recorded, as requested.

T087 keeps the private-FD barrier strict while waiting for transient empty child environment observation; see [the regression evidence](final-private-fd-evidence.md). Final sanitized diagnostics passed all six targeted cases; one unchanged native-history neighbor failed intermittently and passed on isolated retry. Acceptance relies on the fresh whole matrix, not that retry.

## Cobertura conjunta aceptada de la fuente final

La selección completa Python 3.12 terminó con 16604 PASS, 100 SKIP y cero fallos. Su informe individual produce 87.1310926894555% de cobertura; SHA-256 del JSON `b7fc06fd1e8e47b8228cef3bcf2a5478763cee86a148c0ec70405b9a8ddb5590`. El ratchet real terminó con exit 0: Python **87.13% ≥87.00%** y MCP Apps **90.01% ≥90.00%**, usando ambos informes presentes y sin cambiar mínimos. El posthash verifica 1361 fuentes/configs sin diferencias. Las otras cuatro versiones también completaron la selección; los cinco informes se presentan por separado. Commits y push ya se comprobaron; la verificación de CI remoto continúa.

## Cierre de compatibilidad y composición

Las cinco selecciones locales completas T087 equivalentes a CI terminaron con pytest exit 0 y cero fallos/errores en los JUnit de 16704 casos por versión: 3.10 y 3.11 registran 16603 PASS/101 SKIP; 3.12, 3.13 y 3.14 registran 16604 PASS/100 SKIP. Los cinco posthash verifican 1361 registros sin diferencias; la fuente T087 validada queda vinculada a `74c64da609f1c71cae85c9b95e9ae1269b6c1a7de2039eb3129652ea22de243c` y a su manifiesto archivado.

El ratchet obligatorio real usa Python 3.12, como CI, y aprueba 87.1310926894555% frente a 87% y MCP Apps 90.01% frente a 90%. Python 3.14 midió 86.98323713788628% y falló el chequeo adicional por versión del runner temporal; se conserva ese fallo diagnóstico. CI no exige cinco ratchets y no se han cambiado mínimos, exclusiones ni selección. El análisis identificó 801 sentencias menos en el denominador por la medición de anotaciones de Python 3.14.

Después de completar la matriz se repitió `project-composition-check caos`: exit 0, cinco contratos conservados y cero rotos. Las omisiones por capacidades/cuentas siguen documentadas. El commit, hook normal de push y SHA remoto ya se comprobaron; los resultados reales de CI se registran por separado.

La comprobación de seguridad actual del árbol congelado y sus locks está en [evidencia de sandbox y seguridad](final-sandbox-security-evidence.md); los resultados históricos mantienen sus identidades anteriores.

T072 queda completada tras la revisión del diff, el índice seleccionado, los 482 enlaces Markdown y los scanners reales del árbol congelado. T073 conserva su estado pendiente hasta incorporar los resultados de CI; el push HTTPS ya está acreditado.

El primer transporte SSH falló después de aprobar el hook. Un intento HTTPS posterior completó el push; ambos resultados se conservan con su alcance en [verificación del fork](final-fork-publication-evidence.md).

## Correcciones posteriores de fixtures de CI

El primer backend remoto de MCP Apps registró 8 FAIL/16569 PASS/127 SKIP en ubuntu-latest. T088 volvió autocontenida la fixture del proveedor Kiro simulado (134 PASS), y T089 comprobó precondiciones reales antes de seis casos nativos (91 PASS; denegación: 12 PASS/6 SKIP). La revisión independiente encontró y corrigió dos P2 en la clasificación de denegaciones/prerequisitos; ambos quedaron ADDRESSED sin P1/P2 abierto. Producción, configuración, locks y aceptación QEMU permanecen sin cambios. La fuente T089 del commit `3276454a` se ligaba a `6e6a9aa0abf336f1f290bac0c4cbf6019a8d9d395d1443a83d58f1704ec06f87`; los informes previos conservan la identidad T087. Véase [evidencia de fixtures CI](final-ci-fixture-evidence.md). El hook completo y CI posteriores se comprobarán antes del cierre; no se presenta el CI inicial fallido como aprobado.

T090 agrega la corrección de orden de la fixture memory HTTP, con RED/GREEN del shim E2E, 175 vecinos y revisión independiente aprobados. El manifiesto T090 publicado en `68b95bf5` tenía SHA-256 `64e6443b8b0523687c1e276cf97151d7a8c0497e05afe48cb59d5f06146b2e96`; producción sigue idéntica a T087. El hook anterior falló y no publicó T088/T089; la aceptación posterior continúa pendiente. Véase [fixtures CI](final-ci-fixture-evidence.md).

T091 prepara el host nativo del gate obligatorio sin cambiar mínimos/selección/producción. El backend remoto T090 pasó todas sus pruebas pero falló el mínimo Python (86.77 % frente a 87 %), y no se acredita como aprobado. La corrección local pasa 87 casos sin SKIP, incluidos 38 nativos; el P1 de drenaje de descendientes se reprodujo antes del fix y quedó ADDRESSED en re-revisión. La causalidad AppArmor y la aceptación del nuevo SHA permanecen pendientes de ejecución hospedada. Véase [evidencia CI](final-ci-fixture-evidence.md).

T092 cierra las dos observaciones del selector del launcher: revisión final sin P1/P2, 259 PASS/3 SKIP por falta de zsh, con paridad de las tres copias del skill. T093 conserva los tres payloads y aserciones Docker y acota únicamente sus nombres de caso (92 PASS). La recolección completa conserva 16814 casos y no representa ejecución de tests. El manifiesto actual incluye tres documentos distribuidos adicionales; los wheels anteriores son evidencia histórica de sus bytes originales. Los cancelados del CI anterior y su causa sin logs no se presentan como aprobados. Véase [evidencia CI](final-ci-fixture-evidence.md).

## Cierre hospedado y publicación

La fuente T093 conserva sus 1363 registros públicos/1366 ampliados sin cambios. CI del commit publicado `0bacd89a2f1e59a4eea792da140eaa45cd5d3cd0` concluye success, 19 jobs incluidos gates obligatorios; matriz observada 3.10.22–3.14.8: 83513 PASS/557 SKIP/0 FAIL. Ratchet87.08/90.01, QEMU8PASS276.14s/cargo-deny aprobados; composición registrada y directa 5 contratos/0 rotos. Revisión independiente T092/T093 PASS_WITH_RISKS, sin P1/P2 abierto. El primer job3.12 cancelado no cuenta como aprobación; su reintento completo sí. Todos los perfiles/descargas de comprobaciones terminadas retirados después de conservar recibos/hashes. Véanse [CI](final-ci-fixture-evidence.md) y [publicación](final-fork-publication-evidence.md). La actualización documental final no cambia código/configuración/dependencias y conserva los hashes de fuente probados.
