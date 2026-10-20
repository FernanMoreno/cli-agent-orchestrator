# Feature Specification: Persistencia completa en PostgreSQL

**Feature Branch**: rama actual conservada; feature 021-postgresql-persistence
**Created**: 2026-10-11
**Status**: implementación y validación en curso; aceptación completa pendiente. Véanse tasks.md, validation.md y composition-review.md.
**Input**: «migremos todo a postgres», para varias instancias CAOS y agentes concurrentes.

## User Scenarios & Testing

### User Story 1 — Todas las funciones conservan su estado (Priority: P1)

El operador ejecuta sesiones, agentes, mensajes, workflows, Work, memoria, conocimiento, autenticación, permisos e interacciones con PostgreSQL como almacenamiento relacional de runtime.

**Why this priority**: cubrir todas las funciones solicitadas.
**Independent Test**: instalación nueva, recorrido funcional, reinicio y comprobación del estado conservado.
**Acceptance Scenarios**:

1. **Given** una instalación nueva, **When** se configura e inicia, **Then** todas sus funciones persistentes operan sin crear bases SQLite de runtime.
2. **Given** almacenamiento indisponible, **When** se intenta una operación durable, **Then** el fallo se comunica sin guardar estado alternativo divergente.
3. **Given** preguntas y trabajo activos, **When** reinicia la aplicación, **Then** recibos, permisos y estados de recuperación se conservan.
4. **Given** un workflow YAML con historial durable y un trigger inmutable requerido ausente, **When** se intenta reanudar, **Then** el esquema alterado se rechaza con el 422 existente de historial corrupto antes de invocar agentes, y los errores transitorios de PostgreSQL conservan su clasificación.

### User Story 2 — Recuperar estado PostgreSQL de forma segura (Priority: P1)

El operador restaura un backup PostgreSQL nativo a un destino aislado, lo verifica y sólo entonces lo activa. CAOS no importa SQLite ni convierte archivos o bundles SQLite.

**Why this priority**: conservar estado y autoridad con un único formato relacional soportado.
**Independent Test**: capturar los tres scopes PostgreSQL, restaurarlos en un destino distinto y comparar datos, esquema, recibos históricos y permisos antes de activar.
**Acceptance Scenarios**:

1. **Given** un backup PostgreSQL admitido, **When** se restaura en un destino físicamente distinto, **Then** los datos, relaciones, secuencias, recibos históricos y fingerprints se verifican antes de activar y el backup original permanece intacto.
2. **Given** un backup incompleto, incompatible o alterado, **When** se intenta restaurar, **Then** se rechaza antes de activar y se preservan el backup, los archivos existentes y los datos PostgreSQL del destino.
3. **Given** una ruta con estado existente desconocido o un bundle legacy con `sqlite-dump.json`, **When** una operación intenta interpretarlo como estado soportado, **Then** falla cerrada antes de modificar archivos o PostgreSQL.
4. **Given** un usuario invoca un comando u opción retirados de importación SQLite, **When** se analiza la orden, **Then** CAOS la rechaza antes de abrir rutas o conectarse a PostgreSQL.
5. **Given** un bundle OKF moderno en directorio o tar, **When** se procesa mediante su flujo soportado, **Then** conserva su comportamiento sin requerir ni aceptar payloads SQLite.

### User Story 3 — Operaciones concurrentes mantienen autoridad (Priority: P1)

Varias conexiones ejecutan operaciones manteniendo unicidad de respuestas, asignaciones, claims y resultados.

**Why this priority**: concurrencia es motivación del cambio.
**Independent Test**: carreras entre procesos/conexiones, reinicio e inspección del estado durable.
**Acceptance Scenarios**:

1. **Given** respuestas simultáneas, **When** intentan aceptarse, **Then** sólo una es efectiva por versión y los reintentos mantienen su recibo exacto.
2. **Given** un efecto reclamado sin confirmación, **When** se recupera, **Then** conserva la incertidumbre sin repetición automática.
3. **Given** perfiles independientes, **When** comparten infraestructura con ámbitos distintos, **Then** no acceden al estado privado ajeno.
4. **Given** un stream AG-UI autorizado con `cao:write`, **When** los permisos de esa sesión se reducen a `cao:read` mientras el stream permanece conectado, **Then** el siguiente snapshot/delta publica sólo los permisos actuales y deja de ofrecer controles de escritura.
5. **Given** el driver de continuación inició con un esquema verificado, **When** el catálogo del componente cambia antes de habilitar, reclamar o despachar trabajo, **Then** cada límite afectado rechaza antes de modificar estado de continuación o ejecutar efectos.
6. **Given** dos conexiones admiten simultáneamente el mismo paso y generación de un workflow, **When** ambas reservan el siguiente intento, **Then** una conserva el intento y la otra ve el estado durable resultante y rechaza sin crear un duplicado; la misma regla se aplica a pasos script y YAML.
7. **Given** la limpieza de un proceso necesita su lock durable de propietario, **When** no puede abrir una conexión PostgreSQL o adquirir el advisory lock, **Then** la operación devuelve un `WorkConflict` antes de entrar en la sección crítica y no ejecuta efectos de limpieza.
8. **Given** dos proyecciones concurrentes contienen el mismo resultado Work aceptado, **When** ambas comprueban inicialmente que no existe marcador, **Then** la segunda revalida el marcador exacto después de adquirir el lock del run y retorna como idempotente sin repetir la transición del paso.
9. **Given** dos instalaciones usan la misma raíz de wiki pero PostgreSQL main targets distintos, **When** cada una proyecta su memoria, **Then** la caché no sirve la vista de una identidad durable a la otra; facades de la misma identidad sí pueden reutilizar su vista.
10. **Given** la verificación completa de un store Work desde una transacción del repositorio privado de auditoría de memoria, **When** se validan sus contextos de inbox, control y recuperación, **Then** siguen siendo frescos y se informa primero el mismo error que en las lecturas secuenciales originales.

### User Story 4 — Instalación, actualización y recuperación (Priority: P2)

El operador utiliza almacenamiento gestionado o externo y puede verificar copias y restauraciones.

**Why this priority**: operación completa de la instalación personal.
**Independent Test**: instalación real, actualización fallida y restauración a destino aislado.
**Acceptance Scenarios**:

1. **Given** instalación personal nueva, **When** se instala, **Then** aplicación y almacenamiento están configurados, saludables y persisten tras reinicio.
2. **Given** actualización fallida, **When** se recupera, **Then** no se ejecuta una versión incompatible con el esquema.
3. **Given** una copia de seguridad, **When** se restaura, **Then** el destino se verifica antes de activarse.

### Edge Cases

- Pérdida de conexión durante commit; resultado incierto comprobado por idempotencia.
- Bootstrap concurrente, esquemas alterados/nuevos y migraciones interrumpidas.
- IDs, secuencias, timestamps, nulos y JSON preservados.
- Sesiones revocadas, cambio de perfil y credenciales privadas.
- Paginación exacta con timestamps fraccionarios.
- Workers aislados acceden al servicio autorizado sin credenciales generales del almacenamiento.
- Stores auxiliares y coordinación entre peers tienen ámbito explícito.
- Restauración PostgreSQL sin activar la aplicación: la configuración del planificador no altera el estado restaurado antes de verificar el destino; se aplica explícitamente en la instalación normal, conservando una política existente.
- Un formato legacy que incluya `sqlite-dump.json` no es una entrada admitida. Se rechaza de forma explícita y se conserva el archivo original.
- Una instalación nueva no muestra avisos genéricos sobre SQLite. Sólo las invocaciones explícitas de opciones retiradas se rechazan; el estado existente desconocido falla cerradamente y permanece intacto.
- Si falla la conexión o la adquisición del lock de limpieza, se cierra cualquier conexión creada y no se intenta desbloquear un lock que no se obtuvo.

## Requirements

### Functional Requirements

