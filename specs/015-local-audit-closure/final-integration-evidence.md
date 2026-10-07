# Final integration candidate — 2026-10-07

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

The [source manifest](final-source-manifest.json) binds 1,358 source/test/executable-script files
by SHA-256. The fresh Python snapshots additionally verified the project
manifest, lock and CI workflow: 1,361 files each. The historical T080 diagnostic snapshots contained 1,338 files and reproduced the T081 fixture issue. Their temporary copies and raw logs are no longer available after environment cleanup. The baseline Python
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

Commits and push await completion of the final Python matrix, the joint
coverage ratchet and final candidate verification. Remote CI results must
refer to the exact published SHA.

The managed server fixture also binds HOME and CAO_HOME_DIR together while
preserving explicit overrides. Real child-process regression and the original
HTTP refusal case passed; full matrix acceptance remains pending. See
[fixture profile evidence](final-fixture-profile-evidence.md).

Remote-channel cancellation now survives simultaneous send/result completion
on Python 3.10/3.11. Docker TOML preflight uses the already-declared Python 3.10
parser fallback. See [runtime compatibility](final-runtime-compatibility-evidence.md).

A subsequent Python 3.14 SQLite-build fixture finding is corrected without
altering production credential redaction; see [deleted-byte fixture proof](final-sqlite-fixture-evidence.md).
The historical T083 snapshot is preserved in `final-source-manifest-t083.json`. The current linked source manifest binds T077–T087; the reconstructed full matrix completed successfully against that freeze. No commit or publication has occurred.

The Python 3.10 native parser crash is guarded before AST construction, with
intentional statement/f-string complexity limits and large benign controls;
see [parser proof](final-script-parser-evidence.md). Retry tests observe real
transaction completion before closure and enforce closure afterward; see
[retry observer proof](final-retry-observer-evidence.md). These fixes passed the final whole matrix against the unchanged source freeze.

## Recovered validation environment

The original workspace retained all maintained changes after temporary native copies and logs were removed. The reconstructed candidate uses the same baseline and filtered publication scope. The final 1,358-file source freeze SHA-256 is `74c64da609f1c71cae85c9b95e9ae1269b6c1a7de2039eb3129652ea22de243c`; original and candidate have zero differences. Historical recorded results remain scoped to their stated identities; missing raw files cannot be reinspected. New temporary profiles, test copies and scanner exports will be removed after results are recorded, as requested.

T087 keeps the private-FD barrier strict while waiting for transient empty child environment observation; see [the regression evidence](final-private-fd-evidence.md). Final sanitized diagnostics passed all six targeted cases; one unchanged native-history neighbor failed intermittently and passed on isolated retry. Acceptance relies on the fresh whole matrix, not that retry.

## Cobertura conjunta aceptada de la fuente final

La selección completa Python 3.12 terminó con 16604 PASS, 100 SKIP y cero fallos. Su informe individual produce 87.1310926894555% de cobertura; SHA-256 del JSON `b7fc06fd1e8e47b8228cef3bcf2a5478763cee86a148c0ec70405b9a8ddb5590`. El ratchet real terminó con exit 0: Python **87.13% ≥87.00%** y MCP Apps **90.01% ≥90.00%**, usando ambos informes presentes y sin cambiar mínimos. El posthash verifica 1361 fuentes/configs sin diferencias. Las otras cuatro versiones también completaron la selección; los cinco informes se presentan por separado. Commits y publicación siguen pendientes.

## Cierre de compatibilidad y composición

Las cinco selecciones completas de CI terminaron con pytest exit 0 y cero fallos/errores en los JUnit de 16704 casos por versión: 3.10 y 3.11 registran 16603 PASS/101 SKIP; 3.12, 3.13 y 3.14 registran 16604 PASS/100 SKIP. Los cinco posthash verifican 1361 registros sin diferencias; la fuente final sigue vinculada a `74c64da609f1c71cae85c9b95e9ae1269b6c1a7de2039eb3129652ea22de243c`.

El ratchet obligatorio real usa Python 3.12, como CI, y aprueba 87.1310926894555% frente a 87% y MCP Apps 90.01% frente a 90%. Python 3.14 midió 86.98323713788628% y falló el chequeo adicional por versión del runner temporal; se conserva ese fallo diagnóstico. CI no exige cinco ratchets y no se han cambiado mínimos, exclusiones ni selección. El análisis identificó 801 sentencias menos en el denominador por la medición de anotaciones de Python 3.14.

Después de completar la matriz se repitió `project-composition-check caos`: exit 0, cinco contratos conservados y cero rotos. Las omisiones por capacidades/cuentas siguen documentadas. El cierre de publicación continúa pendiente del commit, hook normal de push, SHA remoto y resultados reales de CI.

La comprobación de seguridad actual del árbol congelado y sus locks está en [evidencia de sandbox y seguridad](final-sandbox-security-evidence.md); los resultados históricos mantienen sus identidades anteriores.

T072 queda completada tras la revisión del diff, el índice seleccionado, los 482 enlaces Markdown y los scanners reales del árbol congelado. T073 queda pendiente hasta verificar publicación y CI; no se acredita ningún push todavía.
