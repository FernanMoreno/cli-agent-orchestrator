# Implementation Plan: trabajo colectivo verificable

**Branch real**: `main` | **Feature**: `001-verifiable-orchestration` | **Date**: 2026-09-22
**Spec**: [spec.md](spec.md)
**Status**: diseño aprobado; implementación incremental en curso. El estado por tarea y la evidencia acotada se mantienen en tasks.md y workflow-status.md.

## Summary

El programa cubre los 19 ejes y seis pendientes operativos sin presentar planificación
como implementación. Se entrega por siete historias verificables, reutilizando los
recibos, journals, adaptadores, memoria y superficies existentes.

Se adopta migración incremental: un dueño de estado durable por operación; los modelos
antiguos se convierten en proyecciones compatibles. No se hace una reescritura global
ni se mantienen dos autoridades independientes que puedan discrepar.

## Technical Context

**Language/Version**: Python >=3.10 (entorno local 3.12), TypeScript y Rust existentes.
**Primary Dependencies**: FastAPI, Pydantic, SQLAlchemy, SQLite; React/Vitest y TUI Rust.
**Storage**: persistencia actual y artefactos locales; escritura multinodo a través de
una autoridad por proyecto. No se añade una DB distribuida para la primera entrega.
**Testing**: pytest, SQLite real temporal, subprocess mock_cli/tmux aislado, Vitest,
cargo test y contratos compartidos. PostgreSQL desechable si se toca la ruta SQLAlchemy
que afirma soportarlo; no extender esa promesa a journals actualmente SQLite.
**Target Platform**: plataformas soportadas por los backends actuales; WSL es entorno
de desarrollo existente, no evidencia de equivalencia de todos los backends.
**Project Type**: servidor, CLI/MCP, web y TUI.
**Performance Goals**: reservas atómicas con diez contendientes y dos plazas; equidad
de US2; consultas paginadas y colas acotadas. No inventar objetivo de throughput sin baseline.
**Constraints**: sin proveedores implícitos, sin consumo facturable en CI ordinaria,
sin pérdida del trabajo local, sin merges, commits ni releases no solicitados.
**Scale/Scope**: cinco entradas de trabajo, siete historias, 25 requisitos funcionales.
No se promete HA ni escritura offline multiwriter.

## Constitution Check

- I: decisiones basadas en código; Graphify se verifica contra fuentes.
- II: worktree con cambios preexistentes preservado; no se cambia rama ni historial.
- III: esta secuencia crea spec, plan y tareas antes de tocar implementación.
- IV: los escenarios por frontera se enumeran en contracts/ y quickstart.md.
- V: no hay signoff de producto ni de release a partir de estos documentos.
- Decisión de conocimiento: contratos y razonamiento viven en estos artefactos.
  No se duplica el grafo ni se guarda en Obsidian un diseño aún en revisión.
- Hooks: no existe .specify/extensions.yml; no se ejecutan hooks.
- La constitución inicial era plantilla: se materializan las reglas ya vigentes en
  AGENTS.md/AI_WORKFLOW.md como versión 1.0.0, sin introducir precedencia nueva.

## Architecture and delivery boundaries

### US1: identidad, evidencia y recuperación

Crear modelos de dominio y reducer puros, repositorio transaccional y servicio de trabajo.
Cada mutación persiste estado y evento en la misma transacción. El envío externo ocurre
después del commit de intención; no se mantiene una transacción durante I/O del proveedor.
La frontera incierta se concilia, nunca se convierte en una promesa de exactly-once externa.

Integrar por separado launch, inbox, hijo, handoff y workflow. Un adaptador importa referencias
antiguas sin fabricar acuses ni resultados. Retener todos los intentos; no reutilizar una
fila por terminal como historial. Artefactos inmutables se escriben antes de referenciarlos,
con barrido posterior de huérfanos; ningún resultado apunta a contenido aún no durable.