- **FR-001**: PostgreSQL MUST ser el backend de toda persistencia relacional de runtime administrada por CAOS.
- **FR-002**: CAOS MUST inventariar y migrar todos los stores y consumidores, incluidas autenticación y coordinación persistente.
- **FR-003**: CAOS MUST conservar contratos API/MCP, IDs, eventos y recibos, así como los contratos CLI vigentes salvo los puntos de entrada SQLite retirados explícitamente en FR-039.
- **FR-004**: CAOS MUST mantener atomicidad entre fuente, permisos, Work y aceptación/claim en operaciones compartidas.
- **FR-005**: CAOS MUST conservar CAS, unicidad, idempotencia y recuperación sin duplicar efectos externos.
- **FR-006**: CAOS MUST verificar versión e integridad del esquema antes de escribir y rechazar esquemas desconocidos/alterados.
- **FR-007**: CAOS MUST configurar ámbitos por perfil/instancia y sanitizar errores de conexión.
- **FR-008**: CAOS MUST impedir fallback de runtime a SQLite ante fallos de PostgreSQL.
- **FR-009**: La recuperación MUST usar backups PostgreSQL nativos y verificar todos los scopes, fingerprints, datos, relaciones, secuencias y recibos antes de activar un destino distinto. La captura/restauración MUST conservar el backup de origen y los recibos históricos PostgreSQL. El backup MUST excluir passfiles transitorios de `cao/pg-client-*` sin borrar los temporales existentes en el origen. El restore MUST comprobar la ausencia de todos los esquemas destino antes de ejecutar el primer `pg_restore`; ante fallo posterior MUST compensar sólo los esquemas creados por esa operación, después de comprobar OID y propietario exactos, y MUST preservar esquemas ajenos.
- **FR-010**: CAOS MUST rechazar opciones/comandos explícitos retirados de importación SQLite antes de abrir sus rutas o conectarse a PostgreSQL. Si encuentra estado existente desconocido o incompatible, MUST fallar cerrado y preservar los archivos y los datos del destino; MUST NOT borrar, convertir ni sobrescribir ese estado.
- **FR-011**: La instalación MUST admitir almacenamiento personal gestionado y conexión externa, con readiness y recuperación.
- **FR-012**: Credenciales MUST permanecer fuera de prompts, logs, interfaces públicas y agentes sin autorización.
- **FR-013**: Backups, recovery bundles e inventarios MUST verificar el destino nuevo.
- **FR-014**: La autorización MUST conservar revocación y revalidación tras lectura, incluida AG-UI.
- **FR-015**: La paginación MUST preservar orden y posición exactos sin duplicados/omisiones.
- **FR-016**: Tests aplicables MUST ejecutarse contra el backend real, incluidas carreras e instalación.
- **FR-017**: Omisiones externas MUST documentarse sin contabilizarlas como aceptación pasada.
- **FR-018**: La actualización MUST definir compatibilidad/rollback para el esquema PostgreSQL; un estado PostgreSQL desconocido o incompatible MUST fallar cerrado antes de activarse. El rollback Docker MUST comprobar la etiqueta de propiedad de la instalación tanto del contenedor actual como del anterior antes de detener, renombrar o iniciar cualquiera de ellos; nombres reutilizados o no propios MUST rechazarse sin modificar contenedores.
- **FR-019**: Al reanudar YAML, CAOS MUST traducir una discrepancia nativa del esquema Work a la respuesta existente de historial corrupto (HTTP 422) antes de ejecutar agentes; no MUST traducir errores de autorización o disponibilidad a corrupción.
- **FR-020**: La limpieza de procesos MUST adquirir su advisory lock PostgreSQL antes de iniciar efectos; un fallo de conexión/adquisición MUST producir un `WorkConflict` y evitar entrar en la sección crítica.
- **FR-021**: La caché de proyección de memoria MUST incluir la identidad no secreta completa de `DatabaseTarget.identity` junto a la raíz de wiki y los parámetros de proyección. Un target con distinto host, port, database, schema o store ID MUST tener una entrada distinta, aunque comparta la misma raíz de archivos; credenciales MUST quedar fuera de la clave.
- **FR-022**: La reutilización de conexiones para auditoría de memoria MUST estar opt-in y limitada al repositorio privado creado por `MemoryService._legacy_repository()`. Sus verificaciones de inicialización y lectura MUST usar el mismo límite de sesiones acotado, con una transacción nueva `REPEATABLE READ READ ONLY` y verificación completa por snapshot; los lectores genéricos de Work quedan fuera de ese pool. MUST conservar una transacción, fence y verificación completa nuevos para cada fase de auditoría, con commits independientes y sin reintentos ante resultado incierto. El perfil MUST volver a resolver la configuración actual y distinguir el ámbito, rol, endpoint, esquema, TLS y demás parámetros efectivos de libpq sin exponer secretos; MUST rechazar configuraciones heredadas de libpq que no pueda representar o fijar. Para conexiones TLS, MUST distinguir los certificados de cliente, claves, certificados raíz y listas de revocación efectivos; con TLS desactivado MUST evitar inspeccionar esos archivos. El límite del proceso incluye únicamente las sesiones físicas del repositorio privado de memoria (verificación de inicialización y auditoría), activas, ociosas y retiradas aún prestadas; las sesiones ORM generales tienen su propio límite y no forman parte de este contador. Cualquier lease y sus referencias de conexión/cursor MUST dejar de ser utilizables al devolverse. El manager MUST reservar capacidad global antes de abrir una conexión, mantenerla ocupada hasta confirmar el cierre, revalidar el perfil tras toda espera y antes de usar o publicar la sesión, y en un hijo de `fork` separar los descriptores heredados en la tabla local antes de soltar referencias libpq.
- **FR-023**: Las sesiones del pool privado T055 MUST fijar `plan_cache_mode=force_custom_plan` después de `RESET ALL` y MUST verificarlo en cada lectura de identidad de sesión, tanto al prestar como al devolver el lease. Esta elección MUST quedar limitada al repositorio privado de auditoría de memoria, MUST NOT cambiar el modo predeterminado de conexiones directas y MUST NOT alterar el manifiesto o su digest.
- **FR-024**: Las fixtures PostgreSQL MUST limitar la limpieza al recurso que poseen. Una fixture de esquema MUST NOT terminar sesiones de otros esquemas que comparten la base de datos del worker; la terminación de todas las sesiones sólo corresponde al propietario que elimina esa base de datos. Una regresión real MUST comprobar que la sesión privada de auditoría de memoria sigue operativa al limpiar un esquema hermano, sin añadir reintentos al runtime ante una operación incierta.
- **FR-025**: La normalización pura de una definición de constraint MAY reutilizar el resultado exacto para el mismo texto, con un máximo de 512 entradas y textos de hasta 4096 caracteres; las definiciones mayores MUST seguir la función original. La consulta de catálogo, el manifiesto, la identidad, los permisos y la decisión de autoridad MUST obtenerse de nuevo en cada verificación. La normalización MUST conservar bytes, Unicode y la representación de enteros del manifiesto.
- **FR-026**: Las nueve familias de catálogo MAY compartir una selección materializada de namespace/relaciones dentro de una sola consulta nueva. La verificación de rol MAY agrupar sus consultas dentro de la transacción actual. La verificación de componente MUST rechazar una identidad incorrecta antes de consultar el historial, incluido cuando éste falta o es inaccesible. MUST conservar todas las comprobaciones, el orden de clasificación de errores, los resultados y digests para main/auth/peer, el snapshot del caller y las 21 verificaciones completas/20 commits independientes del recorrido de memoria.
- **FR-027**: MemoryService MAY cargar su dependencia privada de auditoría al importar el módulo. Esa carga MUST crear cero sesiones físicas y MUST NOT resolver configuración de base de datos ni credenciales. La preparación y verificación del repositorio MUST seguir dentro de la operación autorizada; la ruta de memoria desactivada MUST conservar su comportamiento y ausencia de acceso a almacenamiento.
- **FR-028**: El contrato de cursor nativo MUST conservar el tipo de fila: `Connection.execute()` y `executemany()` producen `Record`, mientras los cursores raw con una factoría personalizada admiten su propio tipo. El adaptador de leases MUST mantener esta distinción sin cambiar SQL, transacciones, factorías, expiración ni comportamiento en ejecución. Los consumidores que devuelven `Record | None` MUST superar mypy sin suprimir errores ni añadir conversiones de tipos en cada servicio.
- **FR-029**: La declaración de `Record` MUST representar su soporte existente de slices. Los consumidores de una consulta agregada o de historial que exige una fila MUST rechazar explícitamente su ausencia antes de continuar efectos. La ausencia de historial YAML MUST conservar su clasificación de historial corrupto; la consulta de versión de bootstrap MUST fallar con `SchemaMismatch`. Los resultados válidos, SQL y límites transaccionales MUST permanecer iguales.

### Key Entities

- **Ámbito de almacenamiento**: instancia/perfil, identidad, conexión y versión de esquema.
- **Estado operativo**: sesiones, agentes, mensajes, workflows, Work, claims y eventos.
- **Interacciones**: preguntas/versiones, respuestas, recibos, delegaciones y deliveries.
- **Estado privado**: usuarios/sesiones, permisos, aprobaciones y credenciales.
- **Conocimiento**: memoria, índices, revisiones, relaciones y auditoría.
- **Recibo de procedencia PostgreSQL (histórico)**: identidad fuente/destino, versión, recuentos y verificaciones conservados en las tablas PostgreSQL instaladas.

