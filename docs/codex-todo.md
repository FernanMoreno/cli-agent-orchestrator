# Codex: análisis y pendientes

Publicación, 2026-10-20: el gate común reprodujo el NFR de memoria a 1,010 s en el diagnóstico serial y a 2,200 s en la ejecución completa con ocho trabajadores, frente al límite de 1 s. Esta última terminó con 18.263 pruebas correctas, una fallida y 83 omitidas; el push fue rechazado. Es evidencia del backend, separado de los perfiles y recorridos Codex. [Resultados y límites de evidencia](../specs/021-postgresql-persistence/publication-nfr-20261020.md).

Fecha de revisión: 2026-10-20. Lista de trabajo, sin activación de herramientas ni cambios de permisos. No se aplica la división v1/v2 de OpenCode: aquí se debe registrar la versión concreta del CLI probado.

## Estado y límites de la evidencia

- El adaptador admite configuración MCP por sesión mediante argumentos TOML, incluido `enabled_tools`, y un overlay de capacidad de interacción; requiere recibos de turno.
- La evidencia documentada del 2026-10-19 registra `codex-cli 0.163.0`, modelo `gpt-6-luna`, dos preguntas, dos respuestas humanas, dos ACK, tres snapshots AG-UI y un reinicio de API, con limpieza y autenticación del operador intactas.
- Esa ejecución vinculó módulos seleccionados; no sustituye la congelación de árbol completo ni todos los criterios globales de Spec 022.
- Hay aceptaciones históricas de mensajes genéricos. Falta acreditar el conjunto final de perfiles y el contrato externo `cao.work.message` sin extrapolar desde `send_message`.
- La restricción MCP de tres herramientas creada para el proceso hijo de OpenCode no demuestra una restricción equivalente en Codex.

## Herramientas por perfil

| Perfil propuesto | Herramientas | Finalidad |
| --- | --- | --- |
| Interacción humana | `cao_ask_user`, `cao_wait_for_user_answers`, `cao_acknowledge_user_answers` | Preguntar, esperar y confirmar recepción. |
| Colaboración básica | `send_message`, `list_siblings` | Coordinar agentes dentro del ámbito autorizado. |
| Recuperación de interacciones | `cao_list_interactions`, `cao_get_interaction`, `cao_escalate_interaction` | Recuperar estado persistido y escalar. |
| Coordinación local | `list_local_cao_peers`, `assign_local_cao_task`, `get_local_cao_task`, `cancel_local_cao_task` | Delegación y seguimiento por coordinadores. |
| Supervisión | `inspect_terminal_turn`, `verify_terminal_turn`, `cancel_terminal_turn` | Verificación y cancelación de turnos propios/autorizados. |
| Conocimiento | `load_skill`, `memory_recall`; escritura opcional con `memory_store`, `report_outcome` | Instrucciones, memoria y resultados. |
| Trabajador elástico | `complete_assignment` | Solo dentro de un trabajador elástico configurado. |

El perfil inicial propuesto combina interacción humana, colaboración básica y consulta de interacciones. Reservar `cao_answer_interaction` a delegaciones válidas; no permitir auto-respuestas humanas. Mantener permisos nativos de shell, archivos y red diferenciados de los MCP.

## Pendientes, en orden

- [ ] CX-01 Inventariar por perfil el catálogo MCP visible y las herramientas efectivamente invocables. Verificar el mapeo de `allowed_tools`, `mcpServers` y `enabled_tools`, incluida la configuración vacía.
- [ ] CX-02 Definir perfiles de interacción, colaboración y coordinación con autoridad explícita. Comprobar restricciones en cliente y servidor; una instrucción al modelo no sustituye un control de acceso.
- [ ] CX-03 Habilitar las capacidades adicionales por perfil, sin conceder comodines globales. Verificar escape TOML, nombres de servidor especiales y protección del overlay de capacidad frente a overrides.
- [ ] CX-04 Añadir pruebas de catálogo y ejecución: herramienta permitida/denegada, recurso o prompt no autorizado, permisos parciales, aislamiento entre agentes y recuperación de interacciones.
- [ ] CX-05 Validar mensajes con PostgreSQL, AG-UI y reinicio en la fuente final. Resolver por separado `cao.work.message`, su identidad y autoridad; no tratarlo como alias acreditado de `send_message`.
- [ ] CX-06 Completar aceptación final de preguntas/respuestas/ACK con fuente y runner vinculados. Mantener el diagnóstico exitoso de 2026-10-19 y distinguirlo de la aceptación global pendiente.
- [ ] CX-07 Cubrir espera en curso durante caída de API, error de conexión y recuperación acotada del mismo grupo, sin duplicar pregunta, respuesta o ACK. No inferir recuperación de una espera interrumpida solo por observar un reinicio exitoso.
- [ ] CX-08 Validar turnos Unicode, extracción de respuesta y recibo, errores explícitos de autenticación/cuota, cancelación y reconciliación de timeout con el CLI fijado.
- [ ] CX-09 Verificar varios terminales simultáneos: configuración y credenciales aisladas, límites de trabajo/esperas, cancelación por generación y limpieza sin afectar al operador. Medir control y tiempo del proveedor por separado.
- [ ] CX-10 Ejecutar comprobaciones aplicables de arquitectura/contratos e integración al implementar; actualizar Spec 022/023 y documentación con resultados y limitaciones. No marcar completado código que solo exista en un directorio privado.

