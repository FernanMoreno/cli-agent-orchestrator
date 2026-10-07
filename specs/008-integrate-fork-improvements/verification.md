# Verificación por tandas

## Cierre vigente — 2026-10-02

Esta sección sustituye las notas de progreso antiguas que aparecen debajo. Las 87 capacidades del inventario congelado están cerradas: 19 implementadas y 68 cubiertas por la solución compatible más sólida ya presente. Los 57 pasos del plan están completos en `main`; no se hizo commit, push, merge, publicación ni despliegue. Las identidades y SHA originales se conservan en coverage.json (`coverage.json`; artefacto histórico local).

Los siguientes resultados proceden de tandas completas y separadas; no representan una única ejecución ni suman los reruns focalizados. Omisiones por marcadores/prerrequisitos no cuentan como aceptación.

| Tanda | Resultado | Evidencia final |
|---|---|---|
| Servicios, utilidades, seguridad y modelos | 6.903 pasan; 57 omitidas | `spec008-services-final-complete.log.summary.txt` |
| API | 1.470 pasan | `spec008-api-final-all-green.log.summary.txt` |
| CLI, clientes, grafo, integración no marcada, ejemplos y demás tests | 2.999 pasan; 21 omitidas | `spec008-first-half-final-clean.log.summary.txt` |
| Proveedores | 2.427 pasan; 6 omitidas | `spec008-providers-current-all.log.summary.txt` |
| Plugins | 1.091 pasan | `spec008-plugins-all-current-green.log.summary.txt` |
| MCP, Ops MCP y telemetría | 619 pasan | `spec008-mcp-all-current-green.log.summary.txt`; descriptor SSE final: 34 pasan en `spec008-apps-native-descriptor-green.log.summary.txt` |
| Runtime channel completo | 68 pasan | `spec008-runtime-final-complete.log.summary.txt` |
| Integración marcada, excluido e2e | 85 pasan; 33 omitidas | `spec008-marked-all-integration.log.summary.txt` |
| Ejemplo de workflow ejecutable | 8 pasan; 1 omitida | `spec008-examples-clean-final.log.summary.txt` |
| Web | 465 pasan; build de producción pasa | `spec008-web-final-all-current.log.summary.txt`, `spec008-web-production-current.log.summary.txt` |
| MCP Apps | 84 pasan; typecheck y cuatro bundles pasan | `spec008-apps-final-unit.log.summary.txt`, `spec008-apps-final-typecheck.log.summary.txt`, browser gate reconstruye los cuatro bundles |
| Navegador real | 17 web y 7 Apps pasan | `spec008-web-browser-with-library.log.summary.txt`, `spec008-apps-native-browser-green.log.summary.txt` |
| Rust | 260 pasan; fmt y Clippy locked/all-targets con warnings denegados pasan | `spec008-rust-current-all.log.summary.txt`, `spec008-rust-clippy-final-complete.log.summary.txt` |
| Docs | Lock/install y build de Docusaurus pasan; paridad de skills/docs 192 pasan, 3 omitidas | `spec008-docusaurus-build.log.summary.txt`, `spec008-docs-parity-green.log.summary.txt` |
| Arquitectura/composición | 5 contratos conservados, 0 rotos | `spec008-composition-final-complete.log.summary.txt` |
| Formato/diff | Black e isort completos pasan; diff sin errores de whitespace | `spec008-format-closure-black.log.summary.txt`, `spec008-format-closure-isort.log.summary.txt`, `spec008-diff-closure-check.log.summary.txt` |

Las tandas Python separadas anteriores registran 15.670 casos pasados. Sus sumarios y hashes originales se conservan en el índice de evidencia (`../../docs/audits/evidence/2026-10-02-integration-008/evidence-index.json`; artefacto histórico local). No se suman las pruebas focalizadas que se superponen con estas tandas. El requisito CI de integración se ejecutó además de los marcadores del bucle unitario.

