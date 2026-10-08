# Implementation Plan: Cierre de brechas locales de auditoría

**Branch**: 015-local-audit-closure | **Date**: 2026-10-07 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from specs/015-local-audit-closure/spec.md

**Ampliación de cierre**: el usuario solicita ejecutar las validaciones pendientes
de versiones Python, aislamiento y proveedores, y terminar con commits y push
a `main` de su fork. La fase 10 de tasks.md registra esta ejecución sobre un
candidato aislado y exige resultados reales, bloqueos explícitos y comprobación
del SHA remoto. Publicación de releases/paquetes queda fuera de este cierre.

## Summary

Cerrar los hallazgos de auditoría que atraviesan coordinación entre instancias CAO, ciclo de tareas, eventos, workflows, publicación, pruebas con proveedores, plugins y gates de mantenimiento. La solución extiende los flujos locales existentes, conserva los specs 009–013 como contratos de cada dominio y no añade un servicio alojado ni una nueva cuenta central.

La entrega se divide por historias: primero coherencia del estado de tareas; después coordinación y eventos; luego fiabilidad de workflows; publicación y pruebas aisladas; y finalmente confianza de plugins. Cada historia requiere pruebas de regresión propias y una aceptación integrada que verifique estados, permisos, credenciales y resultados visibles entre sus fronteras.

## Technical Context

**Language/Version**: Python 3.10 o superior según pyproject.toml; TypeScript/JavaScript para Web y MCP Apps; Rust para TUI; YAML para automatización.

**Primary Dependencies**: Mantener las dependencias existentes. Backend/API y lógica CAO en Python; herramientas de interfaz en los ecosistemas ya presentes. No introducir dependencias de runtime hasta que la inspección de código demuestre que son necesarias.

La compatibilidad declarada de plugins usa `packaging`, ya presente en el lockfile e instalado transitivamente. Se declara como dependencia directa para no depender de que otra biblioteca lo siga trayendo; se conserva su versión resuelta. La biblioteca estándar no ofrece un parser equivalente de especificadores PEP 440.

**Storage**: Persistencia local existente para tareas, resultados, autorizaciones, eventos y estado de workflows. La inspección de T018 exige ampliar la tarea local con `requester_principal_id` nullable: migración SQLite aditiva e idempotente; los registros anteriores se conservan. No se elimina ni reescribe evidencia previa.

**Testing**: Pruebas Python con pytest; pruebas Web y MCP Apps con sus scripts npm; pruebas Rust con Cargo; escenarios locales de coordinación entre procesos. project-composition-check es el gate de composición configurado.

**Target Platform**: Una PC del usuario, con instancias CAO ejecutándose en el mismo entorno local y coordinando el mismo proyecto. Las herramientas de proveedor conservan sus propios procesos de autenticación.

**Project Type**: Herramienta local con CLI, API, servicios, Web, TUI, MCP Apps y automatización de mantenimiento.

**Performance Goals**: Las tareas elegibles deben avanzar en rondas sucesivas; los reintentos persistentes deben detenerse dentro de límites configurados; la reanudación de eventos no debe producir huecos ni duplicados visibles. No se fija un SLA de servicio alojado.

**Constraints**: Producto gratuito y local; sin servicio CAO alojado, relay o directorio central. La aceptación debe funcionar con dos o más instancias CAO en una PC. Secretos, resultados y evidencia se mantienen dentro de los perfiles locales pertinentes. Cambios existentes en el árbol de trabajo deben preservarse.

**Scale/Scope**: Cinco historias del spec, 19 requisitos funcionales, superficies Python, Web, MCP Apps, TUI y CI. Una PC; no incluye coordinación CAO entre PCs.

## Constitution Check