- **FR-030**: La admisión de Work MUST liberar los locks exclusivos de autoridad y los locks temporales de la prueba de rutas al finalizar cada candidato elegible. La retirada de un candidato inválido MUST ejecutarse fuera del savepoint candidato para conservar su transición terminal y retirada de cola. El selector del planificador en preview MUST seleccionar sin mutaciones ni eventos. Antes de reclamar capacidad, commit MUST revalidar y bloquear exclusivamente la autoridad del candidato preview; bajo el lock agregado MUST comprobar esa selección antes de modificar cola, secuencia o eventos. Una selección existente distinta al preview MUST provocar rollback y reintento; la ausencia de candidato o capacidad conserva el no-op existente. Capacidad, prioridad, justicia, rutas, transición a `sent`, credenciales y fences MUST conservarse; MUST NOT aumentar timeouts, debilitar exclusiones ni retirar la limpieza de candidatos inválidos cuando la capacidad está llena.

- **FR-031**: La retirada interna de trabajo inválido MUST observar la revisión del intento después de adquirir su lock en orden job→item→attempt. Dos dispatchers que limpian la misma solicitud MUST poder observar la retirada ya completada sin duplicar eventos ni retirar un writer activo. La revisión de la solicitud de cola MUST releerse bajo el lock del job; una solicitud `held` con identidad coincidente MUST conservar el intento y la capacidad del claimant concurrente sin retirada; estados o identidades contradictorios MUST fallar cerrados. La limpieza durable y las pruebas de rutas con rollback conservan su función fuera del selector puro de FR-030.
- **FR-033**: La normalización MUST conservar exactamente las claves JSON de identidad de rutinas y triggers; `None` puede usar la cadena constante `null`, y los demás valores MUST conservar la serialización actual. El fingerprint de un manifiesto recién obtenido por `catalog_manifest` puede omitir el seguimiento de referencias circulares únicamente en un helper interno de ese flujo JSON acíclico; `_digest` general MUST conservar sus bytes, opciones y rechazo de ciclos actuales. MUST mantenerse las nueve familias, lecturas frescas, comprobaciones de drift, orden de errores y commits independientes. La optimización MUST probar igualdad de valores y digests, argumentos no nulos/sobrecargados, Unicode, controles JSON y referencias compartidas acíclicas antes de aceptar tres NFR fríos del código real y la regresión completa.
- **FR-034**: La comprobación de permisos MUST leer los flags del rol, ACL del namespace, ownership y permiso CREATE actuales en cada llamada y conservar su orden de rechazo. Puede reutilizar el plan de la consulta sin parámetros mediante literales de esquema citados de forma segura y un cache acotado de texto SQL puro; MUST NOT almacenar resultados de permisos ni modificar `plan_cache_mode` global. MUST probar cambios de flags/ACL después de preparar la consulta, límites del cache, quoting, igualdad de filas y reutilización tras el reset de leases.
- **FR-035**: La configuración del pool MUST conservar todos los RESET/SET, las siete comprobaciones de parámetros, identidad, autocommit e IDLE en cada adquisición y devolución. Puede agrupar comandos y lectura final en una única consulta compuesta sobre la conexión física propiedad del manager, avanzando sus resultados hasta el SELECT final. MUST NOT omitir resets, exponer handles físicos, alterar factories del borrower ni devolver una sesión tras un error de comandos o validación. Los commits de auditoría y las lecturas completas de catálogo MUST permanecer independientes e inalterados.

- **FR-032**: La consulta de catálogo MAY reutilizar un plan PostgreSQL sin parámetros usando literales de schema/tablas/familias escapados con `psycopg.sql.Literal`. El resultado MUST seguir leyéndose y verificándose en cada llamada. Una caché pura de SQL renderizado MUST limitarse a 128 entradas y contener únicamente nombres no secretos y SQL del módulo; MUST NOT almacenar resultados, permisos, identidades verificadas o checksums. `Connection.execute` y su override de lease MAY aceptar `prepare: bool | None`; el valor predeterminado MUST conservar el camino actual y la preparación explícita MUST conservar `record_factory` y todas las guardas del lease. Los 21 checks completos y 20 commits de auditoría MUST permanecer; el commit read-only de inicialización se cuenta por separado. La aceptación MUST comprobar paridad main/auth/peer, invalidación por drift, leases expirados, row factories prestados y tres NFR fríos sobre la fuente del producto sin alterar el límite de un segundo.

## Success Criteria

- **SC-001**: 100% de stores relacionales inventariados cubiertos; cero stores SQLite activos en runtime.
- **SC-002**: Recorrido funcional completo conserva estado tras reinicio y pasa autorización.
- **SC-003**: Un backup PostgreSQL admitido restaura el 100% de registros, relaciones, secuencias y recibos históricos; el origen permanece intacto y la activación espera la verificación completa.
- **SC-004**: Carreras dejan como máximo un resultado efectivo por intención/versión, sin efectos duplicados.
- **SC-005**: Instalación, actualización, recuperación y restauración pasan en recursos desechables reales.
- **SC-006**: Suites y gates de arquitectura/composición pasan; omisiones y límites identificados.
- **SC-007**: El código, dependencias, CLI, paquete distribuido, fixtures y pruebas funcionales activos no contienen soporte SQLite; se conservan los flujos OKF modernos de directorio/tar y se rechaza sin mutaciones el formato legacy `sqlite-dump.json`.

## Assumptions

- Todo comprende persistencia relacional propiedad de CAOS. Contenido, artefactos, sockets, logs y configuración mantienen su función filesystem y se inventarían expresamente.
- SQLite no se admite ni se procesa en ninguna ruta activa de CAOS. No hay importador, lector legacy, dependencia, CLI, fixture o prueba funcional SQLite. La evidencia histórica puede mencionar SQLite sin convertirlo en soporte activo.
- La restauración admitida parte de un backup PostgreSQL nativo. Los bundles OKF modernos de directorio/tar continúan disponibles en un formato SQLite-free; el formato legacy `sqlite-dump.json` se rechaza y se preserva.
- El schema/ledger/DDL PostgreSQL existente y sus filas de recibos de procedencia son históricos e inmutables; no se eliminan para retirar SQLite.
- Personal nuevo incluye almacenamiento gestionado; externo usa conexión explícita. Ámbitos separados no se fusionan por compartir servidor.
- Implementación/validación sobre recursos privados. No se cambia la instalación del operador sin destino concreto y migración verificable.
- La nueva prioridad conserva las correcciones/evidencia de spec016; la aceptación completa continuará sobre PostgreSQL.

- **FR-036**: SQL timing adapters used by the capacity acceptance tests MUST preserve the native optional `prepare` contract: omitted or None retains the previous call shape, and explicit True/False is forwarded unchanged. Adapting instrumentation MUST NOT change workload, concurrency, timer boundaries or acceptance thresholds.

- **FR-037**: Managed PostgreSQL provisioning MUST wait for the final TCP server on explicit container loopback before role bootstrap. The temporary socket-only initialization server MUST NOT satisfy readiness. Keep the pinned image, owner/database, 120-second timeout, one-second retries, labels and deployment isolation unchanged; test temporary bootstrap and never-ready timeout, then prove real install/backup/restore gates.
- **FR-038**: El producto CAOS MUST NOT incluir ni ejecutar soporte SQLite en runtime, importadores, comandos/flags, dependencias, archivos empaquetados, fixtures, pruebas funcionales, documentación activa o ejemplos. Las validaciones que hoy emulan PostgreSQL con SQLite MUST pasar a PostgreSQL real. Las referencias históricas de auditoría permanecen como evidencia, no como soporte activo.
- **FR-039**: La opción `--import-sqlite`, el comando `import-sqlite`, sus opciones de rutas y cualquier otro punto de entrada SQLite retirado MUST devolver un error explícito antes de leer archivos, crear directorios, adquirir locks de instalación o abrir conexiones. Una instalación nueva válida MUST seguir su flujo normal sin advertencia o permiso genérico adicional.
- **FR-040**: El lector y formato legacy de bundles que contienen `sqlite-dump.json` MUST retirarse. Los bundles OKF modernos en directorio y tar MUST conservarse y MUST usar sólo su formato nativo SQLite-free. Rechazar un bundle legacy o malformado MUST dejar intactos el bundle y los archivos de destino.
- **FR-041**: Los esquemas, registros y filas existentes de recibos/import provenance PostgreSQL MUST conservarse como procedencia histórica de sólo lectura. La retirada del importador SQLite MUST NOT borrar, reescribir ni reinterpretar esos datos.
- **FR-042**: Los DDL PostgreSQL instalados, el ledger de migraciones y los checksums existentes MUST permanecer byte por byte compatibles. La retirada de SQLite no autoriza migraciones destructivas, pérdida de datos, debilitamiento de verificaciones ni cambio de los floors globales de cobertura: backend MUST permanecer en 87% o más y Apps en 90% o más.
- **FR-043**: El proceso Docker de mantenimiento para restore MUST usar un HOME y CAO_HOME_DIR temporales, privados y separados del backup y del destino. Las importaciones en un proceso nuevo MUST NOT crear el destino antes de su publicación atómica ni alterar el backup. MUST conservar el destino explícito de `--root`, la configuración PostgreSQL seleccionada, las verificaciones de autenticación, permisos y esquema, y el rechazo de destinos existentes. Los directorios temporales MUST pertenecer al UID/GID del proceso y tener permisos 0700; MUST desaparecer con el contenedor de mantenimiento.