Prueba nativa aislada: Claude Code/sonnet y Codex/gpt-6.1-sol completaron una tarea con recibo auténtico; OpenCode/opencode-go/kimi-k3 también. El modelo gratis por defecto rechazó la región; esa negativa se conserva como prueba negativa y ahora se clasifica como error, sin selección automática de otro modelo. Ver `claude-codex-native.json`, `opencode-native.json`, `opencode-default-refusal.json` en la evidencia pública. Sus credenciales copiadas fueron eliminadas y los procesos propios cerrados; no se modificaron cuentas/configuración personales.

El puente completó hello autenticado y ocho acciones de aceptación sobre WebSocket real: las siete operaciones de terminal más inspección; un único lanzamiento durable, entrada/salida, directorio, tecla, salida y borrado exacto. `bridge-network.json` y `spec008-real-bridge-final.log.summary.txt` conservan la prueba. También se ejercieron panes tmux reales y el serializer oficial de Kubernetes en un entorno desechable, sin crear recursos de cluster.

La regresión amplia encontró y cerró la carrera de publicación remota, compensación que borraba identidades ajenas, notificaciones tardías del registro, copia incorrecta de subdirectorios de plugins, negativa actual de modelo OpenCode, metadatos de aprobación inconsistentes, fallo SQLite sin 503 y carrera de plazo del coordinador bajo carga. Esta última se reprodujo con demora real de 1,2 s y se corrigió usando el inicio durable más el presupuesto aprobado, sin ampliar tolerancia. Las pruebas antiguas conservan sus garantías al adoptar ajustes privados explícitos de compatibilidad, identidades SQLite reales y parámetros aditivos. El scanner de permisos conserva sus comprobaciones y tolera el formato del código.

[Revisión final de composición](../../docs/audits/2026-10-02-integrated-composition-review.md): **PASS WITH RISKS**, por límites externos explícitos. Authored Python no constituye un sandbox del sistema operativo; managed remote Work y remote PTY se rechazan donde no existe prueba de enforcement; restauración bloqueada nunca se reactiva por sí sola. CLI/cuentas de bd/KAS, despliegue cloud/fleet/cluster, publicación y cualquier combinación de modelos no se certifican mediante estas pruebas locales.

Graphify final: grafo estructural (`../../graphify-out/2026-10-02-spec008-structure/graph.json`; artefacto histórico local), 12.955 nodos y 34.735 relaciones; 384 fuentes Python de `main` verificadas byte a byte frente al snapshot de análisis Linux, incluyendo la corrección final. Se conserva el grafo semántico antiguo. Snapshot/cache de análisis fuera de `src`, sin funcionalidad fuera de la rama. Índice de preservación compara con el workspace sucio inicial, no sólo HEAD; los símbolos privados retirados tienen sustitución documentada y la excepción pública Kiro conserva alias.

El conocimiento durable queda en spec, plan, tareas, inventario, auditorías y evidencia del proyecto. No era necesaria una modificación de un vault personal. Los recursos temporales son herramientas de verificación, no implementaciones pendientes.

## Nueva aceptación real del mismo demo

La segunda prueba solicita e implementa mejoras en el demo existente con Codex, OpenCode y Claude: tres recibos nativos `succeeded`, 5 pruebas de contrato, 20 comprobaciones HTTP y 23 Chromium sobre la versión final. Notas originales conservadas; sin reenvío ni verificación manual del turno. La ordenación la hace el controlador, sin afirmar delegación autónoma del supervisor. [Informe y evidencia](../../docs/audits/2026-10-02-real-demo-improvements.md).

## C01 ejecución

Regresiones originales: seis fallos reproducidos en `/tmp/spec008-core-red.log`; Codex tras reinicio falla en `/tmp/spec008-codex-red.log`; presupuesto inicial durante caída de almacenamiento falla en `/tmp/spec008-start-budget-red.log`.

Correcciones: restauración privada antes de publicación, rechazo de recuperación sin recibo, presupuesto local independiente del registro diagnóstico, incertidumbre después de enviar, digest de Codex restaurado y convergencia de fallos de sustrato. Work pendiente se conserva ante errores de recuperación; paso y puntero actuales se registran antes de perder su propietario. Una fase desconocida de una base antigua no permite un nuevo envío.

