# OpenCode v1 y v2: análisis y pendientes

Publicación, 2026-10-20: el gate común reprodujo el NFR de memoria a 1,010 s en el diagnóstico serial y a 2,200 s en la ejecución completa con ocho trabajadores, frente al límite de 1 s. Esta última terminó con 18.263 pruebas correctas, una fallida y 83 omitidas; el push fue rechazado. Es evidencia del backend, separado de los perfiles y recorridos OpenCode. [Resultados y límites de evidencia](../specs/021-postgresql-persistence/publication-nfr-20261020.md).

Fecha de revisión: 2026-10-20. Esta lista registra trabajo por hacer; no activa herramientas ni cambia permisos. Las casillas abiertas son propuestas o validaciones pendientes, no necesariamente defectos reproducidos.

Actualización 2026-10-21: la [recuperación verificada de memoria](../specs/025-provider-mcp-profiles/memory-recovery-20261021.md) trasladó siete árboles temporales inactivos a disco, conservando su contenido y acceso, sin detener servicios ni cambiar el mínimo de 6.144 MiB. El [recorrido real v1 T053](../specs/025-provider-mcp-profiles/validation-t053.md) pasó preguntas/respuestas/ACK, Unicode/recibo/completed, identidades PostgreSQL/AG-UI/nativas estables tras reiniciar API y cierre/limpieza completos en la fuente f23b1d5f. Esto cierra el flujo restringido administrado, no los perfiles ampliados ni toda la aceptación v1. La [clasificación del error de credenciales T056](../specs/025-provider-mcp-profiles/validation-t056.md) quedó corregida y validada en ce41f190:591 pruebas, rechazo real con clave inválida y control autenticado con Unicode/recibo/completed. Se conservan los fallos anteriores y sus límites; timeout/cancelación, perfiles ampliados y aceptación v1 global siguen pendientes.

La [corrección T057](../specs/025-provider-mcp-profiles/validation-t057.md) pasó616 pruebas y recuperación real por cancelación en a97bf19f: rechazo de generaciones incorrecta/repetida, sustitución de ejecución nativa, modelo correcto y limpieza de configuración (un árbol tras reset, cero tras borrar sesión). Quedan separados el timeout activo y la cancelación mientras una herramienta sigue ejecutándose.


## Estado y límites de la evidencia

- El adaptador detecta las versiones principales 1 y 2. V2 usa `mini --standalone` y configuración por terminal; v1 conserva su ruta nativa.
- V2 tiene correcciones integradas para nombres MCP canónicos, permisos parciales, Code Mode, perfiles nativos y aislamiento de credenciales. El recorrido reciente de preguntas completó respuestas, ACK y un reinicio de API con limpieza y credenciales del operador intactas. La suite enfocada más reciente pasó 351 pruebas; no equivale a aceptación global.
- Existen aceptaciones históricas de mensajes nativos con AG-UI/reinicio y ejecuciones posteriores fallidas. Una aceptación anterior no valida automáticamente la fuente actual ni el alias externo `cao.work.message`.
- V1 pasó el recorrido restringido T053 con binario fijado y los siete perfiles en ambas versiones (14 casos de catálogo/reinicio nativo T063); quedan pendientes mensajes completos y operaciones de perfiles ampliados.
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

- [x] OC-01 Diseñar perfiles explícitos de interacción, colaboración y coordinación, con catálogo, llamadas y permisos coherentes. Mantener disponible el recorrido restringido de tres herramientas.
- [x] OC-02 Añadir primero las capacidades de colaboración básica y consulta de interacciones. Verificar permisos tanto en el cliente como en el servidor; no sustituirlos por instrucciones de texto.
- [x] OC-03 Añadir delegación, supervisión y conocimiento solo a los perfiles correspondientes; distinguir asignación local de trabajadores elásticos.
- [x] OC-04 Probar herramientas permitidas, denegadas, nombres desconocidos, permisos vacíos/parciales, colisiones y aislamiento entre terminales; preservar respuestas humanas y autoridad delegada.
- [ ] OC-05 Validar mensajes genéricos con PostgreSQL, proyección AG-UI y reinicio de API en la fuente final. Resolver y probar por separado el contrato externo `cao.work.message`; no asumir que es alias del MCP `send_message`.
- [ ] OC-06 Verificar concurrencia con múltiples terminales: límites de tareas, esperas y cancelación acotadas, orden de mensajes, ausencia de cruces de credenciales y limpieza propia. Medir latencia del control separada del tiempo del modelo.
- [ ] OC-07 Consolidar evidencia y actualizar las tareas de Spec 022/023; conservar fallos anteriores. La aceptación requiere código integrado en main y recibos vinculados a la fuente ejecutada.

## Pendientes específicos de v1

