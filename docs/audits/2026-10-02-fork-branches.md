# Auditoría de ramas del fork: diferencias con la copia actual

Fecha: 2026-10-02. Base local: `87e2d5f354374ea8acc685275e749992df51cdbd` (`main`) más los cambios del working tree, incluidos Spec007 y despliegue personal. Remotos actualizados mediante fetch; no hubo checkout, merge, cherry-pick, commit, push ni cambios de producto.

## Resultado

**Sí hay mejoras en otras ramas que todavía no tenemos implementadas.** Se inventariaron las **32 ramas publicadas del fork** `FernanMoreno/cli-agent-orchestrator`. Tres son ancestros de HEAD —main, dev-3.0release y aipm-native-governance— y29 tienen commits fuera de esa ascendencia. Las dos ramas locales adicionales también son ancestros de HEAD.

Eso **no significa29 funcionalidades ausentes**. El commit local `9f858b73` integra cambios de varias familias en una revisión distinta. Hay implementaciones equivalentes, sustituciones y adaptaciones posteriores que no coinciden con los patch IDs originales. Se comprobó tanto historial como fuente actual; nombres de archivo ausentes y líneas distintas sólo se usaron para localizar candidatos, no para probar por sí solos que falta una funcionalidad.

El fork/main coincide con HEAD; el original upstream/main está en `a1c3d138` y tiene36 commits fuera de nuestra ascendencia. Algunos efectos ya existen parcialmente. La revisión principal cubre el fork; upstream se revisó adicionalmente por sus cambios recientes directamente relacionados con turnos, permisos y diagnóstico.

## Mejoras confirmadas en ramas del fork

### F01 — Caché del grafo de CAO resistente a timeouts y con presupuesto

Rama: `origin/fix/r3-aprime`, tip `be835e1f`.

La rama añade tareas propiedad de la caché, consulta de estado, deadline600s, máximo2 builds activos+2 pendientes y64 entradas. API usa `asyncio.shield` para no cancelar el build por el timeout del solicitante. Nuestra caché usa locks por key y el builder pertenece al request; no tiene esa API de tareas ni esos límites.

Prueba actual aislada: cancelar el caller cancela el builder; una nueva consulta inicia otro build: `builds=2 cancelled builds=1`. No mide el coste de threads de producción. La rama también documenta que un deadline no puede detener un cuerpo `to_thread` ya iniciado: no venderla como solución a todos los costes residuales.

Fuente actual: `src/cli_agent_orchestrator/graph/cache.py:147-177`. Fuente de rama: mismo archivo, `get_or_build_task`, constantes `GRAPH_BUILD_MAX_S`, `GRAPH_BUILD_CONCURRENCY`, `GRAPH_BUILD_QUEUE_MAX`, `GRAPH_CACHE_MAX_ENTRIES`; `api/main.py:4108` usa shield.

**Prioridad alta para consultas de contexto costosas.** Adaptar la lógica conservando nuestra partición por identidad/owner y políticas de conocimiento; no sustituir el módulo entero por la variante antigua. Es caché del grafo de CAO, distinta del índice externo Graphify.

### F02 — MCP Apps: modo restringido y artefactos disponibles

Rama: `origin/fix/r4-review-round1`, tip `e5b8bc75`.

Incluye middleware `app_surface_only.py`: filtra tools/list y rechaza calls de herramientas fuera de la allowlist cuando se solicita `apps.only`. Error al instalar la restricción impide iniciar el servidor mediante `McpServerStartupError`. Esa capacidad no existe en la copia actual.

También revisa la búsqueda y registro de HTML: contempla la ubicación empaquetada `ext_apps/apps_static`, archivos regulares/no vacíos y diagnósticos más precisos. Varias de esas comprobaciones no están en la versión actual de `ext_apps/apps.py`.

Fuentes: rama `src/cli_agent_orchestrator/mcp_server/app_surface_only.py:15-48`; `plugins/builtin/mcp_apps.py:82-116`; `plugins/registry.py:114`; `ext_apps/apps.py:170` en adelante.

**Candidato acotado.** No corrige por sí mismo pérdida de diagnóstico de turnos ni añade verify/cancel: B07/B08 de la auditoría anterior siguen necesitando trabajo específico. No afirmar que modo Apps reemplaza autorización de las operaciones subyacentes.

### F03 — Autoría conversacional y aprobación antes de ejecutar workflows

