# Auditoría de autonomía y recuperación de agentes

Fecha: 2026-10-02. Estado auditado: working tree actual, incluida implementación de Spec007 sin commit. Alcance: todo el recorrido relacionado con coordinación, recibos tardíos, recuperación, herramientas, contexto y necesidad de intervención humana. Auditoría; no se han corregido los hallazgos.

## Dictamen

**CAO tiene infraestructura útil para autonomía, pero todavía no permite afirmar ejecución desatendida fiable.** Hay tres bugs P1 reproducidos: retry de una tarea ya entregada, salida sin recibo tras una restauración fallida y pérdida del presupuesto de reintentos al fallar persistencia. Además, el seguimiento semántico y la continuación automática tienen cobertura incompleta.

Que Claude, Codex y OpenCode arranquen y respondan no demuestra que el proyecto coordine tareas sin intervención. Un recibo acredita la respuesta del turno; no demuestra que la herramienta elegida sea correcta, que el informe describa archivos actuales o que el objetivo del usuario esté cumplido.

Resultado consolidado: **11 hallazgos de implementación/contrato** (3 P1, 8 P2), **1 riesgo de duplicación no reproducido con servidor real** y **4 grupos de capacidades pendientes**. P1 significa bloqueo para considerar fiable la autonomía; P2 significa degradación acotada o ruta específica. Las capacidades pendientes se clasifican aparte de los bugs: varias son decisiones deliberadas de alcance.

## Comparación con Dots: evidencia y límite

