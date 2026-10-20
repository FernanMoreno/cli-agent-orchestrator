# Claude Code: análisis y pendientes

Publicación, 2026-10-20: el gate común reprodujo el NFR de memoria a 1,010 s en el diagnóstico serial y a 2,200 s en la ejecución completa con ocho trabajadores, frente al límite de 1 s. Esta última terminó con 18.263 pruebas correctas, una fallida y 83 omitidas; el push fue rechazado. Es evidencia del backend, separado de los perfiles y recorridos Claude. [Resultados y límites de evidencia](../specs/021-postgresql-persistence/publication-nfr-20261020.md).

Fecha de revisión: 2026-10-20. Lista de trabajo, sin activación de herramientas ni cambios de permisos. Registrar la versión concreta de Claude Code y el modelo; la división v1/v2 de OpenCode no se traslada a este adaptador.

## Estado y límites de la evidencia

- El adaptador configura MCP por terminal mediante `--mcp-config` y `--strict-mcp-config`, instala el overlay de capacidad y espera una señal de disponibilidad del MCP CAO. Requiere recibos de turno y utiliza restricciones nativas mediante `--disallowedTools`.
- Hay una aceptación histórica de preguntas/respuestas/ACK con AG-UI y reinicio. No acredita automáticamente la fuente final ni todos los perfiles propuestos.
- Los diagnósticos Unicode de 2026-10-19 alcanzaron la instrucción pero acabaron con HTTP 504 al agotar 180 s. El error histórico de comparación no ASCII no se reprodujo; la causa concreta del timeout sigue sin establecerse.
- La ausencia de un error explícito de autenticación no prueba que no haya bloqueo interactivo: uno de esos diagnósticos observó una señal heurística de login. No modificar, borrar ni renovar el login del operador durante la investigación.
- Los mensajes genéricos tienen resultados históricos parciales: envíos persistidos y eventos observados no equivalen a aceptación si falta el sello final de auditoría o la limpieza acreditada.
- La restricción de tres herramientas del proceso hijo de OpenCode no está acreditada como una restricción equivalente en Claude Code.

## Herramientas por perfil

| Perfil propuesto | Herramientas | Condición |
| --- | --- | --- |
| Interacción humana | `cao_ask_user`, `cao_wait_for_user_answers`, `cao_acknowledge_user_answers` | Contrato completo de pregunta, espera y ACK. |
| Colaboración básica | `send_message`, `list_siblings` | Mensajes y descubrimiento autorizados. |
| Recuperación de interacciones | `cao_list_interactions`, `cao_get_interaction`, `cao_escalate_interaction` | Lectura por ámbito y escalado al actor autorizado. |
| Coordinación local | `list_local_cao_peers`, `assign_local_cao_task`, `get_local_cao_task`, `cancel_local_cao_task` | Exclusivo de coordinadores autorizados. |
| Supervisión | `inspect_terminal_turn`, `verify_terminal_turn`, `cancel_terminal_turn` | Identidad y generación verificadas antes de cancelar. |
| Conocimiento | `load_skill`, `memory_recall`; escritura opcional con `memory_store`, `report_outcome` | Permisos separados para lectura y escritura. |
| Trabajador elástico | `complete_assignment` | Solo para el trabajador elástico configurado. |

Empezar por interacción humana, colaboración básica y consulta de interacciones. `cao_answer_interaction` requiere una delegación válida y no autoriza al emisor a contestar su propia pregunta humana. Los permisos de herramientas nativas y los permisos MCP necesitan verificaciones separadas.

## Pendientes, en orden