| Principio | Resultado | Aplicación al plan |
|---|---|---|
| I. Evidencia antes de conclusiones | Pasa con condición | La auditoría previa documenta el error de recibo reproducido y separa de él los hallazgos estáticos. Antes de implementar se vuelven a contrastar rutas y contratos contra el árbol actual. |
| II. Preservación del trabajo y del alcance | Pasa | Se añaden artefactos de planificación bajo specs/015. No se sobrescriben cambios de producto existentes ni se publican, hacen commits o se modifican proyectos externos. |
| III. Diseño y diagnóstico antes de implementación | Pasa | La especificación está aprobada por el usuario. Las tareas exigirán reproducción o prueba de regresión antes de corregir cada comportamiento. |
| IV. Verificación de composición | Pasa con condición | El cierre debe probar estado/recibo/reserva, permisos/eventos/clientes y gates/publicación, además de las pruebas aisladas. |
| V. Cierre honesto y conocimiento durable | Pasa | El plan distingue pruebas, aceptación real, controles omitidos y bloqueos por disponibilidad del proveedor. No declara implementación completada. |

**Gate inicial**: pasa. No hay una violación constitucional conocida que requiera añadir una nueva capa, servicio o dependencia. La revisión de código posterior a Graphify debe confirmar los límites antes de tocar implementación.

## Design Decisions

1. **Los specs 009–013 siguen siendo la fuente detallada por dominio.** El 015 coordina los cierres pendientes y las pruebas entre dominios; no reescribe los flujos ya aprobados.
2. **El estado de tarea y su recibo son una sola transición observable.** Toda ruta de éxito, error y cancelación debe persistir y devolver el mismo resultado terminal. Una reserva del proyecto se libera solo cuando el trabajador ya no puede escribir.
3. **La continuidad de eventos usa cursores confirmados y autorización vigente.** La línea temporal de workflows y su cliente Web ya implementan cursor/replay; ese comportamiento se conserva. Las brechas pendientes son el traspaso historial-suscripción de MCP Apps y el uso de bearer reutilizable en algunos flujos nativos. Para clientes que no pueden enviar headers se usará una sesión o ticket breve, de un solo uso y limitado al flujo; no se pondrá un bearer reutilizable en una URL.
4. **La confianza es local y explícita.** Verificar contenido contra una identidad o referencia confiable disponible localmente; mostrar como no verificada la procedencia sin evidencia y respetar la política del operador. No depender de un marketplace ni de un servicio central.
5. **Publicación y pruebas usan evidencia ligada al contenido exacto.** Los caminos manual y automático comparten el mismo gate; las pruebas de proveedor fallan de forma visible si no hay perfil aislado.
6. **No se agregan dependencias por anticipado.** Se reutiliza el lockfile, los metadatos, los perfiles y el pipeline existentes. El manifiesto de artefactos puede generarse con la biblioteca estándar; si la implementación demuestra que se necesita otra herramienta, se documentará antes de añadirla.

## Verificación contra el árbol actual

La inspección posterior a Graphify se contrastó con las fuentes y pruebas actuales. Graphify estaba desactualizado, por lo que sus relaciones se usaron como orientación y no como prueba. Los siguientes comportamientos ya existen y no deben duplicarse:

| Área | Implementado en el árbol actual | Brecha que queda en el spec |
|---|---|---|
| Eventos de workflows en Web | `GET /workflows/runs/{id}/events` soporta replay/cursor y `web/src/components/workflow/useEventFollow.ts` continúa desde el cursor | Conservar este contrato mientras se cierran las demás superficies de eventos |
| Eventos AG-UI | El endpoint admite cursor/reconexión y redacta `access_token` en logs | Autenticación de EventSource no debe depender de bearer reutilizable en URL |
| Peers locales | Identidad, firma, scopes, nonces, loopback y leases ya tienen implementación y cobertura | Cancelación puede guardar `state=cancelled` sin `result_json`; faltan pruebas de rutas y algunos casos de sesión/propietario |
| MCP Apps | Hay hidratación de historial, deduplicación de eventos y pin del primer origen correlacionado | La primera respuesta aún puede elegir el origen; falta comprobar `event.source` y cerrar la carrera entre historial y live |
| Credenciales de cliente | Web evita redirecciones en solicitudes autenticadas; TUI evita reenviar el bearer en redirecciones | WebSocket aún permite `?token=`, redacción no cubre `token`, `authRequest()` no tiene timeout propio y TUI/orquestación requieren una regla explícita de destino |
| Workflows | Hay recuperación y validación existentes | El driver aún puede sesgar lotes, retiene handles hasta apagado, la observación puede reintentar demasiado pronto y opciones pueden reemplazar campos administrados |
| CI, proveedores y plugins | Hay gates, perfiles y pins de contenido | El preflight manual de release omite prerequisitos programados; proveedor puede recurrir a `Path.home()`; faltan gates de matriz/dependencias y evidencia de publicador de plugin |

