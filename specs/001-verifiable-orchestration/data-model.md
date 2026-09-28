# Modelo de datos propuesto v1

Estado: diseño. Los nombres siguientes no afirman tablas ni endpoints ya implementados.
Persistencia de estado y su evento comparten transacción. Fechas UTC provienen del servidor;
ID opaco, secuencia y generación son conceptos distintos.

## Entidades y relaciones

| Entidad | Campos e invariantes |
|---|---|
| Job | id, schema_version, project_id, principal_id, allowed_providers, grant_id, budget, priority, state, revision; allowlist inmutable por revisión autorizada |
| WorkItem | id, job_id, parent_work_item_id, operation_kind, idempotency_key, request_hash, contract_id, snapshot_id, state, revision, accepted_result_id; operación lógica estable |
| WorkAttempt | id, work_item_id, attempt_number, generation, provider, terminal_id opcional, delivery_phase, state, lease_expires_at, result_id, reconcile_reason; intentos históricos no se sobrescriben |
| Contract | id, schema_version, source_hash, repo_baseline, provider, model, effort, profile_revision, permissions, limits, retry_policy, hash; valores efectivos por operación, ausencia justificada |
| Result | id, attempt_id, schema_version, artifact_refs, validation_state, validator_id, validation_evidence, hash; éxito exige contenido recuperable y validación |
| Artifact | id, project_id, immutable_location, content_hash, byte_length, media_type, producer_attempt_id, source_revision; exportación bajo ACL |
| WorkEvent | event_id, job_id, work_item_id opcional, attempt_id opcional, sequence, event_type, schema_version, actor_id, occurred_at, metadata; payload sin prompts/secretos |
| Grant | id, job_id, principal_id, parent_grant_id opcional, revision, scope, permissions, enforcement_level, expires_at, revoked_at; hijos no amplían autoridad |
| Reservation | id, attempt_id, resource_key, quantity, generation, expires_at, state; suma dentro del límite, escritura exclusiva por recurso normalizado |
| KnowledgeRevision | record_id, revision, scope, scope_id, producer_principal_id, work_item_id, attempt_id, source_artifact_id, source_hash, evidence_refs, confidence, fresh_until, decision, supersedes, tombstone |
| Snapshot | id, schema_version, source_revision_refs, source_hash, delivered_hash, delivered_content_ref, redacted, truncated, created_at; contenido vacío también es snapshot |
| HumanDecision | id, work_item_id, contract_hash, evidence_refs, action, actor_id, reason, authorized_effects, created_at, consumed_at, revoked_at; auditoría inmutable |
| Continuation | id, schema_version, source_attempt_id, contract_ref, snapshot_ref, artifact_refs, completed, pending, uncertain, reason, hash; importación no implica ejecución |
| MigrationRecord | version, checksum, applied_at, verification_result; fallo impide admitir trabajo del esquema afectado |

Relaciones de parentesco y dependencias sólo apuntan dentro del job salvo referencia
externa explícita de lectura. El grafo de dependencias no puede tener ciclos. Prohibir
borrado en cascada del historial al eliminar terminales. Retención usa política y tombstones.

## Cuatro niveles de estado

- Proceso: alive / dead / unknown. unknown representa falta de observación, no inactividad.
- Turno: ready / input_sent / acknowledged / processing / blocked; se vincula a un attempt.
- Work item: queued / running / waiting_children / succeeded / failed / reconcile / cancelled.
- Job: planning / running / waiting / completed / failed / revoked.

`cancelled` amplía explícitamente el listado del roadmap: revocar job y cancelar operación
no son fallos de proveedor. La UI legacy mantiene sus enums durante migración.

## Transiciones y condiciones

1. queued se admite a running sólo con contrato, grant, capacidad y reservas confirmados.
2. El intento registra planned, sent, acknowledged, running y finished/failed/reconcile.
   Un acuse prueba recepción de la tarea, no creación del terminal. La observación puede
   avanzar directamente a running sólo con evidencia que implique recepción, conservando
   ambos eventos en orden; no se fabrica un acuse por falta de pantalla de actividad.