- [x] OC1-01 Preparar un binario v1 con versión y procedencia fijadas, aislado del v2 instalado, sin reinstalación global.
- [x] OC1-02 Validar instalación antes del terminal, agente nativo vinculado y selección visible Go/LongCat sin fallback en los siete perfiles v1. T063 acredita configuración/selección nativa; inferencia y modelo efectivo en la petición siguen en los recorridos reales pendientes.
- [x] OC1-03 Ejecutar prompt Unicode, respuesta con recibo, error de proveedor, timeout, cancelación y limpieza en v1 real.
- [x] OC1-04 Verificar configuración y almacén de credenciales aislados también en v1: siete casos T063 con preservación de auth/config/DB/WAL/SHM del operador y limpieza propia. El uso/validez/renovación de autenticación requiere sus pruebas específicas.
- [ ] OC1-05 Validar los perfiles MCP ampliados con la sintaxis de permisos de v1 y el recorrido preguntas/respuestas/ACK, mensajes y reinicio de API.

## Pendientes específicos de v2

- [ ] OC2-01 Actualizar o retirar los lanzadores externos con hashes/rutas antiguos. Fijar sus entradas a la fuente final antes de ejecutarlos; el recorrido directo exitoso no valida esos lanzadores.
- [x] OC2-02 Validar cada perfil ampliado con nombres MCP canónicos y resolución efectiva de permisos; conservar `codemode=false` en el flujo directo y no conceder `execute` para resolver herramientas ausentes.
- [x] OC2-03 Extender la validación previa para comprobar las herramientas requeridas por cada perfil, manteniendo la comprobación exacta del flujo de tres herramientas.
- [ ] OC2-04 Repetir la aceptación final vinculada a fuente/runner actuales cuando se implementen los nuevos perfiles; comprobar catálogo ofertado al modelo y llamadas reales.
- [ ] OC2-05 Acreditar aislamiento con las modalidades de autenticación que se declaren soportadas, incluida renovación OAuth si corresponde. El éxito con API key de OpenCode Go no acredita OAuth ni todos los proveedores/modelos.
- [ ] OC2-06 Registrar por separado el timeout histórico externo y su reconciliación. No cerrarlo administrativamente basándose solo en una ejecución nueva exitosa.

## Limitación actual de autorización y catálogo pendiente

El helper `_tool_denied_reason` autoriza las operaciones que lo utilizan mediante el selector de servidor `@cao-mcp-server` o `*`; los nombres sueltos de herramientas en `allowed_tools` no son una allowlist MCP por operación. Las rutas de interacción tienen además su autoridad propia. El filtro histórico de tres herramientas solo se instala en el proceso hijo marcado; no es global. La base opt-in de perfiles integrada el 2026-10-20 añade otro filtro por hijo mediante el overlay, según el avance registrado al final; no convierte los nombres de allowed_tools en permisos MCP por operación.

- [x] OC-AUTH Implementar o extender una política del servidor por perfil/herramienta para los nuevos perfiles, antes de dar por efectiva la separación propuesta. Verificar tanto catálogo como invocación directa y conservar las comprobaciones de autoridad de cada operación.
- [x] OC-LOCAL Documentar y probar que la coordinación local pasa hoy por `_tool_denied_reason("assign", local_only=True)` y requiere identidad de terminal y contexto del proyecto; habilitar solo el nombre en el cliente no basta.
- [x] OC-CATALOG Auditar también `handoff`, `assign`, `assign_elastic`, `list_native_children`, `join_native_child`, `get_handoff_result`, `delete_terminal`, `answer_user_prompt` y `emit_ui`, más las familias de workflows, trabajo, Beads, Ralph y escritura/borrado de memoria. Decidir explícitamente por perfil qué se expone y qué se deniega; la tabla anterior es una propuesta inicial, no un inventario exhaustivo ni una exclusión ya implementada.

## Fuentes

- [Adaptador OpenCode](../src/cli_agent_orchestrator/providers/opencode_cli.py), [traductor v2](../src/cli_agent_orchestrator/utils/opencode_v2.py) y [filtro MCP](../src/cli_agent_orchestrator/mcp_server/interaction_pull_flow_only.py).
- [Servidor MCP y contratos de herramientas](../src/cli_agent_orchestrator/mcp_server/server.py).
- [Recorrido nativo](../test/live_provider/opencode_interaction_journey.py), [tareas Spec 022](../specs/022-provider-agui-restart/tasks.md) y [documentación OpenCode](opencode-cli.md).
- El relevo y los recibos privados conservan los resultados históricos. No copiar credenciales ni transcripciones a estos documentos.

## Avance integrado 2026-10-20: base de perfiles y diagnóstico