Pruebas: `/tmp/spec008-core-targeted.log` 343 passed,3 skipped; `/tmp/spec008-workflow-targeted-v2.log` 140 passed; `/tmp/spec008-core-final-batch.log` 68 passed. Pruebas omitidas no cuentan como aceptación. Revisión independiente: `/tmp/cao-integration-core-review.md`, hallazgos corregidos con regresiones adicionales.

## C03 grafo

Implementación inicial: `/tmp/cao-integration-graph-implementation.md`,155 passed. Revisión independiente encuentra carrera de admisión durante apagado: `/tmp/cao-integration-graph-review.md`. Cuatro regresiones RED antes del cierre. Corrección del cierre y reinicio de ciclo verificada: `/tmp/spec008-graph-final-v2.log`,159 passed.

## C02 permisos y contratos

Integración en curso. RED broker3 fallos; RED lanzamiento6 fallos; RED shim8 fallos; RED persistencia/API3 fallos. Las pruebas Kimi Code importadas se conservan como aceptación pendiente de C04. No se afirma aprobación de la suite completa ni cobertura completa del proyecto.

### C03 actual native Apps and consumer recovery

- `test/mcp_server/test_app_surface_only.py` real assembled FastMCP Client: app-only native calls RED (blocked exact alias) then GREEN; model-visible rendering names unchanged; arbitrary prefixes still rejected.
- Native/backend Apps batch: 54 passed (`/tmp/spec008-app-native-green.log`). Bridge integration RED 1 failed/9 passed before mapping, then frontend 82 passed (`/tmp/spec008-app-ui-native.log`). All four HTML bundles rebuilt (`/tmp/spec008-app-ui-native-build.log`); typed config-only environment test passed.
- Independent consumer review found generic structured errors replacing recovery projection and late action response replacing a newer generation. Both corrected; targeted Python 11 passed, frontend 5 passed; independent Python review 20 passed. Review `/tmp/cao-integration-consumers-review.md` accepted final scope/generation/no-redelivery behavior.
- Ops actual scoped batch 102 passed (`/tmp/spec008-ops-turns-final.log`), retaining pending/reconcile/internal distinction and fenced recovery.
- API lifecycle boundary regression: 8 RED, then 8 passed (`/tmp/spec008-api-lifecycle-green.log`). Broad lifecycle batch exposed stale test mocks and concurrently incomplete C04 tmux edits; it has not yet passed and requires rerun after ownership handoff.

C03 final independent combined gate: 280 Python surface tests passed137.37s, plus30 plugin/registry passed31.35s (`/tmp/spec008-c03-reviewer-full.log`, `/tmp/spec008-c03-reviewer-plugin.log`). Reviewer accepted source after native addressing and error/order corrections. Rust T023: 3 original regressions RED, then full221 unit tests passed; additional WaitingUserAnswer receipt diagnosis and separate causal fields final221 passed (`/tmp/spec008-tui-final.log`). Broader endpoint/hermetic Rust gates remain T053.
C08 PR-health example: exact latest frozen source introduced only after missing-file RED;138 local fake-GitHub tests passed. Documentation adaptation to prepared scope approval remains pending T035/T050; no schedule/comment actions performed.
C08 CI: full SHA action pins and locked uv/npm installs adapted while retaining original Work host/real-provider workflows.2 policy regressions RED→2passed. Lock regeneration and completeworkflow/release guard checks remain pending.

T023 parity gates: actual API/main-MCP/CLI recovery consumer regressions22 passed (`/tmp/spec008-consumer-parity.log`); existing web receipt/output/Work tests55passed (`/tmp/spec008-web-turn-parity.log`). Fresh Rust221passed incl blocked dialogs diagnostic, receipt projection and separate numeric causal fields. C03/T023 reviewer accepted.
C05 publication:70 source-service tests passed (`/tmp/spec008-authoring-service-green.log`). API RED2/1pass fixed; old rollback regression revised to canonical winner + actual index rebuild acceptance. CLI RED3 fixed;74 CLI/revision tests passed (`/tmp/spec008-authoring-cli-green.log`). Plan-v2 imports first REDmissingcomponents, then missingpublicenvelope; additive pure/envelope integration retained v1 fixture;136passed1 old classifier-vocabulary assertion updated. Scope/admission not yet active.