Ramas: `origin/feat/583-bolt3-conversational-authoring` (`e6512dd6`) y su continuación `origin/feat/583-bolt3-conversational-` (`4d3e50d6`). La continuación agrega `4d3e50d6`, endurecimiento de plan y launch admission, sobre la familia de autoría. Upstream tiene además revisión posterior de la primera en `4d4bf098`.

Tenemos lectura de fuente y update condicionado por SHA (`workflow_spec_service.py:781`, `:817`), pero **no la capacidad completa** de estas ramas:

- Crear/update de spec Python mediante publicación guardada, verbs CLI create/update y herramientas MCP workflow_create/update/get/validate.
- Secuencia presentar plan, aprobar y ejecutar; postura requerida por defecto y fallo conservador ante settings inválidos.
- En la continuación: `plan-v2`, snapshot privado, scope admission, material/política de launch y resolución de valores del perfil.

Ausencias comprobadas: `services/execution_scope.py`, `frozen_memory_snapshot.py`, `launch_material.py`, `launch_policy.py`, `private_plan_snapshot.py`, `scope_admission.py`, `utils/profile_value_resolution.py`. Nuestro `plan_identifier.py` define plan-v1; settings conserva gate opt-in (`settings_service.py:629`). CLI no registra create/update y MCP no tiene los cuatro authoring tools.

Fuentes de rama: `workflow_spec_service.py:1113`, `:1185`; `mcp_server/server.py:2877-3013`; `settings_service.py:669-714`; `plan_identifier.py:98` en adelante.

**Relevante para autonomía controlada, requiere diseño y migración.** No importar solamente el default de aprobación: bloquearía primeros runs sin tener la secuencia de autoría correspondiente. Coordinar con nuestros contratos Work, autoridad, snapshots y revisión de specs. Estas ramas no resuelven por sí solas el retry post-send B01.

### F04 — Descubrimiento del grafo y timeout accionable

Rama: `origin/feat/graph-provider-discovery`, tip `cf03055e`.

Añade GET `/graph/providers`, Retry-After y datos `retryable/retry_after_s` para timeout de proyección. Nuestra API tiene `/graph/{provider}` pero no el endpoint de catálogo ni ese envelope. Mejora elección de herramientas y diferenciación entre espera y avería; no constituye un controlador de recuperación del agente.

Fuente de rama: `src/cli_agent_orchestrator/api/main.py:4018-4040`; actual `:8817`, `:8834`. Al adaptar, conservar nuestras comprobaciones de acceso al conocimiento.

### F05 — Capacidades opcionales ausentes

- **Kiro KAS Phase1**: `origin/feat/kiro-v3-phase1-profiles` (`49d39fcd`) añade perfiles, lint y compilación Cedar con guard opt-in. Hoy tenemos selección de engine y rechazo Phase0; KAS sigue rechazado. Requiere una decisión explícita de soporte, no activarlo para reparar Claude/Codex/OpenCode.
- **Fleet UI multinodo**: familia fleet-panel/eks-v2-workshop añade NodeSwitcher, selección y rutas por nodo, tratamiento de unreachable/refused y carga inicial del registro. No tenemos NodeSwitcher ni esa ruta completa; tener CLI fleet/worker y Kubernetes no equivale a tener su UI. Comparar preferentemente contra la variante `fleet-panel-web-ui-v2` antes de incorporar capas de workshop.
- **Beads/epics y Ralph**: `feat/beads-integration` y `feat/beads-epic-phase1` contienen bindings bead-terminal, gestión de dependencias, perfil master_orchestrator y loop Ralph. Faltan sus componentes específicos. Work ya cubre parte del problema con evidencia más estricta. Ralph tiene feedback/contador y complete explícito; un nombre “always-on” en el perfil no demuestra validación independiente del objetivo. No importar sus APIs y modelo de tareas entero como parche de autonomía.
- **Ejemplo pr-health**: workflow programado y guard idempotente no presentes. Upstream `785765b7` es posterior al tip del fork `50774147`; revisar esa corrección si se adopta el ejemplo.
- **Publicación semanal**: `feat/weekly-patch-release-schedule` añade schedule y gates de release. Ausente como feature; no prioritario para coordinación y no se activó ninguna publicación.

## Lo que parecía faltar pero ya tiene equivalente