Aplicación de Spec Kit a todos los pendientes: [Spec 025](../specs/025-provider-mcp-profiles/spec.md), [plan](../specs/025-provider-mcp-profiles/plan.md) y [tareas con correspondencias](../specs/025-provider-mcp-profiles/tasks.md). Los gates reales de Spec 022/023/024 conservan sus resultados y siguen separados.

- [x] OC-AUTH-01 Base opt-in del servidor: `interaction` limita a las tres operaciones existentes; `collaboration` añade únicamente `send_message`, `list_siblings`, `cao_list_interactions`, `cao_get_interaction` y `cao_escalate_interaction`. Filtrado exacto de catálogo y llamadas directas, incluidos desconocidos; recursos y prompts denegados. [Inventario actual](../specs/025-provider-mcp-profiles/catalog-inventory.md): 79 herramientas registradas, sin activar MCP Apps opcional. La autoridad interna de cada operación se conserva.
- [x] OC-AUTH-02 El overlay consume `cao_tool_profile` en la declaración MCP canónica, protege la variable interna y rechaza selecciones desconocidas o comandos no reconocidos antes del lanzamiento. Codex protege también esa política frente a overrides MCP. Los filtros vacíos/parciales del cliente se conservan; no se conceden permisos nativos adicionales.
- [x] OC-AUTH-03 Completar propagación/permisos nativos y preflight por perfil: T063 corrigió la intersección indebida y pasó los siete perfiles en cada dialecto, con capacidad, catálogo exacto y reinicio. El recorrido legado conserva tres herramientas. La autoridad por operación y las llamadas del modelo mantienen sus gates separados.

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

## OC1-01 — binario v1 fijado

Preparado OpenCode **1.18.35** en un directorio privado, separado del v2 **2.0.18** instalado. El archivo oficial Linux x64 baseline coincide con el SHA-256 publicado por GitHub y el ejecutable devuelve exactamente `1.18.35`. No hubo instalación global, copia de credenciales ni turno de modelo. [Pin público reproducible](../specs/025-provider-mcp-profiles/opencode-v1-pin.json) y [evidencia de preparación](../specs/025-provider-mcp-profiles/validation-t037.md). OC1-02–05 siguen pendientes: la ruta v1 debe aislar configuración/datos y aplicar perfiles privados antes de ejecutar aceptación real.

## T040 parcial — aislamiento v1 entre agentes

La ruta legada v1 prepara configuración, agente congelado, home, datos/auth, caché, estado, temporales y base de datos en un directorio privado por terminal. Mantiene la sintaxis nativa de permisos y rechaza perfiles administrados todavía no validados. Los overrides tardíos se eliminan del lanzamiento después de copiar el auth compatible en privado; el origen permanece intacto. El formato antiguo `mode` se rechaza porque puede introducir agentes no seleccionados.

La red multiagente tiene una prueba offline con reviewer y worker inicializados simultáneamente: cada uno conserva su configuración/estado y limpiar uno no afecta al otro. Los permisos globales y del agente conservan sus ámbitos; `write`/`patch` se normalizan a `edit` según el código v1 fijado. Pasaron **265 contratos offline**, tipado de **405 archivos**, estilo y composición **5/0**. Esta evidencia no acredita coordinación, mensajes, cancelación, latencia o reinicio reales. [Resultados y límites de T040](../specs/025-provider-mcp-profiles/validation-t040.md).

**Por decisión expresa del usuario, todas las pruebas reales OpenCode siguen pendientes aunque se recupere memoria.** T032 continúa aplazada por separado; T040/T037 y OC1-02–05/OC-05/06 permanecen abiertos. Codex trabaja solo; sin Claude ni subagentes.

## T041 — perfil nativo validado antes de crear el terminal

La creación de un terminal OpenCode valida ahora el perfil instalado y el binding de su política antes de generar identidad, planificar al hijo o asignar recursos. El inicio inmediato/diferido recibe una copia inmutable del perfil seleccionado; cambiar después el archivo no cambia el agente que arranca. El gestor rechaza cruces de identidad/proveedor y el lanzamiento vuelve a comprobar la configuración MCP. La reutilización idempotente conserva su terminal sin volver a instalar ni releer el perfil.

Pasaron **296 pruebas OpenCode y 70 de ciclo de vida/gestor**, tipado de **405 archivos** y estilo de seis archivos. Las regresiones generales reutilizan PostgreSQL existente verificado con esquemas privados; los 31 casos nuevos de validación previa no usan base de datos. No se ejecutó un CLI real. [Evidencia y límites de T041](../specs/025-provider-mcp-profiles/validation-t041.md). T041 cerrado como gate offline; OC1-02 sigue abierto por agente/modelo efectivos reales, y T037/T040 y las pruebas reales continúan pendientes.

## Reanudación expresa de pruebas reales

