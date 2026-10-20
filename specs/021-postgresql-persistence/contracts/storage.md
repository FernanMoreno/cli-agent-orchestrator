# Native storage contract

## Configuration

Targets main/auth/peer son PostgreSQL explícitos con namespace e identidad; config/secret files privados y URL sanitizada. No `DATABASE_FILE` como identidad SQL, no SQLite fallback. Main y Work/interactions usan exactamente el mismo target/conexión; target de auth/peer separado. Ninguna credencial SQL se exporta a prompts o ambientes de agentes.

CAOS has no SQLite support in runtime, import tools, dependencies, packaged data,
functional fixtures or active examples. Explicitly retired SQLite commands and
flags reject before opening source paths, creating directories, taking an
installation lock or connecting to a database. A valid fresh install follows
its ordinary PostgreSQL flow without a generic SQLite warning. Unknown or
incompatible state fails closed and remains untouched.

## Driver / transaction

Conexión psycopg nativa síncrona y SQLAlchemy dialecto PostgreSQL; SQL escrito con %s binds, no interpolación de valores. Record tipado soporta nombre/posición de columna; cursor expone fetchone/fetchall/rowcount. Errores transaccionales conservan SQLSTATE internamente y mensajes públicos sanitizados. `read_snapshot` es REPEATABLE READ READ ONLY. Transaction conserva rollback y no reproduce efectos externos. Locks tienen scope/orden/timeout definido; snapshots no crean ni migran schema.

### Orden de bloqueos de Work

Las mutaciones asociadas a un trabajo toman el lock de su fila `work_jobs`
antes de bloquear o modificar sus items/intentos, insertar recibos con FK a
ellos, reservar secuencias de eventos o tomar el agregado del scheduler. Cuando
la operación requiere autorización, conserva la revalidación y serialización
de la cadena de grants antes del trabajo. Insertar un evento al final no
sustituye este orden: su FK puede necesitar `KEY SHARE` sobre el padre.

Una lectura inicial puede localizar el padre mediante identidades inmutables.
Después de obtener el lock se releen los estados y fences utilizados para
decidir: generación, revisión, lease, prueba de parada y autorización aplicable.
Las transiciones conservan CAS y verifican las filas afectadas. Una espera no
autoriza sobrescribir una revisión que cambió mientras se esperaba.

La aceptación reproduce con conexiones PostgreSQL independientes tanto la
liberación Bubblewrap frente a una transición como la admisión frente a la
liberación de un reintento. Ambas deben terminar sin deadlock y conservar
eventos, recibos y rechazo de estados obsoletos. No se corrige un ciclo de locks
ampliando timeouts, quitando locks de revocación ni repitiendo efectos externos.

## Schema

Owner único con baseline por componente, ledger checksummed y fingerprint pg_catalog de relation kind/persistence, columns/types/default/nullability/keys/constraints/index predicates/triggers/functions/sequences, RLS flags y policy roles/commands/expressions. Migración transaccional/advisory lock. Una versión desconocida o schema modificado falla cerrado. Ningún esquema o versión SQLite se consulta, convierte o usa como prueba de schema PG. Los DDL PostgreSQL instalados, sus ledgers y checksums son inmutables durante la retirada de SQLite.

## Deploy / native recovery

No existe importación de SQLite ni conversión de sus archivos. La recuperación
admitida usa backups PostgreSQL nativos y restaura a un destino físicamente
distinto. Validar todos los scopes, manifest, migraciones, checksums, datos,
relaciones, secuencias y recibos históricos antes de activar; conservar el
backup de origen. Un backup incompleto, incompatible o alterado falla antes de
activar y no modifica el origen, los archivos existentes ni el destino ajeno.
El estado existente desconocido falla cerrado y queda intacto.

El verificador del bundle necesita el target privado exacto de origen y compara
en lectura los datos publicados del offline cut y sus tres observaciones con
el manifest; si ese origen no está accesible, no se rebaja a verificación sólo
de hashes. La ruta registrada del bundle también forma parte de la publicación;
copiarlo a otra ruta requiere un flujo explícito de republicación, que no está
implementado. Durante una captura, el inventario, la inspección de secretos,
las referencias externas y `pg_dump --snapshot` comparten el mismo snapshot
PostgreSQL exportado; una lectura nueva posterior conserva la comprobación de
cambios y del fence. Rollback sólo a imagen/schema PG compatible. Managed
network/volumes/secrets con ownership específico, sin puerto público. Peer
service compartido no se elimina al retirar un perfil.

Los recibos de procedencia ya instalados en PostgreSQL permanecen inmutables y
de sólo lectura. No se borran sus tablas, filas ni DDL al retirar SQLite; se
incluyen y verifican en backup/restauración como datos históricos.

## API / orchestration

Legacy memory audit appends require a fresh, fully verified native transaction
after acquiring the shared Work store fence. A dedicated context may omit the
redundant verification before that transaction, but must retain the verification
after the fence, commit intent before the operation, and commit the terminal phase
before returning. The direct-connect implementation owns and closes its
physical connection; this remains the default for WorkRepository callers. It
rolls back on failure and retains the existing content-free, append-only audit
contract.