- [ ] CL-01 Localizar la primera frontera incorrecta del HTTP 504: lanzamiento, disponibilidad MCP, entrega única del prompt, aceptación del turno, llamada al proveedor, espera interactiva, ejecución de herramientas, recibo y extracción final. Retener solo tiempos y categorías sanitizadas.
- [ ] CL-02 Diferenciar latencia del proveedor, cuota, autenticación y fallo del monitor. Reproducir con versión/modelo fijados y presupuesto exterior que permita observar el cierre; no elevar los 180 s como sustituto de un diagnóstico.
- [ ] CL-03 Corregir el defecto que se reproduzca y añadir una regresión de esa frontera. Conservar Unicode en prompts/respuestas y validar el formato de recibos en su límite; no declarar explicado el registro histórico sin evidencia causal.
- [ ] CL-04 Verificar preservación de las credenciales aisladas y archivado tras parada confirmada, incluso al fallar o renovar tokens. Mantener intactos el login original y los perfiles del operador; no promocionar copias automáticamente.
- [ ] CL-05 Inventariar catálogo y llamadas MCP por perfil. Comprobar qué controla realmente `--strict-mcp-config` y qué controla `--disallowedTools`; configurar servidores no equivale a limitar todas sus herramientas.
- [ ] CL-06 Definir e implementar perfiles de interacción, colaboración y coordinación; añadir las herramientas propuestas con permisos explícitos de cliente y servidor. Mantener la capacidad de interacción protegida frente a overrides.
- [ ] CL-07 Probar herramientas permitidas/denegadas, catálogos parciales, descubrimiento diferido, señal de disponibilidad, recursos/prompts y aislamiento entre terminales; no resolverlo con permisos globales.
- [ ] CL-08 Completar una aceptación de mensajes genéricos con dos envíos, persistencia PostgreSQL, AG-UI, reinicio de API, sello de auditoría y limpieza. Investigar por separado el contrato externo `cao.work.message`.
- [ ] CL-09 Completar la aceptación final de preguntas/respuestas/ACK y reinicio vinculada a fuente/runner actuales. Verificar recuperación acotada de espera, identidad estable y ausencia de duplicados; un reinicio por sí solo no prueba recuperación de una llamada interrumpida.
- [ ] CL-10 Validar concurrencia: trabajos y esperas acotados, cancelación por generación, ausencia de cruces de configuración/credenciales y limpieza propia. Medir latencia de control separada de la inferencia; una cuota del proveedor no es capacidad escalable de CAOS.
- [ ] CL-11 Actualizar Spec 022/023 con evidencia final y cerrar solo los criterios demostrados. Mantener aparte la reconciliación del job histórico `RECONCILE_REQUIRED`; no reinstalar ni reejecutar ese job como atajo.

## Limitación actual de autorización y catálogo pendiente

El helper `_tool_denied_reason` autoriza las operaciones que lo utilizan mediante el selector de servidor `@cao-mcp-server` o `*`; los nombres sueltos de herramientas en `allowed_tools` no son una allowlist MCP por operación. Las rutas de interacción tienen además su autoridad propia. El filtro histórico de tres herramientas solo se instala en el proceso hijo marcado; no es global. La base opt-in de perfiles integrada el 2026-10-20 añade otro filtro por hijo mediante el overlay, según el avance registrado al final; no convierte los nombres de allowed_tools en permisos MCP por operación.

- [ ] CL-AUTH Implementar o extender una política del servidor por perfil/herramienta para los nuevos perfiles, antes de dar por efectiva la separación propuesta. Verificar tanto catálogo como invocación directa y conservar las comprobaciones de autoridad de cada operación.
- [ ] CL-LOCAL Documentar y probar que la coordinación local pasa hoy por `_tool_denied_reason("assign", local_only=True)` y requiere identidad de terminal y contexto del proyecto; habilitar solo el nombre en el cliente no basta.
- [ ] CL-CATALOG Auditar también `handoff`, `assign`, `assign_elastic`, `list_native_children`, `join_native_child`, `get_handoff_result`, `delete_terminal`, `answer_user_prompt` y `emit_ui`, más las familias de workflows, trabajo, Beads, Ralph y escritura/borrado de memoria. Decidir explícitamente por perfil qué se expone y qué se deniega; la tabla anterior es una propuesta inicial, no un inventario exhaustivo ni una exclusión ya implementada.

## Fuentes