Tras medir 6,77 GiB disponibles, el usuario autorizó explícitamente reanudar las pruebas reales OpenCode. La suspensión anterior deja de aplicarse a nuevas ejecuciones OpenCode; sus recibos históricos se conservan. Se medirán al menos 6.144 MiB antes de cada ejecución intensiva, se serializarán y se verificarán fuente/binario/runner, aislamiento, limpieza y estado del operador. T032 continúa aplazada y Codex trabaja solo. T042/T043 registran esta continuación; no se cierra ningún gate por la autorización.

## T042/T043/T044 — primeras comprobaciones reales reanudadas

El binario v1 fijado aceptó la configuración privada generada y resolvió el agente, su modelo explícito y el prompt Unicode, sin credenciales ni llamada al modelo. El servidor MCP estaba deshabilitado en esta comprobación de gramática; todavía no acredita catálogo/perfiles ni un terminal v1 operativo. [Evidencia parcial T042](../specs/025-provider-mcp-profiles/validation-t042.md).

El primer recorrido real v2 quedó en `native_initialize` por un TypeError del adaptador del escritor; fuente/binario permanecieron iguales y la limpieza, el aislamiento y la no modificación del auth pasaron. No llegó al modelo. [Recibo y límites T043](../specs/025-provider-mcp-profiles/validation-t043.md). Corregidas las dos capas que no reenviaban el perfil congelado: cuatro fallos/dos controles RED; seis contratos nuevos y **271 pruebas** de la selección ampliada correctos. Tipado de 405 archivos, estilo y composición 5/0. Se conserva también un intento de regresión con 22 fallos exclusivamente por ruta de socket demasiado larga; repetir la misma selección con ruta corta pasó sin modificar tests ni cobertura. [Corrección y límites T044](../specs/025-provider-mcp-profiles/validation-t044.md). El reintento real y los gates mayores siguen abiertos.

## T045 — orden del perfil congelado

El segundo recorrido real v2 arrancó, conectó MCP, ofreció exactamente las tres herramientas y creó dos preguntas, pero no llegó a respuestas/ACK/reinicio: la serialización del perfil congelado ordenaba sus permisos y el gate exacto lo rechazó. Corregido el productor para preservar el orden de acciones y recursos, sin cambiar el gate ni ampliar permisos. Tres casos RED/GREEN y **493 pruebas combinadas** correctos, tipado de 405 archivos, estilo y composición 5/0. [Evidencia T045](../specs/025-provider-mcp-profiles/validation-t045.md). El próximo reintento real debe usar fuente y runner nuevos; T043 sigue abierto. T046 conserva el hallazgo confirmado relacionado de renderizado/binding, que esta corrección no resuelve.

## T043/T045 — aceptación real v2 sobre fuente corregida

Pasó el recorrido completo en **v2.0.18**, vinculado a **398bae68** y al SHA del runner/binario: dos preguntas, dos respuestas y ambos ACK; un reinicio de API; tres observaciones AG-UI con identidad/ledger/proveedor/base de datos estables; estado final COMPLETED antes del plazo; catálogo/ofertas exactos de tres herramientas y sin llamadas ajenas. Limpieza completa, temporales eliminados, bindings restaurados y auth del operador intacto. Se conservaron el TypeError inicial, el fallo posterior de orden y una admisión bloqueada por memoria; el éxito procede de otra medición que sí superó 6.144 MiB.

T043 y T045 cerrados en su alcance restringido, con límites y criterios originales. Siguen abiertos v1 real completo, perfiles ampliados reales, mensajes externos, concurrencia, otras autenticaciones y el hallazgo confirmado T046. T032 no se reanudó. [Evidencia final](../specs/025-provider-mcp-profiles/validation-t043.md).

## T046 — integridad del orden de permisos

Corregido el renderizado y binding de políticas nativas: las nuevas instalaciones usan un sobre v3 explícito que conserva y valida el orden de reglas y recursos. Se mantienen los bytes/hashes de los formatos antiguos; los casos antiguos ambiguos se rechazan con indicación de reinstalar, sin modificar artefactos del operador. El número del sobre no cambia el soporte del CLI: v1 administrado sigue pendiente.

Pasaron **642 pruebas**, incluidas 35 nuevas de orden, compatibilidad, ambas fases del instalador y propagación privada; tipado de 405 archivos, estilo y composición 5/0. Los lanzamientos de esos contratos están simulados: la aceptación real ampliada continúa abierta. T032 permanece aplazada. [Evidencia T046](../specs/025-provider-mcp-profiles/validation-t046.md).

Después del commit **dab6d4fd**, otro recorrido real v2 pasó sobre las 498 fuentes vinculadas: dos preguntas/respuestas/ACK, reinicio de API e identidad AG-UI estable; COMPLETED antes del plazo, catálogo/ofertas exactos, limpieza y auth intactos. Admisión con 6.325 MiB; recorrido de 42,77 s. Este éxito vuelve a acreditar el flujo restringido sobre la fuente actual; no acredita una instalación real del sobre v3 ni operaciones de perfiles ampliados. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t046.md).