La decisión humana T093 del 2026-09-24 autoriza sólo el contrato interno por fases.
El operador `Principal` autenticado, registrado y admin dueño del job es el único
provisionador de launch mediante interfaz administrativa interna. La revisión activa
inmutable `(principal_id, selector, revision)` es la fuente durable de job,
grant/revisión, contrato/hash, snapshot/hash, adaptador y lease; selector, body,
nombre, sesión o `contexts` no constituyen autoridad. Resolver y admitir revalidan
la misma provisión y grant antes de reserva y efecto; retiro o sustitución bloquea
una orden nueva. El replay legacy sólo lee una orden exacta ya persistida.

### US2: autoridad, capacidad y planificación

Job congela allowlist; grant deriva de identidad autenticada del servidor. Descendientes
sólo reducen privilegios. Control de grant y generación se repite antes de cada efecto
controlado por CAO, no sólo durante creación. Revocación se registra y bloquea nuevos efectos.

Cada hijo y step de workflow gestionado tiene identidad de origen verificable y
grant propio, explícito, limitado al job, contrato, snapshot y acciones autorizadas.
El issuer/revoker es el operador dueño original; `delegate` requiere concesión viva
separada. Un port interno autenticado sella la resolución de origen; admisión
persiste en una transacción el vínculo exacto padre/hijo (intento y generación),
grants/revisiones, contrato/snapshot, entrega e idempotencia. Revalidar antes de
cada efecto controlable no promete deshacer un efecto externo ya iniciado.

Para T019, el intento Work padre obtiene una credencial interna aleatoria de alta
entropía creada y vinculada durablemente antes de lanzar el efecto externo.
`WorkRepository` guarda sólo su digest en Work DB, con instalación, principal,
item, intento, generación, revisión del grant, lease y expiración exactos; el
secreto bruto no se persiste ni entra en delivery, prompt, entorno, terminal,
logs o eventos. Un proxy MCP server-owned por intento recibe el secreto sólo por
descriptor privado heredado y llama al servicio de origen por socket privado.
El proxy presenta allí la credencial, nunca el proceso agente.
El backend Docker crea un contenedor por intento, sin montar el socket del
daemon en el worker, con rootfs de sólo lectura, red/IPC/PID aislados,
capacidades retiradas, `no-new-privileges`, recursos acotados y mounts mínimos
derivados del contrato. El endpoint proxy se expone sólo dentro del contenedor
propietario; el secreto sigue en el broker y el worker nunca recibe el socket
del daemon ni una credencial. Si Docker no puede garantizar un límite requerido
por el contrato, el backend rechaza antes de crear o iniciar el worker. Cada
petición al servicio revalida digest, binding, generación, grant, lease y
estado de recuperación vivos.

