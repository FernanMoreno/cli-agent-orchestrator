# Data model

## Scopes

Main contiene estado co-transaccional. Auth contiene cuentas/sesiones/limits/audit con rol separado. Peer es compartido por usuario y acotado por host. Cada scope tiene UUID durable, namespace, migration ledger y fingerprint. Secrets no forman parte de IDs públicos.

## Mappings

TEXT/IDs/JSON firmados se conservan; los enteros de dominio usan BIGINT (signed64), nunca se estrechan a PG INTEGER32; timestamps usan DOUBLE PRECISION cuando lo requiere el contrato; bytes usan BYTEA. Las identidades y secuencias PostgreSQL existentes se conservan, no se recalculan desde archivos de otra base. Boolean ORM PostgreSQL defaults boolean válidos. NULL uniqueness/partial indexes, FK/checks y triggers inmutables se expresan nativamente. DDL, ledger y checksums instalados permanecen sin cambios.

## Ownership and transitions

No cambios a lifecycle Work/interactions/receipts. Prepared→claimed→settled/uncertain se conserva y efectos externos usan claims/settlement sin replay; publicación offline-cut mantiene su fence durante el efecto según contracts/storage.md. Fuente/grant/version/CAS/receipt se validan dentro de una conexión main. Epoch de auth y revocaciones conservadas; recuperación controla reinstalación/sesiones según contrato actual. Peer PID sólo se compara en host propio.

## Historical PostgreSQL provenance receipt

El componente registrado `import_receipts` por scope y la tabla PostgreSQL
`_cao_import_receipts` son procedencia histórica instalada. Sus esquemas y filas
son inmutables y de sólo lectura: el runtime y la restauración pueden leerlas,
pero no crear recibos nuevos ni modificar, borrar o reinterpretar recibos
existentes. La retirada de SQLite no elimina la tabla ni su registro de
migración. Backup/restauración PostgreSQL conserva identidad/fingerprint/digest
de origen, UUID/schema/hash físico de destino, manifest y estados históricos.
La restauración verifica y conserva esos valores de origen sin sustituirlos por
la identidad física del destino nuevo.