Las nuevas rutas de prueba declaradas en `tasks.md` se crearán solo para contratos que hoy no tienen una prueba de ruta. El código existente y el árbol de trabajo ya contienen cambios previos extensos; cualquier implementación debe preservar esos cambios y revisar el diff antes de tocar las fuentes.

## Decisión TDD

El modo de implementación será tests-first para cada historia: primero añadir o ajustar la regresión en la ruta indicada por `tasks.md`, confirmar que falla por el defecto esperado y después modificar la capa propietaria. No ejecutar ni afirmar pruebas durante esta etapa de planificación. Las pruebas de dos procesos, proveedores y composición se reservan para sus tareas de aceptación.

## Implementation Order

### Precisiones de ejecución — 2026-10-07

- **Recuperación de observación**: espera exponencial de 1, 2, 4, 8, 16 y 30 segundos, con techo de 30 segundos. Tras seis fallos consecutivos se termina la ronda fallida y se vuelve a intentar en el mantenimiento de 60 segundos, conservando el cursor de páginas para no postergar los terminales posteriores. El fin del inventario o un fallo al enumerarlo reinicia el cursor; un avance correcto reinicia el contador. Nunca se reenvía la entrada del agente para recuperar observación.
- **Autenticación Web**: un presupuesto total de 10 segundos por solicitud, incluyendo la lectura del cuerpo, con cancelación al cambiar la generación de autenticación. Timeout señala indisponibilidad; no revocación de la sesión.
- **Tickets de eventos y terminal**: vencimiento de 30 segundos, uso único y vínculo con principal, ámbito y recurso. Se emiten mediante una solicitud autenticada. Cada reconexión solicita un ticket nuevo y conserva el último cursor confirmado; nunca reutiliza el ticket consumido. Se verifican los permisos actuales, incluida revocación, en el transporte.
- **Plugins**: la política predeterminada permite inspeccionar y almacenar deshabilitado un plugin sin procedencia verificada. Habilitarlo exige aprobación local explícita del contenido exacto y revisión de permisos; contenido alterado o incompatible se rechaza. Un pin no verifica al productor y la aprobación de confianza no concede permisos de ejecución.
- **Python**: conservar las versiones anunciadas 3.10–3.14 y ampliar la matriz CI para verificarlas todas; no reducir compatibilidad para satisfacer el gate.
- **Par no disponible**: añadir una regresión explícita de consulta de tarea, además de las consultas de sesiones, que conserve el recibo ya conocido y distinga indisponibilidad de ausencia de resultado.
- **T047**: ampliar los casos de permisos existentes con procedencia ausente, cambios después de revisión y aprobación de contenido exacto; no duplicar la cobertura actual de no concesión automática.
- **Propietario autenticado (T018)**: el origen conserva el principal verificado que creó la asignación. Consultas y cancelaciones públicas comprueban ese vínculo antes de contactar al par. Un registro anterior sin vínculo queda restringido al operador local o administrador; la identidad no se acepta del cuerpo ni se transmite como credencial al par. El proyecto solicitado debe coincidir con el proyecto del terminal solicitante.
- **Compatibilidad de almacenamiento**: `requester_principal_id` se añade sin destruir datos y con migración repetible. Lectores antiguos ignoran la columna; volver a una versión antigua conserva los datos, pero pierde la nueva garantía de autorización, por lo que no constituye una reversión segura de esa garantía. No se hace contracción del esquema.
- **Sesiones de pares**: una capacidad explícita `session:read` controla el snapshot firmado por proyecto. Los permisos anteriores que no la incluyen no se amplían automáticamente.
- **Candidato de publicación**: la versión debe estar preparada en el commit que superó CI y los controles de seguridad. El preflight bloquea si la versión propuesta exige cambiar metadatos después de validar; no se genera un commit distinto entre la aprobación y la construcción. Los cambios de versión siguen el flujo normal de revisión y CI.