## NFR follow-up — strict memory round-trip (2026-10-14)

- **FR-044**: The existing covered PostgreSQL memory round-trip MUST remain strictly below 1.0 second in each of three independent cold runs of `test/services/test_memory_service_index_roundtrip.py::test_roundtrip_runs_under_one_second`. Use the pinned runtime, unchanged test body/timer boundaries, fresh Python processes, the accepted native PostgreSQL fixture, and the same full-coverage options; do not warm the operation or its connection/catalog plan before timing. Preserve the fresh nine-family catalog verification, 21 full Work checks, 20 durable audit write commits, and separate read-only initialization commit. Any optimization of the routine-dependency query MUST preserve exact normalized values and manifest digests for `columns`, `constraints`, `indexes`, `triggers`, `rules`, `sequences`, `relations`, `policies`, and `routines` across main/auth/peer and multi-component targets. Preserve trigger UNION/deduplication, all five routine dependency families (constraints, attribute defaults, indexes, policies and rewrite rules), current `objsubid` treatment, fresh drift detection, identity/history/error ordering, caller-owned transaction/snapshot behavior, DDL, checksums and ledger rows. Do not cache catalog results or lower the time, coverage or test-selection gates.

- **FR-045**: Any optional PostgreSQL 18 fast path for `NOT NULL` constraint definitions MUST reproduce the native definition text exactly, including identifier quoting and the server's suffix order for `NO INHERIT`, `DEFERRABLE`, `INITIALLY DEFERRED`, `NOT ENFORCED`, and `NOT VALID` when applicable. It may bypass `pg_get_constraintdef` only for fresh `pg_constraint` and `pg_attribute` metadata with a verified one-dimensional, single-column `conkey` whose lower bound is 1 and a present, non-dropped matching attribute. Null, malformed, unsupported, or missing catalog data MUST use the existing `pg_get_constraintdef` path. Select a fast path only after the live connection reports PostgreSQL major version 18; PostgreSQL 16/17, unknown, and future major versions MUST retain the existing path. A bounded cache may contain only rendered SQL text keyed by server version; it MUST NOT cache catalog rows, definitions, manifest values, or verification results. Preserve the exact nine-family normalized manifest and digest, fresh drift checks, caller-owned snapshot behavior, main/auth/peer and multi-component parity, DDL, migration checksums, and ledger rows. The existing native renderer remains acceptable when measurement does not show a substantial improvement in the unchanged strict cold round-trip.

- **FR-046**: The audit logger and wiki compiler MUST share the same single-line log sanitizer while allowing `audit_log` to import the sanitizer without loading the wiki compiler. Keep the sanitizer in a pure standard-library module, re-export the same callable from `wiki_compiler` for compatibility, and preserve its current ANSI/control/newline/Unicode-surrogate handling and truncation behavior byte for byte. This dependency change MUST NOT change audit event ordering or append behavior, memory scoring, or wiki compilation behavior.

- **FR-047**: For each operating system in the Rust CI matrix, CI MUST provision and launch an ephemeral PostgreSQL service owned by that job, configure distinct main, auth, and peer stores with separate migration/runtime credentials, apply and verify native schemas before starting the actual CAO server, and pass the server only the private runtime configuration. The three Rust live endpoint contract tests MUST execute against that server without mocks or skips. PostgreSQL setup, schema verification, server health, or endpoint failure MUST fail the job. Cleanup MUST be limited to the job's private cluster and targets it created. Existing `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked`, all existing timeouts, and test-selection semantics remain intact.
- **FR-048**: Acceptance of a live provider interaction MUST use each requested installed provider CLI, its real configured authentication, and a supported model. The existing bounded journey MUST receive an actual provider-generated question, publish the human answer, and observe provider acknowledgement; a zero-exit CLI without an acknowledgement MUST fail. No mock response may count as provider acceptance. Preserve operator authentication state, isolate child configuration where required, and record only sanitized lifecycle metadata without prompts, responses, credentials, or provider logs. An unavailable, unconfigured, or failed provider remains pending/failed, not passed.
- **FR-049**: La verificación del contexto Work puede agrupar las lecturas de inbox, control y recuperación únicamente dentro del repositorio privado de auditoría de memoria, cuando su lease posee una transacción activa. La ruta agrupada MUST conservar las tres filas nativas y validar inbox → control → recuperación en ese orden. MUST mantener completas y frescas las comprobaciones previas de componente, catálogo, historial, rol e identidad. La lectura especulativa MUST ejecutarse bajo savepoint y locks `ACCESS SHARE NOWAIT` en el orden original; si falla, MUST recuperar ese savepoint y ejecutar una sola vez las lecturas secuenciales originales para conservar su error y precedencia. Un fallo al recuperar MUST cerrar en fallo y taintar el lease. Repositorios Work genéricos, conexiones prestadas y verificaciones autocommit MUST conservar las lecturas secuenciales. No se cambian transacciones externas, commits, fences, DDL, checksums ni el recuento de verificaciones.

- **SC-008**: Three of three independent cold covered memory round-trip runs finish below 1.0 second, the complete selected backend and Apps suites have no unresolved failures or unexplained omissions, and measured coverage remains at least 87% for backend and 90% for Apps. Explicit host-gated checks must be identified and reported as pending when their external prerequisite is unavailable; they must not be silently excluded or reported as passing.

- **SC-009**: If a PostgreSQL 18 `NOT NULL` fast path is introduced, its output matches the native renderer byte for byte for eligible and fallback catalog shapes, including quoting, Unicode identifiers, constraint flags, renamed or dropped columns, inheritance, and partitions. PostgreSQL 16/17/unknown/future-version selection retains the existing renderer. All nine manifest families and digests, caller snapshots, and drift outcomes remain identical across main/auth/peer and multiple components. The fast path is accepted only with a substantial measured improvement under FR-044; diagnostic query timings alone do not satisfy this criterion. No fast path is required without that improvement.

- **SC-010**: Importing `audit_log` does not import `wiki_compiler`; importing `wiki_compiler._sanitize_for_log` remains compatible and refers to the shared sanitizer. The pure import/behavior tests prove byte-for-byte parity for existing sanitizer cases, and no audit append, event ordering, scoring, or compiler tests regress.

- **SC-011**: The real bounded interaction journey completes with an acknowledged provider-generated question for each requested provider (OpenCode, Claude Code, and Codex). A process exit without a question/answer/acknowledgement is a failure. Auth state and operator HOME remain unchanged; only sanitized receipt metadata is retained. Local list/catalog tests alone do not satisfy this criterion.
- **SC-012**: La verificación privada del contexto produce las mismas filas y errores ordenados que las tres consultas originales; cuando una consulta agrupada falla, la transacción conserva la primera discrepancia secuencial y el estado del lease sólo se invalida si falla la recuperación del savepoint. Las conexiones genéricas y prestadas permanecen en la ruta secuencial. Esta comprobación no sustituye las tres mediciones cubiertas FR-044.

### Current verification status — 2026-10-14

