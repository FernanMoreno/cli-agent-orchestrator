# OpenCode v1 y v2: análisis y pendientes

Fecha de revisión: 2026-10-20. Esta lista registra trabajo por hacer; no activa herramientas ni cambia permisos. Las casillas abiertas son propuestas o validaciones pendientes, no necesariamente defectos reproducidos.

## Estado y límites de la evidencia

- El adaptador detecta las versiones principales 1 y 2. V2 usa `mini --standalone` y configuración por terminal; v1 conserva su ruta nativa.
- V2 tiene correcciones integradas para nombres MCP canónicos, permisos parciales, Code Mode, perfiles nativos y aislamiento de credenciales. El recorrido reciente de preguntas completó respuestas, ACK y un reinicio de API con limpieza y credenciales del operador intactas. La suite enfocada más reciente pasó 351 pruebas; no equivale a aceptación global.
- Existen aceptaciones históricas de mensajes nativos con AG-UI/reinicio y ejecuciones posteriores fallidas. Una aceptación anterior no valida automáticamente la fuente actual ni el alias externo `cao.work.message`.
- V1 conserva cobertura offline, pero no se ha acreditado aquí una ejecución real completa reciente con un binario v1 fijado.
- El timeout histórico de inicialización de 120 s no se ha reproducido en la ejecución corregida. Su causa histórica exacta y la reconciliación del registro externo siguen sin demostrarse.

## Herramientas por perfil

Las tres herramientas del recorrido restringido son `cao_ask_user`, `cao_wait_for_user_answers` y `cao_acknowledge_user_answers`. Ese proceso MCP tiene un filtro real de catálogo y llamadas; ampliar solo los permisos del cliente no lo amplía.

| Perfil propuesto | Herramientas adicionales | Condición |
| --- | --- | --- |
| Colaboración básica | `send_message`, `list_siblings` | Mensajes y descubrimiento dentro de la autoridad del agente. |
| Recuperación de interacciones | `cao_list_interactions`, `cao_get_interaction`, `cao_escalate_interaction` | Consultar el ámbito autorizado y escalar decisiones humanas. |
| Coordinación local | `list_local_cao_peers`, `assign_local_cao_task`, `get_local_cao_task`, `cancel_local_cao_task` | Solo coordinadores y tareas propias/autorizadas. |
| Supervisión de turnos | `inspect_terminal_turn`, `verify_terminal_turn`, `cancel_terminal_turn` | Cancelación con generación e identidad verificadas. |
| Conocimiento | `load_skill`, `memory_recall`; opcionalmente `memory_store`, `report_outcome` | Escritura solo cuando el perfil lo requiera. |
| Trabajador elástico | `complete_assignment` | Exclusiva del trabajador elástico configurado; no es un cierre genérico. |

`cao_answer_interaction` corresponde a una aclaración delegada y autorizada; no debe permitir al emisor contestar su propia pregunta humana. Las herramientas nativas de archivos, shell, búsqueda y tests necesitan sus propios permisos y no forman parte de estas tres herramientas MCP.

## Pendientes comunes, en orden

- [ ] OC-01 Diseñar perfiles explícitos de interacción, colaboración y coordinación, con catálogo, llamadas y permisos coherentes. Mantener disponible el recorrido restringido de tres herramientas.
- [ ] OC-02 Añadir primero las capacidades de colaboración básica y consulta de interacciones. Verificar permisos tanto en el cliente como en el servidor; no sustituirlos por instrucciones de texto.
- [ ] OC-03 Añadir delegación, supervisión y conocimiento solo a los perfiles correspondientes; distinguir asignación local de trabajadores elásticos.
- [ ] OC-04 Probar herramientas permitidas, denegadas, nombres desconocidos, permisos vacíos/parciales, colisiones y aislamiento entre terminales; preservar respuestas humanas y autoridad delegada.
- [ ] OC-05 Validar mensajes genéricos con PostgreSQL, proyección AG-UI y reinicio de API en la fuente final. Resolver y probar por separado el contrato externo `cao.work.message`; no asumir que es alias del MCP `send_message`.
- [ ] OC-06 Verificar concurrencia con múltiples terminales: límites de tareas, esperas y cancelación acotadas, orden de mensajes, ausencia de cruces de credenciales y limpieza propia. Medir latencia del control separada del tiempo del modelo.
- [ ] OC-07 Consolidar evidencia y actualizar las tareas de Spec 022/023; conservar fallos anteriores. La aceptación requiere código integrado en main y recibos vinculados a la fuente ejecutada.

## Pendientes específicos de v1

