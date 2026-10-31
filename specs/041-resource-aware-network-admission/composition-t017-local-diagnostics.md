# Composición T017: diagnóstico local autorizado

PASS para el contrato local v1; no acepta toda US6 distribuida ni M1/M2/M3.

- Subsistemas: DTOs de control, ledger, monitor y cachés, proyector puro, runtime y router compartido API/Bridge. Vecinos: auth JWT, lifecycle/control CAS, PG sampler, cierres físicos, Inbox y Work.
- Contrato: lectura sin SQL/probes/loader/archivo de política ni import tardío del proyector; autorización antes del runtime, errores fijos y no-store. Cantidades/counters no se presentan como agentes, demanda ni nodos.
- Coherencia: un solo valor de incertidumbre externa para el snapshot completo; objetos inmutables bajo lifecycle→owner sin esperar refresh_guard ni recorrer leases. Presupuesto null sin base vigente, cero conocido conserva su significado.
- Procedencia: generación no prueba causalidad tras un cambio solo de política PG. Configuración instalada tras configure exitoso; basis_current exige la observación exacta aceptada. Probes PG/recursos tienen edad/generación propias y no conceden autoridad.
- Fallos de borrador conservados: ruta ausente6FAIL, fronteras4FAIL y configuración en curso2FAIL; corregidos antes de aceptación. Núcleo22PASS y final211PASS sin errores ni omisiones; ejecuciones solapadas, no se suman como casos distintos.
- Dependencias reales: HTTP/JWKS/PostgreSQL y listenerBridge, auth y CAS/ACL/revocación sin elevar cobertura de jerarquía. Contrato HTTP validado directamente; no existe consumidor externo nuevo que requiera Pact independiente.
- Arquitectura:440archivos/1956dependencias/5KEPT/0BROKEN. Black/isort10 y mypy6PASS (alcance habitual de funciones no tipadas). MismoPG y dueño vivo,0clientes/0bases temporales/0roles; contenedor preservado.
- Graphify: nodos recientes de admission no figuran en el grafo; fuente/callers contrastados directamente. Se preserva el índice ajeno y no se fuerza reconstrucción.
- Límites: T018 control/otras entradas SQL y cierres siguen pendientes; M1 físico, M2/M3, SDK/UI y gates nativos/globales separados.

[Recibos y bytes probados](validation-t017-local-diagnostics.json).
