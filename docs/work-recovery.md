# Recuperación y compatibilidad de trabajo durable

## Esquemas v1–v13

Las tablas `work_*` son aditivas y no reescriben terminales, recibos ni hijos nativos.
El ledger `work_migrations` registra versión, checksum y verificación. El escritor
compara tablas, columnas, constraints e índices con el esquema esperado y comprueba
referencias antes de aceptar el store. Una creación interrumpida revierte toda su
transacción. Una instalación parcial externa, un checksum distinto o una versión
desconocida se rechaza; nunca se marca como aplicada ni se repara por conjetura.

La versión 2 añade principales, grants inmutables y revocaciones. Conserva el
checksum, timestamp y contenido de la migración 1. Antes de actualizar, verifica
la historia completa existente; después verifica cada versión dentro de una sola
transacción. Un fallo durante el DDL revierte todas las tablas y entradas nuevas.
Cada ampliación conserva las entradas anteriores del ledger:

| Versión | Ampliación |
|---|---|
| 1 | Jobs, items, intentos, resultados y eventos |
| 2 | Principales, grants y revocaciones |
| 3 | Reservas de rutas y fencing |
| 4 | Contratos efectivos de steps |
| 5 | Cola, capacidad y presupuesto del scheduler |
| 6 | Referencias a evidencia de worktrees |
| 7 | Revisiones y decisiones de conocimiento |
| 8 | Frontera de entrada para reparto justo del scheduler |
| 9 | Snapshots acotados e inmutables y referencias a revisiones de conocimiento |
| 10 | Binding inmutable de contrato, grant y snapshot por intento/generación |
| 11 | Datos de entrega inmutables, versión del adaptador y hashes de petición/entrega |
| 12 | Auditoría inmutable de accesos a conocimiento, separada del CAS de cada registro |
| 13 | Intenciones, resultados y denegaciones de acceso a memoria legacy, sin contenido |

La versión 11 no reconstruye mensajes ni parámetros que la versión 10 no guardó.
El dispatcher registrado deja esos bindings antiguos en cola, sin consumir plazas
ni inferir un payload. Las operaciones nuevas guardan entrega, binding y cola en
la misma transacción. El replay compara la petición original incluso si su
adaptador ya no está instalado; ejecutar exige la versión registrada y datos
recuperables por ese modelo. Los eventos contienen hashes, nunca el payload.

La versión 8 añade una columna e inmutabilidad; no modifica el DDL ni el checksum
histórico de la versión 5. Un escritor antiguo debe rechazar una versión posterior
desconocida. Las tablas legacy continúan disponibles para sus lectores.

Los registros legacy permanecen en sus tablas originales. No se convierte `idle`
en éxito ni `acknowledged` de un hijo antiguo en acuse de recepción de una tarea.
Eliminar un terminal no borra el historial `work_*`: `terminal_id` es una referencia
de observación, no una clave con borrado en cascada.

## Migración Work v25→v26 y restauración aislada

La migración Work v25→v26 es aditiva: conserva las tablas y entradas del ledger v25
y añade el esquema v26. El DDL y el avance del ledger se confirman en una transacción.
La prueba de integración verifica que, si falla el DDL, la base permanece en v25 y
no queda creada la tabla nueva. Después de un commit v26, el verificador v25 falla
cerrado; no se hace downgrade SQL ni se ejecuta un escritor v25 sobre ese store.

Un bundle publicado v1/perfil 25 sólo acredita integridad histórica. No contiene la
evidencia requerida para autorizar restore y no puede activar T070. El restore privado
T070 acepta un bundle publicado `recovery-bundle-v2` de perfil 26 con su receipt y
evidencia obligatoria verificable. Evidencia incompleta, corrupta o mezclada con
versiones incompatibles falla cerrada.

Tras un reinicio, el lease v2 debe revalidarse contra el estado durable vigente
antes de cualquier operación o copia; la evidencia guardada en el bundle no
sustituye esa revalidación.

T070 crea una copia aislada y deja su estado `blocked_restore`. Reconcilia sólo
intentos `sent`, `acknowledged` o `running`; no relanza intentos ni llama proveedores.
La prueba integra captura real y restore a rutas distintas y confirma que el origen
permanece intacto. La copia recuperada no se reactiva ni se instala implícitamente
como base del operador; la reactivación requiere un procedimiento aparte.

## Rollback operativo

1. Detener admisiones y escritores de la versión nueva. Confirmar por separado el
   cese de procesos externos: detener el servidor no demuestra detener un CLI.
2. Conservar una copia consistente de SQLite y de todos los artefactos referenciados.
   No copiar solamente el archivo principal de una DB activa con WAL.
3. Después de confirmar v26, operar `work_*` sólo con una versión compatible con v26.
   El verificador v25 probado rechaza el esquema v26; no se debe intentar continuar
   como si el store hubiese vuelto a v25. Las tablas legacy no se eliminan.
4. Para reactivar el escritor nuevo, verificar de nuevo esquema, ledger, referencias
   y resultados. Las operaciones posiblemente enviadas requieren conciliación; no
   reenviarlas por ausencia de terminal o por restaurar una copia antigua.