## T032/T047 — validación aplazada completada

La instrucción expresa de terminar lo aplazado reanudó esta validación. La selección actual de 16 módulos reprodujo una expectativa antigua de metadatos tras T035: 604 correctas/una fallida. Corregida la prueba para exigir filtros protegidos parcial/vacío e identidad de capacidades aislada, la misma selección pasó **605 pruebas**, incluidos los **dos procesos MCP stdio reales**. Estos ejecutan Python/CAOS; no se lanzó Claude Code ni un modelo ni se usó su login. Se conservan los fallos y las negativas anteriores por memoria. T032 se cierra en su alcance de transporte/regresión, no como aceptación real autenticada del proveedor ni de T007 completo. [Evidencia actualizada](../specs/025-provider-mcp-profiles/validation-t032.md).

## T048 — agente administrado v3 en recorrido real

Corregido el rechazo de las dos variables protegidas de perfil/filtro en el entorno aislado del lanzador real, manteniendo env -i y el rechazo de variables ajenas. Seis fallos/dos controles RED y **285 pruebas** combinadas correctas; tipado405, estilo y composición5/0.

El primer recorrido real cargó v3, conectó MCP y ofreció las tres herramientas, pero agotó el plazo antes de preguntar. Una repetición con la misma fuente y criterios pasó en **41,51 s**: perfil/filtro exactos observados en el proveedor y el proceso hijo, preguntas/respuestas/ACK, reinicio e identidad AG-UI, limpieza y auth intactos. T048 cerrado en ese alcance; se conserva el timeout sin atribuir una causa no demostrada. El artefacto se preparó con el renderizador real en el árbol privado: esto no acredita el instalador completo ni los perfiles ampliados/v1. [Evidencia y nota de auditoría de recibos](../specs/025-provider-mcp-profiles/validation-t048.md).

## T049 — base administrada de v1

Implementada la ruta privada v1 para sobres v3 validados, perfiles/filtros protegidos y comprobación de permisos nativos efectivos antes del lanzamiento. Se mantienen el rechazo de sobres antiguos en v1 y de su interacción administrada todavía no validada. Se conservan orden/recursos, normalización de nombres sin colisiones, configuración del operador y limpieza propia.

Pasaron **441 pruebas**, incluidas 43 nuevas; tipado de 405 archivos, estilo y composición5/0. Se conservan RED y dos negativas iniciales por memoria; la ampliada arrancó después de que el usuario liberara memoria. La comprobación del binario v1 fijado se ejecuta después de integrar esta fuente. T040/T042/T049 y la aceptación completa de v1 continúan abiertas hasta sus criterios reales. [Evidencia T049](../specs/025-provider-mcp-profiles/validation-t049.md).

La comprobación nativa T049 pasó sobre **fad7a331**: v1.18.35 resolvió agente/modelo/Unicode y conectó su proceso MCP real con catálogo exacto de tres herramientas y apagado del observador confirmado. Procesos propios limpios; fuente/binario iguales; sin credenciales, modelo, PostgreSQL o API. T049 cerrado como preparación acotada. T040/T042/T050 siguen abiertos por el terminal/modelo/interacción y demás criterios reales de v1; continúa siendo el primer grupo de trabajo.

T051: selección activa de credenciales v1 corregida; 28 contratos y 475 pruebas combinadas pasaron, además de exportación nativa real aislada. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t051.md). El recorrido real API/tmux de producción también pasó en304dfdc6: Unicode, recibo activo, completed y limpieza íntegra. No cierra OC1.

T052: conexión MCP de v1 ligada a señales privadas de descubrimiento del cliente; 12 contratos y 490 pruebas ampliadas pasaron. Se conservó una ejecución fallida por inodos agotados y se repitió sin alterar criterios. [Evidencia](../specs/025-provider-mcp-profiles/validation-t052.md). El diagnóstico real administrado pasó encc8edda8: señal propia y catálogo exacto. No se observó cierre del observador; interacción v1 aún cerrada.

T053: candidato de interacción explícita nativa v1 con perfil v3, capacidad privada, preflight completo y descubrimiento del cliente. 13 contratos/503 pruebas pasaron; aún pendiente aceptación real de preguntas/ACK/reinicio. [Evidencia](../specs/025-provider-mcp-profiles/validation-t053.md).

Primer arranque real T053: perfil/capacidad/catálogo/conexión correctos, pero el guard de memoria detuvo el recorrido antes del modelo (5.867 MiB <6.144). Limpieza completa; preguntas/ACK/reinicio siguen abiertos.

