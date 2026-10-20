# Impacto antes de implementación

Fecha: 2026-10-10. Autorización: implementar spec 016 con subagentes GPT-6 Luna, esfuerzo max, y evaluación continua.

## Puerta de entrada

- Spec Kit: plan, modelo, contratos, quickstart y tareas disponibles; `check-prerequisites.sh --json --require-tasks --include-tasks` pasa. Checklist de requisitos: 16/16; es calidad de requisitos, no evidencia de implementación.
- Graphify: copia aislada del grafo existente en `/tmp/caos-spec016-baseline/graph.json`; `graphify explain src_cli_agent_orchestrator_services_terminal_service_send_input --graph /tmp/caos-spec016-baseline/graph.json` pasa y devuelve 35 relaciones. Una consulta inicial sin ID fue ambigua; se corrigió con el ID completo.
- Extracción actual independiente: la primera ejecución sobre el montaje Windows extrajo AST de 410 archivos, pero quedó esperando filesystem durante el ensamblado; se terminó únicamente ese proceso propio. Una copia de fuentes en `/tmp/caos-spec016-graph-snapshot` permitió completar `graphify extract src/cli_agent_orchestrator --code-only --no-cluster --out /tmp/caos-spec016-graph-snapshot --max-workers 2`: exit 0, 416 unidades de código, 11 868 nodos y 29 080 aristas. Es una instantánea incremental, que se actualizará tras estabilizar la implementación. No se modifica ni interrumpe el trabajo Graphify preexistente.
- El grafo previo puede tener líneas antiguas. Se verificaron las relaciones importantes contra el código actual antes de autorizar cambios.

## Relaciones verificadas y límites

`terminal_service.send_input` recibe entregas de inbox/flujo y protege terminales Work mediante `ensure_terminal_is_not_work_owned`/`WorkBackendView`. Su bloqueo de preguntas depende actualmente del opt-in del proveedor: debe proteger también preguntas requeridas del registro, sin crear un bypass de entrada ordinaria.

`agent_step` usa un presupuesto monotónico; una pregunta requerida necesita presupuesto activo separado del vencimiento humano. La precedencia de resultados ya verificados y los recibos de entrega impiden repetir la tarea inicial.

La creación de terminal antecede a inicialización del proveedor. La ruta de capacidad privada debe establecerse antes de escribir configuración MCP. El helper global de entorno no debe compartir ni permitir sobrescribir credenciales por perfiles.

MCP usa HTTP, no base de datos/tmux. Principal sellado del humano y capacidad por fuente tienen autoridades distintas. Scope global de lectura no concede contenido; propietario autenticado, relación de supervisor y revocación se validan por recurso.

WorkRepository mantiene esquema 39 y checksum de objetos `work_*`; el esquema `interaction_*` se versiona independientemente y comparte transacción al comprobar autoridad Work. WorkDecisions sigue siendo la autoridad de aprobaciones Work. Work V2 process y ejecución remota sin canal autorizado no adquieren herramientas, red ni permisos nuevos.

La interfaz usa fetchJSON, sesión de autenticación y epoch de nodo; se añade Questions al final de las pestañas y se invalida contenido al cambiar nodo. Eventos contienen solo metadatos, mientras el detalle necesita autorización.

## Decisión de pruebas

TDD para comportamiento nuevo: pruebas antes de implementación de validación tipada, capacidad/ACL, concurrencia e idempotencia, reanudación/expiración, contratos HTTP/MCP y formularios. Evaluación tras cada entrega y después composición real con SQLite, regresiones de aislamiento, build web y puerta arquitectónica. Mocks de proveedores no acreditan soporte nativo real; las capacidades nativas no demostradas quedan manuales con limitación explícita.

Referencia inicial: 175 pruebas Python, 470 pruebas web y project-composition-check pasan. Evidencia detallada en evaluation.md.

## Actualización final de estructura

La extracción sobre la copia Linux del árbol estabilizado completó con exit 0: **417 unidades, 12 034 nodos y 29 894 aristas**, sin clustering ni extracción LLM. Comando ejecutado desde `/tmp/caos-spec016-graph-snapshot`:

    graphify extract src/cli_agent_orchestrator --code-only --no-cluster --out /tmp/caos-spec016-graph-snapshot --max-workers 2