C05 authoring/public identity slice accepted independent review `/tmp/cao-c05-publication-review.md`: final152 plan/envelope/API/revision tests passed,70service/74CLI-revision/43MCP. Reviewer found event-follow bearer omission; fixed retaining Accept and Last-Event-ID, all24 mocked event tests passed with explicit marker override (`/tmp/spec008-c05-review-events-all.log`). Default pytest markers exclude integration tests; scoped gates explicitly include hermetic marked tests. Active scope/admission/default-required migration remain pending.
C06 runtime building blocks started from frozen remote branch: originalmissingmodule RED; firstparts34passed1missingbridge. Protocol/token/registry/bridge are additive, not yet exposed through main API; durable operation/placement/incarnation hardening remains required before channel completion.

C05 MCP existing lifecycle auth audit reproduced3 failures (start/status/SSE). Root added local credentials to22 API_BASE_URL calls and merged SSE headers; foreign broker/release credentials preserved. Scoped workflow MCP68passed (`/tmp/spec008-mcp-workflow-auth-green.log`). Expanded foreign release control added by reviewer; broader MCP gate still required after these additions.
C06 runtime pure/config gate35passed (`/tmp/spec008-runtime-config-green.log`): config invalidity refuses before database/background initialization. Channel is not registered yet; durable commands, runtime placement schema and Work fencing pending.
C04 owner explicitly released only agent_profile model/schema/parser to root for KAS; strict Kimi swarm fields preserved. KAS models/compiler/render/lint/facade added aftermissingmodulesRED, stronger C02 v2 install availability preserved rather than branch '*' default. Provider launch remains old refusal until full opt-in guard and approved policy integration.

C04 implementation handoff: `/tmp/cao-integration-execution-implementation.md`; all owned source released. Fresh provider/config/install/policy/actual Work recovery2152passed6skipped, receipt/cap/routing/API51passed, Herdr composition83passed, lifecycle38passed, composition5kept0broken. Broad core1543passed1skipped6deselected had7 stale-fixture failures; those nodes repaired and covered by fresh51gate, not misreported as a fully green broad run. Independent execution review pending.

C08 weekly-release source integrated from frozen branch with newest compatible action SHA pins; isolated executable preflight tests plus CI policy14passed (`/tmp/spec008-release-green.log`). Scheduled workflow is gated to upstream repository, verifies exact-head CI and the prior GitHub/PyPI release, skips no-new-commit weeks and serializes release jobs. No release workflow executed. Root `uv lock` regenerated after FastMCP/TOML/wcwidth/WebSocket floors and frozen upstream security upgrades; urllib3 2.8.0 and PyJWT2.15.1 selected (`/tmp/spec008-root-lock.log`). Panel lock regenerated independently.

C06 KAS preparation: existing profile/compiler/render tests66passed; actual CLI install/lint plus existing install facade regression75passed (`/tmp/spec008-kas-install-final.log`). Read-only lint redacts prompt; real install writes separate KAS artifact and redacted sidecar, preserves native v2 availability. Stronger guard requires profile even with opt-in and compares persisted exact policy material;32passed (`/tmp/spec008-kas-guard-green.log`). Guard not yet wired at production launch/restore/reuse sites; T040 remains open awaiting additive persisted proof and integration.

C06 fleet: root port preserves main dashboard authentication, Profiles/Plugins/Work/007 surfaces and authoring navigation lock. First broad run found real setup gating bug plus stale standalone mocks; fixed and45access/profile tests passed.9 panel proxy tests and44 existing panel tests passed; fleet/base routing10passed and transport/auth33passed. Full web424passed33files (`/tmp/spec008-fleet-web-all-final.log`); build succeeded (`/tmp/spec008-fleet-web-build.log`). Review found missing Plugins namespace, per-node credential failure isolation and a late error-body epoch race; proposed patch applied, focused acceptance pending. T041 remains open until review closure and usable deployment/routing proof.