SC-008 remains open. The latest completed full backend run is still R21: it exited 1 with 17,390 unique selected nodes, 17,252 passed, 113 skipped, and 25 unique failed node IDs (15 FAIL and 12 ERROR phase reports; two nodes failed in multiple phases). Coverage was 67,793/77,816 statements (87.1196155%, branch coverage disabled): above the line floor, but not a passing full-suite gate. Older R13 coverage was 86.733308%. The final R3 source seal has 3,111 entries and SHA256 `ac4351478f2173853ab6e38af731722f8cb8b58043146877bcd002b6d0c9bcb6`; its environment binding is `14c94804967e1fee4044a9f7281f8396b48aa999692957a507f4237262447207`. Against that source, the three unchanged cold covered FR-044 runs all failed the `<1.0 s` condition: 1.084414205 s, 1.065482402 s, and 1.005557229 s. Source pre/post and HOME guards passed; there was no prewarm. The R3 T097 QEMU run expected 91 cases: 8 passed, 2 failed after the unchanged 65-second callback wait, 81 were not reached, and none were skipped. The first incorrect observable was dispatch without an upstream callback; no evidence attributes the delay to TCG. The run left the sealed source unchanged, and its marker database was removed after verifying OID/owner; the managed PostgreSQL service remained healthy. The separate selector contract passed 5 tests, which does not make the QEMU run green. Focused local E2E (36), prepared-node Docker (1), Apps (92 across 12 files at 90.01%), Web (504 across 45 files), T085, T087, and Linux T088 receipts are recorded in `tasks.md`. Claude Code completed an earlier two-question acknowledged journey. The earlier Codex attempt failed, but the latest private-harness Codex run passed two answered and acknowledged questions; the latest private-harness OpenCode run exited 0 without a question or acknowledgment; its cause is unknown and read-only MCP preflight is pending. The harness implementation has since landed in the current source tree under T096; these provider journeys remain earlier private-run receipts, not fresh live-provider acceptance on current source. SC-011, T095, and aggregate T083 remain open. T088's macOS matrix leg, the full-suite/omission reconciliation, and T076 signoff remain open.

Scoped R4 updates do not change that aggregate status. The source-bound Web20
selection passed 20/20 with zero skips and exit 0 (log SHA256
`159ae898419dad4115e61b38686b65a671e7fce76d9087712905eb8a97706554`; summary
`frontend-private-r3/web20-20261014-214156-714984-ac1ogaku/summary.json`). The
guarded package run passed against source manifest
`c4827350259bb4b4815126d11c15b2f9141cc11a8b71716f335cf8db6b37407e`: 389
wheel/sdist modules and five active SQLite guards passed, and HEAD/index/HOME
were unchanged (runner exit 0; receipt SHA256
`f0a4c966274532de62db189ff10441edfbc666cf3951a0137a5d496bb83fc13f`). T092's
offline harness evidence is 18 passing tests across two modules and four
bound source/test input files; it is not evidence of four modules or live
provider completion. T093's Core candidate is now present in main (`memory_service.py` SHA256
`58ec723493698a8c95ebb0a99e4781a4c469608115e8af9a1f6fbc994a89814f`; focused
native test SHA256 `33f89d93251747e538da44b876f7ca07a578769a0cea097f013d275992bd638f`).
The fresh native caller slice passed 21 tests in 25.798 s. Private paired run 02
improved from 1.105620873 s to 1.004116244 s, but both failed `<1.0 s`. A
separate Core/decoder pair measured 1.032364007 s and 1.120388702 s respectively;
both failed, combined was slower, and the decoder was not ported. Both post-timing
catalog-digest audit probes errored and remain under investigation. These results
do not close FR-044 or T093; require resolution and the unchanged three cold
strict runs.

### T087 run-step regression contracts (focused implementation complete; aggregate acceptance remains open)

Before the T087 implementation, two composition regressions were reproduced.
The local path could reopen a turn after a blocking send and demote a completion
observed for that turn. The remote path could reject a valid dispatch:
`terminal_service.send_input` returned the peer's turn sequence, but
`StatusMonitor.notify_input_in_flight` checked only local turn counters, which
remain zero for a remote terminal. `run_agent_step` then raised reconciliation
before its completion wait even when the peer had published the exact current
turn.

The post-send boundary must acknowledge the same dispatched turn without
creating another logical turn or demoting a completion already recorded for
it. For remote terminals, validate the requested sequence against the current
authoritative remote projection. Accept only an exact current positive integer
sequence and a well-formed completion sequence between zero and that current
sequence; a completion already observed for the exact turn is valid. Missing,
malformed, or superseded projections fail closed. Remote acknowledgement must
not mutate local monitor counters, captures, buffers, or local status events.
Keep pre-send ownership, interaction/authentication, receipt, cancellation,
delivery, and at-most-once guards intact; do not use timing sleeps to create or
conceal either regression.

Focused implementation evidence is recorded in `tasks.md`, `plan.md`,
`research.md`, and `graphify-impact.md`. It does not close SC-008: the full
backend run, aggregate E2E omissions, three cold FR-044 runs and final T076
signoff remain open.

### Follow-up evidence — 2026-10-14

A fresh no-coverage native memory regression selected 234 tests across 11 modules: 233 passed and the unchanged `test_roundtrip_runs_under_one_second` assertion failed at 1.161985059 s. Its JUnit SHA256 is `2673890c4974210e28e3f7a45c25bceaa05aa72c41092eb68168b1863ddc6794`. This is diagnostic regression evidence, not the three-run strict covered acceptance. A scoped four-check review passed Black/isort for the two changed files, ImportLinter, and project composition (399 files, 1,775 dependencies, 5 kept, 0 broken); receipt SHA256 `d82f19af42bcc3ea94460a531390ed489459c1bf031e8307667a46aa18b7cf7f`. Manual Core review found no blocking conversion defect and documented the pre-existing cross-tier preflight race, which this port did not introduce (review SHA256 `38188ba53187f7e8818df2eeb840eecde19bd31b7392008bb8fa44e4b04d50a6`).

The later private Core pair still failed both strict measurements (baseline 1.105620873 s; candidate 1.004116244 s). In a separate Core/decoder pair, Core measured 1.032364007 s and combined measured 1.120388702 s; both failed and the combined arm was slower. Both post-timing catalog digest audit probes errored and remain under investigation (pair receipt SHA256 `d9982040d92fd8d6b9c0efb42fe3204bd850abc6445fa30aa962128cdc93ab13`). Do not treat these diagnostics as a pass or close T093/FR-044.

The latest private provider-harness Codex journey passed: two questions, two answers, identity-matched acknowledgment, CLI exit 0 in 14.17 s (receipt SHA256 `23ed395e80178b2d512c2edb1a945001e4459485688f99ea4233503277f06fd2`). The latest OpenCode journey exited 0 in 11.11 s but produced zero questions/acknowledgments; its cause remains unknown (receipt SHA256 `76dfae9dbb375c40de6b1a50938b6dc4cc03442407c1fb538a88fc939611e9d6`). These are private harness receipts, not mainline provider-matrix acceptance. The QEMU2 two-selector diagnostic completed 1 pass and 1 failure (0 skipped): `after_upstream` passed and `before_upstream_effect` failed. Cleanup removed its marker database without error and root verified the owned runner container absent and PostgreSQL healthy. It is diagnostic only; T094 remains open. T076, T081, T083, T093, T094 and T095 remain open.

### T096 provider-harness update and QEMU2 diagnostic — 2026-10-14

The bounded live-provider harness changes are now in the current source tree. Exact inputs: `test/live_provider/interaction_cli_journey.py` SHA256 `8b136a5746e101f16f94c540d99e2db2d69d55f5d9fab62f3ea8e3d39074e130`; `test/live_provider/test_interaction_cli_journey.py` SHA256 `2792ec1d699d2f78b63254b37fd07da86ba18cad3044d632575405abf2f5f890`; `test/live_provider/opencode_interaction_journey.py` SHA256 `848d150f87136fec84e425db76ad305b62c4821cdc59d1f94761bf1a4e9e44fd`; and `test/live_provider/test_provider_harness_upgrade.py` SHA256 `efd58e94f18a8b8b034afb518d3f158199b5274e1a5fc1cabe6330bc95ca64bc`. The first main-bound run exposed three Codex auth-profile rejection/partial-staging failures (73 passed, 3 failed, no skips; RED JUnit SHA256 `35dcac728e1d92f77a7d2afc130be30fd5702ced6edbb0f95d5b725ecc18cdab`); after validating the auth profile before overlay creation and preserving source bytes/rollback on rejected staging, the fresh main-bound run passed all 76 tests across the two modules, with 0 errors/skips in 3.211 s (JUnit SHA256 `89415323d7a5a44cf7418c31f42d2891d76a2335a7ef7b85bfc8e934c91916e7`). Black, isort and compile checks also passed. This verifies harness staging/privacy contracts, not live calls to all providers. Earlier Codex/Claude positive live journeys remain historical private-harness evidence; the OpenCode zero-question/zero-ack journey remains red. T095 and SC-011 remain open.