- **CRUD web de perfiles**: componentes antiguos CreateProfileWizard/EditCloneModal no existen, pero ProfilesPanel usa ProfileCreateModal/ProfileEditorModal y permite crear, editar y clonar (`web/src/components/ProfilesPanel.tsx:494-512`). No duplicar pantallas sólo por no tener sus nombres.
- **TUI Python inicial**: falta `src/cli_agent_orchestrator/tui/*` de feat/321, pero el proyecto tiene TUI Rust. Es alternativa histórica, no prueba de ausencia del front door. Sus estados nuevos sí tienen la carencia identificada en B09.
- **Memoria e import/export**: faltan `_archive_format.py`, memory_export.py y memory_import.py originales, pero CLI export/import y `services/memory_archive/{base,okf}.py` ofrecen la capacidad vigente (`cli/commands/memory.py:865`, `:953`). No importar otra serialización en paralelo.
- **Vault Obsidian**: núcleo, reconciliación y servicios existen. La rama no es una feature entera pendiente. Sus diferencias requieren comparación puntual: nuestro links.py acepta paths relativos con `..` normalizados dentro del vault; la rama antigua los rechaza. Sobrescribirla retiraría comportamiento soportado, no sería automáticamente un endurecimiento correcto.
- **Kubernetes/elastic/fleet CLI**: broker, assign_elastic asíncrono, selección de proveedor, espera de readiness y worker logs/release ya existen. Nuestra CLI también maneja mejor el worker arrancando que algunas variantes anteriores. No contar todos sus commits divergentes como trabajo ausente.
- **ReDoS de parsers**: `fix/redos-provider-output-parsers` tiene escáneres alternativos, pero ya tenemos límites y separación por línea equivalentes: Codex porcentaje `\d{1,3}`, Kimi gap de modelo0..80, MiniMax head/hint por línea y whitespace horizontal. No se acreditó vulnerabilidad pendiente por no usar esos mismos helpers. Cualquier trasplante debe conservar spinner Codex actual y recibos007, que esa rama antigua no contiene.
- **fix/remove-some-comments**: principalmente variante descriptiva; no revela solución al seguimiento autónomo.

## Hallazgos adicionales en upstream reciente

### U01 — Secuencia causal del turno, PR812 / issue735

Rama `upstream/joeguo/735-turn-sequence`, tip `28e85a3a`.

Añade protocolo de send/clear/input-delivered/abort, índices correlativos turn/turn_completed, rechazo de lecturas anteriores al despacho, distinción entre evidencia de trabajo y ownership, y modelo aleatorio de interleavings sobre monitor real (incluidas formas Kiro/Grok). Es especialmente relevante para no tomar un COMPLETED antiguo por el nuevo envío.

**Implementación parcial ya existe aquí:** buffer/capture generations y rechazo de algunas capturas antiguas. No tenemos el protocolo completo `notify_input_delivered`, `abort_turn`, `turn_state` ni índices correlativos. Fuentes de rama: `services/status_monitor.py:1421`, `:1505`, `:1578`, `:1594`; `test/services/test_status_monitor_model.py`. Complementa la evidencia de recibos, no prueba éxito semántico ni sustituye settlement durable.

**Incompatibilidad concreta:** la rama declara `Terminal.turn: Optional[int]`; nuestra007 usa `Terminal.turn: Optional[Dict[str, Any]]` (`models/terminal.py:111`) como diagnóstico de recuperación. Copiarla literalmente rompe el contrato de API/web/consumidores. Diseñar nombres separados y conservar ambas garantías. Su backstop de liveness nunca debe liberar un receipt sin verificar.

**Prioridad alta de evaluación**, sin afirmar que los tests del candidato pasen sobre nuestra combinación: no se ejecutó su modelo completo ni prueba real de esa integración.

### U02 — Permisos y diagnóstico que sí faltan

Pruebas diferenciales aisladas verifican:

| Caso | Working tree actual | upstream/main |
| --- | --- | --- |
| Rol desconocido sin allowedTools explícito | Otorga `['*']` | ValueError, rechaza (`dcf13191`, PR746). |
| allowedTools explícito vacío + servidor MCP de perfil | Añade `@cao-mcp-server` | Mantiene lista vacía (`c040cf79`, PR803). |
| ShimHTTPError409 con `detail.kind=decision_required` | Mensaje genérico HTTP409 | Incluye kind/message (`7db0d114`, PR842). |
| Marker OpenCode v1 con nombre “Sisyphus - Ultraworker” | No hace match | Hace match (`5b9aa852`, PR816). |

Fuentes actuales: `utils/tool_mapping.py:150-164`; `src/cao_workflow/exceptions.py:40-48`; `providers/opencode_cli.py:62`. El caso OpenCode corresponde a marker **v1**;007 tiene patrón y flujo v2 distintos. No extender el fallo demostrado a todo OpenCode.