Fleet review follow-ups: missing Plugins/credential isolation/error epoch fixes passed13panel and60HTTP/base/routing tests. Reviewer then reproduced authenticated retry bootstrap bypass; discovery now follows available principal identity and dashboard waits for actual fleet success or definitive404. Login/setup and routine local-session renewal retain their flows;42bootstrap/access/profile tests passed (`/tmp/spec008-fleet-bootstrap-final.log`). Two cancellation/early-WebSocket-accept resource leaks reproduced and patched; fresh15panel resource tests and final build pending. Remote durable operation store added independently but remains unregistered pending central schema/lifecycle/Work wiring; tests run in isolated SQLite only.

Fleet independent review accepted `/tmp/cao-c06-fleet-review.md` after seven concrete findings corrected. Fresh final15panel (`/tmp/spec008-fleet-resources-final.log`),60API/routing,42auth/bootstrap/profile,4reviewer-bootstrap and final production buildpassed. Reviewer architecture5kept0broken; scoped diffclean. Minor NodeSwitcher selector order moved before conditionalreturn to preserve React hook ordering. T041 live deployment/routing acceptance still belongs to T055; no remote cluster activated. Remote durable operation journal39tests passed (`/tmp/spec008-remote-durable-green.log`), proving restart claim, changed-material refusal, incarnation invalidation and immutable late settlement. Production schema/channel/Work wiring remains pending; purejournal does not count as completedremote capability.

C04 independent review accepted `/tmp/cao-c04-execution-review.md`. Removed one identical duplicate `_pane_mark` definition after AST equivalence proof; reviewer confirmed exactly one definition, preserved baseline symbols and frozen historical catalogs, scoped diffclean. T024–T029 checked. C05 MCP/C08 release independent review accepted `/tmp/cao-c05-mcp-c08-release-review.md`;4fresh auth/isolation regressions passed, including foreign release-token control. Remaining channel/KAS/private-scope/continuation work remains explicit.

### C06 KAS native policy and C05 recovery inventory follow-up

KAS uses an exact effective per-terminal private profile/artifact, private source snapshot, persisted SHA-256 proof, no-replace concurrent publication and bounded no-follow reads. Default launch stays disabled; explicit opt-in also requires validated Cedar material. Actual initialization rejects missing/drifted proof, conflicting explicit model/tools and native trust-all-tools dialogs. It revalidates after the shell wait before the first launch write. Review reproduced these boundary gaps before their fixes; reviewer evidence: `/tmp/spec008-kas-review-final.log` (11 passed), strengthened retention check in the reviewer report `/tmp/cao-c06-kas-review.md`. Root provider/terminal/agent-step regression: `/tmp/spec008-kas-production-final.log` (299 passed). Exact-stop/lifecycle suite: `/tmp/spec008-kas-lifecycle-final.log` (88 passed, 3 deselected). Structured new KAS HTTP refusals retain code/profile_field/message; legacy constructor strings remain compatible: `/tmp/spec008-kas-http-green.log` (2 passed).

Current recovery profile v33 explicitly adds the six prepared workflow plan/attachment/alias tables and KAS digest; v32/v31 and older catalogs stay frozen. Independent read-only inspection and actual authorized capture/restore pass; complete recovery bundle suite: `/tmp/spec008-recovery-bundle-final.log` (85 passed). The earlier v33 full run had three stale current-version fixture assertions; corrected only current-profile fixtures, retaining exact historic v32 rejection and inspection.

Ruling: KAS cannot accept a trust-all-tools consent dialog even with opt-in enabled. Its compiled native grant must already hold; accepting that dialog would make the restricted grant unproven. Cost if the wrapper needs that consent for restricted KAS: this wrapper is refused until its native behavior has been independently proved, rather than silently widening authority.

No real KAS process or external deployment has been launched in these checks. C06 task completion still needs the narrow private-proof sweep cleanup re-review and final integration coverage.

### C07 T046 — bounded late observations