The QEMU2 diagnostic selected only two SIGKILL cases: `after_upstream` passed and `before_upstream_effect` failed, with 0 skipped (receipt SHA256 `51cbee10d4c702e04d28ed6ec7484c93306f0a51024900f4a83758353e93972e`). The marker database was removed, cleanup reported no error, the runner container was absent by ID, and the owned PostgreSQL service remained healthy with no OOM or restart. Timing showed dispatch at 28.953 s, backend process start at 40.032 s, setup ACK at 12.754 s (<20 s), GO release at 90.679 s, outer wait timeout at 94.022 s, and upstream callback at 104.070 s (64.038 s after backend start and 13.391 s after GO). The source diagnosis identified a test-helper deadline-origin error; the helper-only correction now waits for either the upstream event or the real dispatch future and preserves the existing post-SIGKILL/cleanup timeout. Its 10 offline contract tests pass. The earlier two-case diagnostic predates the correction; no fresh QEMU run has launched, and a prepared tar is not acceptance evidence. T094 remains open.

### T093 and T094 evidence update — 2026-10-15

A private owner-JSON loader pair passed its semantic, call-counter/catalog/loader, source-integrity, and HOME/CODEX_HOME guards, with main source unchanged, but did not meet performance acceptance: baseline was 1.306698168 s and candidate 1.565738595 s (candidate 259.040427 ms slower; both fail the unchanged `<1.0 s` bound). No candidate was ported. Pair receipt: `nfr-fix-r23/owner-json-loader-r1/paired-runs/pair-01/pair-receipt.json`, SHA256 `dcb51b9cb3cc2fdb1326097b60e584221ceb6fe64e8c30f5442e2c55c2b3088d`.

The corrected T094 test-helper contracts passed 10/10 offline tests in 4.01 s (JUnit SHA256 `cef799393c147333824de70fa6bc40b9260e1bcc938c43a84ee52f8d11eadcc2`; log SHA256 `238189ef2b5b53c54b71fae94f4aab46c2686a22e91c8c6a4517c5537fe783d5`). An independent source-only review passed Python 3.10 `BaseException.add_note` compatibility and original-exception preservation for `test/integration/t097/test_proxy_sigkill_t097_restored.py` SHA256 `6f6da51600c0f43c0fc1c7a0a3fd0e87493547c5179482474f220c075c1f033b` and `test/scripts/test_t097_sigkill_wait_contract.py` SHA256 `0e5a95e3bb2cc8991c65eaa9838821159cd08dc7701f0236b37b18223bc379c9` (review SHA256 `5d0453379873c62dab86bb83c9fb1f26605b24c3a226d4e40aef203fe59719d8`). The fresh QEMU run has not launched after the helper correction; the prepared tar is not acceptance evidence. T093, T094, T095, T076, the strict NFR, and the macOS CI leg remain open.

### Diagnostic recovery — 2026-10-15

The catalog-plan baseline run is a real strict-NFR failure: the unchanged round-trip assertion measured 1.805611058 s against `<1.0 s`. The original wrapper exited 1 before writing its run receipt because its expected-red parser fullmatched only the assertion message, while pytest appended the exact `assert 1.8056110579927918 < 1.0` line. A separate private postprocessor cross-checked that exact message/assertion against the two clock samples, JUnit and pytest log. It confirmed all nine source-report proof checks, ten plan entries, `errors=[]`, and 71 work-migration tables. The original failure and exit remain preserved; postprocessing does not change the NFR result or constitute a pass. Receipt: `nfr-fix-r23/catalog-plan-core2-r1/recovery-r1/recovered-result-receipt.json` (SHA256 `0af8126623d8a33c17b9709fb7936efa8bc1e978c93a39b1cec108c699e5b9a1`); its strict parser's offline negative/positive suite passed 10/10 (validation receipt SHA256 `c4635fcb806a2cb1b9ac97dadeeb87f1ce33294f56df144bf4f8957cb7087caa`). The original wrapper did not persist its parent-process HOME or main-source before/after guard values; the recovered receipt marks those fields unavailable rather than inferring them.

After the T094 helper correction, the source-bound QEMU2 diagnostic passed both selected SIGKILL cases (`before_upstream_effect` and `after_upstream`) in 235.15 s, with zero warnings; the runner container exited 0 and owned-resource cleanup succeeded. The original wrapper still exited 1 and its `diagnostic_failed` receipt is retained: its summary parser reported zero passing selectors despite the raw guest log showing two. An offline recovery receipt binds that original status to the raw log and recovered 2/2 result (SHA256 `addf5550137ad6236a2b9be1e033ccf651b0a63895ed078d9290dccd6cab18e5`). This remains a two-case diagnostic, not the full 91-case QEMU acceptance; preserve the historical R3 full-run failures and keep T094/T076 open.

### Service, T098, and host-gate follow-up — 2026-10-15

Docker Desktop recovered. The owned PostgreSQL 18.6 container kept the same identity; its published port was reassigned from 61997 to 56421. The new sanitized metadata snapshot SHA256 is `200b53e7bd46f495ed1246cab792eb0e6909bbec3cc1ad962a05b99f2a14f1d1`. The recovery receipt confirms the version/admin check and unchanged historical results (receipt SHA256 `b792881742290c78bcc596ca0d65a00a602b6aefb0a0041a779de4d53b9c57e5`). No credentials or secret values are recorded here.

T098's private R3 offline checks passed 18 tests plus 9 subtests, and its native baseline and candidate selections each passed 8 tests. In the scored strict pair, baseline measured 1.024650518 s and failed `<1.0 s`; candidate measured 0.967219559 s and passed. The separate three-run candidate cohort measured 1.017240271 s (fail), 1.034487912 s (fail), and 0.973277629 s (pass), so only one of three runs met the limit and T098 remains open. A phase-timing run is diagnostic only: 1.032294341 s total and 21 catalog reads over 592.153 ms, with private-harness guards intact; it is not acceptance evidence (receipt SHA256 `54add99c7eaa859e9baaf3bd25eee6f7f4ab04c3cf20ddede0dc7eb9b7d38045`).

The FULL91 preflight exited 2 after measuring 6062 MiB available against the unchanged 6144 MiB floor; it did not start a VM. The user cannot free more memory, so FULL91 remains pending and the floor is unchanged (preflight log SHA256 `de5c72bed00571290a21fe8342b635caa7541c1bd8abfb298a945b660779f760`). The OpenCode R8 real-provider attempt timed out after 83.13 s with zero questions and zero acknowledgments; cleanup status was incomplete; `private_files_preserved` was a cleanup-failure category. This is not provider acceptance. No final provider matrix, AGUI restart, global rerun, or R3/R8 main-source port has occurred. A separate projection proposal passed 101 offline tests with 6 warnings; this does not establish or claim a runtime fix. Global acceptance remains open.

### OpenCode R9 diagnostic and narrow guard result — 2026-10-15

The real OpenCode R9 attempt ran 83.54 s and timed out without a question or acknowledgment. At observation, `turn_sequence=1`, `initialized=true`, `MCP_ready=false`, and the TUI was idle; the expected log had not appeared before cleanup. The offline probe found that the bundled MCP command was recognized before it was wrapped with `env -i` and was unrecognized after wrapping; it did not establish a live MCP connection. The current diagnosis is that the harness lookup omits the readiness check; no product runtime fix has been made. The root-owned cleanup receipt records the exact owned tmux kill exiting 0 and the server gone, with a stale socket remaining; root-owned files were preserved (`R9launcher/runs/514ad4818318477b819d5c13e58dc6ca/root-owned-tmux-cleanup-r1.json`). Do not infer an R10 result or provider acceptance from this run.

A source-only SQLite guard selection passed 5 test functions with 1 warning in 125.53 s using `--noconftest --assertplain`. This was a narrow guard run, not a full suite or coverage result. Aggregate provider and full-suite gates remain open.

### T099–T101 context-tail implementation status — 2026-10-15

The reviewed R3 context-tail batch is implemented in the two Work/audit
repository files. Offline tests passed 18 cases plus 9 subtests. The final
native PostgreSQL run passed all 8 contract cases and its source-origin,
source-hash, HOME, and verification-count guards. A first native attempt had
7 passes and one test-instrumentation failure: the borrowed-connection
observer tried set membership with a non-hashable `psycopg.sql.SQL` object.
The test-only observer now checks the statement type before membership; no
product change was made for that failure. This verifies the scoped
context-tail contract, not the strict round-trip budget: the prior candidate
cohort passed only 1 of 3 cold runs, so FR-044 and the unchanged `<1.0 s`
acceptance remain open.