T054/T055: el diagnóstico sin credenciales mostró fallback silencioso de Go a Big Pickle; LAST no aceptó resultado final. El candidato T055 comprueba el inventario nativo exacto antes del lanzamiento: 24 contratos/527 pruebas pasaron. Negativa real y control autenticado pasaron en4f032e95: HTTP400 sin tarea/fallback y modelo/Unicode/recibo/completed correctos con clave activa; limpieza íntegra. OC1 completo sigue abierto. [Diagnóstico](../specs/025-provider-mcp-profiles/validation-t054.md) y [corrección/límites](../specs/025-provider-mcp-profiles/validation-t055.md).

Actualización 2026-10-21, T058: [timeout activo y cancelación en curso](../specs/025-provider-mcp-profiles/validation-t058.md) pasaron en5d6262d0. La herramienta nativa ejecutó sleep120, la API agotó el presupuesto75 con504/processing/sin reenvíos ni recibo; el proceso seguía vivo y fue detenido por la cancelación de su generación. Se verificaron rechazos409, nuevo CLI/modelo, un/cero árboles privados y limpieza íntegra. Esto cierra OC1-03 con los controles Unicode/error previos; no explica los timeouts históricos ni cierra perfiles/mensajes/instalación.

Actualización 2026-10-21, T059: el [instalador de producción y los siete catálogos nativos v1](../specs/025-provider-mcp-profiles/validation-t059.md) pasaron en936f7de6:21 comandos reales, binding ready-v3 y permisos efectivos, catálogos exactos, observadores completos y ningún proceso residual. No hubo credenciales ni trabajo de modelo en esta prueba; ofertas al modelo, operaciones autorizadas, mensajes y aceptación global permanecen separados.

Actualización 2026-10-21, T060/T061: el [recorrido con dos agentes](../specs/025-provider-mcp-profiles/validation-t060.md) encontró un rechazo real del perfil de mensajes correctamente autorizado: el selector MCP forzaba el protocolo humano de tres herramientas. El [candidato T061](../specs/025-provider-mcp-profiles/validation-t061.md) distingue perfiles sin herramientas dependientes de capacidad humana y conserva las exigencias de interacción explícita. Pasan46 contratos nuevos/662 pruebas ampliadas, tipos405 y arquitectura5/0, con revisión independiente Codex/ECC. T060-r4 sobre42bdbe7d verificó ambos mensajes reales/PG/AG-UI/reinicio e identidades y cierre de observadores, aceptando el alcance acotado T061. El diagnóstico completo falló porque quedaron dos directorios privados antes de la limpieza exterior. La corrección de propiedad T062 y la repetición íntegra siguen pendientes; no se cierra todo el recorrido ni la aceptación v1.

Actualización 2026-10-21, T062: la fuga tras reiniciar la API se reprodujo en el límite de propiedad del proveedor. El [candidato de propiedad privada durable](../specs/025-provider-mcp-profiles/validation-t062.md) añade consulta exacta por terminal, identidad de lanzamiento, retirada recuperable y negativa ante pruebas ambiguas o referencias obsoletas. 753 pruebas ampliadas, tipado406, formato de seis archivos y composición5/0 pasan; revisión final Codex/ECC aprobada. La repetición real completa sobre fuente integrada sigue pendiente. Los fallos anteriores permanecen conservados.

T062 integrado y subido en3aef365a. La repetición real T060-r5 creó ambos agentes con aislamiento/permisos/catálogos correctos, pero el CLI mostró endpoint upstream no disponible/reintento5 y agotó75s sin llamadas MCP ni mensaje; no alcanzó el reinicio. Limpieza cero directorios antes del teardown exterior y procesos/fuente/credenciales intactos. T060/T062 completos siguen abiertos; no se combinan ejecuciones parciales para declarar una aceptación global. [Evidencia](../specs/025-provider-mcp-profiles/validation-t060.md).

T063: corregida la reducción silenciosa de perfiles mixtos en lanzamientos administrados v3 con autorización MCP. El overlay recibe el mapa exacto hijo/perfil validado; conserva la restricción de tres herramientas en lanzamientos humanos sin selector MCP, políticas antiguas y otros hijos. No se han modificado los cinco contratos heredados que detectaron una primera corrección demasiado amplia. Pasan893 pruebas ampliadas, tipado406, formato5 y composición5/0, con revisión Codex/ECC. La matriz restante de catálogo/reinicio por perfil y dialecto y las operaciones/modelo siguen pendientes; el piloto acotado posterior se registra debajo. [Evidencia](../specs/025-provider-mcp-profiles/validation-t063.md).