Docker tiene dos perfiles locales separados: el backend de runtime por intento
y una imagen de aceptación que ejecuta las pruebas Linux de Bubblewrap. Esta
última fija base e imagen, registra el kernel y exige Bubblewrap 0.13.0,
Landlock ABI >=9, namespaces de usuario y cuenta broker dedicada. Docker Desktop
comparte el kernel Linux de su VM WSL2; el resultado demuestra la composición
en ese entorno local, no certifica un host de producción. T097 completó la
aceptación del perfil Ubuntu QEMU guest y no existe host de despliegue previsto.
Por ello, `WORK_BACKENDS` queda vacío por defecto; sólo la composición local
explícita puede registrar el backend después de pasar sus gates. El backend
verifica versión
>=0.12.0 antes de preparar el sandbox y falla cerrado
con 0.11.1 o una versión indeterminada: el [aviso upstream
GHSA-pxhw-h44j-8pfx](https://github.com/containers/bubblewrap/security/advisories/GHSA-pxhw-h44j-8pfx)
describe CVE-2026-87766 por seguimiento de symlink durante setup y la corrección
en 0.12.0. Este host tiene `/usr/bin/bwrap` 0.11.1 y queda fuera de capacidad
Work con ese candidato. El backend debe traducir
`ProcessRestrictionContract` en restricciones ejecutables de rutas (incluidas
lecturas de runtime mínimas y escrituras allowlisted), comandos en cada frontera
de ejecución de procesos, red e IPC. Una dimensión no aplicable o no imponible
para el contrato concreto rechaza el preflight; si el contrato actual no expresa
un límite necesario, se versiona/extiende antes del registro. Una allowlist de grant o un
`preflight_work` exitoso por sí solos no prueban enforcement. El proxy y su
secreto permanecen fuera del árbol y espacio visibles al agente; sólo un
transporte IPC mínimo por intento puede estar expuesto, y otro intento no puede
abrirlo ni heredar endpoint/descriptores. No se registra ni activa el backend
antes de demostrar esos límites con procesos adversariales.

**Estado de implementación verificado al 2026-09-29:** el perfil local
`DockerWorkBackend` ejecuta un ELF estático en contenedor read-only, sin mounts
del host ni red, con supervisor por intento y cleanup. Un socket MCP privado
por intento transporta sólo las operaciones gestionadas autorizadas: el intento
padre puede admitir un hijo preprovisionado y el receptor puede aceptar su
entrega exacta. `WorkOrigins` vuelve a validar credencial, binding y grants; un
adapter `agent_step` se registra sólo en la composición interna sellada del
gateway. No se habilita ingreso público ni se da autoridad al camino legacy de
`utils/orchestration.py`. Las pruebas de Docker pasaron 4/4; la aceptación
Bubblewrap en guest QEMU pasó 8/8 sin skips. Ambos perfiles siguen fuera de
`WORK_BACKENDS`: esto cierra la integración local T019, no acredita un host de
producción ni habilita despliegues.

La identidad de host forma parte del límite Bubblewrap. El creador de cada user
namespace recibe capacidades dentro de ese namespace; por tanto, cambiar sólo
`uid_map` por intento no protege al worker de procesos host con el UID efectivo
del broker. El proceso CAO que crea namespaces y proxies debe ejecutarse bajo una
cuenta OS dedicada configurada por `CAO_WORK_BROKER_ACCOUNT`, no root, con shell
`nologin`/`false` y sin procesos ajenos. `BubblewrapWorkBackend.preflight_work`
verifica la cuenta local, su UID y shell frente al UID efectivo del proceso antes
de cualquier probe de Landlock o Bubblewrap; la falta de configuración o cualquier
discrepancia rechaza en cerrado. Esa cuenta se considera de confianza. Cada
contrato con tools exige una fábrica server-owned
que devuelva un `WorkMcpProxy` nuevo ligado al intento; sin proxy, preflight
rechaza antes de admitir. El sandbox no recibe red directa.

La aceptación reproducible corre en un guest Ubuntu 26.10 efímero bajo QEMU TCG,
orquestado desde el runner GitHub-hosted `ubuntu-24.04`. El workflow fija la
imagen cloud diaria por fecha y SHA-256, verifica el source archive upstream,
compila e instala Bubblewrap 0.13.0 como root:root 0755, configura sólo en el
guest los sysctls requeridos y crea la cuenta broker no interactiva desde
`CAO_WORK_BROKER_ACCOUNT`. Ejecuta la suite completa como esa cuenta y requiere
Landlock ABI >= 9, ocho casos sin skips y digest allowlisted. TCG usa
`T097_TEST_WORKER_TIMEOUT_SECONDS=45` porque dos pruebas exceden el timeout de
10 s bajo emulación. Esta aceptación valida el guest fijado, no el kernel del
runner ni hosts de despliegue. Cada despliegue sigue rechazándose en preflight
si su host no cumple los gates de Bubblewrap, Landlock, namespace y broker.

La aceptación de T097 requiere prueba de rechazo anterior al efecto con
Bubblewrap 0.11.1 y pruebas Linux aisladas con versión admitida, contratos vacíos y
no vacíos, rutas alias/symlink, ejecución descendiente, red/IPC y un intento
hermano que intente leer/escribir fuera de allowlist, ejecutar comandos fuera
de allowlist, conectar fuera de allowlist y acceder al proxy ajeno. Debe cubrir
el guard fresco en la frontera de cada efecto protegido, revocación o cambio de
generación entre preflight y efecto, launch/continuación/reinicio, muerte de
proxy, terminación y limpieza de procesos/mounts/sockets. Todo fallo parcial
de setup, guard o cleanup bloquea nuevo efecto o deja conciliación explícita
sin reissue ni redelivery. Sólo la plataforma probada obtiene capacidad Work;
hosts sin esa capacidad siguen rechazando. T097 no acredita la credencial v27,
el puente hijo/handoff, Work ACK ni la entrada pública propios de T019/T035.
`WorkAdmission._preflight` es el primer gate: exige backend Work registrado y
prueba de que intentos hermanos no pueden abrir su endpoint ni heredar sus
descriptores, antes de `create_work_session`/`create_work_window`. El registro
Work actual está vacío; Tmux y Herdr rechazan preflight y no se habilitan por
este diseño. No hay fallback a ellos ni a un token en entorno.
Revocación o reemplazo cortan nuevas peticiones. Tras reinicio, un proxy aún
vivo reutiliza su credencial sólo si el binding sigue vigente; si se perdió,
el intento entra en conciliación sin reemisión de credencial ni redelivery.

T098 compone el launch con el dispatcher registrado existente, sin convertir
la respuesta HTTP de admisión en permiso para ejecutar. El dispatcher recupera
la entrega persistida, reserva y pasa a `sent` con CAS, vuelve a validar la
autoridad al borde del efecto y delega sólo en el backend Work seleccionado.
El adaptador launch deja de usar `terminal_service` para esta ruta. El efecto de
proceso recibe un capability de servidor ligado al binding durable; no expone
los métodos legacy de sesión/ventana como alternativa. Bubblewrap registra su
ACK y la identidad exacta antes de GO, y `WorkProcessSupervisor` conserva la
identidad de cleanup. Si la autoridad cambia antes de GO, se aborta el setup;
si el cleanup no se confirma, el intento queda en conciliación sin redelivery.

La política de ejecución v1 acepta únicamente los ELF estáticos y contenidos
que coinciden con el mapping inmutable exacto del contrato. Archivos escribibles
se montan `noexec`, FS_EXECUTE se concede por identidad verificada y seccomp
deniega memfd y rutas de ejecución por descriptor que salgan del mapping. Un
loader dinámico invocado directamente y scripts con shebang se rechazan mientras
el contrato no incluya una clausura verificable de intérprete/cargador. Fork y
doble fork heredan Landlock/seccomp y permanecen bajo la identidad supervisada.
Las pruebas scratch prueban esa composición de código, no la aceptación QEMU.
El run QEMU verde cerró T097/C08 para el guest fijado, pero no registra el
backend para despliegue. `WORK_BACKENDS` continúa vacío por defecto. Una
composición local Docker puede registrar el backend para sus ejecuciones
opt-in sólo después de probar aislamiento por intento, recovery y proxy. Si se
define un host de despliegue en el futuro, deberá comprobar allí Bubblewrap,
ABI, namespaces y broker antes de registrar el backend en esa instalación.

El padre autenticado sólo selecciona refs de hijo y receptor preprovisionadas
por el operador; no recibe sus credenciales ni construye sus `Principals`.
`WorkOrigins` resuelve esas refs y verifica concesiones vivas, ámbito y acción
antes de sellar `OriginRequestV1`; hijo y receptor se autentican como actores
separados. `caller_id`, nombre, bearer local del operador y ACK nativo no son
fuentes de autoridad Work.

Sólo un receptor autenticado y registrado con acción `task_received` vigente
emite el acuse. El receipt versionado contiene receptor y autorización exacta,
delivery y hash, intento/generación vigentes, nonce/event-id único y prueba de
aceptación durable del receptor. Un owner del servidor lo autentica y persiste
junto al Work ACK en la misma transición de `WorkRepository`; un duplicado
idéntico es idempotente y uno contradictorio o tardío se rechaza. El acuse no
acredita ejecución ni resultado.
`native_children.acknowledged`, `send_input` y telemetría de terminal conservan
su significado legacy y no se elevan a Work ACK.

### T093: contrato interno versionado y secuencia aprobada

El contrato propuesto tras aprobación usa `OriginRequestV1` discriminado por
`launch|child|handoff|workflow`, `Principal` autenticado fuera del body, refs
exactas de sujeto/autorización y, cuando aplica, intento padre/generación,
intención no privilegiada, clave de idempotencia y revisión esperada. La salida
privada `ResolvedOriginV1` queda sellada por runtime con refs inmutables de
job/grant/contrato/snapshot/entrega, fingerprint y expiración; no es un token
bearer del cliente. `TaskReceivedReceiptV1` es un registro separado e
inmutable, único por delivery/intento/generación y por nonce/event-id.

Owners: `WorkProvisioning` conserva provisiones; `WorkOriginAuthority` conserva
sujetos/autorizaciones; `WorkOrigins` resuelve origen y lineage;
`WorkAdmission` vincula y encola; `WorkContracts` revalida antes de efecto;
`WorkRepository` es dueño de transición y receipt Work. T094 implementó el
owner del receipt autenticado de receptor junto a `WorkService` usando la
misma transacción de `WorkRepository`, nunca un booleano de HTTP.
Las tablas internas v19–v23 y el guard v24 ya existen en fuente; esta nota no
declara conectados los ingress. T094 añadió el receipt interno en v25 y T069
elevó el Work store a v26. Migraciones posteriores ya llevaron el schema a
v35; el digest de credencial T019 debe añadirse en v36, con checksum, unicidad
y FK compuestas, sin backfill legacy. La aceptación exacta del receptor necesita
registro durable separado antes de emitir receipt; añadirlo en v37, también sin
backfill. Fases T019: (1) RED de credencial, aislamiento y aceptación durable;
(2) digest v36 y creación durable previa al efecto; (3) aceptación v37 y puente
interno padre/proxy/origen/hijo/receptor con binding cercado; (4) backend Docker
por intento y aceptación Bubblewrap local en Docker; (5) reinicio, revocación,
replay, fallo parcial y mixed-version. Ninguna de estas fases activa ingress
público, proveedor real ni DB del operador.
T017, T019, T020 y T035 conservan gates independientes.

Rollback operativo desactiva nueva admisión, preserva historial e impone
conciliación para envíos inciertos; nunca hace downgrade SQL in-place.
Un lector/escritor anterior que no entiende el protocolo nuevo falla cerrado
y no ejecuta managed; legacy sólo conserva lectura/replay exacto sin ascenso
de autoridad. El rollback de v27 requiere código compatible o snapshot v26
aislado y conciliado; versiones mezcladas no ejercen la credencial. El restore
aislado T070 y la prueba de rollback T071 están implementados, pero no activan
reactivación automática. La aprobación
T093 no autoriza activar nuevas entradas públicas, MCP, proveedores reales ni
usar la DB de operador.

La ruta ordinaria actual `cao launch → POST /sessions → session_service →
terminal_service` no proporciona `Principal`, selector de provisión ni una
idempotency key server-owned para Work. El parámetro `idempotency_key` legacy
de `/sessions` es opcional y proviene del caller; no equivale al binding de
Work. Antes de implementar T017 debe aprobarse/componerse un ingreso interno
autenticado que entregue esas tres piezas y el handoff a `LaunchRuntime`;
no se derivan de `agents`, nombre de sesión, request body o `caller_id`.
Mientras falte ese contrato, el RED de no-efecto no puede recibir un GREEN
honesto y la ruta legacy permanece distinta del ingreso durable.

No equiparar allowed_tools ni prompt con sandbox. El contrato declara nivel de enforcement:
control-plane para operaciones CAO y aislamiento verificable para rutas/comandos/red del
proceso CLI. Si un backend no puede imponer una restricción requerida, rechazar admisión.
Un proveedor que ejecuta comandos locales arbitrarios no puede anunciar enforcement fuerte.

Reservas y presupuesto usan una transacción con límites y fencing. Scheduler comienza con
round-robin por job dentro de igual prioridad y aging entre prioridades; rechaza ciclos.
Cuota de proveedor, presupuesto de job y lease son estados distintos.

### US3: conocimiento y contexto

Extender memoria con identidad de revisión y decisión, sin convertir registros históricos
en aprobaciones. Encapsular publicación y consulta en una política común para rutas locales,
HTTP y MCP. Snapshot guarda exactamente el bloque redactado entregado; su hash identifica
ese bloque y distingue el hash de la fuente completa. Persistir antes de inyectar.

Resolver el fallo de baseline sin excluir arbitrariamente archivos del contrato: la prueba
unitaria debe controlar su repositorio; el producto conserva el rechazo explícito a un
baseline incompleto. Cualquier nueva política de exclusión requiere contrato explícito.

### US4: continuidad e intervención

Crear paquete versionado con refs y hashes verificables. Importar no ejecuta automáticamente.
El intento anterior debe estar detenido o cercado de forma comprobable; si puede reanudarse
solo por cuota, el nuevo intento no se admite sin conciliación. Aprobaciones enlazan revisión
de contrato, evidencias y efectos; decisiones idempotentes no reescriben autor ni fecha.

El gate de replay conserva una identidad inmutable del intento previamente observado sólo
cuando la política permite reejecutarlo. El callback de registro lleva esa decisión al writer;
éste compara la identidad dentro de la transacción que persiste el contrato y rechaza cambios
concurrentes. Un retry sin autorización de replay conserva las reglas estrictas existentes.
Las rutas API que crean o revocan decisiones Work exigen permiso de escritura antes de invocar
el dueño durable. Las pruebas usan SQLite real y fixtures inicializadas con el esquema Work
completo para verificar que la aceptación cubre el estado actual del contrato.

### US5: proyecciones y backends

Un DTO versionado conserva job, work item, attempt, turn y proceso por separado. API/MCP/CLI,
web y TUI consumen fixtures comunes; tokens visuales sólo proyectan ese DTO. Mantener campos
legacy durante transición; no renombrar TerminalStatus masivamente.

Preflight combina capacidades del adaptador, backend e instalación real; el catálogo no
autoriza. Implementar observación genérica de herdr para no nativos con pruebas de transporte,
o rechazar expresamente la combinación hasta que se demuestre soporte. O03 sólo se cierra
con soporte real, no con el rechazo transitorio.

### US6: autoridad multinodo y recuperación

Los clientes remotos leen y escriben a una autoridad por proyecto con ACL y compare-and-set
de revisión. Publicación durante partición falla de forma explícita; un cache local no
se presenta como commit remoto. Mantener versiones, conflictos y tombstones en recuperación.

Backup coordina DB, artefactos y memoria en un corte consistente; restore valida referencias
antes de habilitar ejecución. Migraciones son aditivas con versión y comprobación obligatoria;
rollback operativo usa versión de código compatible o restauración aislada, nunca DROP ciego.
Lectores viejos no pueden ejecutar trabajo gestionado por el nuevo formato.

#### Corte offline interno aprobado para T069 (sin activar transporte)

La autoridad Work, no la captura ni un caller, crea/revoca un lease durable
para un `Principal` de operador autenticado y autorizado específicamente para
esta operación. La identidad viene de una fábrica de autenticación verificada,
no de un DTO/body ni del fallback local implícito. El store y su identidad
se fijan server-side; el lease versionado conserva `lease_id`,
`store_identity`, `operator_principal_id`,
owner, alcance **sólo de writers Work registrados**, época/fence monotónicos,
TTL, estado/revocación y revisión. Alta/baja de writer, adquisición,
revocación y cambio de fence se serializan con el mismo gate transaccional;
cada efecto de writer registrado consulta el corte durable dentro de su
transacción inmediatamente antes del efecto. Se deniega un lease si hay un
writer registrado activo; bajo lease no entra otro. Un
writer ajeno al registro queda fuera de la garantía y jamás se informa como
quiescente. El registro debe cubrir los escritores Work de los objetos
declarados que se quieran acreditar; si un productor mutable no participa,
la captura no le atribuye estabilidad.

La matriz de productores de DB, resultados, delivery, snapshots y memoria
identifica qué escrituras pasan realmente por el registro/fence; la cobertura
del bundle clasifica cada objeto por digest y roles como `integrity-only`,
sin atribuirle quiescencia. Si el perfil requiere estabilidad de una referencia
fuera de registro, la captura falla cerrada.

La captura conserva staging privado: verifica primero el lease vivo y cero
writers registrados activos **antes de leer el inventario de origen**;
fija identidad del store, epoch/fence e inventario before y los revalida
después de copiar todos los objetos. Caducidad,
revocación, alta de writer, cambio de fence o inventario divergente abortan
antes de manifest/receipt. La comprobación final y promoción del bundle se
serializan con revocación/registro; un fallo entre SQLite y filesystem deja
un huérfano no elegible, nunca un receipt válido. Reinicio no revive el
lease por memoria de proceso: consulta estado durable y exige owner/fence
vigentes. El rechazo deja un registro durable con identidad del lease,
fase y motivo clasificado/no sensible, separado del manifest no publicado.
La evidencia canónica y hasheada del bundle liga lease, operador,
store, scope, época/fence, tiempos y pruebas before/after de cero writers
registrados e inventario, junto a los hashes de objetos y clasificación
de cobertura por objeto. El manifest v2 incluye `capture_id` y las tres
observaciones `before`, `after` y `promote`; el verificador las coteja con el
ledger y la fila de publicación durables del store server-owned. Una carpeta
huérfana sin esa fila no es elegible. El verificador comprueba esa evidencia
y sus límites, pero no prueba writers
no registrados ni concede restauración.

Compatibilidad: ampliar aditivamente el Work store v25 a v26 con
ledger/checksum/rollback probado, sin modificar ni degradar datos v25.
El inventario cerrado obtiene perfil v26 exacto: una tabla o FK desconocida
no se adopta implícitamente. Emitir `recovery-bundle-v2` con
`profile_version=26` y `cut_evidence.version=2` obligatoria, canónica y
ligada al hash del manifest; leer v1 v24/v25 sólo para integridad histórica
y nunca elevarlo a v2. Un lector o writer
sin soporte de v26/fence no opera durante el corte; despliegues mixtos y
versiones futuras fallan cerrados, no se confía en que un binario antiguo
respete el lease. Un fallo de DDL revierte atómicamente a v25. Tras commit
v26, el rollback de código sólo usa versión compatible con v26; jamás activa
writers v25 sobre ese store. Regresar datos a v25 requeriría snapshot v25
aislado, conciliación explícita de todo estado posterior y autorización
separada de restore T070: no hay downgrade in-place, DROP ni pérdida tácita.
Transporte queda fuera de este contrato.

### US7: operación, mantenibilidad y release

Dividir edición obsoleta de workflow y manifest efectivo por step. error_kind ya tiene
columna y migración; cubrir escritores faltantes y mantener fallback sólo para histórico.
Inventariar configuración con excepciones de enforcement explícitas; no cambiar autenticación
a settings por mera uniformidad.

Extraer módulos por contrato durante cada historia, conservar entrypoints como adaptadores.
Triage de specs se basa en código y pruebas, no en etiquetas. Upstream se prepara sin merge.
Matriz real registra cada escenario y proveedor, skips y errores; su filtro no es allowlist runtime.

## Project Structure

### Documentation (this feature)

- spec.md: requisitos y escenarios de usuario.
- plan.md: decisiones, entregas y fronteras.
- research.md: evidencia actual y alternativas.
- data-model.md: entidades, invariantes y transiciones.
- contracts/execution.md: admisión, recibos, eventos y autoridad.
- contracts/knowledge.md: revisiones, snapshots, continuidad y multinodo.
- quickstart.md: validaciones existentes y pruebas objetivo por etapa.
- tasks.md: tareas ordenadas, sin marcar implementaciones futuras como hechas.
- checklists/requirements.md: revisión de especificación.
- workflow-status.md: evidencia y estado real de cada gate.

### Source Code (repository root)

Rutas nuevas propuestas (no existentes por el hecho de nombrarlas):
- src/cli_agent_orchestrator/models/work.py: DTO y estados versionados.
- src/cli_agent_orchestrator/services/work_reducer.py: transiciones puras.
- src/cli_agent_orchestrator/clients/work_repository.py: transacciones de dominio.
- src/cli_agent_orchestrator/services/work_service.py: coordinación de operación.
- src/cli_agent_orchestrator/services/work_authority.py: grants y admisión.
- src/cli_agent_orchestrator/services/work_provisioning.py: provisión interna launch.
- src/cli_agent_orchestrator/services/work_origin.py: autoridad y lineage de origen.
- src/cli_agent_orchestrator/models/work_origin.py: refs versionadas de origen.
- src/cli_agent_orchestrator/services/work_scheduler.py: recursos y fairness.
- src/cli_agent_orchestrator/services/work_reservations.py: ownership de rutas.
- src/cli_agent_orchestrator/services/work_projection.py: proyección común.
- src/cli_agent_orchestrator/services/work_continuation.py: exportación e importación.
- src/cli_agent_orchestrator/services/work_decisions.py: decisiones humanas.
- src/cli_agent_orchestrator/services/knowledge_policy.py: publicación y acceso.
- src/cli_agent_orchestrator/services/knowledge_revisions.py: revisión autoritativa.
- src/cli_agent_orchestrator/services/delegation_snapshot.py: contexto universal.
- src/cli_agent_orchestrator/services/recovery_bundle.py: backup y restore.

Adaptadores existentes a modificar en su etapa:
- clients/database.py; services/terminal_service.py, inbox_service.py, agent_step.py,
  workflow_service.py, workflow_journal.py, step_output_store.py, memory_service.py,
  memory_gateway.py, config_service.py, settings_service.py, approval_store.py.
- providers/base.py, catalog.py, manager.py; backends/base.py, herdr_backend.py,
  tmux_backend.py; security/auth.py; api/main.py; mcp_server/server.py.
- utils/orchestration.py; cli/commands/launch.py, workflow.py.
- web/src/api.ts y componentes; tui/src/server.rs, renderer.rs; design-tokens/status.json.
- test/ sigue siendo ubicación de pruebas de producto; tests/ contiene infraestructura
  local preexistente y no se renombra.

## Delivery order and dependencies

1. Preparar baseline y verificar configuración de gates.
2. US1: modelos, migración mínima, reducer, repositorio, evidencia; integrar las cinco entradas.
   Antes de cerrar launch/hijos/workflows, aplicar el contrato interno T093 por
   fases y sus REDs; la decisión documental no sustituye la prueba de ingreso.
3. US2: capacidades mínimas, allowlist, grants, reservas y scheduler sobre US1.
4. US3: memoria y snapshots sobre US1/US2; controles locales de R14 aquí.
5. US4: decisiones y continuación sobre US1/US2/US3.
6. US5: proyección completa y herdr; catálogo mínimo ya usado en US2.
7. US6: autoridad multinodo, compatibilidad y recuperación sobre contratos estables.
8. US7: hardening y release; configuración, error_kind y triage pueden empezar antes
   cuando no modifican archivos compartidos con otra etapa.

Cada entrega repite AI_WORKFLOW.md completo para su diff. Un plan de programa no sustituye
prueba de composición ni revisión de los contratos de una entrega concreta.

## Verification and rollback

La secuencia de cierre es tests, composition-check, composición, diff, decisión Graphify,
decisión de conocimiento y verificación. quickstart.md define comandos y escenarios.
Antes de activar rutas universales, probar lectura de registros históricos y reinicio.
No activar ejecución nueva si falta migración, grant, snapshot requerido o versión compatible.
Exigir además origen exacto y receipt del receptor autenticado antes de afirmar
`task_received`; fallo de autenticación, generación o replay no debe transicionar.

Desactivar admisión nueva detiene crecimiento del estado; no entrega automáticamente trabajo
nuevo a ejecutores antiguos. Drenar intentos o conciliarlos antes de rollback.
Restaurar copias sólo en destino aislado y verificar antes de conmutar.

## Complexity Tracking

La escala requiere siete entregas; un commit monolítico no permitiría aislar fallos.
Se rechazan event sourcing integral, motor distribuido nuevo y reescritura de proveedores:
el estado transaccional más eventos durables cubre las garantías requeridas.
No se justifica una excepción a la constitución.
