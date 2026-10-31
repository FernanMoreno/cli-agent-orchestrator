# Composición T018: selección, INPUT y dominios

PASS en este componente local; T018 y la aceptación M1/M2/M3 siguen abiertos.

- Cambios: Work selector, Bridge, fence nativo, monitor y factoría operativa. Vecinos revisados: repositorios PG/ORM, lock advisory, turnos, terminal_service, journal y ACK, lifecycle API/Bridge y auditpool.
- Ownership: control SQL1 antes de entrega2; callbacks físicos unidos y scopes reales. Token INPUT exacto de uso único; reserva distinta del efecto. El cursor/lock/conexión no se retira por cancelar el await.
- Fallos reproducidos: SQL previo sin reserva; copia de capability; denegación envuelta como error interno; Event prematuro antes del advisory; pool4 usado como techo global. Se conservan los RED y40PASS/1FAIL intermedios.
- Contratos: sin nuevo esquema público ni migración. Errors tipados y replay conservan resultado durable; orden por terminal, auth/CAS de recursos, frescura, budget/ceiling y cierre incierto siguen. El sampler mantiene PG pressure incluso sin dominio local; supplier ausente no equivale a supplier desconocido.
- Integración: Work90PASS, Bridge42PASS, monitor95PASS con PostgreSQL real;3negativos de monitor reforzados y repetidos, incluidos en95. Pruebas HTTP/Bridge reales sirven de contrato local; Pact externo no aplica a este cambio interno sin nuevo contrato de wire. Los5/20/40 presupuestos son simulados y no aceptación física de agentes.
- Arquitectura: project-composition-check caos,439archivos/1951dependencias/5KEPT/0BROKEN. Black/isort9 y mypy5PASS (scope habitual de funciones no tipadas).
- PostgreSQL: mismo contenedor y dueño vivo verificados;0clientes adicionales/0bases temporales/0roles. No se detuvo infraestructura ni se modificaron credenciales.
- Graphify:34anclas/151aristas para Bridge y8/18para fence; monitor/runtime nuevos no figuran en el grafo. Fuente contrastada; no se reconstruye ni sobrescribe el índice ajeno.
- Límites: control/bootstrap y otras entradas SQL, cierres, diagnóstico y aceptación física siguen pendientes; muestreo PG no es reserva compartidaM2, ni placementM3 ni SDK/UI.

[Recibos y bytes probados](validation-t018-selection-input-domains.json).