Además, `d884ecdb` (PR836) aplica política efectiva a `KiroAgentConfig.tools` en instalación; aquí sigue `profile.tools else ['*']` (`services/install_service.py:676`). `50629f0a` (PR838) unifica bearer local en llamadas CLI; aquí algunas llamadas session/info/terminal usan requests sin headers, aunque Work y browser ya tienen auth propia (`cli/commands/session.py:20-38`, `info.py:45`). Revisar por superficie, conservando el aislamiento de tokens locales y nuestra autenticación browser; no reemplazarla por la de upstream.

Otros commits recientes incluyen cambios de namespace tmux/panes, providers Grok/Kimi/Kiro y dependencias. Se inventariaron como candidatos; no se certificaron todas sus conductas o advisories en esta revisión. Son revisiones adicionales a priorizar según los proveedores/despliegues usados, no fixes demostrados de B01-B03.

### U03 — Runtime remoto de ejecución

Rama `upstream/feat/745-remote-runtime`, tip `a989264c`.

Añade cao-bridge, canal de runtime/token/registry, commands/result/status y reconciliación de launch/reconnect/delete. No tenemos `runtime_channel/*`. Tiene relación con seguimiento remoto G04, pero es otra arquitectura: un servidor central con runtimes de ejecución frente a nuestro target_host de nodos CAO y backends Work. No mezclarla mecánicamente ni presentarla como fix inmediato del callback actual.

## Matriz completa del fork

“Commits fuera” significa ausencia de ascendencia, **no número de features faltantes**. Incluye merges y se solapa entre ramas. Evidencia completa, patch IDs y paths: fork-branches.json (`evidence/2026-10-02-fork-branches/fork-branches.json`; artefacto histórico local), source-inventory.json (`evidence/2026-10-02-fork-branches/source-inventory.json`; artefacto histórico local).

| Rama origin/ | Tip | Commits fuera | Resultado de revisión |
| --- | --- | ---: | --- |
| main | 87e2d5f3 | 0 | HEAD; cambios locales007 adicionales. |
| aipm-native-governance | 7c258a8c | 0 | Ancestro, integrado. |
| dev-3.0release | 948c3d80 | 0 | Ancestro, integrado. |
| feat/321-cao-tui | d6d309f4 | 5 | Alternativa Python; TUI Rust vigente. |
| feat/510-web-profile-management | 3e1a548f | 2 | CRUD equivalente con otros componentes. |
| feat/583-bolt3-conversational- | 4d3e50d6 | 19 | Falta autoría completa y plan-v2/admission; F03. |
| feat/583-bolt3-conversational-authoring | e6512dd6 | 18 | Falta autoría CLI/MCP y secuencia approval; F03. |
| feat/644-obsidian-vault-knowledge-source | 4f7c8cdb | 65 | Núcleo existente; variantes puntuales no importar en bloque. |
| feat/beads-epic-phase1 | b7a3e3e1 | 40 | Beads/epics/Ralph específicos ausentes; F05. |
| feat/beads-integration | 521adc7e | 11 | Beads y master UI específicos ausentes; F05. |
| feat/graph-provider-discovery | cf03055e | 2 | Catálogo y timeout estructurado ausentes; F04. |
| feat/kiro-v3-phase1-profiles | 49d39fcd | 8 | Phase1 KAS/Cedar ausente; Phase0 presente. |
| feat/memory-implementation-phase-1 | f98d00b1 | 15 | Memoria/plugin vigentes; rama inicial antigua. |
| feat/memory-import-export | 094fd84e | 1 | Capacidad vigente con memory_archive. |
| feat/pr-health-workflow-example | 50774147 | 2 | Ejemplo ausente; versión upstream posterior. |
| feat/weekly-patch-release-schedule | 578fa980 | 2 | Schedule de release ausente; no activado. |
| feature/add-k8s-containers-support | f7059f29 | 15 | Infra básica existente; comparar detalles si se despliega. |
| feature/kimi-cli | 598f0617 | 49 | Provider Kimi y correcciones propias presentes; rama antigua no sustituye fuente vigente. |
| fix/r3-aprime | be835e1f | 26 | Presupuesto/tareas de caché ausentes; F01. |
| fix/r4-review-round1 | e5b8bc75 | 23 | Restricción Apps y parte de diagnósticos/artefactos ausentes; F02. |
| fix/redos-provider-output-parsers | 48be4261 | 2 | Mitigaciones equivalentes, escáneres diferentes. |
| fix/remove-some-comments | 992e4594 | 2 | Variante descriptiva, no mejora de coordinación acreditada. |
| joeguo/eks-elastic-contrib | b33bc6aa | 23 | Primitivas principales presentes; revisar variantes de integración. |
| joeguo/eks-v2-fleet-cli | ebf9ac93 | 13 | Fleet/worker CLI presentes, con adaptaciones posteriores. |
| joeguo/eks-v2-workshop | 06c3e888 | 25 | CLI/infra parcial presentes; fleet UI por nodo ausente. |
| joeguo/fleet-panel-web-ui | 84df3a92 | 51 | Fleet UI por nodo ausente; no duplicar broker existente. |
| joeguo/fleet-panel-web-ui-v2 | b395f5a7 | 12 | Variante acotada de fleet UI ausente. |
| joeguo/fleet-panel-workshop | b6166420 | 48 | Workshop/fleet UI ausente en parte; familia compartida. |
| joeguo/k8s-claude-code | bff3f1a8 | 22 | Infra/proveedor existentes; no verificar despliegue por historial. |
| joeguo/k8s-elastic | c18b29c2 | 29 | Elastic broker/delegación existentes. |
| joeguo/k8s-elastic-perf | 74c53080 | 31 | Primitivas async presentes; métricas históricas no validadas localmente. |
| joeguo/k8s-elastic-perf-r2 | 2c53b7ef | 32 | Variante de la misma familia, no feature independiente. |

