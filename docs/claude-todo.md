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

## Continuación T031 — diagnóstico de completado y turno real Claude

Integrados contadores de sondas de recibo e intentos de recuperación del prompt y la señal existente de entrega verificada por recuperación. Pasan por el HTTP 504 y el sanitizador cerrado, que conserva compatibilidad con el formato anterior. No cambia plazos, reintentos, permisos ni criterios de completado. Pasaron **214 pruebas**, tipado de **404 archivos**, estilo y composición **5/0**; revisión de Claude completada.

El diagnóstico real corregido con Claude Code **2.1.303** y modelo solicitado `claude-sonnet-4-6` completó una instrucción Unicode: **HTTP 200 en 21,909 s**, marcador exacto, recibo nativo `succeeded` asentado y limpieza confirmada. El primer verificador privado falló por leer `output` en vez de `last_message`; su fallo se conserva y no se atribuye al producto. PostgreSQL/fuente/binario permanecieron verificados y los metadatos de las credenciales del operador no cambiaron. T031 cerrado; T006/CL-01–04 siguen abiertos porque el 504 histórico no se reprodujo. Este turno sin herramientas no acredita preguntas/ACK/AG-UI/reinicio, perfiles ampliados, mensajes Work ni gates globales. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t031.md).

## Continuación T032 — filtro explícito de Claude

Implementada la propagación de `enabled_tools` para hijos MCP Claude con perfil CAO explícito: la lista vacía deniega todo y una parcial solo reduce el perfil. El servidor aplica el mismo conjunto inmutable al catálogo y a las llamadas directas. Se protegen los marcadores internos; el comportamiento legado sin perfil y los permisos por operación se conservan.

Se reprodujeron **25 fallos/2 correctas** antes del cambio; la primera selección corregida pasó **66 pruebas**. Tipado **404 archivos**, composición **5/0** y revisiones de producto/pruebas completados. La selección ampliada y las nuevas pruebas del proceso stdio real siguen pendientes: el control de memoria rechazó su arranque por estar por debajo de 6.144 MiB. El usuario indicó mantener pendiente esa validación. T032/T007 no se cierran por esta evidencia parcial. [Resultados y límites](../specs/025-provider-mcp-profiles/validation-t032.md).

## Continuación T033 — negativa de política ignorada en ruta nativa

Corregido el bypass de configuración: un perfil Claude con `native_agent` y `cao_tool_profile` ya no devuelve un comando que descarta la política. Se rechaza esa combinación, incluso con valores nulos/inválidos, entradas Pydantic o permisos nativos amplios. La ruta nativa sin política CAO explícita y el perfil CAO completo conservan su comportamiento.

Pasaron **243 pruebas**, incluidas **36 nuevas**, tras reproducir **30 fallos y 6 controles correctos**. Los dos casos retocados durante la revisión de aislamiento también pasaron en una ejecución unitaria serial. Tipado **404 archivos**, composición **5/0** y estilo correctos; revisión realizada solo con Codex. T033 cerrado. No se ejecutaron CLIs de proveedor ni se reactivaron la selección de 16 módulos o los dos casos stdio aplazados de T032. T007 y las aceptaciones reales/globales siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t033.md).

## Continuación T034 — filtros descartados al instalar OpenCode

Corregida una pérdida de configuración en la instalación nativa de OpenCode: combinar `cao_tool_profile` con `enabled_tools` no nulo se rechaza antes de publicar artefactos, en vez de instalar silenciosamente el perfil completo. Incluye listas vacías/parciales y valores malformados. Los filtros nulos/ausentes y la preparación legada conservan su comportamiento. No se ha implementado aún la propagación nativa del filtro ni se reconstruyen filtros perdidos en instalaciones antiguas.

Pasaron **52 pruebas**, incluidas **29 nuevas**, tras reproducir **24 fallos/28 controles correctos**; tipado **404 archivos**, estilo y composición **5/0**. Revisión solo con Codex, sin CLIs reales ni cambios de credenciales o permisos de otros proveedores. T034 cerrado; propagación nativa, T007, T032 ampliada aplazada y gates reales/globales siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t034.md).