## Static verification follow-up — 2026-10-16

Complete the configured source typing and Python formatting gates alongside T083. Correct the reproduced four typing errors without changing PostgreSQL SQL, parameters, table identity, freshness, authorization, transaction ordering or the memory-audit session cap. Formatting must remain within the files reported by the full checks and preserve executable syntax and import inventory. These static corrections do not substitute for the final real PostgreSQL regression or strict cold timing acceptance.


## Provider acceptance lifecycle follow-up — 2026-10-16

Selected native-provider acceptance must validate the canonical session name returned by the production API, including SESSION_PREFIX, rather than reject a correctly created terminal for differing from its bare requested name. The adapter must preserve the existing exact receiver, model, Unicode prompt, tool contracts, deadlines and cleanup checks.

The real-provider matrix fixture must own an isolated short tmux socket root before server startup and close only its verified empty tmux server after the API and its sessions have stopped. Preserve the production exit-empty-off behavior. Unknown ownership, reused identity, nonempty sessions, live API, changed socket or unconfirmed exit must fail cleanup and retain evidence. A failed acceptance must remain a failed acceptance even when resources are subsequently cleaned.

## QEMU dead-monitor fixture PID publication clarification (2026-10-16)

The T094 full-suite acceptance must distinguish a published monitor PID from a merely created PID file. The test controller must publish the complete monitor PID atomically before the consumer observes its final pathname. The existing five-second readiness budget, exact parent/start-time/executable/pidfd checks, recovery assertions, process ownership rules, guest selectors and full 91-case requirement remain unchanged. A fixture publication failure is a test failure, never a reason to skip or relax identity verification. R14 failed with an empty PID file: 79 passed, one failed, 11 unexecuted; this does not satisfy T094.


## Spec 021 diagnostic R2 — required-wait signal timeout

### Status

Diagnostic contract integrated; final validation remains pending. The historical R13 failure timed out at the test's existing `asyncio.wait_for(wait_started.wait(), timeout=1.0)` boundary, while the isolated test and seven-test module passed. The first incorrect boundary remains unknown. This work does not claim a product defect or fix.

R1 is retained unchanged. It was prepared before the required Graphify step; that workflow omission is recorded rather than backdated or hidden. R2 completes the source-backed graph review before rebuilding the smaller private candidate.

### Problem and scope

The event is set only after the test's wrapper enters `InteractionService.wait_for_required` with a positive timeout. Before it can be set, `_wait_for_completion` may snapshot the active source, poll terminal status, check native-manual closure, and perform a zero-time required-wait read. Any exception or normal early completion in these boundaries leaves the event wait to expire without explaining why.

R2 adds test-local timing and task-outcome diagnostics only. It leaves production code, the selected integration test's DB composition, and all normal call/await ordering unchanged. No provider, API, PostgreSQL, or integration test is executed in this preparation.

### Requirements

- Preserve the existing completion budget `2.0`, signal deadline `1.0`, and pending-answer sleep `2.1` exactly.
- Record only fixed boundary labels, monotonic elapsed nanoseconds, exception type names, and the terminal background-task state (`pending`, `returned`, `cancelled`, or `raised:<type>`); cap the trace at 32 rows.
- Do not record exception messages, IDs, prompts, answers, result bodies, SQL, DSNs, credentials, or environment values.
- Diagnostic wrappers must re-raise the identical exception object and return the identical result object from the wrapped operation.
- A task return before the event is still a test failure. An exception before the event must remain visible as the cause of the existing `TimeoutError`; pending or cancelled tasks must leave `TimeoutError` as the raised type.
- Do not add sleeps, retries, DB reads, clock changes, or production hooks.

### Acceptance

The private fake regression verifies early exception, early return, and pending-task outcomes while asserting that the event timeout remains exactly `1.0`. It checks that the original `TimeoutError` type is preserved, and that an early task exception is chained by identity. A fake pass validates only this diagnostic boundary; it is not evidence about the historical production failure. The full integration test remains unrun until a serial quiet window and separate authorization.

### R3 compatibility addendum (tasks T112–T115)

R2 used `BaseException.add_note`, which is unavailable on Python 3.10 even though the project supports Python 3.10. R3 changes only how the test-local diagnostic text is attached: it replaces the caught timeout's `args` with one diagnostic string. The same timeout object is raised, and an already-failed task's exact exception remains its `__cause__`. This compatibility correction does not identify or fix the historical failure.

The private regression uses a synthetic timeout exception class with no `add_note` method. It checks exception identity, cause identity, the unchanged 1.0-second deadline, and the early-return and pending-task failure states. R2 and its receipt remain unchanged.


## Feature Specification: Local Claude Unicode Credential Retention

**Development target**: `main` (no commit)

**Created**: 2026-10-16

**Status**: Integrated in main; main verification and live acceptance pending

**Input**: User direction to prepare a safe, private-only local Claude Unicode matrix lane that preserves its staged credential until confirmed shutdown and then archives the private copy.

### User Scenarios & Testing

#### User Story 1 — Preserve the selected Claude matrix credential (Priority: P1)

An operator opts in to the existing, explicitly selected local `claude_code -> claude_code` native-child matrix cell with its Unicode run-step instruction. The run’s isolated credential copy remains in a durable private workspace until the existing session, API process, and owned tmux server have all been confirmed stopped. After that teardown proof, the copied credential is archived before removing the workspace, even when the functional matrix assertion failed.

**Why this priority**: The lane must retain its private auth copy for investigation without changing the operator’s login or auth staging.

**Independent Test**: Exercise the actual fixture and cleanup helpers with fake files and fake owner/process boundaries; make no provider, HTTP, PostgreSQL, or external-manager calls.

**Acceptance Scenarios**:

1. **Given** the exact Claude self-cell and an existing owner-owned private evidence root, **when** the fixture prepares its run, **then** it creates a unique workspace outside pytest’s temporary tree with `workspace/home/.claude/.credentials.json` as the canonical credential path and private directory/file modes.
2. **Given** the exact selected cell, **when** session deletion succeeds and teardown confirms API-process stop/owner removal and owned empty-tmux cleanup, **then** the private credential is archived outside `workspace` before that workspace is removed, regardless of the functional matrix outcome.
3. **Given** a missing or invalid evidence root, failed session deletion, uncertain API stop, unconfirmed tmux cleanup, or archive failure, **when** fixture setup or teardown reaches that boundary, **then** auth is not copied before root preflight and the durable workspace is retained without archiving on an unconfirmed path. A functional assertion failure alone does not disable archival after all teardown proofs succeed.
4. **Given** a non-Claude cell or an ordinary offline/default test run, **when** the matrix fixture is evaluated, **then** its current temporary-workspace behavior and provider/test selection remain unchanged.

#### Edge Cases

- The evidence root is missing, relative, a symlink, not a directory, owned by another uid, or not mode `0700`.
- The required credential file is absent, replaced, linked, multiply linked, oversized, or has an invalid owner/mode.
- The Claude API-key environment path is selected instead of the file credential required by the archive lane.
- Session deletion fails; the API stop raises or remains registered; the tmux root is nonempty, foreign, or cannot prove its server exited.
- The archive destination already exists, changes identity, or cannot be synced.
- The functional test fails or skips, while session deletion and teardown may still independently succeed or remain uncertain.
- Workspace removal fails after the archive has been committed.

### Requirements

#### Functional Requirements

- **FR-001**: Durable retention MUST be opt-in and limited to the explicitly selected `claude_code -> claude_code` matrix cell. It MUST NOT change matrix selection, model, prompt, timeout, Unicode marker, scenario criteria, or quota handling.
- **FR-002**: The archive-enabled cell MUST require an explicit evidence-root setting and validate an existing absolute, real directory owned by the current user with mode `0700` before server startup or auth copying. The durable root and workspace MUST be outside pytest’s temporary base. Missing or invalid roots MUST fail closed.
- **FR-003**: The isolated server HOME MUST live below that durable evidence root, with the canonical relative layout `workspace/home/.claude/.credentials.json`; the workspace/home/`.claude` directories MUST be mode `0700` and the copied credential MUST be mode `0600`.
- **FR-004**: The operator auth source and any staged source file MUST remain byte- and metadata-unchanged. No credential bytes, raw provider output, or credential-bearing path may be placed in test/JUnit evidence.
- **FR-005**: For the opt-in Claude self-cell, the private copy MUST be archived using the existing fixed `message` layout only after successful session deletion and teardown confirmation that the CAO process stopped and was deregistered and the exact private tmux cleanup succeeded. Functional assertion status MUST NOT gate archival. Workspace removal MUST occur only after archive success.
- **FR-006**: Any missing/failed session deletion, uncertain API stop, unconfirmed tmux cleanup, or archive failure MUST retain the durable workspace and MUST NOT claim archival success. A functional test failure or skip alone MUST NOT retain credentials after full teardown proof. Cleanup failure MUST remain a test failure or preserve the original test outcome with a fixed sanitized cleanup note.
- **FR-008**: This opt-in covers only the fixed `.claude/.credentials.json` copy in the exact Claude self-cell. It MUST NOT archive `.claude.json`, other auth records, or credentials from other Claude-containing matrix cells.
- **FR-007**: Default/offline behavior and all non-Claude matrix cells MUST preserve their existing temp-root and auth behavior.