3. running puede pasar a waiting_children; resultado del padre espera política de join
   congelada. v1 requiere todos los hijos necesarios; fallo o conciliación de uno es visible.
4. Sólo resultado validado y durable permite succeeded. No basta digest o pantalla idle.
5. Lease expirado tras posible entrega pasa a reconcile y bloquea reemplazo inseguro.
   Renovación requiere generación actual y lease todavía vigente. No revive cancelados.
6. Cancelación registra intención y revoca nuevos efectos antes de pedir teardown.
   Resultado concurrente se resuelve por CAS; evidencia tardía se retiene sin cambiar el ganador.
7. Reintentar crea otro WorkAttempt. Si el anterior pudo ejecutar, sólo una decisión
   de conciliación documentada y fencing comprobable autoriza nuevo intento.
8. completed de job exige todos los work items requeridos con resultados aceptados.
   Cleanup pendiente se proyecta aparte; no confundir resultado con recursos liberados.

## Unicidad e idempotencia

- (job_id, operation_kind, idempotency_key) identifica petición; distinto request_hash es conflicto.
- (work_item_id, attempt_number) es único; generation aumenta y nunca se reutiliza.
- (job_id, sequence) y event_id son únicos; consumidores deduplican por event_id.
- Decisión idempotente conserva actor y fecha originales; argumentos diferentes son conflicto.
- Cada transición compara revision/generation y estado esperado dentro de la transacción.
- El resultado aceptado de un work item se fija una vez; una corrección requiere revisión
  explícita y mantiene el resultado anterior como evidencia, no lo sobrescribe.

## Grants, reservas y recursos

Las rutas se resuelven respecto al checkout autorizado, normalizando aliases y symlinks.
Reservas por ruta protegen también relaciones ancestro/descendiente; reservar un directorio
entra en conflicto con escribir cualquiera de sus descendientes. No se usan strings sin normalizar.
La reserva es cooperación de CAO; restricciones contra un CLI no confiable requieren
enforcement del backend o sandbox. Rechazar garantías que el backend no puede imponer.
Caducar reserva no demuestra detener al escritor externo: antes de reasignar se cerca
o confirma su cese. Archivar diff/untracked permitido por grant antes de limpiar worktree.

## Memoria y continuidad

Decisiones: proposed, verified, approved, rejected, superseded. verified acredita evidencia;
approved autoriza uso según política. Sólo approved puede ser instrucción compartida.
Las otras decisiones pueden consultarse como evidencia rotulada si ACL lo permite.
Revisión nueva no hereda aprobación automáticamente. Confianza no sustituye aprobación.
Snapshot hash de fuente y delivered_hash son distintos cuando hay redacción/truncamiento.
Reanudar valida el hash del contenido efectivamente recibido, no reconstruye memoria viva.
Paquete contiene referencias, no credenciales; importación falla ante incompatibilidad,
artefactos ausentes o intento previo sin fencing/cese confirmado.

## Migración y compatibilidad

- Añadir tablas/columnas y ledger; verificar antes de activar la ruta nueva.
- Registros antiguos permanecen legibles con provenance=legacy y evidencia ausente explícita.
- No interpretar acknowledged de NativeChildModel antiguo como acuse de tarea: actualmente
  puede representar que existe el terminal. El adaptador conserva significado y versión.
- Copiar referencias antiguas de forma idempotente; sin doble escritura autoritativa.
- La columna error_kind existente se reutiliza; no crear otra migración de la misma columna.
- Esquemas de paquetes y eventos tienen versión mayor; lectores rechazan mayores desconocidas.
- La copia de seguridad incluye DB y archivos; manifest de backup enumera hashes y corte
  de eventos. Restore aislado verifica integridad y revocaciones antes de habilitar ejecución.