No existe downgrade SQL: no se borran tablas ni se altera el ledger o sus checksums
para admitir una versión antigua. Si se necesita volver a v25 después del commit
v26, sólo se usa un snapshot v25 aislado, con conciliación explícita del estado
posterior y autorización separada conforme a T070. T070 acepta únicamente bundle
v2/perfil 26 y no restaura snapshots v25. Nunca se hace downgrade in-place ni
`DROP`; tampoco se restaura por conjetura ante errores. Este store usa SQLite, igual
que el journal actual; no promete soporte PostgreSQL.

## Límites operativos actuales

Una reserva vencida no prueba que un proceso haya cesado: ni rutas ni capacidad se
reasignan automáticamente. La liberación exige evidencia de cese verificada por el
servidor. Un worktree con cambios se conserva en cuarentena; archivarlo tampoco lo
elimina. El recolector de artefactos protege referencias de resultados y worktrees.

Las revisiones de conocimiento y sus decisiones son inmutables. Un tombstone impide
entrega posterior mediante el servicio, pero no elimina físicamente el contenido
histórico de SQLite ni de copias anteriores. No constituye borrado criptográfico.

La verificación de esquema y las pruebas de restore aislado no demuestran el cese de
procesos externos: detener el servidor no prueba que un CLI haya terminado. Tampoco
demuestran uso seguro de proveedores, activación mediante ingress público ni una
guardia global que cubra todas las entradas. Por ello, el restore probado conserva
`blocked_restore`; la reactivación y el rollback operativo requieren validación y
autorización separadas antes de declarar recuperación de producto.

## Frontera de memoria (T040)

`MemoryService(knowledge_policy=...)` utiliza la autoridad versionada existente:
proponer no aprueba ni publica instrucciones. Cada operación valida principal,
proyecto/job, capacidad y cadena vigente del grant en la misma transacción del
acceso. Los consumidores HTTP `/v1/knowledge` utilizan esta fachada; MCP selecciona
la autoridad configurada por `CAO_MEMORY_API_URL`, sin fallback local ante error.

La auditoría v12 guarda actor, acción, resultado, instante y hashes de autoridad y
objetivo (incluyendo registro/revisión). No guarda contenido. Lecturas e instrucciones
requieren escritura de auditoría antes de devolver datos; si falla, el acceso falla.
Las denegaciones de la fachada se registran tras revertir la operación. Estas filas
no avanzan las versiones CAS ni sustituyen los eventos de revisión del conocimiento.

La memoria de archivos legacy queda restringida al operador local, con autenticación
desactivada y sockets locales verificados para HTTP. Un JWT, incluso admin, no permite
usar `/memory`, `/internal/memory`, contexto de terminal ni proyecciones/exportación
del grafo de memoria. Sus herramientas MCP pasan por HTTP también en modo local.
Para acceso autenticado compartido deben usarse `/v1/knowledge`, sus herramientas MCP
y grants explícitos; no se convierten archivos legacy en revisiones aprobadas.

La redacción común precede al almacenamiento y a los límites de contexto; también
protege recuerdos históricos y contexto devuelto por el curador. Los archivos legacy
conservan su almacenamiento y carácter no aprobado; no se convierten automáticamente
en revisiones de conocimiento. Sus operaciones usan ahora la auditoría v13 en la
misma SQLite de sus metadatos, incluyendo archivos, relaciones, compilación, lint,
reparación y grafos antes de consultar caché. La clave del grafo incluye las rutas
resueltas del wiki y SQLite, además del scope y modo de lint: dos almacenes no
comparten una proyección aunque pidan el mismo scope. La preparación verifica/migra ese store antes del primer
acceso por instancia; no se inventan grants ni bases paralelas. Se requiere SQLite
persistente: una base en memoria no acredita auditoría durable.

La intención `authorized` se confirma antes del efecto o de leer contenido; después
se registra `completed` o `failed`. Una denegación a un principal verificado registra
`denied`; si falla esa escritura, HTTP responde disponibilidad (503), no un 403 que
aparente decisión registrada. Los fallos de política/auditoría no se ocultan como
enriquecimientos opcionales. SQLite y archivos no son una transacción conjunta:
un fallo posterior al efecto deja intención durable y error explícito de resultado
potencialmente parcial, con `operation_id`. No debe inferirse que no hubo escritura
ni repetirse automáticamente. El log legible anterior sigue siendo secundario.
La replicación y recuperación entre nodos siguen en tareas posteriores.

La reparación local/de arranque autoriza y audita tanto `plan` como `apply`.
El dry-run conserva archivos, índices y metadatos de memoria; escribe únicamente
su auditoría y prepara aditivamente el esquema durable cuando hace falta. Con
autenticación habilitada, startup no fabrica identidad de operador para reparar.
Lint redacta antes de truncar/enviar texto al modelo y conserva el mismo SQLite
para la auditoría y las relaciones derivadas. Los detectores siguen leyendo
metadatos mediante una sesión protegida contra escrituras.