Estas decisiones concretan el diseño aprobado. La evidencia por historia registra cualquier ajuste exigido por los contratos actuales del código antes de considerar terminado el requisito.

1. Ejecutar las regresiones nuevas contra las fuentes actuales; separar defectos reproducidos de riesgos estáticos.
2. Implementar US1: estado, recibo, resultado y reserva de proyecto, incluyendo carreras entre cancelación y finalización.
3. Implementar US2: autorización de pares y sesiones, brecha historial-suscripción de MCP Apps, origen de mensajes y credenciales de AG-UI, WebSocket, Web, TUI y llamadas entre hosts.
4. Implementar US3: equidad del avance, backoff acotado, limpieza de tareas y validación de configuración de workflows.
5. Implementar US4: gate idéntico para publicación manual/programada, aislamiento de proveedores, controles CI y manifiesto de artefactos.
6. Implementar US5: evidencia de procedencia, integridad y permisos visible en el flujo local de plugin.
7. Ejecutar aceptación de dos o más instancias CAO en una PC; completar suites y revisión de composición.

US1 es la primera entrega mínima: corrige un error de recibo demostrado y permite comprobar un resultado útil sin depender de proveedor externo. US2 y US3 comparten coordinación/estado y se completan antes de la aceptación integrada. US4 y US5 son incrementos independientes que cierran los riesgos de mantenimiento y distribución local.

## Project Structure

### Documentation (this feature)

- specs/015-local-audit-closure/plan.md — este plan.
- specs/015-local-audit-closure/research.md — decisiones y alternativas.
- specs/015-local-audit-closure/data-model.md — invariantes de entidades locales.
- specs/015-local-audit-closure/contracts/audit-closure.md — contratos observables entre subsistemas.
- specs/015-local-audit-closure/quickstart.md — guía de validación.
- specs/015-local-audit-closure/tasks.md — tareas ordenadas por historia.

### Source Code (repository root)

- src/cli_agent_orchestrator/services/ — peers locales, tareas, workflows y recuperación.
- src/cli_agent_orchestrator/api/ — rutas de coordinación, autorización y eventos.
- src/cli_agent_orchestrator/mcp_server/ — herramientas y transporte MCP de coordinación.
- src/cli_agent_orchestrator/agent_plugins/ — resolución y confianza de plugins.
- src/cao_workflow/ y src/cli_agent_orchestrator/models/ — opciones e identidad de workflows.
- web/src/ — autenticación, API, eventos y estados de interfaz Web.
- cao_mcp_apps/src/ — flujo de eventos y mensajes de interfaz embebida.
- tui/src/ — solicitudes locales y transporte autenticado del TUI.
- .github/workflows/ y .github/dependabot.yml — gates de release, pruebas de proveedores y actualizaciones.
- test/services/test_local_peer_recovery.py, test/services/test_work_reservations.py, test/services/test_local_peer_auth.py y test/mcp_server/test_local_peer_transport.py — regresiones de peers y tareas.
- test/api/test_local_coordination_routes.py — nueva cobertura de rutas locales y snapshots de sesiones.
- test/api/test_agui_auth_hardening.py, test/api/test_agui_stream_reconnect.py, test/api/test_ws_auth.py, test/utils/test_logging.py — autorización, transporte y redacción.
- web/src/test/, cao_mcp_apps/src/test/ y tui/src/server.rs — comportamiento de clientes locales.
- test/services/test_continuation_responsiveness.py, test/services/test_terminal_observation_recovery.py y test/models/test_workflow.py — fiabilidad de workflows.
- test/test_integration_008_release_policy.py, test/test_integration_008_ci_policy.py y test/e2e/test_real_provider_matrix.py — publicación, CI y proveedores.
- test/agent_plugins/ — resolución, procedencia, confianza y permisos de plugins.
- test/scripts/test_build_release_manifest.py — nueva prueba del manifiesto de artefactos.