- [Adaptador Claude Code](../src/cli_agent_orchestrator/providers/claude_code.py), [overlays](../src/cli_agent_orchestrator/providers/mcp_interactions.py) y [servidor MCP](../src/cli_agent_orchestrator/mcp_server/server.py).
- [Tareas y diagnósticos Spec 022](../specs/022-provider-agui-restart/tasks.md), [recorrido común](../test/live_provider/interaction_cli_journey.py) y [documentación Claude Code](claude-code.md).

No se han iniciado proveedores ni leído contenidos de credenciales para redactar esta lista. Los resultados históricos y los pendientes de validación deben permanecer diferenciados.

## Avance integrado 2026-10-20: base de perfiles y diagnóstico

Aplicación de Spec Kit a todos los pendientes: [Spec 025](../specs/025-provider-mcp-profiles/spec.md), [plan](../specs/025-provider-mcp-profiles/plan.md) y [tareas con correspondencias](../specs/025-provider-mcp-profiles/tasks.md). Los gates reales de Spec 022/023/024 conservan sus resultados y siguen separados.

- [x] CL-AUTH-01 Base opt-in del servidor: `interaction` limita a las tres operaciones existentes; `collaboration` añade únicamente `send_message`, `list_siblings`, `cao_list_interactions`, `cao_get_interaction` y `cao_escalate_interaction`. Filtrado exacto de catálogo y llamadas directas, incluidos desconocidos; recursos y prompts denegados. [Inventario actual](../specs/025-provider-mcp-profiles/catalog-inventory.md): 79 herramientas registradas, sin activar MCP Apps opcional. La autoridad interna de cada operación se conserva.
- [x] CL-AUTH-02 El overlay consume `cao_tool_profile` en la declaración MCP canónica, protege la variable interna y rechaza selecciones desconocidas o comandos no reconocidos antes del lanzamiento. Codex protege también esa política frente a overrides MCP. Los filtros vacíos/parciales del cliente se conservan; no se conceden permisos nativos adicionales.
- [ ] CL-AUTH-03 Completar propagación/permisos nativos y preflight por perfil antes de aceptación real ampliada. El marcador histórico de OpenCode sigue intersectando a tres herramientas, incluso con `collaboration`; v1 no queda validado por el overlay privado de v2. Los pendientes AUTH/01–04 de alcance mayor no se cierran por esta base.

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

## Continuación T007/T027 — preflight explícito sin opt-in

OpenCode v2 ahora valida las concesiones de cada hijo CAO activo con perfil explícito aunque el terminal no solicite el canal de interacción. Las concesiones ausentes/parciales, el segundo servidor sin concesiones, las colisiones, el agente no seleccionado y los comandos de texto inválidos fallan antes del lanzamiento. Los perfiles no anuncian por sí solos el canal de preguntas; se conservan los hijos deshabilitados y el comportamiento legado sin perfil.

La selección final pasó **279 pruebas**, incluidas **20 regresiones nuevas** de inicialización, con cero fallos/omitidas. Composición pasó 5 contratos/0 rotos y Black/isort pasaron. Es un cambio del adaptador OpenCode; no amplía permisos de Codex/Claude ni acredita un recorrido real nuevo. T007/T022 y el NFR global siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t027.md).

## Continuación T022 — política nativa por agente

Implementado en OpenCode: la instalación consume perfiles válidos y publica un agente nativo con estados pendiente/listo y vinculación a su configuración. Los fallos intermedios y las entradas compartidas sustituidas impiden el arranque; una reinstalación o refresh correcto lo repara. CAO v2 aplica el perfil en su copia privada, sin contaminar el MCP compartido. Se conservan el opt-in histórico de tres herramientas, los permisos por operación y los lanzadores persistentes. Los agentes con esta marca, incluso al volver a un perfil vacío, requieren CAO v2; la invocación directa de OpenCode no aplica esta política.

Pasaron **527 pruebas**, incluidas **72 nuevas**, tipado de **404 archivos**, estilo y composición **5/0**. Revisión de Claude integrada. T022 queda cerrado como implementación; T007 y las aceptaciones reales/globales siguen abiertos. No cambia permisos de Codex/Claude ni explica su diagnóstico histórico. [Resultados, fallos preservados y límites](../specs/025-provider-mcp-profiles/validation-t022.md).