T055 permits an opt-in process-wide pool only for the private audit repository
created by `MemoryService._legacy_repository()`. Generic `WorkRepository`
instances retain T053's direct physical connect/close lifecycle. The manager
re-resolves the target on checkout and fails closed if the repository's cached
scope, role, or logical/physical store identity is stale; it never routes an
existing repository to another store. A private connection-provider seam and
memory-audit-specific repository factory select the pooled implementation; a
public WorkRepository pool switch is not part of the contract. A pooled audit
phase has an exclusive, bounded lease and still performs its own transaction,
store fence and full post-fence verification; the authorized and terminal rows
remain separate commits, and the lease is returned before filesystem effects.
The process cap includes idle and retired-but-still-leased sessions. Password
rotation retires old profiles without logging credentials or profile hashes;
in-flight work may finish, but a retired connection is discarded on return.
After `RESET ALL`, private pooled sessions set and validate
`plan_cache_mode=force_custom_plan` on checkout and return. This setting is
limited to the opt-in legacy-memory pool; generic WorkRepository connections
keep their existing PostgreSQL session defaults, and the setting does not
cache schema, identity, authorization, or verification results.
The same four-session cap also covers this private repository's initialization
and read-snapshot verification connections. Initialization still uses a fresh
repeatable-read, read-only transaction and full verification; generic
WorkRepository readers and general SQLAlchemy metadata sessions remain outside
this scoped counter. The profile includes resolved connection parameters,
private secret-file
identity, a process-keyed password HMAC, and effective libpq/TLS trust inputs.
The opt-in path rejects inherited libpq settings it cannot represent (including
service files, `PGOPTIONS`, alternate host routing, and custom SSL certificate,
key, root-certificate or CRL paths). For every TLS-enabled mode, it includes the
effective HOME-derived (or Windows `%APPDATA%`-derived) identities and keyed content fingerprints of default
`root.crt`, `root.crl`, `postgresql.crt` and `postgresql.key` files when present;
with `sslmode=disable`, it does not inspect TLS files. It does not inspect
unrelated operator files.
Errors, rollback/cleanup failures and uncertain commits invalidate the lease,
with no retry. Saved pooled connection/raw/cursor references become unusable
after lease return. Pooling must not cross into a caller-owned SQLAlchemy
transaction or any other WorkRepository writer.

The implementation uses a single native manager rather than per-profile
SQLAlchemy `QueuePool` instances because the four-session limit spans multiple
profiles, openings in progress and retired generations with active leases. The
manager owns those cross-profile reservations and hides the physical psycopg
connection behind the guarded lease. After fork, the child detaches inherited
socket descriptors in its own descriptor table before releasing libpq
references and rejects inherited wrappers by PID. A physical session remains
counted until close is confirmed; a close failure cannot free capacity.

API/MCP/CLI y receipt lifecycle conservados. Permisos se revalidan después de lectura antes de emitir AG-UI. Paginación mantiene frontera timestamp/ID exacta. Runtime propietario de terminal/sockets/sidecars sigue explícito; cambiar DB no habilita ejecutar sobre recursos ajenos.

## Source-review decisions

- Structural catalog verification may batch its independent reads into one
  statement snapshot. Every manifest category retains its exact ordering, types,
  normalization, completeness and integrity checks. Catalog results remain fresh
  on every verification; no stored manifest, migration checksum, authority fence,
  row-lock mode or timeout is weakened to improve performance.

- `Record` ofrece `keys()`, `dict(row)`, `tuple(row)`, longitud, nombre/ordinal y comparación explícita con tuple; cursor/RETURNING son nativos.
- PostgreSQL externo usa `verify-full` por defecto; `disable` sólo es configuración explícita de una red local administrada. Runtime y migrador son roles distintos; runtime no es owner, SUPERUSER, BYPASSRLS ni tiene CREATE. Principal/Work grants siguen siendo autoridad de aplicación.
- Identidad lógica UUID conservada en traslado; endpoint/schema verifican conexión física. `_cao_store_identity` pertenece al migrador. Los recibos históricos registran procedencia existente y epoch y se leen sin modificar. Fuente detenida/fenced antes de activar destino; no se permite operar ambos.
- No hay versiones/esquemas SQLite admitidos como fuente ni adaptador. Todo formato legacy desconocido, incluido el bundle con `sqlite-dump.json`, se rechaza sin mutación. Los bundles OKF modernos de directorio/tar siguen soportados en formato SQLite-free.
- Peer tiene un solo bootstrap owner por host/usuario, independiente del perfil. Procesos requieren reconciliación de PID/start-time/endpoint local; leases held/interrupted/reconcile se conservan con fencing explícito.
- Orden separado: terminal `insertion_sequence`, pregunta `question_sequence`, Work FIFO definido por baseline propio. Los valores, secuencias y filas existentes de PostgreSQL se conservan; no se vuelven a calcular desde archivos SQLite.
- ORM DateTime conserva la diferencia de columnas naive/aware y los valores legacy; sesiones PostgreSQL usan UTC. REAL epoch sigue DOUBLE PRECISION exacto. Las pruebas cubren round-trip, timezone y orden, sin reinterpretar silenciosamente timestamps legacy.
- Publicación de bundle bajo offline-cut tiene una excepción concreta al efecto fuera de TX: conservar el fence durante publish, con recibo/finalización/reconciliación crash-safe y sin retry del efecto. El port no puede desplazar esa frontera mecánicamente.
- Concurrencia requiere control/grant/root locks ordenados, CAS, capacidad/ausencia protegida por lock agregado, auth high-water/lockout serializados y session lifecycle entre procesos. No basta sustituir BEGIN IMMEDIATE por BEGIN.
- Las siete FK cíclicas identificadas en la fuente admiten `DEFERRABLE INITIALLY IMMEDIATE`; el resto conserva validación inmediata. El fingerprint CHECK normaliza exclusivamente la coerción equivalente de arrays de literales varchar sin límite a text[], que PostgreSQL reescribe durante pg_dump/pg_restore; conserva bytes de los literales y toda otra definición. No se actualiza un ledger modificado para hacer pasar una verificación ni se altera el DDL/checksum instalado para retirar SQLite.