#### Key Entities

- **Evidence root**: Existing owner-private durable directory explicitly supplied for this opt-in cell.
- **Run workspace**: Unique isolated CAO HOME and runtime files; retained whenever teardown or archival is unconfirmed, independent of functional assertion status.
- **Private credential copy**: The `.credentials.json` copied into the isolated HOME; never the operator source/stage.
- **Archive copy**: Exclusive mode-`0600` copy under the evidence root, outside the workspace.
- **Teardown proof**: Successful session delete, stopped/deregistered API owner, and confirmed cleanup of the exact empty private tmux server.

### Success Criteria

#### Measurable Outcomes

- **SC-001**: A fake successful fixture path produces exactly one archive copy with byte equality and mode `0600`, and removes the workspace only after archive completion.
- **SC-002**: Each injected failure at preflight, session deletion, process stop, tmux cleanup, or archive leaves the durable workspace present and no archive is claimed on an unconfirmed boundary; a separate injected functional assertion failure with successful teardown still archives and then removes the workspace.
- **SC-003**: Source and stage file identity/content snapshots remain unchanged across copy, archive, and cleanup tests.
- **SC-004**: Offline/default and non-Claude contract tests show no new archive-root requirement or behavior change.
- **SC-005**: The later authorized real run remains limited to the exact Claude self-cell and retains its existing four substantive matrix scenarios and quota not-applicable outcomes unchanged; offline contracts alone do not constitute live-provider acceptance.

### Assumptions

- The operator has already configured the existing isolated auth home and the selected file-based Claude credential; this feature never discovers or edits operator auth.
- The archive root is prepared by the operator/harness before the live cell starts; the test may create only fixed private child directories beneath it.
- The existing archive helper’s `message` layout and stability/ownership checks remain the sole credential-copy implementation.
- This does not enable archival for other Claude-containing cells and does not cover `.claude.json`.
- This candidate does not reconcile or validate an external job manager’s Claude/OpenCode run-step status.


## Measured catalog-rendering evaluation — 2026-10-18

The current covered diagnostic measured a 1.146-second memory body, including
21 fresh catalog reads (619 ms) and 21 manifest hashes (71 ms). Vault config
loading totaled 3 ms and is not the selected optimization boundary. Evaluate
one statement-local PostgreSQL 18 hypothesis: render eligible equivalent
NOT NULL definitions once per fresh signature, then restore every original
constraint row/name/order. Use the native renderer, the existing FR-045
attribute/array guards and all five definition flags. No definitions or catalog
results may survive the statement. Other types/shapes and non-18 versions keep
the individual native path. Preserve all FR-044 checks, commits and thresholds.
A diagnostic gain alone is not acceptance; reject the candidate if exact
nine-family/digest, snapshot or failure semantics differ, or if the measured
benefit is not substantial and repeatable.

## Benchmark preparation isolation — 2026-10-18

An explicit fixture may collect unreachable Python objects after native database
and temporary-path preparation and before the unchanged memory benchmark body.
Garbage collection must remain enabled with unchanged thresholds during the
operation. Do not warm MemoryService, connections or catalog plans, change timer
boundaries, suppress collections inside the operation, or lower any FR-044 gate.
Record this as benchmark preparation isolation, not a product speed improvement.
Three fresh covered processes and the full backend remain required.

## Capacity phase attribution — 2026-10-18

Capacity diagnostics must include the expiry-before-read transaction and the
read-connection setup (including Work verification) which precede the traced
metadata SQL. Preserve the existing confirmed-registration clock, workload,
concurrency, fresh checks and strict two-second assertion. Diagnostic coverage
does not itself resolve or explain the historical full-suite latency failure.

## Unicode credential boundary — 2026-10-18

Untrusted non-ASCII token headers and invalid non-ASCII configured service/runtime
tokens must never reach hmac.compare_digest's string overload. Treat these
ASCII token protocol credentials as nonmatching, preserving authentication and
human/service ownership separation. Preserve exact constant-time comparison
for valid ASCII tokens. Unicode prompts, human text and answers remain valid.
This is a separately reproduced boundary defect; it does not establish the
root cause or reconciliation of historical Claude run-step errors.

## Packaged UI completeness — 2026-10-18

Wheel and sdist build jobs must generate both Web UI and all four MCP App HTML
resources from checked-in source before packaging. A clean checkout must not
silently omit ignored generated assets. This changes preparation only and grants
no commit, workflow dispatch, upload or publication authorization.

## Scope authority source-auditor compatibility — T125

The static interaction-authority audit MUST recognize the canonical shared
`ascii_token_matches` guard at the existing live route and verified-human
boundaries. It MUST verify that the bound helper rejects non-ASCII `presented`
and `expected` values before calling `hmac.compare_digest`, rather than require
the comparison to remain inline in each endpoint. Negative regressions MUST
reject both a weakened helper without the ASCII checks and a route module rebound
to a different helper. Existing exact-route recognition and runtime authority,
scope, identity, and token behavior remain unchanged; this is a test-auditor
correction that preserves the product permission checks.


## Independent terminal telemetry in required-wait composition — T126

The required-wait integration fixture must model terminal telemetry independently
of the persisted interaction ledger. Real source/native/required wait checks and
all acceptance/MCP-pull assertions remain active. Preserve the 1 s startup
signal limit, 2 s active-task budget and the pending wait beyond that budget.


## Deterministic lost-response recovery fixture — T127

The remote recovery test must cause a transport-acknowledgement failure after
bridge handoff independently of the operation authority deadline. Use the normal
command budget and keep the real durable bridge/store/fence path. Prove one
delegated native send_keys paste, successful durable settlement, replay of that
same result, unchanged stored state/result and no additional paste.


## Typed Work authority and message projections — T128

The configured `mypy src/` gate must pass without suppressing diagnostics or
changing mypy.ini. Express the already-validated grant job identity, recursive
JSON body-free projection and exact send/receive/ack request models explicitly.
Preserve job authority validation, message-body omission, operation rejection
precedence and concrete Pydantic model validation.


## Catalog JSON representation experiment — T129

Evaluate the server-side construction of the routines definition without changing
the verified catalog meaning. Preserve all nine families, fresh verification,
21 Work checks, 20 separate audit commits plus the initialization commit, runtime
role and context checks, and the strict one-second requirement. Any candidate
must preserve decoded values and their numeric types, normalization/digests,
argument ordering, Unicode, NULL/empty aggregates and unsupported-routine
rejection. Keep identity_arguments as JSONB because SQL orders by that field.
Experiments remain private diagnostics until root review, semantic parity and
unchanged final acceptance establish a safe improvement.


#### T130 diagnostic follow-up: share target catalog subsets

The strict loaded memory NFR remains open after T129 was rejected. Evaluate
whether one fresh catalog statement can reuse materialized subsets of target
constraints, column defaults, indexes, policies and rewrite rules between their
family projections and routine-dependency EXISTS clauses. This is distinct from
the rejected T082 target_objects(classid,objid) rewrite. Preserve all nine
families, exact projected types/values/order, routine classes/keys/OR/UNION and
subobject treatment, runtime-role/context verification and audit durability.
Never restrict family rows to objects with dependencies. No result/proof cache,
query-count reduction, commit removal, threshold or worker change is allowed.
A private experiment is not a delivered product change or NFR acceptance.


#### T131 QEMU process-exit proof race

Current R2 QEMU acceptance reproduced a supervisor boundary failure after a
post-upstream SIGKILL: both pidfds were readable, but the immediate
`Popen.wait(timeout=0)` was not yet able to return a child status. The existing
wait(timeout=8) returned UNCERTAIN immediately instead of using its remaining
budget. Correct this only if owned monitor/init exit and eventual child status
are proved within the original deadline. Unknown ownership, pidfd errors,
reaping errors and deadline exhaustion must remain conservative. Never infer
TERMINATED merely from elapsed time or relax the security/test assertions.
