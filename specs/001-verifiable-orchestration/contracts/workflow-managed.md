# Contrato de steps Work gestionados en workflows

**Estado:** T020 cerrado para aceptación local con Docker; contrato implementado.
Este cierre no habilita un backend global, un proveedor externo ni un ingreso
público nuevo.

## Autoridad y selección por step

- `POST /workflows/runs` recibe la identidad autenticada fuera del body. Para
  cada `(principal, workflow_id, workflow_revision, step_id)`, el servidor
  resuelve una provisión versionada. El selector y las refs de autoridad son
  configuración server-owned: el YAML, el script, los inputs, `run_id` y
  `caller_id` no los eligen ni los crean.
- `workflow_journal.get_run(run_id)` aporta el registro durable para resume y
  script: `workflow_name`, `tier`, `generation` y `spec_snapshot`. La revisión
  YAML es SHA-256 de los bytes de `spec_snapshot`; la revisión script es
  SHA-256 del `source` UTF-8 guardado en su snapshot. El cache `ScriptRunRecord`
  no es autoridad ni requisito para recuperar un run.
- La provisión fija refs/revisiones de sujeto y autorización `workflow`, job y
  grant, contrato efectivo de `agent_step`, snapshot, backend/adapter y lease.
  Fija también refs/revisiones preprovisionadas de sujeto y autorización
  `receiver`, con acciones separadas `task_received` y `task_result`. Si una
  ref, grant, contrato, snapshot o backend falta, fue revocado o cambió, el step
  falla cerrado antes de Work dispatch.
- Reutilizar `WorkProvisioning`, `WorkWorkflowOrigins`, `WorkAdmission`,
  `WorkContracts`, `WorkOrigins` y `WorkService`. `WorkWorkflowOrigins` obtiene
  refs de la provisión resuelta y vuelve a validarlas; no acepta refs de
  autoridad aportadas por el llamador. El mismo camino sirve a YAML y script.
  Un step sin provisión gestionada conserva su camino legacy y jamás se
  reclasifica por su fila de journal, terminal, nombre o telemetría.

## Binding durable

Migración aditiva Work **v39** (la versión base actual es v38), sin backfill:

- Guardar una provisión inmutable por revisión de mapping server-owned y un
  binding inmutable por intento de step en `work_workflow_step_provisions` y
  `work_workflow_step_bindings`. Añadir FKs, unicidad, checksums y triggers de
  no update/delete siguiendo los schemas Work existentes.
- La clave única de binding es `(tier, run_id, run_generation, step_id,
  workflow_step_attempt)`. El contenido congela `workflow_id/revision` y hash
  del spec, provision ID/revision/fingerprint, sujeto/autorización/grant refs
  y revisiones, contrato/hash, snapshot/hash, delivery ID/hash, `work_item_id`,
  `work_attempt_id/generation`, y refs/revisiones del sujeto y autorización
  `receiver`. Un binding nunca se edita para apuntar a otro Work, entrega,
  receptor o resultado.
- `WorkWorkflowOrigins.resolve_step_admitter(principal, workflow_id, spec_hash,
  step_id)` resuelve la provisión y devuelve callback server-owned. Para
  recuperar, `recover_step(principal, workflow_id, spec_hash, tier, run_id,
  run_generation, step_id, workflow_step_attempt)` lee y revalida el mismo
  binding; nunca crea otro Work item. `read_step_binding(tier, run_id,
  run_generation, step_id, workflow_step_attempt)` es lectura exacta para el
  projector y devuelve `None` si no existe.
- Crear el binding en la misma transacción Work que admite el item/attempt y
  fija su idempotency key. La fila journal `work_pending` se escribe antes del
  dispatch externo; si el proceso cae antes de guardar binding, recovery vuelve
  a resolver la misma provisión/revisión y repite la misma admisión idempotente.
  Sin binding completo y entrega durable, el dispatcher no envía.
- `step_attempt` avanza sólo por una transición durable de retry autorizada por
  el workflow y cercada por el intento observado. Reinicio, replay, timeout,
  pérdida de proxy, ACK ausente, receipt tardío o fallo del projector no
  incrementan `step_attempt`, Work item ni Work generation. Un nuevo Work item
  o generation requiere ese nuevo intento explícito. No se reemite una
  credencial para un intento cuyo envío sea incierto.

## Receipt y resultado

- El receptor usa el proxy MCP privado por intento y credenciales separadas:
  credencial de intento Work y credencial de receptor emitida por el servidor.
  El secreto bruto sólo viaja por el descriptor privado heredado del runtime;
  nunca entra en payload, prompt, environment, terminal, eventos o logs.
- `cao.work.task_received` reutiliza `TaskReceivedReceiptV1`. El owner valida
  receiver subject/auth/revision, acción vigente, Work binding, intento,
  generation, delivery ID/hash y aceptación durable exactos. Receipt y Work ACK
  persisten atómicamente en `WorkRepository`; sólo `WorkService` puede
  reconocer el ACK.
