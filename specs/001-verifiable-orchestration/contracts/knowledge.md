# Contrato de conocimiento y continuidad v1 (propuesto)

## Publicación y revisión

Operaciones propuestas: propose_revision, review_revision, read_revision, tombstone_revision,
freeze_snapshot, export_continuation, validate_continuation, import_continuation.
Todas reciben principal efectivo y ámbito autorizado. Un body no puede declarar identidad
del publicador, proyecto o job que excedan ese ámbito. Retener versión fuente y evidencias.

proposed puede pasar a verified, approved o rejected mediante decisión autorizada.
approved sólo ocurre con evidencia examinada y autorización explícita; nueva revisión
vuelve a proposed. superseded identifica revisión reemplazada, sin borrado de historia.
Retención puede retirar contenido con tombstone; referencias responden estado de retirada.

Redacción se realiza antes de truncar o persistir cualquier texto destinado a compartir.
La política cubre todas las rutas, no sólo scope federated. Auditoría contiene identificadores,
operación y decisión, sin copiar secretos, prompts ni cuerpos de memoria.

## Snapshot

freeze_snapshot resuelve una vez, redacta, limita y persiste; sólo después entrega contenido.
Hermanos se enlazan al snapshot del padre/contrato, no resuelven cada uno memoria actual.
CAS resuelve carreras leyendo el ganador. Snapshot vacío es válido y no provoca fallback live.
Persistencia fallida de snapshot requerido bloquea delegación; para compatibilidad legacy
se declara ausencia sin atribuir garantías de snapshot a la ruta antigua.

Snapshot incluye versión, referencias de revisiones, hash de fuente y hash de contenido
entregado, flags de redacción/truncamiento y referencia durable de contenido. La fuente
completa puede diferir del contenido visible; ambos hashes tienen nombres y usos distintos.

## Continuación

Paquete: schema_version, source_attempt_id, contract, snapshot, artifacts, completed,
pending, uncertain, reason y package_hash. Los hashes cubren serialización canónica y bytes
de artefactos; no son firma ni autorización. El importador valida principal y grant vigentes.

Importar valida versión, límites de tamaño, hashes, ámbitos, rutas contenidas, artefactos
disponibles y capacidades del sustituto. No deserializa código ni ejecuta contenido del paquete.
Registra importación idempotente; otra versión con misma clave devuelve conflicto.
Sólo crea intento ejecutable cuando conciliación confirma cese/fencing del anterior.
Un elemento uncertain nunca se trata como completed ni se repite sin decisión explícita.

## Protocolo multinodo

Primera versión: autoridad única por proyecto, múltiples clientes con CAS de revisión.
Escritura: expected_revision, record_id, contenido y referencias; principal fuera del body.
Éxito devuelve nueva revisión y cursor; dos writes sobre revisión N producen un éxito
y un 409, no last-write-wins silencioso. Ausencia de autoridad produce 503, sin fallback local.
ACL se evalúa en autoridad para lectura, escritura, revisión, exportación y recuperación.

Recuperación: checkpoint y cursor versionados; páginas ordenadas incluyen tombstones.
Si el cursor caducó, respuesta explícita exige nuevo snapshot autorizado. Revocación
invalida acceso a páginas posteriores; cache no amplía permisos y expone frescura.
Versiones mayores desconocidas se rechazan antes de cualquier mutación.

## Backup y restore

Backup crea corte consistente de DB, eventos, snapshots, artefactos y memoria. Manifest
enumera versiones, referencias y hashes; no copia secretos de proveedores por defecto.
Restore escribe en destino aislado, verifica hashes y referencias, preserva revocaciones
y marca intentos antes activos para conciliación. No relanza trabajo automáticamente.
Rollback exige lector compatible o copia verificada; nunca borra historial para facilitar downgrade.

## Escenarios obligatorios

1. Propuesta no aparece como instrucción aprobada; revisión nueva no hereda aprobación.
2. Dos hermanos y reinicio reciben bytes idénticos del snapshot persistido.
3. Secreto en frontera de truncamiento no llega a snapshot, evento ni diagnóstico.
4. Manipulación de paquete/artefacto, path traversal o formato incompatible impiden importación.
5. Sustituto fuera de allowlist o intento anterior autocontinuable impiden ejecución.
6. Dos nodos sobre revisión común producen un conflicto; partición no crea commit ficticio.
7. Tombstone se propaga en recuperación y revocación impide lecturas posteriores.
8. Restore real temporal conserva referencias y no admite trabajo hasta verificar esquema.