**Structure Decision**: Se extienden los subsistemas existentes. Los dos archivos de prueba nuevos nombrados en tareas cubren rutas de coordinación y el manifiesto de distribución; el ticket de eventos se limita al proceso CAO local. No se presupone una migración de datos ni un servicio central.

## Cross-Subsystem Acceptance

- **Cancelación y tarea**: confirmar que almacenamiento, API/MCP, estado visible y reserva del proyecto coinciden después de completar, fallar, cancelar, desconectar y reconciliar.
- **Eventos y autenticación**: publicar eventos mientras Web/MCP Apps se desconectan; reconectar desde el cursor confirmado; revocar autorización durante una conexión; comprobar que un origen inválido no resuelve solicitudes.
- **Workflow y peers**: procesar una cola mayor que un lote con tareas no elegibles mezcladas; perder almacenamiento y un proceso par durante consultas; validar estados, reintentos y sesiones resultantes.
- **Release y proveedores**: probar publicación manual y automática con el mismo contenido validado y con contenido cambiado; iniciar un proveedor solo con perfil aislado y revisar limpieza en éxito, fallo y cancelación.
- **Plugin y artefacto**: probar plugin verificado, cambiado, incompatible y sin procedencia; comprobar política y evidencia de versión/inventario de componentes.

## Risks and Mitigations

| Riesgo | Mitigación |
|---|---|
| El árbol de trabajo tiene muchos cambios y Graphify estaba desactualizado | Verificar rutas en fuentes actuales; editar solo archivos de implementación acordados y preservar los cambios existentes. |
| Una cancelación y un recibo se actualizan en almacenamientos diferentes | Definir una única transición observable, cubrir fallo parcial y consultar la respuesta externa tras recuperación. |
| Navegadores limitan credenciales en EventSource | Preferir auth por header donde exista; en caso contrario, ticket efímero y de un solo uso ligado al permiso y flujo. |
| Un proveedor no está disponible por región o cuenta | Registrar el caso como bloqueado/no disponible, no como pase o defecto de CAO; mantener aceptación simulada/local para el resto. |
| La procedencia de plugins no tiene raíz de confianza central | Mantener la raíz de confianza local y explícita; no afirmar que un pin por sí solo identifica al publicador. |
| Pruebas manuales pueden omitir casos automatizados | Compartir gate de publicación y probar ambos disparadores sobre el mismo candidato. |

## Complexity Tracking

No se propone nueva infraestructura ni servicio remoto. Se declara directamente `packaging`, ya resuelto, y se necesita la ampliación aditiva de propietario autenticado descrita en T018. La complejidad está en coordinar contratos entre subsistemas; se contiene mediante incrementos por historia y criterios de aceptación cruzados.

## Convergencia solicitada — controles globales y recuperación

El usuario amplía el alcance para corregir todos los bloqueos locales de verificación. T057–T064 conservan las reglas de CI y resuelven los contratos de tipos existentes; `mypy.ini` es la configuración efectiva del comando obligatorio, sin habilitar nuevas supresiones. La revisión adicional cubre desaparición concurrente de proyecciones remotas y validación de los escalares recibidos antes de devolverlos como tipos de servicio.