Se verificaron hashes idénticos entre copia y fuente actual de `services/interactions/service.py`, `clients/interaction_repository.py` y `services/terminal_service.py`. `graphify explain` del ID `services_interactions_service_interactionservice_wait_for_answers` muestra 17 relaciones; las llamadas a transacción, fuente vigente, caducidad, recibo y satisfacción de grupo coinciden con el código. El ID `services_terminal_service_install_interaction_source_capability` muestra 7 relaciones; se verifican publicación privada antes de inicialización y fallo de instalación que deshabilita exclusivamente la capacidad explícita. Las relaciones marcadas INFERRED se usan como navegación, nunca como evidencia sin lectura de fuente.

Una invocación final inicial usó por error el cwd del montaje Windows; se terminó sólo su proceso propio antes de repetir desde la copia Linux. El grafo compartido y los procesos preexistentes se preservan.

## Actualización de la ronda de pruebas completas

Tras el registro observacional y binding de assignment, `graphify update /tmp/caos-spec016-graph-snapshot --no-cluster` termina con exit 0: **449 archivos extraídos, 12411 nodos y 33156 aristas**. Log `/tmp/caos-spec016-final-graphify.log`. No se usa LLM ni se modifica el grafo compartido. El recuento incluye todos los archivos admitidos por update; no se presenta como número de módulos Python.

`graphify explain InteractionService --graph /tmp/caos-spec016-graph-snapshot/graphify-out/graph.json` termina con exit 0. Se contrastan con source las llamadas de create_group, wait_for_answers, acknowledge_answers y _current_source_by_capability, además de binding observacional y fences de terminal/monitor. Los cinco archivos críticos (servicio, repositorio, terminal, monitor y router) coinciden por SHA-256 con el workspace. La navegación generada no acredita ejecución ni aceptación de una CLI externa.

## Actualización tras las correcciones nativas

`graphify update /tmp/caos-spec016-graph-snapshot --no-cluster` vuelve a terminar con exit 0: **12426 nodos / 33224 aristas**, manifest con **450 entradas** de los formatos admitidos. Log `/tmp/caos-spec016-verified-graphify.log`. Se verifican hashes idénticos de servicio, repositorio, terminal, monitor, ejecutor y router. El grafo compartido se conserva.

`graphify explain wait_for_native_manual --graph /tmp/caos-spec016-graph-snapshot/graphify-out/graph.json` pasa: **13 relaciones** en el ID exacto `src_cli_agent_orchestrator_services_interactions_service_interactionservice_wait_for_native_manual`. Las relaciones de transacción, validación de fuente, caducidad y cierre nativo se contrastan con la nueva selección por question/observation y con el pin del ejecutor entre polls. El gate de dispatch consulta el ledger antes de avanzar turno o enviar bytes; la recuperación del monitor valida el digest del target y sólo cierra físicamente tras dos capturas cambiadas estables. La comprobación arquitectónica posterior pasa con **408 archivos / 1713 dependencias / 5 contratos conservados / 0 rotos**.

## Puerta previa de la continuación AG-UI

`graphify explain src_cli_agent_orchestrator_api_main_agui_stream` sobre la extracción aislada final pasa: 10 relaciones; `_stream_authorization`, validate, enablement, cursores y ToolCallLifecycleTracker contrastados con source. El stream actual sólo refresca snapshots tras eventos de flota y emite heartbeat en idle; no tiene lector de recibos de interacciones. `InteractionService.list_metadata` valida Principal y obtiene estado desde SQLite; el adaptador deberá conservar esa frontera o exponer un lector metadata-only explícito sin fabricar Principals a partir de scopes. UI state/fleet bus y run plane son consumidores inmediatos; ningún mapper podrá inferir returned/ack desde accepted o estado de terminal. Los scripts Docker/guest se inspeccionan directamente: el graph de Python no sustituye sus guards de kernel, imagen y checkout.

## Impacto de cursor antes de corrección

Graphify aislado actual identifica cursor_encode (capability.py L54), import por service, calls de list_metadata y agent_list (estructura anterior a extracción helper). Source actual contrastada: list_metadata y projection_snapshot delegan a _metadata_page_on_connection, cuyo cursor convierte _metadata.created_at datetime a timestamp; agent_list repite conversión. cursor_encode usa :.6f y SQLite guarda REAL time.time. Son dos fronteras de precisión; preservar fila original + round-trip exacto. Scope: capability encode, shared metadata page, agent list, tests deterministas/compatibilidad; decoder y permisos conservados. Grafo requiere refresh final por helper/proyección nuevos.
