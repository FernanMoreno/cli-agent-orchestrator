# Evidencia de cierre — 2026-09-30

Estado vigente: cierre documental del alcance local acordado; 001 tiene 129 tareas
y 002 tiene 7, todas completadas. [Resumen vigente](acceptance-status.md).
T085 prepara/verifica el candidato independiente y no integra upstream en main.
El checklist requirements.md conserva sus 15 revisiones de calidad aprobadas;
no es un certificado de implementación. El estado de cada tarea está en tasks.md.

## Dependencias externas

- T084 / FR-019: completada para Codex, Claude y OpenCode v1, con 0 € adicionales
  autorizados. Las nueve combinaciones pasan en tmux y en Herdr0.9.1: 18 celdas.
  Versiones/modelos/escenarios en [t084-live-matrix.json](t084-live-matrix.json).
  Cuota no agotada: espera/continuación figuran not_applicable.
- T085 / FR-024: candidato independiente autorizado, 12 conflictos resueltos,
  aceptación Python completa por inventario validada. [Revisión](t085-integration-review.md).
  No se incorporó el merge a main ni se publicó.
- O03 / FR-022: aceptados localmente mock_cli y la matriz autorizada de tres
  proveedores en tmux/Herdr; otras combinaciones conservan aceptación pendiente.
- No existe host de producción por decisión de alcance ya registrada. Docker
  local acredita el perfil local; WORK_BACKENDS sigue vacío por defecto.

## Trazabilidad de 25 requisitos

Las suites siguientes son evidencia ejecutable, no una afirmación de que cada
proveedor real haya pasado. Los resultados frescos se registran al final de
workflow-status.md; resultados omitidos o fallidos no se convierten en éxitos.

| FR | Suites / evidencia principal | Límite de aceptación |
|---|---|---|
| 001 | work_repository, work_service, e2e/work_lifecycle | entradas mock y Docker local |
| 002 | work_reducer, work_service, t020/workflow_acceptance | receipt/result autenticados managed |
| 003 | work_launch, work_lineage_origin, work_cancellation, T019/T020 | backend local opt-in |
| 004 | work_repository, workflow_journal, workflow_step_projection_journal | transacción estado/evento |
| 005 | work_authority, work_admission, work_contract_binding | rechazo antes de efecto |
| 006 | work_origin_authority, work_provisioning, API Work | principal/grant propios; sin identidad del body |
| 007 | work_reservations, work_replacement_fencing | SQLite real y fencing |
| 008 | work_scheduler, integration/work_admission | concurrencia, ciclos y equidad |
| 009 | knowledge_revisions, knowledge_policy | procedencia y decisión versionada |
| 010 | delegation_snapshot, knowledge_context, work_lifecycle, T020 | hashes/bytes tras reinicio |
| 011 | work_continuation, work_cancellation | artefactos verificados; sin redelivery incierta |
| 012 | provider_capabilities, API providers, TUI endpoint_contract | capacidades individuales, no parejas fijas |
| 013 | API work_projection, Web work-state, TUI renderer | fixtures comunes |
| 014 | knowledge_policy, memory_knowledge_policy, knowledge_authority | ámbitos y permisos vivos |
| 015 | knowledge_multinode, knowledge_recovery | autoridad única/CAS; partición falla cerrada |
| 016 | work_decisions, recovery_decision_intake, API decisions | escritura exige autoridad; CAS |
| 017 | .importlinter, replay_branch, dueños revisados | deuda indirecta journal→DB→memoria descrita |
| 018 | work_migrations, recovery_bundle, work_recovery | main: perfil30/schema39; candidato: perfil31/schema39; históricos verify-only |
| 019 | real_provider_matrix_contract; T084 | 18 celdas autorizadas aceptadas; cuota no agotada not_applicable |
| 020 | API workflow_revision, step_contract | revisión y contrato por intento |
| 021 | config_service; docs/configuration.md | registro y precedencia |
| 022 | herdr_work_status, e2e/herdr_generic_status | mock_cli y matriz autorizada aceptados; O03 ampliado pendiente |
| 023 | script_error_kind, workflow_service, script_runner | error_kind durable |
| 024 | upstream-integration-plan.md, upstream-preparation.md | T085 candidato aislado preparado/aceptado; merge en main separado |
| 025 | docs/historical-spec-triage.md, tasks.md | cierres y dependencias explícitas |

## Trazabilidad de 10 criterios de éxito