## Orden recomendado y relación con la auditoría anterior

1. Corregir B01/B02/B03 de [auditoría de autonomía](2026-10-02-autonomous-orchestration.md). No se acreditó que alguna rama revisada contenga esos tres fixes específicos sobre nuestros recibos007.
2. Evaluar U02 por permisos/diagnóstico y marker OpenCode v1; cambios acotados con tests de contrato, conservando nuestra auth y soporte v2.
3. Adaptar F01/F02/F04 conservando nuestras garantías de acceso y recuperación; evaluar U01 con nombres/protocolo compatibles y modelo de interleavings.
4. Diseñar F03 junto con contratos Work/approval. Elegir por separado KAS, Beads, fleet UI y runtime remoto si forman parte del producto deseado.

Ninguna rama revisada acredita por sí sola el controlador completo de progreso, corrección de herramientas y validación de artefactos actuales pedido en G03. Hay piezas reutilizables y prototipos; aún requiere composición y aceptación propias.

Para implementación posterior significativa: Spec Kit, Graphify comprobado contra fuente, regresiones de composición y gates del proyecto. Esta revisión no autoriza ni realiza integración de ramas.

## Evidencia, reproducción y límites

Se verificó que los32 SHA de origin coinciden con `git ls-remote --heads origin` después de fetch. Referencias congeladas: fork (`evidence/2026-10-02-fork-branches/fork-remote-heads.txt`; artefacto histórico local), upstream (`evidence/2026-10-02-fork-branches/upstream-remote-heads.txt`; artefacto histórico local).

Checks diferenciales: código actual real; definiciones puras seleccionadas de upstream compiladas desde Git; builder asyncio simulado. No credenciales, procesos de proveedores, tmux o DB personal. Ejecutar desde raíz:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/audits/evidence/2026-10-02-fork-branches/differential_checks.py
```

Salida comprobada (`evidence/2026-10-02-fork-branches/differential_checks.txt`; artefacto histórico local). El script expone diferencias, no es un test de integración verde del candidato. Las definiciones de upstream se leen del SHA congelado `a1c3d138c38e58d1df04f9850b828e0b3df513e6`; no dependen de cómo avance upstream/main.

No se ejecutaron suites enteras de29 ramas ni despliegues Kubernetes/remote runtime. Cobertura: inventario exhaustivo de referencias/deltas del fork, revisión semántica por familias y candidatos relevantes, cinco comparaciones ejecutables acotadas. No se pretende certificar cada línea divergente ni recomendar todas las features ausentes.

Sólo se añadieron informe y evidencias. Cambios previos preservados; sin resolver conflictos, modificar credenciales ni incorporar código de otras ramas.

Los artefactos históricos locales mencionados en este informe se conservan fuera del conjunto publicado; no son evidencia de una ejecución nueva. Las comprobaciones actuales de cierre se registran en el spec 015.