## Continuación T035 — propagación nativa de filtros OpenCode

Implementadas listas `enabled_tools` válidas por agente: vacía deniega todo, parcial intersecta el perfil y los nombres desconocidos no amplían. El formato nativo v2 vincula filtros inmutables al agente y su configuración; el formato v1 sin filtro conserva sus bytes y binding. El hijo privado recibe el filtro protegido, sin ponerlo en el MCP compartido. El preflight usa la misma intersección y exige las tres herramientas si se solicita interacción. Se conserva el rechazo de valores malformados. Esto sustituye únicamente el rechazo de listas válidas de T034.

Verificación final: **193 pruebas** en siete módulos, compatibilidad exacta con el código anterior en **12 casos**, tipado **404 archivos**, estilo y composición **5/0**. Revisión solo con Codex. Sin CLI real, autenticación ni PostgreSQL; T032 ampliada no reanudada. T035 cerrado; el resto de la lista OpenCode, autoridad ampliada, v1 real, T007 y gates reales/globales siguen en curso. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t035.md).

## Continuación T036 — catálogo ampliado y autoridad

Integrados siete perfiles cerrados: interacción, colaboración, `coordination`, `supervision`, `knowledge`, `knowledge_writer` y `elastic_worker`. Cada perfil añade solo su familia; la colaboración básica no obtiene delegación, supervisión ni escritura. Auditadas las **79 operaciones** del catálogo: las restantes se deniegan también por llamada directa. Asignación general, handoff, borrado, Work/workflows, Beads/Ralph y borrado de memoria siguen fuera de estos perfiles.

La supervisión ahora exige metadatos actuales del terminal propio o de un hijo asignado directamente al llamante antes de inspeccionar/verificar/cancelar; conserva permisos del servidor, generación y autoridad Work. Coordinación mantiene el gate `assign` local con identidad/proyecto, y el cierre elástico exige trabajador configurado. Conocimiento conserva los controles existentes de ámbito, memoria y aprendizaje; escritura es un perfil separado.

Pasaron **181 pruebas** de catálogo/transporte/autoridad/preflight y ocho comprobaciones suplementarias de los cuatro handlers locales sin identidad/proyecto, sin enviar tareas ni mensajes. Tipado **404 archivos**, estilo y composición **5/0**, revisión solo con Codex. Las casillas cerradas de OpenCode corresponden a implementación y contratos offline; **OC1 y la aceptación real de OC2/OC-05/06 siguen pendientes**, junto a los lanzadores externos y modalidades de autenticación. T032 aplazada no se reanudó. [Inventario completo](../specs/025-provider-mcp-profiles/catalog-inventory.md) y [evidencia/límites](../specs/025-provider-mcp-profiles/validation-t036.md).

## T032/T047 — validación aplazada completada

La instrucción expresa de terminar lo aplazado reanudó esta validación. La selección actual de 16 módulos reprodujo una expectativa antigua de metadatos tras T035: 604 correctas/una fallida. Corregida la prueba para exigir filtros protegidos parcial/vacío e identidad de capacidades aislada, la misma selección pasó **605 pruebas**, incluidos los **dos procesos MCP stdio reales**. Estos ejecutan Python/CAOS; no se lanzó Claude Code ni un modelo ni se usó su login. Se conservan los fallos y las negativas anteriores por memoria. T032 se cierra en su alcance de transporte/regresión, no como aceptación real autenticada del proveedor ni de T007 completo. [Evidencia actualizada](../specs/025-provider-mcp-profiles/validation-t032.md).


Preparación global compartida, 2026-10-22: R4 actualizó HEAD/digest/rutas sobre main73bb0c0b y pasó seis grupos puros de control de archivos, con aprobación de Claude en modo lectura. Las comprobaciones de fuente, intérpretes y PostgreSQL precedieron al bloqueo de memoria; **no arrancaron** los tres NFR fríos, la suite completa, Apps ni el ratchet. Los gates siguen cerrados tras tres admisiones bloqueadas por memoria y los límites/coberturas originales no cambian. No se detuvieron las pruebas Angular ajenas observadas. [Preparación, bloqueo y límites](../specs/025-provider-mcp-profiles/global-gate-preflight-20261022.md). Los resultados históricos de memoria/cobertura y la aceptación global de este proveedor siguen pendientes.