Ordinary receipt-backed turns, including supervisors, now receive six separate durable evidence-read slots with a 60-second cooldown and finite horizon after initial reconciliation. The observer never sends input, pins the current generation and rechecks ordinary Work ownership at result CAS. Lifespan owns and drains reads; store outages retain evidence and retry the next sweep. Authentic restored Codex receipt behind a picker settles without clearing its blocked status; a forged nonce does not. Root/reviewer final gate14 passed, `/tmp/spec008-late-review-fix.log`; initial database/recovery gate116 passed, `/tmp/spec008-late-inventory-green.log`. Independent review accepted `/tmp/cao-c07-late-observer-review.md`. Frozen v33 preserved, v34 adds observation state; subsequent v35 assignment schema remains under integration. Managed Work result acceptance keeps its authenticated reducer path.

### C05 scoped plans — reviewed integration

T032–T035 source implementation and independent review accepted:197 focused tests, actual provisioned child preparation/admission/HEAD-drift1; reviewer4 actual Git/revocation/KAS-resource/corrupt-manifest cases; composition5 kept0 broken. Reports `/tmp/cao-c05-scope-implementation.md`, `/tmp/cao-c05-scope-review.md`. Web review67 passed. Follow-up cross-result composition found missing owner retention inside credentials/result readers; C07 work is correcting this actual v2 boundary and validating restart/history separately before full acceptance. Same-user authored Python and strict child HEAD approval pins remain documented.

### Additional C07 whole-assignment and historical-result composition

The concrete composed v2 Work runtime passes authenticated receipt issuance,
result acceptance, journal projection and fresh-runtime reads. Five independent
cases preserve accepted historical facts after origin/grant revocation and live
profile drift while refusing subsequent effects (`/tmp/cao-c07-scoped-history-red.txt`,
filename retained after turning green). Standard Work service/projector and
assignment regression gate: 42 passed (`/tmp/spec008-work-owner-assignment-final.log`).
The projector fixture now explicitly expects its internal historical read mode;
no caller can request that mode to weaken effect authorization.

Recovery profile 36 adds the Beads operation ledger and five remote operation,
placement, runtime, observation and cancellation tables. Historical profiles through
35 remain frozen. Actual recovery/export/import tests: 85 passed
(`/tmp/spec008-inventory36-green.log`). These records preserve observations and
uncertainty; they do not constitute portable Work grants.

Independent local/fresh/elastic whole-assignment tests: 32 passed
(`/tmp/cao-c07-whole-assignment-final.log`). They cross real SQLite and native
creation/deferred delivery with isolated fake backends and a fake broker; real
RS256 authentication is checked separately. Callback changes conflict with the
same fresh intent, profiles resolve before admission, and elastic allocation is
claimed before broker effects. Elastic parent generation/incarnation is checked
under the original dispatch lock immediately before allocation. Public retry keys
remain the caller's keys. Unknown allocation/delivery never causes another worker
or automatic release of possibly running work. Final composition review pending.

Beads metadata/adapter/bulk tests: 15 passed (`/tmp/spec008-beads-bulk-gate.log`),
MCP transport and assignment HTTP regression: 3 passed
(`/tmp/spec008-beads-transport-gate.log`), initial Tasks UI tests: 2 passed
(`/tmp/spec008-beads-ui-green2.log`). Beads execution/controller and full UI parity
remain pending; no claim of a live `bd` binary or completed T036–T039.

### Remaining-fork parity findings and additional integration

The independent remaining-family audit (`/tmp/cao-final-fork-parity-review.md`)
identified three genuine gaps rather than declaring them equivalent: command
catalog coverage, compressed/native archive ingestion and non-Git project marker
continuity. The source audit separately verified 508 component, 92 memory and
88 profile UI cases; ten old elastic tests need migration to the durable API.

The Click/catalog boundary now passes all four tests with 119 explicitly classified
commands (`/tmp/spec008-catalog-green.log`). Rust unit tests pass 221 and three
additional hermetic tests pass. The full Rust command exits nonzero because two
live endpoint contracts hit an existing service with `browser_origin_denied`;
no existing service/auth setting was changed. Those contracts require an isolated
appropriately configured current server during T055.