La suite ampliada detecta una frontera adicional: el catálogo cerrado de recuperación debe representar las tablas actuales de pares/proyectos sin modificar los catálogos históricos. El catálogo actual 39 enumera exactamente esas tablas y clasifica su autoridad como no portable: una tabla de pares con filas bloquea captura y restauración, evitando reactivar emparejamientos, grants o replay de otra instancia. Se mantiene el schema de Work y se prueban catálogos anteriores y bundles con hashes correctos pero autoridad no admitida.

## Convergencia del catálogo CLI/TUI

El control ejecutable bidireccional detecta nueve hojas Click añadidas por coordinación y confianza que faltan en el catálogo Rust: seis `peer` y `plugin disable/enable/review`. El árbol real tiene 141 comandos frente a 132 declarados. Se completa la representación cerrada (`CommandId`, orden, fila y ruta) con la política predeterminada HIDE de la TUI, sin incorporar una nueva interfaz interactiva ni ampliar su autoridad. Los tests Python comparan ambos árboles y los tests Rust verifican variantes, clasificación, rutas y cardinalidad antes del cierre de T064. Graphify se consultó y sus referencias se contrastaron con `catalog.rs` y `server.rs`; el snapshot final debe incluir esas fuentes.

## Convergencia del ratchet de cobertura

La selección completa de diagnóstico mide Python en 85.53% y MCP Apps en 90.64%; el mínimo Python de 87% sigue vigente. T067 amplía pruebas funcionales de fronteras poco ejercitadas (ciclo local de pares, contratos de backend, procesos y snapshots privados). Se conservan el baseline, las inclusiones y los controles reales. Las pruebas emplean SQLite, archivos y procesos temporales cuando corresponda, y simulan únicamente efectos nativos externos; no sustituyen pruebas host/proveedores ni se acreditan como tales. El gate final necesita ambos informes reales y una ejecución completa posterior a las correcciones.

La validación final detectó que el migrador de `workflow_index` usa el contexto transaccional de SQLite sin cerrar la conexión que crea. En Python 3.13, los avisos retenidos por pytest conservan esos objetos y elevan el consumo de memoria. T077 añade cierre explícito después del commit/rollback, sin cambiar SQL, esquemas, checksum, política de errores ni formatos persistidos. La regresión comprueba ambas salidas con una conexión SQLite real. El rollback de código restaura el contexto previo; no hay migración de datos nueva ni transición de lectores/escritores.

### Signature-boundary convergence (T078)

Actual verification accepted all-zero/identity small-order public keys with
forged signatures in bounded probes. Restrict peer key enrollment and legacy
grant verification to canonical encodings without known small-order points,
using the published libsodium point-encoding reference. Keep cryptography
responsible for signature verification; introduce no cryptographic primitive
or dependency. Generated CAO keys and signed request material remain compatible.
Use real generated keys in the separate invalid-signature fixture; cover weak
key rejection, both sign-bit encodings, noncanonical encodings, legacy grants
and legitimate pairing/authentication before restarting final acceptance.

### Remaining startup-owned connections (T079)

An actual isolated Python 3.13 init_db allocation probe identified 20 other
migration-owned connections, the private Beads schema comparison database and
the private expected Work schema database. Two startup calls produced 43
ResourceWarnings and retained memory; T077 no longer appears in the trace.
Close only these observed owners using the existing transaction context inside
closing. Preserve SQL, checksums, caller-owned connections and failure policy.
Regression tests cover migration commit/rollback before close, expected catalog
equality and Beads borrowed-connection validity on success/error. No data
migration or policy relaxation is necessary. The external matrix runner also
keeps worker TMPDIR as an ancestor of pytest basetemp while HOME/CAO_HOME/tmux
remain isolated; verify containment/profile composition before whole reruns.

### T080 — propiedad de conexiones del journal

Las consultas reales `get_run()` y `read_events()` demostraron 200 avisos de conexiones abiertas en 200 llamadas. Los contextos transaccionales de `_connect()` / `_connect_event()` no cierran el handle. Se añadirá cierre externo en sus operaciones propietarias, conservando SQL, commit/rollback, tipos devueltos y los caminos con conexiones prestadas. Las pruebas de éxito/error preceden al cambio; la matriz incompleta anterior se conserva como diagnóstico y se repite sobre un nuevo freeze.

