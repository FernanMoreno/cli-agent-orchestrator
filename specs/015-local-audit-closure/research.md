# Research: Cierre de brechas locales de auditoría

**Fecha**: 2026-10-07\
**Spec**: [spec.md](spec.md)\
**Base revisada**: informe de estado 009–013, specs aprobados, AI_WORKFLOW.md y estructura local de Spec Kit.

## Decisiones

### R-001 — Conservar 009–013 como contratos de dominio

**Decisión**: El spec 015 concentra los cierres de auditoría y las pruebas entre dominios. Los specs 009–013 mantienen sus requisitos detallados y no se copian ni sustituyen.

**Rationale**: El informe ya identifica requisitos en cada spec para coordinación local, releases, eventos, workflows y plugins. El trabajo nuevo consiste en cerrar la evidencia y asegurar las fronteras donde varios subsistemas comparten estado o credenciales.

**Alternativas consideradas**:

- Crear otro diseño completo para cada área: descartado porque repetiría contratos que el usuario ya aprobó.
- Cambiar el objetivo a un servicio de coordinación central: descartado porque el producto debe seguir siendo local y gratuito.

### R-002 — No añadir infraestructura alojada ni dependencias nuevas por defecto

**Decisión**: Extender las capas, almacenamiento, perfiles y automatización existentes. Mantener peers en la misma PC. Una nueva dependencia solo se considerará si la inspección de código demuestra que no es posible satisfacer un requisito con las herramientas instaladas.

**Rationale**: El objetivo no necesita directorio, relay, marketplace ni cuenta CAO central. Las dependencias nuevas elevarían el costo de instalación y mantenimiento de una herramienta local.

**Alternativa considerada**: Delegar coordinación, confianza o emisión de tokens a un servicio remoto; descartada por alcance y funcionamiento local.

### R-003 — Tratar recibo y estado final como un mismo resultado

**Decisión**: Cada resultado terminal (éxito, fallo o cancelación) debe persistirse y exponerse como una transición coherente. La reserva temporal del proyecto se libera solo cuando el trabajo ya no puede escribir.

**Rationale**: La aceptación real reprodujo una tarea cuyo estado superior decía cancelada mientras el recibo decía en ejecución. La misma transición puede afectar API, MCP, reconciliación y liberación del proyecto.

**Alternativas consideradas**:

- Corregir solo el texto mostrado: descartado porque la persistencia y respuestas posteriores seguirían contradiciéndose.
- Liberar siempre el proyecto en cuanto llega una solicitud de cancelación: descartado porque el trabajador podría seguir escribiendo.

### R-004 — Evitar credenciales reutilizables en el transporte de eventos

**Decisión**: Usar autorización por sesión/header cuando el cliente lo permita. Si un cliente embebido no puede enviar esos headers, usar únicamente un ticket efímero, de un solo uso y limitado al flujo autorizado. Los cursores reanudan desde el último evento confirmado.

**Rationale**: Algunos clientes de eventos no pueden personalizar headers. Un ticket acotado permite autenticar ese transporte sin poner un bearer reutilizable en la URL.

**Alternativas consideradas**:

- Poner el bearer en la URL: descartado porque URLs pueden terminar en historial, diagnósticos o logs.
- Quitar autenticación al flujo de eventos: descartado porque expone información de trabajo.

### R-005 — Mantener la confianza de plugins bajo control local

**Decisión**: Mostrar evidencia de origen, identidad declarada, versión, compatibilidad, permisos e integridad del contenido. Comparar esa evidencia con la política local elegida por el operador. Marcar expresamente como no verificado lo que carezca de evidencia.

**Rationale**: Fijar un commit identifica el contenido solicitado, pero no identifica por sí solo a su autor. El operador debe poder decidir sin depender de un marketplace o cuenta central.

**Alternativas consideradas**:

- Tratar un pin de commit como identidad autenticada del publicador: descartado porque no demuestra quién lo publicó.
- Requerir una autoridad de confianza alojada: descartado por alcance local.
- Añadir una biblioteca criptográfica antes de revisar el formato y dependencias existentes: descartado hasta confirmar necesidad.