- [ ] OC1-01 Preparar un binario v1 con versión y procedencia fijadas, aislado del v2 instalado, sin reinstalación global.
- [ ] OC1-02 Validar instalación del perfil antes de crear terminal; comprobar agente y modelo efectivos, evitando fallback silencioso.
- [ ] OC1-03 Ejecutar prompt Unicode, respuesta con recibo, error de proveedor, timeout, cancelación y limpieza en v1 real.
- [ ] OC1-04 Verificar credenciales y configuración aisladas también en v1; el aislamiento de `OPENCODE_DB` de v2 no demuestra esta propiedad para v1.
- [ ] OC1-05 Validar los perfiles MCP ampliados con la sintaxis de permisos de v1 y el recorrido preguntas/respuestas/ACK, mensajes y reinicio de API.

## Pendientes específicos de v2

- [ ] OC2-01 Actualizar o retirar los lanzadores externos con hashes/rutas antiguos. Fijar sus entradas a la fuente final antes de ejecutarlos; el recorrido directo exitoso no valida esos lanzadores.
- [ ] OC2-02 Validar cada perfil ampliado con nombres MCP canónicos y resolución efectiva de permisos; conservar `codemode=false` en el flujo directo y no conceder `execute` para resolver herramientas ausentes.
- [ ] OC2-03 Extender la validación previa para comprobar las herramientas requeridas por cada perfil, manteniendo la comprobación exacta del flujo de tres herramientas.
- [ ] OC2-04 Repetir la aceptación final vinculada a fuente/runner actuales cuando se implementen los nuevos perfiles; comprobar catálogo ofertado al modelo y llamadas reales.
- [ ] OC2-05 Acreditar aislamiento con las modalidades de autenticación que se declaren soportadas, incluida renovación OAuth si corresponde. El éxito con API key de OpenCode Go no acredita OAuth ni todos los proveedores/modelos.
- [ ] OC2-06 Registrar por separado el timeout histórico externo y su reconciliación. No cerrarlo administrativamente basándose solo en una ejecución nueva exitosa.

## Limitación actual de autorización y catálogo pendiente

El helper `_tool_denied_reason` autoriza las operaciones que lo utilizan mediante el selector de servidor `@cao-mcp-server` o `*`; los nombres sueltos de herramientas en `allowed_tools` no son una allowlist MCP por operación. Las rutas de interacción tienen además su autoridad propia. El filtro histórico de tres herramientas solo se instala en el proceso hijo marcado; no es global. La base opt-in de perfiles integrada el 2026-10-20 añade otro filtro por hijo mediante el overlay, según el avance registrado al final; no convierte los nombres de allowed_tools en permisos MCP por operación.

- [ ] OC-AUTH Implementar o extender una política del servidor por perfil/herramienta para los nuevos perfiles, antes de dar por efectiva la separación propuesta. Verificar tanto catálogo como invocación directa y conservar las comprobaciones de autoridad de cada operación.
- [ ] OC-LOCAL Documentar y probar que la coordinación local pasa hoy por `_tool_denied_reason("assign", local_only=True)` y requiere identidad de terminal y contexto del proyecto; habilitar solo el nombre en el cliente no basta.
- [ ] OC-CATALOG Auditar también `handoff`, `assign`, `assign_elastic`, `list_native_children`, `join_native_child`, `get_handoff_result`, `delete_terminal`, `answer_user_prompt` y `emit_ui`, más las familias de workflows, trabajo, Beads, Ralph y escritura/borrado de memoria. Decidir explícitamente por perfil qué se expone y qué se deniega; la tabla anterior es una propuesta inicial, no un inventario exhaustivo ni una exclusión ya implementada.

## Fuentes

- [Adaptador OpenCode](../src/cli_agent_orchestrator/providers/opencode_cli.py), [traductor v2](../src/cli_agent_orchestrator/utils/opencode_v2.py) y [filtro MCP](../src/cli_agent_orchestrator/mcp_server/interaction_pull_flow_only.py).
- [Servidor MCP y contratos de herramientas](../src/cli_agent_orchestrator/mcp_server/server.py).
- [Recorrido nativo](../test/live_provider/opencode_interaction_journey.py), [tareas Spec 022](../specs/022-provider-agui-restart/tasks.md) y [documentación OpenCode](opencode-cli.md).
- El relevo y los recibos privados conservan los resultados históricos. No copiar credenciales ni transcripciones a estos documentos.

## Avance integrado 2026-10-20: base de perfiles y diagnóstico

Aplicación de Spec Kit a todos los pendientes: [Spec 025](../specs/025-provider-mcp-profiles/spec.md), [plan](../specs/025-provider-mcp-profiles/plan.md) y [tareas con correspondencias](../specs/025-provider-mcp-profiles/tasks.md). Los gates reales de Spec 022/023/024 conservan sus resultados y siguen separados.