El inventario adicional T080 incluye 55 operaciones propietarias y tres
aperturas fallidas antes del handoff (browser, Work y migración de terminales).
El cierre de terminales respeta commits por columna; las conexiones prestadas
y los resultados materializados conservan su contrato. El freeze sólo cambia
propiedad de recursos, con 34 regresiones nuevas y revisión independiente.

### T081–T083 — composición de fixtures, cancelación y Python 3.10

El servidor de prueba reemplaza HOME y CAO_HOME_DIR juntos antes de importar
la aplicación; los overrides explícitos permanecen posteriores. Una regresión
con proceso hijo y el rechazo de escape por HTTP real verifican aislamiento
sin cambiar selección de perfiles ni la exclusión de propietarios en producción.

Una cancelación coincidente con el fin del envío o del resultado debe propagarse
en todas las versiones anunciadas. La espera limitada posee y drena su hijo,
preserva el plazo cero sin envío y mantiene reconciliación y compensación
persistentes. Las dos carreras se reproducen antes de sustituir wait_for en esas
fronteras; las pruebas existentes de backpressure y plazo siguen vigentes.

Los preflights Docker usan tomllib con el fallback tomli ya declarado para
Python 3.10. Los cuatro fallos reproducidos de políticas de workspace y MCP
initialize preceden al cambio; no se altera el lock ni se omiten pruebas.
El freeze de aceptación incorpora los 21 scripts ejecutables rastreados además
de las fuentes y pruebas, para vincular también estas correcciones a sus bytes.

### T085/T086 — seguridad del parser y observación de rollback

El lint no puede invocar el parser C con complejidad capaz de derribar el host.
Un preanálisis estándar limita cada sentencia lógica a 1.024 tokens relevantes;
conserva scripts largos con sentencias normales, comentarios y literales planos.
En Python anterior a 3.12, el contenido opaco de f-strings se limita de forma
conservadora a 4.096 caracteres. Los rechazos usan syntax/error existente;
no se ejecuta el target ni se añaden procesos o dependencias al lint. La cadena
adversarial de 400 KB se conserva en una regresión aislada por proceso hijo.

La prueba de autorización durable observa la transacción entre el rollback y
el cierre delegado original. Debe seguir exigiendo autorización activa,
rechazo por mismatch, estado/contador intactos y conexión cerrada después.

### T087: observabilidad del hijo antes de liberar su capacidad

La prueba del canal privado debe esperar hasta un segundo cuando `/proc/<pid>/environ` todavía está vacío durante el arranque. Sólo la fixture espera; el runtime conserva registro durable antes de escribir la credencial al FD. La prueba rechaza inmediatamente un entorno observable sin FD, la credencial en el entorno o un proceso salido; conserva las verificaciones SQL, el límite exterior y el reap real.

### Alcance del mínimo de cobertura en la matriz final

La matriz de compatibilidad de CI ejecuta la selección completa en Python 3.10–3.14 y registra su cobertura individual; no impone `--cov-fail-under` ni ejecuta el ratchet en cada celda. El control obligatorio de cobertura se ejecuta en el job MCP Apps con Python 3.12, usando un único `coverage.json` y el informe frontend, con mínimos existentes de 87% y 90%. T069 conserva las cinco ejecuciones y sus porcentajes reales; no añade cinco controles distintos del 87%.

Ruling: el chequeo adicional del runner temporal que comparó cada porcentaje individual con 87% se conserva como diagnóstico, sin convertirlo en una política de publicación nueva. Python 3.14 aprobó la suite completa y midió 86.98323713788628%; ese diagnóstico adicional falló y no se acredita como aprobado. El análisis real del mismo archivo mostró que Python 3.14 deja de contar campos anotados como sentencias ejecutables (118 frente a 34 en `workflow_managed.py`), reduciendo el denominador global en 801 líneas. El ratchet obligatorio de Python 3.12 aprobó con 87.1310926894555% y MCP Apps con 90.01%, sin cambios de mínimos, exclusiones ni selección. Si esta interpretación fuera incorrecta, habría que ampliar la política explícita de CI y obtener nueva evidencia para ese control adicional.

