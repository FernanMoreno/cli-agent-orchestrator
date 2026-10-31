# T010 — composición de control y ejecución Bridge

Resultado: PASS de implementación T010. M1/T019 no aceptado.

## Superficie y contratos

AdmissionParticipant permite reutilizar el controlador concreto InboxAdmissionRuntime para API y Bridge, conservando guard de tipo, DTOs estrictos, JWT verificado cao:admin, CAS y propiedad de SQL/control. El runtime/router ya no importan ni construyen el singleton Inbox ni el API principal. La factoría scoped solo corre tras opt-in, ruta y autenticación válidos. _amain liga el mismo ledger/reloj antes del lector, con identidad de proceso local y clave lógica por perfil/runtime_id; no copia credenciales.

BridgeAdmissionParticipant conserva el testigo de retirada en el Bridge, no en el adaptador recreable. Rechaza reemplazar lectores, handlers, cierres de socket o leases vivos. El runtime sigue sin conceder autoridad por un protocolo estructural. La reserva INPUT mantiene las guardas de terminal/encarnación/epoch, los recibos y la exclusión Work, según los checkpoints anteriores.

AdmissionControlServer usa solo el router compartido, loopback127.0.0.1 y puerto efímero; no central lifespan, docs u OpenAPI. Uvicorn no captura las señales de Bridge. Posee Task, readiness y listeners durante inicio/cancelación; el cierre une también un listener creado después de pedir parada. La muerte inesperada del Task cierra admisión antes del supervisor. URL es propiedad/log del operador, no contrato de texto para modelos; ready file sigue PID+newline.

El cierre protegido cierra admisión, une lector/handlers/cierres de socket y trabajo INPUT físico, retira actores de fondo, luego HTTP/control/monitor. Cancelar la espera no acredita retiro. El saludo pendiente tiene socket propio desde antes del Hello y no cuenta como canal listo.

## Fallos reproducidos y correcciones

La revisión independiente encontró que stop no podía cerrar el socket anterior al Hello: el lector quedaba esperando y retenía monitor/HTTP. Un peer WebSocket local real que no responde reprodujo el bloqueo; la propiedad del socket pendiente y su cierre/drenaje lo corrigen. Un test adicional reprodujo reemplazo de controlador por adaptador nuevo: el testigo de retiro ahora pertenece a Bridge. El primer HTTP nativo usaba un JWT lector sintético sin sub; se corrigió el dato, conservando401 sin identidad,403 sin permisos y200 del admin. La anotación del listado Uvicorn se ajustó al tipo exacto heredado sin desactivar tipos. Se conservan todos los RED/fallos.

## Evidencia y límites

163 PASS, 0 fallos/errores/omisiones, 8 avisos, 197.43s; fuente y soporte intactos. Incluye todo test/runtime_channel, autorización/lifecycle API, dispatcher Work, controlador, identidades y dos escenarios HTTP/JWKS/PostgreSQL reales. Capacidades/backend controlados donde corresponde, sin modelo/CLI. Tipos cuatro módulos y formato siete archivos correctos. Composición436archivos/1934dependencias/5KEPT/0BROKEN. Mismo contenedor/dueño PostgreSQL, cierre0clientes/0bases/0roles de prueba. Revisión Codex en tres rondas; hallazgo material corregido y revalidado. Diff revisado por el lead. Graphify inicial parcial contrastado contra fuente; refresh diferido por trabajo generado ajeno.

El esquema ResourceEnvelope y credenciales owner/writer separadas requieren aprovisionamiento explícito; runtime startup no crea autoridad. Claims incompletos mantienen capacidad cero incluso tras registro. No se acepta M1/M2/M3, SDK/UI ni los gates globales. La lección durable se guardó mediante transacción Obsidian save-20261031-bridge-admission-lifecycle, lint estricto correcto. Véase [recibo](validation-t010-control.json).