Actualización global compartida, 2026-10-22 (R5/T132): la memoria permitió ejecutar el primer NFR frío cubierto sobre main5628bdf6, que **falló en 1.023152965s** frente a `<1s`. No se ejecutaron los otros dos NFR, backend completo, Apps ni ratchet. Un diagnóstico separado conservó el fallo y 21 verificaciones/20 auditorías, situando 523.697ms en el catálogo sin demostrar aún una corrección segura. Limpieza propia verificada; no se cambian límites ni se cierra aceptación global. [Recibo y diagnóstico actual](../specs/021-postgresql-persistence/current-source-nfr-20261022.md). Trabajo posterior solo con Codex por instrucción del usuario.


Actualización compartida2026-10-25: T140 corrige la equidad del inbox al unificar la admisión de eventos de estado en2Q lugares, conservando límites/orden/cierre.129 pruebas del controlador, API y PostgreSQL pasan; esto es infraestructura compartida y no una ejecución nativa reciente de este cliente ni un cierre de sus gates. [Alcance y evidencia](../specs/025-provider-mcp-profiles/validation-t140.md).


Actualización compartida2026-10-25: T141 elimina el bloqueo global de reconstrucción en ProviderManager.get_provider. Las restauraciones de terminales distintos avanzan de forma independiente y los lectores del mismo terminal siguen esperando a su recibo restaurado antes de ver el adaptador.187 regresiones pasan, incluida PostgreSQL con adaptadores Codex reales sin iniciar CLI/modelo; arquitectura5/0 y revisión de Claude/Luna. Esto no cierra las carreras previas de creación/limpieza, OC-06 ni la aceptación nativa actual de este cliente. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t141.md).


Actualización compartida2026-10-25: T142 corrige solapamientos del registro de proveedores: la reconstrucción no pisa la creación explícita, una limpieza anterior no elimina al propietario nuevo y el listado usa una instantánea. Se conservan las opciones de lanzamiento y la limpieza reentrante devuelveFalse para retener el reemplazo y sus metadatos.241 regresiones pasan, incluida PostgreSQL/perfiles/recibos; esto no acredita el ciclo completo hasta borrar la fila, varias instancias ni inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t142.md).


Actualización compartida2026-10-25: T144 completa la integración asíncrona de T142. La publicación del proveedor y el tramo final ya autorizado de reversión se ejecutan fuera del bucle, conservando contexto y propiedad del trabajador ante cancelación repetida.273 regresiones pasan, incluida PostgreSQL, creación/worktrees y los controles anteriores; arquitectura5/0. Esto no acredita cancelación segura de todo el ciclo ni inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t144.md).


Actualización compartida2026-10-25: T145 corrige la reconstrucción entre limpieza y borrado: retira cualquier adaptador reaparecido y protege el borrado individual y los barridos finales de sesión/Flow. Mantiene el monitor fuera del bloqueo para evitar una inversión de orden.398 regresiones pasan;35 controles finales verifican los avisos posteriores de Flow y los20 casos nuevos. PostgreSQL real, arquitectura5/0, revisión Claude/Luna y recursos propios cerrados. T143 sigue abierto para publicación/inicialización concurrente, identidad por fila y alcance distribuido; no acredita inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t145.md).


Actualización compartida2026-10-25: T146 reserva el arranque antes de publicar su fila y aplaza la retirada extranjera antes de efectos, también en Herdr y canal remoto. La publicación comprueba el registro actual; los permisos de compensación no se heredan en callbacks normales.676 pruebas pasan con PostgreSQL, perfiles, Work, API/MCP y Herdr; arquitectura5/0 y revisión Claude/Luna. Una cancelación con trabajadores sin parada demostrada conserva el propietario hasta reiniciar su proceso; no acredita aceptación nativa actual ni cierra toda T143. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t146.md).