## Ajuste de fixtures tras CI remoto — T088/T089

La primera publicación confirmó que la aceptación local dependía de dos capacidades ausentes en ubuntu-latest: un binario kiro-cli visible en PATH de la fixture de proveedor simulado y permisos reales de namespaces user/net/IPC/PID. El backend de MCP Apps registró 8 FAIL/16569 PASS/127 SKIP/23 deselected en 3657.69 s. La aceptación QEMU obligatoria sí aprobó los ocho casos reales.

Se conserva producción: el preflight Kiro rechaza un CLI ausente y Work rechaza namespace uid_map denegado antes del lanzamiento. T088 usa el seam de capabilities ya declarado cuando la propia fixture sustituye Provider.initialize; no necesita cuenta ni convierte pruebas unitarias en proveedores reales. T089 prueba exactamente la capacidad de producción antes de los seis casos nativos y registra SKIP explícito sólo por capacidad reconocida; no se modifica el job QEMU obligatorio ni se omiten regresiones de rechazo. Las reproducciones RED preceden los cambios; la fuente T087 validada conserva su identidad y los resultados posteriores se ligan a un manifiesto nuevo.

T090 corrige únicamente la fixture local de memory HTTP: el shim E2E cambia constants.API_BASE_URL durante la sesión sin cambiar el alias importado por el cliente. Se reproduce el orden antes de fijar una autoridad local explícita en el seam del consumidor; la aserción independiente del destino, bearer y rechazo de bypass conserva el contrato. La revisión y 175 vecinos pasan; el hook completo posterior y CI siguen pendientes.

## T091 — capacidades nativas del runner y cobertura

Diagnóstico: CI T090 pasó 16579 casos pero midió 86.77% frente al mínimo 87%; idénticas 73223 sentencias, 261 cubiertas menos que el host local validado. El supervisor aporta 223 de esa diferencia; un módulo nativo real ejecutado nuevamente (38 PASS, cero SKIP) cubrió 221 sentencias faltantes de CI. Esta atribución no mezcla informes ni acredita una nueva cobertura global.

Se prepara un helper exclusivo de CI compartido por matriz Python y cobertura backend. Primero registra identidad acotada del host/probe y ejecuta el probe real de producción. Si falla únicamente por denegación namespace exit1 EPERM/EACCES, y se observa key AppArmor userns=1 en un runner Ubuntu GitHub-hosted desechable, permite un único ajuste temporal del mismo key a0; exige el probe real exitoso posterior. En otros hosts/errores/key ausente/valor0/timeout/fallo de sudo o probe, falla. No desactiva AppArmor globalmente, seccomp/Yama ni eleva los tests. Conserva selección, versiones, mínimos87/90 y QEMU estricto8.

El ajuste dura sólo la ejecución de las pruebas. Antes de restaurarlo/verificarlo en finally, el helper confirma que su PGID/SID no conserva procesos vivos, incluso si el líder ya terminó; TERM y KILL tienen plazos acotados. Un drenaje no confirmado bloquea la restauración, falla explícitamente y deja la eliminación final de estado a la VM desechable. El estado e informe temporal propios se borran tras uso. La causalidad AppArmor del runner concreto sólo se acreditará si CI imprime denegación previa, cambio observado y probe exitoso posterior. La VM desechable elimina el estado residual si hay SIGKILL; no se promete finally frente a SIGKILL. Antes del helper se escriben pruebas RED de ausencia de ajuste cuando ya es apto, rechazos, fallo postprobe, ejecución no elevada y restauración en éxito/fallo/cancelación. Después se requieren revisión independiente, composición, diff, hook y CI reales.