## Limitación actual de autorización y catálogo pendiente

El helper `_tool_denied_reason` autoriza las operaciones que lo utilizan mediante el selector de servidor `@cao-mcp-server` o `*`; los nombres sueltos de herramientas en `allowed_tools` no son una allowlist MCP por operación. Las rutas de interacción tienen además su autoridad propia. El filtro histórico de tres herramientas solo se instala en el proceso hijo marcado; no es global. La base opt-in de perfiles integrada el 2026-10-20 añade otro filtro por hijo mediante el overlay, según el avance registrado al final; no convierte los nombres de allowed_tools en permisos MCP por operación.

- [ ] CX-AUTH Implementar o extender una política del servidor por perfil/herramienta para los nuevos perfiles, antes de dar por efectiva la separación propuesta. Verificar tanto catálogo como invocación directa y conservar las comprobaciones de autoridad de cada operación.
- [ ] CX-LOCAL Documentar y probar que la coordinación local pasa hoy por `_tool_denied_reason("assign", local_only=True)` y requiere identidad de terminal y contexto del proyecto; habilitar solo el nombre en el cliente no basta.
- [ ] CX-CATALOG Auditar también `handoff`, `assign`, `assign_elastic`, `list_native_children`, `join_native_child`, `get_handoff_result`, `delete_terminal`, `answer_user_prompt` y `emit_ui`, más las familias de workflows, trabajo, Beads, Ralph y escritura/borrado de memoria. Decidir explícitamente por perfil qué se expone y qué se deniega; la tabla anterior es una propuesta inicial, no un inventario exhaustivo ni una exclusión ya implementada.

## Fuentes

- [Adaptador Codex](../src/cli_agent_orchestrator/providers/codex.py) y [overlays de interacción](../src/cli_agent_orchestrator/providers/mcp_interactions.py).
- [Servidor MCP](../src/cli_agent_orchestrator/mcp_server/server.py), [tareas y evidencia Spec 022](../specs/022-provider-agui-restart/tasks.md) y [documentación Codex](codex-cli.md).

No se han ejecutado nuevas llamadas reales a Codex para crear esta lista. Las versiones y resultados anteriores son evidencia fechada, no garantías sobre cualquier instalación posterior.

## Avance integrado 2026-10-20: base de perfiles y diagnóstico

Aplicación de Spec Kit a todos los pendientes: [Spec 025](../specs/025-provider-mcp-profiles/spec.md), [plan](../specs/025-provider-mcp-profiles/plan.md) y [tareas con correspondencias](../specs/025-provider-mcp-profiles/tasks.md). Los gates reales de Spec 022/023/024 conservan sus resultados y siguen separados.

- [x] CX-AUTH-01 Base opt-in del servidor: `interaction` limita a las tres operaciones existentes; `collaboration` añade únicamente `send_message`, `list_siblings`, `cao_list_interactions`, `cao_get_interaction` y `cao_escalate_interaction`. Filtrado exacto de catálogo y llamadas directas, incluidos desconocidos; recursos y prompts denegados. [Inventario actual](../specs/025-provider-mcp-profiles/catalog-inventory.md): 79 herramientas registradas, sin activar MCP Apps opcional. La autoridad interna de cada operación se conserva.
- [x] CX-AUTH-02 El overlay consume `cao_tool_profile` en la declaración MCP canónica, protege la variable interna y rechaza selecciones desconocidas o comandos no reconocidos antes del lanzamiento. Codex protege también esa política frente a overrides MCP. Los filtros vacíos/parciales del cliente se conservan; no se conceden permisos nativos adicionales.
- [ ] CX-AUTH-03 Completar propagación/permisos nativos y preflight por perfil antes de aceptación real ampliada. El marcador histórico de OpenCode sigue intersectando a tres herramientas, incluso con `collaboration`; v1 no queda validado por el overlay privado de v2. Los pendientes AUTH/01–04 de alcance mayor no se cierran por esta base.

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