### R-006 — Hacer que los caminos de publicación compartan evidencia

**Decisión**: Los flujos manuales y automáticos consultan el mismo resultado de controles, ligado al contenido exacto. Los controles omitidos o que no pudieron ejecutarse permanecen visibles y nunca cuentan como aprobados.

**Rationale**: La auditoría encontró que el camino manual puede evitar precondiciones exigidas al automático y que algunos gates permiten omisiones silenciosas.

**Alternativa considerada**: Mantener permisos manuales como excepción global: descartado porque una publicación podría no corresponder al contenido revisado.

### R-007 — Separar fallo del producto de indisponibilidad del proveedor

**Decisión**: Una aceptación real que no puede ejecutarse por región, autenticación o disponibilidad del modelo se registra como bloqueada/no disponible, junto con el prerrequisito faltante. No cuenta como aprobado ni como fallo de CAO.

**Rationale**: OpenCode arrancó y aceptó la tarea, pero el modelo configurado indicó que no estaba disponible en la región. Ese resultado no mide la entrega final de la tarea.

## Verificación contra el código actual — 2026-10-07

### R-008 — Límites y políticas para implementación

Se concretan los puntos abiertos: autenticación Web con presupuesto total de 10 segundos; tickets de 30 segundos y uso único renovados en cada reconexión manteniendo cursor; backoff de observación 1/2/4/8/16/30 segundos y mantenimiento de 60 segundos tras seis fallos consecutivos. Los plugins no verificados se pueden revisar o instalar deshabilitados, pero requieren aprobación explícita del contenido exacto antes de habilitar y nunca reciben permisos automáticamente. Se conservan Python 3.10–3.14 y se amplía CI. Véase [plan.md](plan.md#precisiones-de-ejecución--2026-10-07).

La autorización del usuario para empezar el plan cubre estas precisiones reversibles. No se requiere investigación externa: son decisiones locales del producto contrastadas con sus fuentes.

### R-009 — Propietario autenticado durable

La inspección de rutas confirmó que comprobar scopes y existencia de terminal no asocia la asignación con el usuario autenticado: dos principales podrían presentar el mismo terminal. Se conserva el principal verificado en la tarea del origen mediante una columna nullable aditiva. La consulta/cancelación pública verifica este vínculo antes de contactar al destino; los registros anteriores sin propietario quedan limitados al operador local o administrador. No se intenta inferir propiedad desde metadatos editables ni se envía una credencial de usuario al par.

La migración es repetible y conserva datos; los lectores antiguos ignoran la columna, aunque revertir el código pierde la nueva comprobación de autorización. El contrato firmado del par mantiene identidad de origen, proyecto y terminal solicitante. Las sesiones requieren `session:read` explícito y los grants anteriores no se amplían automáticamente.

### R-010 — Publicar el mismo commit validado

El pipeline anterior creaba un commit de cambio de versión después de consultar los gates del SHA previo. Para mantener evidencia del contenido exacto, la versión se prepara mediante el flujo normal de CI y publicación comprueba que ya coincide con el candidato validado. Si requiere modificarlo, el preflight bloquea e indica preparar esa modificación antes de publicar. Se evita añadir ramas remotas temporales o una orquestación nueva de CI.

### R-011 — Equidad durante fallos de observación

Una regresión de más de seis páginas fallidas determina el comportamiento del cooldown: conserva la posición del inventario entre esperas de mantenimiento, para que el límite de reintentos no impida llegar a terminales posteriores. El cursor reinicia al completar el inventario o fallar su enumeración, sin reenviar entradas de agentes.

### R-012 — Declarar una dependencia de compatibilidad ya instalada

La revisión de plugins compara rangos de versiones PEP 440 mediante `packaging.specifiers` y `packaging.version`. `packaging` ya está resuelto en el lockfile e instalado transitivamente; se declara como dependencia directa conservando esa resolución. Esto evita que su retirada por una dependencia intermedia rompa el runtime. No se introduce una nueva biblioteca criptográfica ni un nuevo resolvedor de paquetes.

Graphify se encontraba desactualizado respecto al árbol de trabajo. Se usó solo para encontrar vecindades y cada conclusión se confirmó contra fuentes y pruebas actuales. Sus artefactos modificados previamente se conservaron.

- `src/cli_agent_orchestrator/services/local_peer_service.py`: la cancelación puede persistir el estado sin guardar el mismo recibo en `result_json`; esta incoherencia coincide con el defecto reproducido.
- `src/cli_agent_orchestrator/api/main.py` y `web/src/components/workflow/useEventFollow.ts`: el seguimiento de eventos de workflows ya cuenta con replay/cursor; no se debe reconstruir.
- `src/cli_agent_orchestrator/api/main.py`: AG-UI acepta bearer como parámetro `access_token` para EventSource y ya procesa cursores; la brecha pendiente es el transporte reutilizable en la URL.
- `cao_mcp_apps/src/event-stream/EventStreamView.tsx`: carga historial antes de abrir EventSource sin pasar un cursor común; un evento que llegue en esa transición necesita backfill.
- `cao_mcp_apps/src/shared/mcpApp.ts`: el primer mensaje correlacionado fija `hostOrigin` antes de verificar el origen esperado, y no se compara `event.source`.
- `web/src/api.ts` y `src/cli_agent_orchestrator/api/main.py`: el WebSocket de terminal admite `?token=`; `src/cli_agent_orchestrator/utils/logging.py` redacta `access_token` y `ticket`, no `token`.
- `web/src/auth.ts`: `authRequest()` usa fetch directamente y no impone su propio timeout.
- `tui/src/server.rs` y `src/cli_agent_orchestrator/utils/orchestration.py`: hay que verificar el destino antes de adjuntar el token local; el TUI sí rechaza redirecciones.
- `src/cli_agent_orchestrator/services/workflow_continuation_driver.py`, `terminal_observation_recovery.py`, `src/cao_workflow/__init__.py` y `models/workflow.py`: siguen las brechas de lotes, reintentos, vida de handles, precedencia de opciones y rechazo de campos desconocidos descritas por el spec.
- `.github/workflows/release.yml`: el preflight manual marca `should_release=true` sin ejecutar los prerrequisitos programados. `test/test_integration_008_release_policy.py` codifica ese comportamiento actual y debe cambiar con la regresión.
- `test/e2e/test_real_provider_matrix.py`: `_configured_auth_home()` vuelve a `Path.home()` cuando falta la variable aislada.
- `src/cli_agent_orchestrator/agent_plugins/resolver.py` fija commits completos y los registra, pero ese pin no verifica la identidad del publicador. El flujo CLI y sus registros necesitan exponer esa diferencia.

No se necesita investigación externa para elegir una nueva dependencia. La decisión de implementación de tickets o manifiestos debe reutilizar mecanismos y formatos locales existentes, y justificar cualquier dependencia adicional antes de introducirla.

## Hallazgos de convergencia posterior

El control global de tipos se reprodujo con 531 diagnósticos; se corrigen contratos de fuentes completas manteniendo la configuración efectiva de mypy.ini. La fuente y las pruebas, no el grafo previo ni un modo de comprobación supuesto, acreditan el cierre.

La enumeración y la lectura de proyecciones remotas son operaciones distintas. La desaparición entre ambas requiere manejar None en cada consumidor; un cast no prueba existencia. Los tipos de escalares de un payload remoto también requieren validación antes de devolver strings, enteros, booleanos o turnos como contratos de servicio. Se conservan los defaults y los resultados válidos existentes.

La recuperación portable no puede reactivar autoridad de pares. El catálogo actual 39 representa exactamente las tablas local_peer_* y bloquea cualquier fila de esas familias. Los catálogos históricos permanecen congelados. Un inventario de tablas no cierra por sí solo objetos SQLite ejecutables: el catálogo actual admite los cuatro triggers nativos cuyo DDL ya verifican Beads/continuación y ninguna vista nativa; los objetos Work siguen bajo las migraciones exactas. Se reproduce el rechazo con objetos inertes, SQLite real y bundles locales de hashes/publicación válidos, sin operaciones sobre sistemas externos.