Actualización 2026-10-21: T060-r6 sobre9cc35033 completó descubrimiento/envío MCP y persistió un mensaje, pero no llegó a resultado LAST/completed dentro de75s; causa del turno sin terminar aún no demostrada. Limpieza inmediata cero raíces y estado del operador intacto. El piloto nativo T063 collaboration/v1 sobre la misma fuente pasó catálogo exacto de ocho herramientas, capacidad, reinicio con identidades estables y retirada inmediata de raíz/capacidad antes del teardown exterior. No envió tarea al modelo y no sustituye la aceptación completa de mensajes ni operaciones; quedan trece casos de catálogo/reinicio. [Mensajes](../specs/025-provider-mcp-profiles/validation-t060.md), [piloto](../specs/025-provider-mcp-profiles/validation-t063.md).

Actualización 2026-10-21, matriz T063 completa: pasaron **14/14 casos reales nativos**, siete perfiles en cada dialecto, sobre9cc35033. Instalación/binding/catálogo exactos, capacidad API, reinicio con identidades estables, sellado elegante y cero raíces/capacidades inmediatamente tras borrar sesión; limpieza y estado del operador verificados. Mínimo6703MiB frente a6144MiB. Cierra T063 y las casillas acotadas OC-AUTH-03/OC1-02/04; no hubo tarea al modelo ni llamada de herramienta, por lo que mensajes, operaciones, autenticación efectiva y concurrencia siguen separados. [Matriz y límites](../specs/025-provider-mcp-profiles/validation-t063.md), [recibos sanitizados](../specs/025-provider-mcp-profiles/t063-native-catalog-receipts.json).

Actualización T060,2026-10-21: R11 sobre9cc35033 aceptó la entrada y secuencia1, pero agotó75s sin llamadas MCP ni mensajes PostgreSQL; no llegó al reinicio de API. Borrado inmediato sin raíces y limpieza completa verificados. El búfer final contenía un error de endpoint cuya aparición dentro del plazo no está fechada; no demuestra todavía la causa ni un defecto del clasificador. Los fallos R5/R6/R11 se conservan por separado. V2 R5 superó el arranque corregido, pero se detuvo antes de terminales/modelo al contrastar el perfil instalado; la discrepancia del verificador está en diagnóstico. [Recibos y límites](../specs/025-provider-mcp-profiles/validation-t060.md).

Actualización T064/T065,2026-10-21: la corrección de concurrencia Work y la separación de ejecución bloqueante ya pasan **414 pruebas Work/API** sobre el lote final integrado, tipado407, estilo11 y composición registrada5/0. Se conservaron y corrigieron los fallos de cancelación inicial/repetida; la segunda cancelación puede devolver al llamante manteniendo custodia de la limpieza, y el cierre de API la espera por completo. La prueba real de dos trabajadores Docker con cao.work.message/reinicio sigue pendiente, al igual que los mensajes nativos y los gates de escala/modelo/globales. [Concurrencia y fallos conservados](../specs/025-provider-mcp-profiles/validation-t064.md), [ejecución bloqueante y límites](../specs/025-provider-mcp-profiles/validation-t065.md).

El diagnóstico v2 actual conserva los fallos de verificador R5/R6 y su limpieza/retiro independiente de raíces; la variante preparada corrige permisos/metadatos y añade observaciones acotadas sin ampliar75s, con150 controles locales, pero permanece bloqueada hasta fijarla al commit final. Esto no es aceptación del proveedor ni cierra OC-05/06. [Cronología](../specs/025-provider-mcp-profiles/validation-t060.md).