Bounded OKF tar ingestion and canonical native-v1 conversion now pass eleven tests,
including actual current-store round trips, tampered hashes/private scopes and
unsafe member refusal (`/tmp/spec008-memory-tar-actual.log`). Existing importer/
exporter and transport regression: 76 passed (`/tmp/spec008-memory-tar-broader.log`).

Opt-in private project marker/registry tests pass three actual SQLite/directory
cases (`/tmp/spec008-project-marker-green.log`): rename continuity, distinct
surviving copy, readonly corrupt/unknown fallback, explicit override/disabled
no-write. Current recovery profile37 freezes its new registry table while leaving
36 and older catalogs unchanged. Broader memory/recovery gate remains running.

Whole-assignment T045 independent final review is PASS: 33 cases and composition
5 contracts kept/0 broken (`/tmp/cao-c07-assignment-review.md`). Global required-mode
elastic refusal occurs before admission/allocation. The native runtime bridge is
still under its own final owner/review gates.

### Remote causal ordering and native memory parity closure

Independent remote review `/tmp/cao-c06-remote-review.md` passes 16 native-SQLite/provider/Bridge/API cases and composition 5 kept / 0 broken. The final fixes serialize status capture and revision allocation with terminal command effects, retain exact-delete uncertainty as HTTP 504/reconciliation, and prevent authenticated late launch proof from resurrecting a normally deleted placement after reconnect. T042/T043 are complete. The interrupted broad consumer run is not a full-suite result; current split consumer evidence remains 316 and 32 tests in their separate recorded gates.

Independent memory review `/tmp/cao-t052-memory-review.md` closes descriptor-anchored marker publication, frozen native all-scope credential rejection, retention of every historical fact as escaped content, and target-identity comparison at actual audited store/removal. Current memory import/export and meaningful new acceptance gate: `/tmp/spec008-memory-target-full2.log`, 97 passed. Composition: `/tmp/spec008-memory-review-composition-final.log`, 5 kept / 0 broken. No archived row ID, scope ID, pathname, or marker becomes Work authority.

Recovery profile 38 adds literal reviewed continuation/coordinator and Beads correlation tables/FKs while preserving frozen profiles 28 through 37 and Work schema 39. Read-only inspection verifies exact additive DDL, triggers and indexes; initialization never repairs modified objects. Current full recovery gate passed 85 cases in the combined Beads/recovery run; one Beads assertion incorrectly counted the fixture's preexisting unrelated run and has been replaced by absence of the specific refused second run. The dedicated actual Beads Work gate passes five cases, including approval refusal, material CAS, one-run retry, accepted-artifact completion and unresolved cleanup retaining ownership.

## Cierre de continuación y paridad adicional

C07 pasa 294 pruebas de regresión en `/tmp/cao-c07-closure-gate.log` y diez pruebas independientes en `/tmp/cao-c07-continuation-review-final-current.log`; el informe independiente `/tmp/cao-c07-continuation-review.md` no conserva defectos importantes. Incluye proceso Python real, credencial por descriptor privado, reinicio con propietario RSA verificado, segunda generación una sola vez, evidencia de archivo con grant real, rechazo de FIFO, revocación y cese incompleto. La lectura de evidencia dañada indica `work_verified_completed=false` y `validation_refused`.

Beads proyecta resultados aceptados y correlaciones de intentos/terminales en la tarea y el progreso del epic, conservando el hash material de la tarea. Una tarea editada después del resultado no recibe certificación para el contenido nuevo; cierre externo y finalización de Work permanecen estados distintos. `/tmp/spec008-beads-progress-final.log`: 14 pruebas reales de SQLite/API y propietario. `/tmp/spec008-beads-progress-ui.log`: 14 pruebas de interfaz y contratos de incertidumbre.

La integración de escritura atómica conserva el parámetro `mode` de main además de la corrección de umask de upstream. `/tmp/spec008-atomic-consumers-green.log`: 137 pruebas, incluidos consumidores Gemini y settings. El instalador de plugins copia únicamente el subdirectorio local solicitado después de validar contención; evita copiar un checkout entero y conserva enlaces para validación. Su primera reproducción válida está en `/tmp/spec008-plugin-subdir-real-red.log`; la batería final de plugins sigue pendiente.