- Añadir al mismo proxy privado `cao.work.submit_result`, con un único payload
  de datos `WorkflowStepResultV1`:

  ```json
  {"schema_version":1,"status":"completed","output":{}}
  ```

  `output` es un objeto JSON, que puede estar vacío; el sobre no admite claves
  adicionales, claves JSON duplicadas, números no finitos ni más de 1 MiB
  codificado en UTF-8. El worker no provee identidad, validador ni estado
  de aceptación. El servidor deriva el Work item/attempt/generación de las
  credenciales y exige que el receipt y binding vigentes coincidan con la
  entrega congelada. Un duplicado de los mismos bytes es idempotente; bytes
  distintos tras un resultado aceptado se rechazan.
- Antes de finalizar Work, el owner valida `output` contra el `output_schema`
  de la revisión congelada usando Draft 2020-12 cuando exista (sin schema, el
  valor JSON es válido). `WorkService.settle_attempt` publica el artefacto
  durable, comprueba su lectura/hash y hace CAS a `finished`; una salida de
  terminal o la terminación del proceso no sustituye este paso. Un Work que
  termina en error sólo se proyecta desde su estado Work durable; `reconcile`
  sigue pendiente/bloqueado y no se transforma en éxito.
- `WorkflowStepResultV1` sólo expresa completion. Errores siguen el ciclo de
  vida Work durable y la política de fallo/retry del workflow. Resultado JSON
  inválido no finaliza Work ni permite consumir un retry implícito.
- Un fallo de proceso posterior al dispatch sólo es durable cuando el adaptador
  server-owned recibe un `DockerWorkExecution` del intento exacto con código de
  salida no cero, proceso detenido, contenedor eliminado e imagen del intento
  eliminada. `WorkService` compara intento/generación actuales y rechaza el
  fallo si existe un resultado registrado o aceptado; el CAS de Work escribe
  juntos `failed`, `cleanup_state=complete` y metadata de código/limpieza, sin
  guardar stdout/stderr. Excepción, cancelación o limpieza incierta conserva
  `reconcile`; salida cero sin resultado conserva el estado pendiente.

## Proyección y recovery

- Añadir un projector dueño de workflow que recupere bindings no proyectados al
  arrancar y antes de cualquier resume. Relee desde `WorkService` el Work exacto
  y el artefacto aceptado, revalida delivery/hash, contrato, snapshot, envelope
  y `output_schema`, y nunca toma el resultado de eventos ni del terminal.
- La finalización de journal y el marcador de proyección se escriben juntos en
  una transacción SQLite con la fila append-only `work_workflow_step_projections`
  y CAS de `workflow_run.state/generation`, tier,
  `workflow_run_step.state=work_pending`, `step_attempt`, binding,
  `work_attempt_id/generation`, resultado y hash. El journal guarda la ref/hash
  de Work resultado como marcador durable de proyección. El CAS cambia
  exactamente una fila; un resultado idéntico ya proyectado es idempotente y un
  conflicto no sobrescribe estado. El contenido sigue en el almacén durable de
  artefactos; el journal guarda referencia/hash y salida serializada validada.
- Si el projector cae antes del commit, recovery repite la lectura y el CAS; si
  cae después, reconoce el mismo resultado proyectado. Un Work incierto,
  revocado, binding ausente, schema drift o CAS perdido deja el workflow
  pendiente/bloqueado y requiere conciliación explícita. Un step gestionado
  pendiente no avanza run generation al reanudar. Primero se proyecta el
  resultado; sólo después puede continuar el workflow.
- YAML suministra el callback gestionado por step a `workflow_service.start_run`
  y su recuperación. Script resuelve la misma provisión desde el run record al
  entrar por `run_step`; la ruta legacy de terminal permanece cerrada mientras
  la fila diga `work_pending`. El API de inicio inyecta `Principal`; `run_id`,
  step ID y variables de entorno sólo localizan el run y nunca son autoridad.

## Aceptación local

- Incluir un worker determinista Docker de test, separado del worker T019
  `launch`, para `agent_step`. Sólo se construye/activa mediante un flag de
  aceptación explícito; imagen y digest quedan fijados y `WORK_BACKENDS` sigue
  vacío por defecto. El worker no usa un proveedor externo y habla con el proxy
  MCP privado real: receipt autenticado, resultado JSON válido, y salida por
  terminal sin resultado deben producir respectivamente ACK+resultado durable y
  workflow pendiente. Docker mantiene el aislamiento T019; no se monta el socket
  daemon ni se exponen secretos por env/prompt/log.
- Probar YAML y script con reinicio del API/owner entre dispatch, receipt,
  resultado y projector; duplicado idéntico/contradictorio; receipt o resultado
  de otra entrega/generación; revocación; ausencia/drift de provisión; resultado
  inválido; CAS concurrente; retry explícito y no retry por timeout/restart; y
  rechazo de inferencia desde terminal, journal legacy o `caller_id`.
- El worker `T122_FAIL` debe demostrar para YAML y script que un exit 23 con
  limpieza verificada se proyecta como fallo, que reiniciar no crea otro Work ni
  vuelve a ejecutar el intento y que sólo el endpoint admin cercado admite el
  siguiente binding. Repetir el mismo retry devuelve la misma identidad; un
  resultado aceptado gana frente a un exit tardío y una limpieza no verificada
  nunca se convierte en fallo retryable.