Actualización2026-10-21, fuente publicada65e34150: el recorrido externo Work probó dos procesos Docker vivos, envío real cao.work.message, AG-UI, cierre de API y replay durable; falló al arrancar la generación de reemplazo. T052 solo la planifica, sin admitirla para ejecución; la admisión y procedencia por generación se implementan separadamente, sin saltarse permisos ni copiar orígenes. [Resultado parcial y límites](../specs/025-provider-mcp-profiles/validation-t064.md#published-source-and-actual-external-work-recheck). El recheck nativo v2 R8 falló por un nombre de sesión incorrecto en el validador, y el recheck v1 T058-R5 por un RuntimeError local cuya etapa no quedó registrada; sus fallos y limpiezas se conservan en sus documentos. Ninguno cierra mensajes/perfiles/escala/globales.


## Continuación Work T066/T067 — 2026-10-22

T067 está integrado y validado: orden canónico de bloqueos entre trabajos, retiros inmediatos solo cuando el mínimo de los trabajos posteriores lo permite, y agrupación lineal de retiros aplazados. Se conservan selección, capacidad, autoridad y CAS. Dos reproducciones deterministas PostgreSQL fallaron antes de la corrección y pasaron después; la regresión final pasó **486 pruebas en34 módulos**, tipado409, estilo13 y composición registrada5/0. Claude aprobó la revisión final del código en modo lectura. Su tarea de edición anterior agotó40 turnos y no se presenta como completada; sus resultados ejecutados y fallos previos se conservan. [Bloqueos y evidencia](../specs/025-provider-mcp-profiles/validation-t067.md).

T066 añade admisión explícita y autorizada de la generación de reemplazo, con procedencia inmutable V4 compatible con retención de eventos y limpieza posterior. `retry_work` sigue creando únicamente el intento planificado. El código está integrado y sus comprobaciones pasan, pero T066 y OC-05 mantienen pendiente la aceptación externa nueva con dos trabajadores, mensajes/ACK, reinicio y limpieza. Esta regresión no cierra perfiles/modelos nativos, escala ni gates globales. [Admisión y límites](../specs/025-provider-mcp-profiles/validation-t066.md).


Aceptación externa posterior, 2026-10-22: T064/T065/T066 ya completaron el recorrido Work real en main 2b3cb03c, **2 mensajes/2 ACK**, respuesta perdida sin duplicado, reinicio/replay AG-UI, generaciones2 del receptor y emisor, dos procesos reales simultáneos y limpieza propia completa. [Recibo sanitizado](../specs/025-provider-mcp-profiles/validation-t066-external-receipt.json). La preparación pendiente descrita arriba es el estado anterior a este recibo. OC-05 conserva pendiente el contrato genérico/nativo en la fuente final; OC-06 conserva escala y latencia de control. No se cierran los fallos históricos ni los gates globales.


## Corrección de descubrimiento T068 — 2026-10-22

La nueva ejecución nativa v2 R9b falló antes de mensajes: el terminal emisor recibió `group=null` aunque se envió el grupo correcto. La limpieza propia terminó completa y la autenticación del operador quedó intacta. Se identificaron dos omisiones de API: el cuerpo de creación de terminal adicional descartaba `group`/`metadata` y la ruta no los reenviaba al servicio. La corrección comparte los validadores existentes con la creación inicial y transmite ambos campos; conserva permisos, identidad y límites.

Doce casos HTTP fallaron antes del cambio; la regresión posterior pasó **471 pruebas en diez módulos**, estilo de ambos archivos, tipado409 y composición5/0, con aprobación de Claude en modo lectura. [Fallo real, corrección y límites](../specs/025-provider-mcp-profiles/validation-t068.md). T068/T011/T038 mantienen pendiente la aceptación nativa nueva tras publicar y actualizar los hashes del lanzador; no se elimina la comprobación del grupo ni se reutiliza el recibo fallido.


Reprueba nativa posterior a T068: R9c ya superó metadatos/grupo y readiness de ambos terminales, pero falló al iniciar la captura AG-UI antes de mensajes. Se reprodujo offline una precondición omitida del lanzador privado: limpia el entorno y desactiva Apps sin activar AG-UI, cuya ruta queda404. La configuración privada y el diagnóstico de etapa se corregirán sin cambiar el comportamiento por defecto del producto ni los criterios. Limpieza completa y autenticación intacta; aceptación nativa pendiente. [Detalle de R9c](../specs/025-provider-mcp-profiles/validation-t068.md#fresh-native-r9c-creation-fixed-ag-ui-setup-still-failed).


Reprueba nativa R9d, 2026-10-22: la activación privada de AG-UI ya pasó, junto con grupo, permisos y readiness de ambos terminales. El primer turno no completó el contrato: catálogos exactos, cero llamadas MCP, cero mensajes y captura final con marcadores de endpoint no disponible/error upstream. Claude revisó esa captura en modo lectura; no quedaron probados el origen y el instante exacto del marcador ni el código HTTP. Se detectó además un regex privado que omite «Endpoint is unavailable» y marcadores que pueden coincidir con el prompt. El candidato privado R9e ya corrige el regex y etiqueta el posible eco del prompt:197 controles heredados y35 nuevos pasaron también en verificación independiente, con aprobación final de Claude. Sigue cerrado, sin nuevo recorrido real v2 ni clasificación anticipada; conserva modelo,75 s,6.144 MiB y criterios. Limpieza completa y credenciales intactas. [Recibo fallido y límites](../specs/025-provider-mcp-profiles/validation-t068.md#fresh-native-r9d-provider-failure-before-mcp-dispatch). Las casillas de aceptación nativa siguen abiertas.


Reprueba v1 T060-R12, misma fuente publicada b6197720: se instalaron ambos perfiles y el receptor pasó creación/grupo/permisos/readiness, pero antes de crear el emisor quedaron **6.125 MiB frente al mínimo de6.144**. Se abortó sin cambiar el límite; no hubo fase de mensajes ni reinicio. DELETE200, cero raíces, procesos/API/tmux/targets propios limpiados y credenciales intactas. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t060.md#r12-current-source-memory-admission-stopped-before-sender). No se atribuye este bloqueo al proveedor ni al fallo histórico R11.