El broker se probó con el cliente Kubernetes real, instalado únicamente en un entorno desechable bajo `/tmp/cao-spec008-broker-env`. `/tmp/spec008-broker-actual-serializer.log` termina con `FAILURES: none`; no se conectó a ningún cluster ni se activaron recursos. Esta prueba completa el requisito de serialización que faltaba en T044, junto con 93 pruebas de CLI de fleet y la comparación de los 22 archivos EKS congelados. No certifica un despliegue externo.

## Aceptación autónoma real del supervisor (2026-10-02)

**FAIL global.** La nueva prueba da una única orden inicial a Claude y deja que cree y coordine Codex y OpenCode. No equivale a la prueba anterior con coordinación directa del operador. Codex recuperó un recibo auténtico temporalmente bloqueado sin nueva orden. OpenCode sí emitió sus recibos, pero el parser seleccionó un bloque anterior terminado en error de transporte y dejó el turno en reconciliación. El reinicio conservó identidades; una cuota 429 impidió acreditar cierre completo. Chromium detectó una variable sin declarar en el demo y GET stats devuelve 404. No se aplicaron correcciones para rescatar estos casos.

El [informe de aceptación autónoma](../../docs/audits/2026-10-02-autonomous-acceptance.md) distingue resultados parciales, límites externos e intentos inválidos del controlador. Conserva evidencia reproducible, comprobaciones de composición y conservación de datos; esta aceptación permanece abierta.


## Autonomous repair final verification — 2026-10-02

Phase 9 T058–T067 completed on main without commit/push/merge. Final native Claude/Codex/OpenCode acceptance passed both authentic late-receipt and server-restart scenarios: one initial order per phase, two children, unchanged identities after restart, all peer/supervisor messages delivered, no duplicate assignment, immutable original notes, and zero operator corrective messages/verify/retry calls. The normal predecessor run passed; cancelled diagnostic runs are retained as superseded evidence, not final PASS.

Regression evidence: 2739 passed/13 skipped; updated soft-tool/launch contracts 493 passed/3 skipped; final integration/recovery gate 438 passed; final Claude/receipt gate 239 passed, including 17 focused native-frame cases. These sets overlap. Demo: 13 tests, 30 real HTTP checks, 15 Chromium checks. Composition: five contracts kept, zero broken; final verdict PASS WITH RISKS because Copilot installation is broken and nine native executables are absent. All 13 registered adapters retain tested shared reconstruction/admission contracts; strict receipts apply to the four opted-in adapters.

Durable report: [autonomous repairs](../../docs/audits/2026-10-02-autonomous-repairs.md). Evidence: final verification (`../../docs/audits/evidence/2026-10-02-autonomous-repairs/final-verification.json`; artefacto histórico local), manifest (`../../docs/audits/evidence/2026-10-02-autonomous-repairs/evidence-index.json`; artefacto histórico local), cleanup (`../../docs/audits/evidence/2026-10-02-autonomous-repairs/cleanup-progress.json`; artefacto histórico local). Source/native/Graphify hashes match all 385 Python files. Temporary credential copies, sessions, servers, tests and replay helpers are removed after archiving sanitized evidence.


## 2026-10-03 — remaining provider installation and Copilot readiness

T068–T070 closed with native Copilot profile/session creation and two distinct API-delivered turns on the same demo (13 unittest tests, then README read), no corrective input, preserved notes hash and owned-resource cleanup. All thirteen CLI version probes passed. Eight newly installed CLIs lack native authentication; Hermes passed a direct read-only demo request via an existing GitHub Copilot credential without persisting it, but its CAO orchestration is unverified. Provider regressions: 51 passed; overlapping monitor/inbox/provider contract gate: 268 passed, two deprecation warnings. Composition: five architecture contracts kept, none broken. Graphify current source snapshot: 385 Python hashes verified. No commits or pushes. Details: docs/audits/2026-10-03-provider-readiness.md.

Los artefactos históricos locales mencionados en este informe se conservan fuera del conjunto publicado; no son evidencia de una ejecución nueva. Las comprobaciones actuales de cierre se registran en el spec 015.