- [x] OC-AUTH-01 Base opt-in del servidor: `interaction` limita a las tres operaciones existentes; `collaboration` añade únicamente `send_message`, `list_siblings`, `cao_list_interactions`, `cao_get_interaction` y `cao_escalate_interaction`. Filtrado exacto de catálogo y llamadas directas, incluidos desconocidos; recursos y prompts denegados. [Inventario actual](../specs/025-provider-mcp-profiles/catalog-inventory.md): 79 herramientas registradas, sin activar MCP Apps opcional. La autoridad interna de cada operación se conserva.
- [x] OC-AUTH-02 El overlay consume `cao_tool_profile` en la declaración MCP canónica, protege la variable interna y rechaza selecciones desconocidas o comandos no reconocidos antes del lanzamiento. Codex protege también esa política frente a overrides MCP. Los filtros vacíos/parciales del cliente se conservan; no se conceden permisos nativos adicionales.
- [ ] OC-AUTH-03 Completar propagación/permisos nativos y preflight por perfil antes de aceptación real ampliada. El marcador histórico de OpenCode sigue intersectando a tres herramientas, incluso con `collaboration`; v1 no queda validado por el overlay privado de v2. Los pendientes AUTH/01–04 de alcance mayor no se cierran por esta base.

La configuración opt-in es `"cao_tool_profile": "interaction"` o `"collaboration"` dentro de una entrada cuyo comando sea el MCP CAO canónico. No se ha aplicado a las configuraciones del operador ni ejecutado proveedores para esta fase. La lista sigue describiendo propuestas donde no se indica expresamente integración.

La espera de completado ahora adjunta observaciones cerradas al 504 (`wait_diagnostics`): último estado, trabajo observado, errores/candidatos de recibo y extracción intentada. La matriz real conserva únicamente esa proyección sanitizada al fallar, en vez de perderla tras el assert. No cambia el presupuesto de 180 s, los criterios de completado, cuota, entrega ni reintentos. Esto mejora CL-01; no demuestra autenticación, latencia ni causa histórica del timeout.

Verificación de esta fase: 127 pruebas offline de contratos/configuración/diagnóstico y límites del harness pasaron; 237 pruebas adicionales seleccionadas de proveedor/configuración pasaron (36 de inicialización/diálogos fuera de esa selección). Sin aceptación real nueva. El estado de revisión independiente y composición se registra en [validación](../specs/025-provider-mcp-profiles/validation.md).

Configuración de la base: un comando de texto con perfil utiliza `env`; si también declara un mapa `environment`, el lanzamiento se rechaza para evitar perder la política en Codex/Claude. El formato de comando en lista de OpenCode utiliza `environment`. La propagación desde el instalador nativo continúa en T007.

## Continuación T007 — 2026-10-20

- Integrado: el preflight de OpenCode v2 comprueba todas las herramientas del perfil efectivo a partir de `environment` generado. Un perfil `collaboration` requiere sus ocho herramientas; con el marcador histórico la intersección sigue siendo exactamente tres. Configuración inválida o política en el campo `env` ignorado se rechaza. Se conservan Code Mode desactivado, colisiones y resolución de la última regla.
- Integrado: Codex emite `enabled_tools` como intersección del perfil y el filtro explícito del cliente. Un filtro vacío sigue vacío; un parcial no se amplía; sin filtro se anuncian solo las herramientas del perfil. El comportamiento sin perfil conserva su configuración previa.
- Corregido: el instalador nativo de OpenCode descartaba `cao_tool_profile` y podía informar éxito. Hasta completar la propagación por agente, rechaza ese metadato y los marcadores internos en `env`/`environment` antes de cambiar artefactos nativos, contexto o proveedor registrado. No se almacena política por agente en el servidor compartido ni en un sidecar de concesiones best-effort.
- Corregido: el CLI devolvía código 0 cuando el servicio rechazaba la instalación. Ahora sale con error y conserva el mensaje `Error:`. Las instalaciones correctas siguen con código 0. Las descargas/copias de fuente y el staging explícito `--env` previo conservan sus efectos existentes; no se promete rollback global.

T007/T022 siguen abiertos: falta publicación de política nativa por agente con protección frente a instalaciones parciales y activar su preflight cuando proceda sin opt-in de interacción. Los perfiles ampliados no tienen aceptación real por estos cambios. El diagnóstico causal del timeout de Claude y el binario v1 fijado siguen pendientes. Evidencia del incremento en [validación T007](../specs/025-provider-mcp-profiles/validation-t007.md).

Durante esta continuación se reprodujo además una carrera del test de catálogo nativo: `/doc` podía responder antes de que su recibo privado `service.json` fuera legible. Se corrigió la espera dentro del mismo límite de 15 s, sin cambiar el límite exterior de 45 s ni los criterios de catálogo/limpieza. Se conservan el fallo y la ejecución corregida con el binario v2.0.18 fijado. Es una validación de catálogo; no acredita la aceptación real ampliada ni explica el 504 de Claude.

Verificación final de esta continuación: **319 pruebas offline pasadas**, más **1 comprobación nativa de catálogo** pasada con OpenCode v2.0.18; cinco contratos de arquitectura conservados. El fallo previo del catálogo se conserva junto al resultado corregido. Revisión de Claude integrada y recibos de fuente en [validación T007](../specs/025-provider-mcp-profiles/validation-t007.md). Ningún gate de aceptación ampliada se cierra por estos resultados.
