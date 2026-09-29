# Contrato de ejecución v1 (propuesto)

## Fronteras y responsables

| Frontera | Dueño | Entrada | Salida y condición |
|---|---|---|---|
| API/MCP/CLI a admisión | work_authority | principal autenticado, job, operación, clave idempotente | grant/capacidades comprobados o rechazo sin recursos |
| Admisión a persistencia | work_repository | contrato efectivo, snapshot requerido, recursos | item/attempt/reservas y evento confirmados juntos |
| Persistencia a backend | work_service | attempt_id y generation vigentes | envío registrado; fallo incierto conserva intento |
| Proveedor a resultado | work_service | evidencia de recepción/resultado | resultado validado durable o reconcile |
| Dominio a clientes | work_projection | estado durable y secuencia | DTO común, sin deducir éxito de idle |
| Cancelación a cleanup | work_decisions | decisión autorizada y revisión | no nuevos efectos; limpieza confirmada o pendiente visible |

## Operaciones propuestas del servicio

Las firmas identifican interfaces de diseño; DTO de dominio se define en models/work.py.
Los endpoints finales se añaden con compatibilidad a api/main.py, sin cambiar rutas actuales.

```python
create_job(principal, request) -> JobView
admit_work(principal, job_id, request, idempotency_key) -> WorkView
get_work(principal, work_item_id) -> WorkView
renew_attempt(principal, attempt_id, generation, expected_revision) -> AttemptView
record_delivery(attempt_id, generation, evidence) -> AttemptView
settle_attempt(attempt_id, generation, result) -> WorkView
join_children(principal, work_item_id, after_revision, timeout_seconds) -> JoinView
decide_work(principal, work_item_id, decision, idempotency_key) -> DecisionView
read_events(principal, job_id, after_sequence, limit) -> EventPage
```

`principal` siempre procede de autenticación/enlace servidor, nunca de caller_id del body.
Un caller_id sólo selecciona referencia, cuya pertenencia se comprueba. Modo local
sin auth representa identidad del operador local y no se permite como remoto compartido.

## Autoridad

- Job fija allowlist, grant y presupuesto. Descendientes heredan restricciones por intersección.
- grant_revision y attempt_generation se comprueban en la transacción de admisión y antes
  de cada efecto CAO. Credenciales no viajan en metadata ni eventos.
- Revocación bloquea efectos nuevos, solicita cancelación y declara efectos ya iniciados;
  no promete deshacer efectos externos ni detener un CLI que carece de mecanismo de cese.
- Preflight declara soporte de modelo, esfuerzo, herramientas, estado, memoria, continuidad
  y enforcement por backend. Binario presente no acredita autenticación o capacidad.
- La ruta de procesos debe imponer límites de ruta, comandos y red declarados. Si no puede,
  rechaza el grant restringido; una advertencia o prompt no satisfacen este contrato.
- Preservar diferencia legacy entre allowed_tools=None y []; ninguna significa elevar grant.

## Atomicidad e incertidumbre

Persistir intención antes del envío. Estado, reserva y evento se confirman en una única
transacción; publicar al bus sucede después y admite repetición deduplicable.
No mantener locks de DB durante espera de modelo. Si envío pudo ocurrir sin confirmación,
registrar reconcile; el consumidor no infiere que debe reenviar.

El resultado se escribe primero a almacenamiento inmutable, luego se acepta con CAS y
evento. Si falla el segundo paso, el artefacto queda huérfano recuperable; si falla el
primero, no existe éxito. Sweeper sólo elimina huérfanos fuera de retención y sin refs.

No basta fencing de DB para detener comandos ya ejecutándose fuera de ella. Reasignación
de ruta o continuación exige confirmar cese o aislamiento que impida efectos compartidos.

## Semántica HTTP y errores

Propuesta de rutas aditivas: POST /jobs; POST /jobs/{job_id}/work-items;
GET /work-items/{id}; POST /work-items/{id}/decisions; POST /work-items/{id}/join;
POST /work-attempts/{id}/renew; GET /jobs/{id}/events.

- 401: identidad requerida ausente o inválida.
- 403: principal válido sin autoridad o proveedor fuera de allowlist.
- 404: recurso inexistente o no visible en el ámbito autorizado.
- 409: revisión/generación/clave idempotente incompatible o ownership en conflicto.
- 422: contrato inválido, versión incompatible o capacidad requerida no soportada.
- 429: presupuesto/concurrencia agotados; Retry-After sólo cuando existe estimación válida.
- 503: store o autoridad inaccesible; nunca equivale a permiso concedido o recurso ausente.
- 202: cancelación/conciliación aceptada pendiente; no equivale a tarea terminada.

Envelope: code, message redactado, job_id/work_item_id/attempt_id visibles, revision,
retryable y required_action. retryable describe repetir consulta/petición idempotente,
no repetir automáticamente ejecución externa incierta.

## Eventos y proyección

EventPage incluye schema_version, events, next_cursor, high_water y gaps explícitos.
Orden total por job, no orden global inventado entre jobs. Cursor avanza por secuencia
confirmada. Retención informa intervalo borrado; payload no incluye prompt/transcript.
DTO incluye job_state, work_state, attempt_state, turn_state, process_state, revision,
result_ref, cleanup_state y required_action. Cliente ignora revisiones antiguas; en empate
consulta autoridad. Las interfaces usan textos/colores generados desde el mismo catálogo.

## Escenarios obligatorios

Crear o revocar `POST /work-items/{id}/decisions` y
`POST /work-decisions/{id}/revoke` requiere permiso de escritura. En una reejecución
admitida explícitamente por la política, el gate vincula la autorización al intento
observado; el writer devuelve conflicto si ese intento cambió antes de registrar el
nuevo contrato. Las rutas y el writer no infieren autorización desde el texto explicativo.

1. Diez admissions simultáneas con dos plazas: nunca tres reservas activas.
2. Petición repetida idéntica devuelve mismo item; distinto hash devuelve conflicto.
3. Caída en cada frontera intención/envío/acuse/resultado/evento y recuperación de DB real.
4. Resultado tardío tras cancelación o generación nueva no cambia ganador.
5. Revocación entre preflight y envío bloquea el efecto pendiente.
6. Eliminación de terminal conserva resultado, hijos, intentos y cleanup pendiente.
7. Join con hijo muerto/lease expirado devuelve reconcile, no espera indefinida.
8. Checkout con cambios sin commit no se elimina hasta conservar evidencia y aplicar política.
9. Evento duplicado y reconexión con retención no duplican estado ni ocultan huecos.
10. Auth remota y caller_id falsificado no amplían permisos; backend sin enforcement se rechaza.