| SC | Evidencia y frontera |
|---|---|
| 001 | Work lifecycle: cinco caminos, identidades y reinicio; managed T019/T020 separado de legacy |
| 002 | reducer/service, cancel/replacement, T020 no-result/failure/retry |
| 003 | authority/provisioning/admission: rechazo pre-efecto, descendientes y revocación |
| 004 | scheduler/reservations: diez contendientes, dos plazas y equidad |
| 005 | snapshots: launch, inbox, hijo/handoff y YAML/script con hashes/bytes idénticos |
| 006 | recovery bundle + restore aislado: integridad, blocked_restore y conciliación |
| 007 | API/Web/TUI: mismo fixture de estado; Herdr real: mock_cli y matriz autorizada de tres proveedores |
| 008 | 25 FR trazados; cierre local documentado y límites ampliados explícitos |
| 009 | suite ordinaria sin llamadas facturables; T084 aceptada con presupuesto 0 € adicionales, cuotas no agotadas |
| 010 | decisiones/grant y replay CAS: intento obsoleto no sustituye el concurrente |

## Decisiones sobre artefactos durables

Graphify se consultó antes de implementar y se contrastó con código. Se difiere
el refresh completo por el bloqueo de rutas OneDrive/9p ya reproducido. No se
publica un grafo parcial; se restauró únicamente el stamp que cambió la consulta.
No se copia el grafo ni logs al vault. Las invariantes revisadas quedan en los
contratos y este informe del repositorio; esta sesión no añade una transacción
Obsidian sin selección explícita de contenido por el usuario.

## Gates locales anteriores a T084/T085 (histórico)

| Gate | Resultado / exit |
|---|---|
| Python historias/regresiones | 2680 passed, 4 skipped; exit 0 |
| Python integración/ingresos adicionales | 368 passed, 5 skipped; exit 0 |
| Docker real T019/T020 | 14 passed; exit 0 |
| Docker unidad/supervisor/launch tras última corrección | 35 passed; exit 0 |
| Web Vitest | 341 passed en 20 archivos; exit 0 |
| TUI/Rust con servidor HTTP temporal | 252 passed; exit 0 |
| project-composition-check | 5 contratos kept, 0 broken; exit 0 |

Los gates se solapan: no sumar las cifras como número de casos únicos. La
batería principal terminó tras las correcciones de YAML, autoridad y lifespan;
Docker se corrigió después y tuvo una repetición específica completa fresca.
Los nueve skipped de Python corresponden a Bubblewrap scratch/pidfds ausentes,
Landlock ABI7 frente a ABI9, Kiro CLI ausente, una ruta YAML condicionada a CLI
real y Herdr CLI ausente. La aceptación YAML/script mock_cli del ciclo de vida
sí pasó; las combinaciones reales ausentes no se declaran aceptadas.

No se elimina la evidencia de fallos anteriores: 67 regresiones iniciales y el
fallo Docker de la repetición se diagnosticaron antes de las correcciones.
El informe inicial conserva causas y límites. En esa intervención, T084/T085
y O03 seguían pendientes. La aceptación posterior de la matriz autorizada y del
candidato está registrada más abajo y en acceptance-status.md; las combinaciones
adicionales conservan sus límites.


## Primera aceptación T085 — 2026-09-30 (histórico, antes de T123–T129)

Candidato upstream preparado y verificado en clon independiente. Inventario
Python único: **13.476 casos**; **13.357 passed, 118 skipped, 1 xfailed**;
cero nodeids faltantes y cero fallos vigentes. Aceptación por particiones y
repetición de archivos afectados, no una ejecución monolítica que se atribuya
a los intentos interrumpidos. Los skips conservan sus límites de entorno y el
xfail conocido no se convierte en passed. Los fallos anteriores quedan
registrados junto a sus reruns; la prueba final de telemetría pasa en ambos
checkouts con DB propia y esquema Work real.

Gates finales: arquitectura candidato 5 kept/0 broken; composición del checkout
original PASS; black/isort sobre 958 archivos exit0; enlaces Markdown/diff
check exit0. Tras normalizar formato se repitieron 1.682 casos de runtime
con 3 skipped y 1 xfailed; 825 casos de settlement/Work/replay pasan.
Web, TUI, MCP Apps, Docker y Agent Plugins conservan sus gates documentados
en la revisión. El wheel final instalado coincide byte a byte con nueve módulos
del candidato y contiene sus recursos Web/TUI/MCP Apps/Agent Plugins.

**T085 completada; ambos specs quedan sin tareas abiertas.** Esto acredita
preparación y aceptación local del candidato; su integración en main sigue
siendo una acción separada. O03 no certifica proveedores adicionales a los
autorizados; no existe despliegue/host de producción dentro de este cierre.
Artefactos locales de cobertura y parche en `/home/felni/ct/`; manifiesto
final: `/home/felni/ct/t085-candidate-manifest.json`.


## Corrección posterior de aceptación — 2026-09-30

Las 119 incidencias del resultado anterior están resueltas en el candidato:
**13.491 passed por inventario**, con 15 regresiones nuevas y aceptación real
por entorno. Véase [omitted-acceptance.md](omitted-acceptance.md) y su JSON por nodeid.
Los contadores antiguos arriba describen ejecuciones históricas.