La documentación pública describe un coordinador persistente que delega, revisa resultados y hace seguimiento; distingue terminar una ejecución de alcanzar el resultado solicitado. Esa conducta es el punto de comparación de esta auditoría. No publica su implementación interna de recibos ni permite atribuirle un algoritmo concreto. [Dots](https://learn.chatgpt.com/docs/dots), [Tasks and memory](https://learn.chatgpt.com/docs/dots/tasks-and-memory).

Dots también admite errores y decisiones que necesitan al usuario. La meta razonable para CAO es reducir intervenciones rutinarias mediante observación y correcciones acotadas, conservando decisiones de autorización, login y acciones fuera del permiso existente. No prometer cero intervenciones ni modelos infalibles. [Controls](https://learn.chatgpt.com/docs/dots/controls).

## Cobertura del proyecto

| Dominio revisado | Mecanismo actual y conclusión |
| --- | --- |
| Proveedores Claude/Codex/OpenCode y reconstrucción | Recibos comunes y extracción específica; falla la publicación de cache antes de restauración y una extracción Codex tras reinicio. |
| Monitor, tmux, recuperación y persistencia | Verificación, CAS y cancelación por generación; falla el backoff con DB caída; reconcile detiene observación automática. |
| Assign, handoff, hijos, inbox y remoto | Creación, entrega y resultado diferenciados; inbox conserva incertidumbre; seguimiento remoto y asignación ambigua no tienen contrato uniforme. |
| Perfiles, catálogo y permisos de herramientas | Resolución y enforcement existen; prompt supervisor contradice su rol y un guard no respeta globs. |
| Workflows YAML y scripts | Journals, esquemas y replay; retry YAML y excepciones blocking tienen fallos de composición. |
| Work managed, reducer, scheduler, adapters | Credenciales, generaciones, artefactos y prueba de stop; base más estricta reutilizable. Continuación de workflow principalmente a demanda. |
| Contexto, memoria, snapshots, continuation y recovery bundles | Integridad y autoridad útiles; no equivalen a comprobar vigencia de un informe ni a reactivar procesos automáticamente. |
| API, CLI, MCP principal y web | Contrato007 de pending/reconcile/verified y acciones fenced presente. |
| Ops MCP, MCP Apps y TUI | Deriva de contrato y diagnóstico: estados, acciones y recuperación no tienen paridad. |
| AGUI, eventos, observabilidad y outcomes | Proyecciones/telemetría y feedback declarativo; no constituyen controlador semántico de progreso. |

Método: consulta de Graphify de los caminos receipt/reconcile/inbox/provider/workflow/supervisor/tools/memory, comprobación contra fuente actual y reproducciones aisladas. El grafo global contiene referencias antiguas; no se usaron sus líneas como prueba sin comprobar código. Se utilizó también el grafo acotado regenerado durante Spec007. Se revisaron las relaciones con specs001/006/007, no se cambió su estado de implementación.

## Bugs P1

### B01 — Timeout después de entrega permite otro trabajador

**Ruta:** workflow YAML legacy con retries disponibles; primer trabajador está PROCESSING y vence el timeout.

`_wait_for_completion` crea el error de timeout sin `delivery_may_have_occurred=True`, aunque corre después del envío. `run_agent_step` conserva el terminal y registra reconcile; el engine YAML sólo interrumpe retries si ese flag es True. Continúa con otra ejecución sin prueba de parada del primer trabajador.

Fuentes: `src/cli_agent_orchestrator/services/agent_step.py:224`, `:548`, `:1422-1439`; `src/cli_agent_orchestrator/services/workflow_service.py:1107-1115`; defaults de retries/timeout en `src/cli_agent_orchestrator/constants.py:913`, `:919`.

**Prueba:** `_wait_for_completion` real devuelve flag False; `start_run` real con SQLite temporal, contrato/pre-delivery recorder real y worker simulado produce `calls=2 state=completed attempts=2`. La reserva del contrato no impide el segundo envío. Otra prueba de `run_agent_step` real con transporte simulado produjo send=1/delete=0. No se lanzaron proveedores reales.

**Impacto:** dos escritores/efectos posibles sobre la misma tarea. **Corrección recomendada:** incertidumbre post-send debe cortar retry, preservar identidad y reconciliar. Timeout anterior al envío requiere tratamiento distinto. No inferir parada por tiempo transcurrido.

**Aceptación:** PROCESSING → timeout → cero nuevas entregas; primer terminal e intento inspectables; retry sólo después de prueba de stop y autorización apropiada. Test que una el timeout real y el engine, además del test existente con flag True sintético.

### B02 — Restauración fallida permite devolver LAST sin recibo

**Ruta:** primer lookup de proveedor tras reinicio falla leyendo/restaurando receipt; siguiente lookup reutiliza la instancia parcial.

`create_provider` publica la instancia en `_providers` antes de que `get_provider` restaure el estado durable. Si falla esa lectura, el siguiente lookup devuelve cache sin recibo. Cuando DB vuelve a estar disponible con recovery pending, LAST puede extraer texto histórico y `_verify_receipt_bearing_result` lo devuelve porque receipt_state es None.

Fuentes: `src/cli_agent_orchestrator/providers/manager.py:210`, `:234`, `:272-279`; `src/cli_agent_orchestrator/services/terminal_service.py:3474`, `:3788`; retorno de output en `src/cli_agent_orchestrator/api/main.py:4753`.

**Prueba:** ProviderManager/CodexProvider reales, metadata/DB/extractor/transporte simulados: una lectura DB fallida, segundo lookup sin receipt; LAST devuelve `Historical answer WITHOUT active receipt` mientras recovery sigue pending, con **0 settlements**. HTTP no se ejecutó end-to-end; su conversión a éxito se verificó por fuente. No se demuestra éxito durable del hijo ni repetición de input: el claim durable de nueva tarea puede seguir bloqueándola.

**Corrección recomendada:** publicar sólo instancia restaurada íntegramente, o mantener estado explícitamente indisponible y reintentar reconstrucción; eliminar cache parcial en error. Contrastar siempre recuperación durable activa y estado del proveedor.

**Aceptación:** fallo transitorio de restauración nunca habilita LAST como resultado verificado; dos lectores concurrentes no observan instancia parcial; recuperación posterior restaura la generación correcta.

### B03 — DB caída rompe límite y backoff del verificador

**Ruta:** falla captura/verificación y también falla persistir su diagnóstico.

`record_terminal_turn_verification_failure` corre antes de incrementar intentos/backoff local. Si lanza, ambos quedan a cero; finally programa otro Timer al mínimo de 10ms. El deadline depende de la misma escritura sin protección, por lo que esa indisponibilidad también puede invalidar el cierre de presupuesto.

Fuentes: `src/cli_agent_orchestrator/services/terminal_service.py:410`, `:481`, `:607-619`.

**Prueba:** Thread/Timer capturados sin ejecutarlos en background; cinco rondas manuales con DB simulada caída mantienen `attempts=0 reconciled=False retry_delay=0.01`. Es reproducción del mecanismo que permite reintentos sin límite, no una medición de carga de producción.

**Impacto:** tormenta potencial de hilos/capturas durante un fallo de almacenamiento. **Corrección recomendada:** backoff/deadline locales conservadores independientes del almacenamiento del diagnóstico; persistencia reintentada con presupuesto separado. Nunca liberar el turno ni enviar la tarea por este error.

**Aceptación:** fallo DB durante captura y deadline conserva fence, limita frecuencia y trabajo total; no se produce éxito artificial ni reenvío.

## Bugs y diferencias de contrato P2

| ID | Disparador y evidencia | Corrección y aceptación recomendadas |
| --- | --- | --- |
| B04 | Codex responde y muestra el picker específico “Approaching rate limits”; tras reinicio el digest sigue válido, pero extractor/witness exigen nonce plaintext, eliminado deliberadamente al restaurar. Repro antes: resultado + WAITING_USER_ANSWER; después: None/None. `providers/base.py:438`; `providers/codex.py:1942`, `:1998-2000`. | Verificar candidatos con digest restaurado bajo la misma gramática estricta. Recuperar respuesta auténtica sin responder picker ni reconstruir tarea. |
| B05 | Excepción del extractor en workflow blocking/directo: el engine no converge errores fuera de WorkflowEngineError; finally elimina driver. Repro `ValueError; run=running step=running active=False`. `services/agent_step.py:1454-1474`; `services/workflow_service.py:1310`, `:1503`; `api/main.py:6716-6727`. | Propiedad común del cierre/reconcile de errores de ejecución, preservando incertidumbre. Nunca dejar running sin owner/driver. La ruta async tiene backstop (`api/main.py:6388-6466`); no se le atribuye el mismo fallo. |
| B06 | Ops MCP interpreta pending202 como JSON de output: read_session_output devuelve “invalid response payload”;409 reduce diagnóstico a texto JSON. Repro con funciones del AST y HTTP simulado. `ops_mcp_server/server.py:57`, `:132-136`, `:670`, `:823`. | Compartir envelope de resultado; preservar state/generation/reason/actions para202/409. No llamar avería a pending; no se afirma falso success=True en get_terminal_output. |
| B07 | MCP Apps pierde `terminal.turn`; pending queda output vacío,409/500 se suprimen en vista. No ofrece verify/cancel. `mcp_server/app_tools.py:217`; `services/ui_state_service.py:127`; `cao_mcp_apps/src/shared/types.ts:60`, `:75`; `cao_mcp_apps/src/agent/AgentView.tsx:109`. | Proyección segura del turno y acciones fenced con scopes existentes. No mostrar nonce/hash. Paridad de contrato007 incompleta; alcance de la implementación original priorizó web/CLI/MCP principal. |
| B08 | MCP Apps muestra Pause/Resume por scope, pero backend siempre responde unsupported. `cao_mcp_apps/src/shared/TaskControl.tsx:23`; `mcp_server/app_tools.py:459`. | Renderizar capacidades efectivas; deshabilitar/ocultar lo no implementado. Interrupt envía C-c y no sustituye cancelación durable007. |
| B09 | TUI convierte waiting_quota/reconcile en Unknown y no conserva turn. `tui/src/types.rs:154`, `:248-280`; `tui/src/handoff.rs:283`, `:381`. | Modelar estados vigentes y diagnóstico, conservar fallback para estados futuros. Se demuestra pérdida de información/espera, no reenvío inseguro. |
| B10 | Perfil code_supervisor exige escribir task/feedback files, pero rol concede sólo CAO + lectura/listado. `agent_store/code_supervisor.md:32`, `:43`, `:54`; `constants.py:786`; `utils/tool_mapping.py:32`, `:36`. | Task inline o persistencia delegada explícita/herramienta acotada. Test prompt-capacidades: ninguna obligación previa exige herramienta bloqueada. No conceder Bash/Write automáticamente. |
| B11 | Override `--allowed-tools '@cao-*'`: matcher/config reconoce glob, guard CAO sólo igualdad exacta. `cli/commands/launch.py:327`; `utils/tool_mapping.py:234`; `services/install_service.py:401`; `mcp_server/server.py:1543`. | Reusar matcher sobre catálogo concreto. Test glob permitido → guard permitido; patrón ajeno → denegado. Es denegación indebida, no escalada de privilegios. |

En esa tabla, rutas sin prefijo completo son relativas a `src/cli_agent_orchestrator/`. Todas las localizaciones se verificaron contra la fuente actual.

## Riesgo pendiente de reproducción integrada

**R01 — Assign ambiguo puede duplicar trabajo.** El prompt recomienda retries, pero `_assign_impl` no posee identidad durable de la asignación completa. POST aceptado con respuesta perdida deja al coordinador sin certeza; otro assign puede crear otro trabajador. Fuente: `agent_store/code_supervisor.md:25`; `utils/orchestration.py:1462`, `:1532`, `:1620`, `:1643`. La propia documentación de handoff distingue deduplicar creación de deduplicar entrega (`:1096`).

Se comprobó el control-flow, **no se reprodujo pérdida real de respuesta con servidor vivo** ni se demostró retry automático del coordinador. No contar como cuarto bug P1 probado. Recomendación: aceptación desconocida como estado explícito e identidad estable que cubra creación y entrega. Test de servidor aceptando POST antes de perder respuesta; reconciliar el intento existente sin otro worker.

## Capacidades pendientes para reducir intervención rutinaria

### G01 — Reobservación de recibos tardíos

Reconcile detiene el scheduler y no se reabre por un nuevo COMPLETED visual; reconstrucción sólo reanuda verifying. Tres intentos (~0/2/6s) o deadline55s pueden terminar antes de que llegue evidencia válida. Repro de estado sellado: scheduler devuelve True y crea cero workers. La verificación explícita sí vuelve a observar.

Fuentes: `services/terminal_service.py:537-539`, `:612`; `providers/manager.py:313`; `services/status_monitor.py:860`; `services/turn_recovery_service.py:57`, `:73`.

Es política acotada de007, **no regresión respecto a su especificación**. Para autonomía, separar incertidumbre de abandono de observación: eventos de evidencia + barrido durable de baja frecuencia, deduplicados por terminal/generación, presupuesto por ventana y settlement CAS. Receipt tardío auténtico debe resolver el mismo intento sin repaste. Timeout nunca significa éxito ni autoriza cancelación.

### G02 — Continuación durable del workflow

Work managed puede quedar WORK_PENDING y finalizar el drive. Submit_result autentica y guarda resultado; en los caminos inspeccionados no se encontró hook durable que proyecte el resultado y continúe automáticamente los siguientes pasos. Projector se ejecuta en startup y operaciones explícitas. Fuentes: `services/workflow_service.py:941`, `:1303`, `:1334`; `services/work_service.py:954-1027`; `services/work_origin.py:761`; `services/workflow_step_projector.py:195`; rutas explícitas `api/main.py:1462`, `:4985`, `:5355`, `:8721`.

Reusar projector, journal y exclusión de drivers con orden durable de continuación; no crear un segundo engine ni tratar completed visual como resultado Work. Aceptación: resultado aceptado genera una sola continuación incluso tras reinicio/duplicación de evento; revocación y generación obsoleta no ejecutan efectos.

### G03 — Corrección de herramientas, contexto y objetivo

Hoy el coordinador semántico es el LLM y su perfil. Las funciones loop/stall/no-progress del engine están expresamente reservadas y lanzan NotBuiltYet (`services/workflow_service.py:2293-2305`). AGUI proyecta estados/eventos; no decide progreso ni correcciones (`services/agui/supervisor_dashboard.py:168-221`, `lifecycle_tracker.py`, `cross_provider_sync.py`). Outcome almacena feedback declarado por el caller, no valida independientemente artefactos (`services/outcome_service.py:93-155`).

Falta un contrato de tarea/checkpoint con criterio de terminado y un controlador opt-in que:

1. Observe el intento vigente y distinga entrega, ejecución, respuesta verificada y objetivo cumplido.
2. Detecte ausencia de avance mediante evidencia, consulte herramientas realmente disponibles y prepare una corrección concreta y limitada.
3. Envíe la corrección sólo cuando el turno admita input, mediante inbox existente; no interrumpa/reenvíe una tarea incierta.
4. Compruebe archivos actuales, revisión/hash y pruebas necesarias antes de aceptar un informe antiguo como verdad actual.
5. Escale con causa, evidencia y acción pendiente después de un presupuesto definido.

Snapshot autorizado/frozen (`services/delegation_snapshot.py:95`, `:155`, `:198`) asegura integridad y alcance; no asegura vigencia del working tree. Mantener snapshot/identidad de la ejecución anterior y producir contexto actualizado para el follow-up. No mutar retrospectivamente la evidencia ni enseñar al modelo a ignorar permisos.

Esto puede reducir búsquedas de herramientas equivocadas y lecturas de informes antiguos; no garantiza eliminar todas las decisiones erróneas del modelo. Requiere tests de corrección → progreso y corrección inútil repetida → escalado acotado.

### G04 — Operación remota y recuperación de infraestructura

Assign remoto transmite callback pero no caller_id/recibo local; list/join consultan nodo local. Callback funciona como vía existente, pero no hay seguimiento durable uniforme de todos los remotos. Fuentes: `utils/orchestration.py:1462`, `:2053`, `:2074`; `services/terminal_service.py:1529`.

Scheduler no libera capacidad por expiración porque lease vencido no prueba stop (`services/work_scheduler.py:324-327`, `:441`, `:510`). Recovery bundles restauran SQLite en blocked_restore y no adoptan procesos/inbox (`services/recovery_bundle.py:1252-1295`). Continuation import es preflight descriptivo, no nueva ejecución (`services/work_continuation.py:178`, `:377`). Son protecciones que conservar; falta seguimiento operativo explícito para holds estancados, nodos caídos y reactivación autorizada.

## Primitivas a conservar y reutilizar

- Recibos comunes en Claude/Codex/OpenCode; Base declara que son evidencia cooperativa, no autenticación (`providers/base.py:37`, `:362`, `:417`).
- Settlement CAS de recibo/resultado/hijo antes de publicar COMPLETED (`services/terminal_service.py:3504`, `:3519`).
- Verify vuelve a observar; cancel fija bloqueo durable por generación antes de reset de pane, con shell explícito para evitar repetir comando original (`services/turn_recovery_service.py:73`, `:134-152`; `clients/tmux.py:1725`).
- Inbox incierto después del paste queda RECONCILE y no se redelivera; retries de pendientes están separados (`services/inbox_service.py:180`, `:233`).
- Work managed exige identidad/generación/credenciales y prueba de stop para retry (`services/work_service.py:456-537`, `:903`, `:954`; `services/work_reducer.py:82-87`).
- Artefactos content-addressed validan ubicación, tamaño y digest (`services/step_output_store.py:186-193`, `:281-304`). Replay/script gates y permisos ya tienen infraestructura; no reinventarla.
- API/CLI/MCP principal/web conservan pending202, reconcile409 y diagnóstico accionable por generación (`api/main.py:4698-4755`; `utils/orchestration.py:1891`, `:1955-2002`; `web/src/components/TurnRecoveryPanel.tsx:54`, `:76`).

Spec007 excluye garantizar elección correcta de herramientas/destinatarios (`specs/007-fix-agent-turns/spec.md:126`). Ampliar esa capacidad requiere diseño de producto; corregir B01-B11 exige primero regresiones puntuales. Work001 no toma salida legacy como prueba canónica (`specs/001-verifiable-orchestration/spec.md:286`, `:299`).

## Orden técnico recomendado

1. Corregir B01/B02/B03 y añadir tests de sus composiciones; después B04/B05. Evitar duplicación y falsos resultados antes de automatizar más.
2. Corregir contratos de consumidores y prompt/permiso: B06-B11. Un único diagnóstico consistente para pending/reconcile/error y acciones reales.
3. Diseñar continuidad G01/G02 sobre mecanismos durables actuales, con presupuestos separados para observación y ejecución; resolver R01 antes de retries autónomos de assign.
4. Diseñar controlador G03 y cobertura G04 con checkpoints, validación actual y escalado explícito.

Es una secuencia propuesta, **no un plan de implementación ejecutado**. Para siguientes cambios significativos, usar Spec Kit con requisitos/aceptación y tasks; contrastar Graphify contra fuente, revisar composición y comprobar contratos/integraciones. No reabrir ni declarar incompleta toda007 por capacidades que excluía.

## Evidencia ejecutable y verificaciones

Reproducciones preservadas, ejecutables desde la raíz, sin proveedor/tmux/DB personal. Usan mocks y SQLite temporal; los marcadores de recibo son sintéticos y la salida se redacta:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/audits/evidence/2026-10-02-autonomous-orchestration/turns_repro.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/audits/evidence/2026-10-02-autonomous-orchestration/workflows_repro.py
```

Resultados: turns_repro.txt (`evidence/2026-10-02-autonomous-orchestration/turns_repro.txt`; artefacto histórico local), workflows_repro.txt (`evidence/2026-10-02-autonomous-orchestration/workflows_repro.txt`; artefacto histórico local). Las reproducciones exponen el comportamiento actual; no son tests verdes de una corrección.

| Verificación ejecutada | Resultado y alcance |
| --- | --- |
| AGUI dashboard/sync/lifecycle, delegation snapshot, outcome, learned patterns, step output store, event bus | **136 passed**,28.23s; log (`evidence/2026-10-02-autonomous-orchestration/baseline-tests.txt`; artefacto histórico local). |
| Workflow service, agent step, reducer, scheduler, projector, continuation, deferred submit | **272 passed**,3 warnings,47.17s; log (`evidence/2026-10-02-autonomous-orchestration/workflow-tests.txt`; artefacto histórico local). |
| Turn recovery, receipt delivery, provider manager | **58 passed**,34.59s. |
| API/MCP turn recovery, turn consumers, ops MCP | **81 passed**,5 warnings,21.24s, repetición sin coverage; log (`evidence/2026-10-02-autonomous-orchestration/consumer-tests.txt`; artefacto histórico local). |
| Tool mapping, MCP resolution, turn consumers | **81 passed**,12.03s, sin coverage. Hay solapamiento con el conjunto anterior; no sumar como casos únicos. |
| Web API/turn-recovery/turn-output | **54 passed**,3 archivos. |
| project-composition-check | **PASS**,5 contratos conservados,0 rotos; log (`evidence/2026-10-02-autonomous-orchestration/composition-check.txt`; artefacto histórico local). |

Los casos existentes verdes no cubren las composiciones defectuosas descritas. El gate comprueba dependencias/imports; no certifica autonomía semántica. No se repitió una validación real multiproveedor en esta auditoría ni se acredita operación desatendida con mocks. Las pruebas reales anteriores de007 siguen documentadas en `specs/007-fix-agent-turns/real-validation.md` y no se sustituyen por esta auditoría.

Una ejecución inicial de81 casos con coverage terminó en error interno SQLite de coverage (`no such table: file`), no en fallo de producto; se repitió con coverage desactivado y exit0. La primera suite de58 usó coverage por defecto. Las restantes verificaciones Python utilizaron no-cov/no-cache y bytecode desactivado para evitar colisión del almacenamiento de coverage compartido.

No se modificó código de producto, tests de producto, perfiles ni estado de la instalación. Se preservaron cambios ajenos y de007. Se añadieron este informe y evidencias documentales; sin commit, push, despliegue ni cambios de credenciales. El singleflight multi-proceso y posibles timers residuales tras teardown no se validaron: preguntas abiertas, no bugs afirmados.

Los artefactos históricos locales mencionados en este informe se conservan fuera del conjunto publicado; no son evidencia de una ejecución nueva. Las comprobaciones actuales de cierre se registran en el spec 015.
