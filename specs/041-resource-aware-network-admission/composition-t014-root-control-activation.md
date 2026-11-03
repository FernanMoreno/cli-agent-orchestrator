# T014.R.H2.3 — Activación firmada previa a SQL

Veredicto: **PASS WITH RISKS**, componente opt-in; no aceptación global.

El emisor ya implementado reutiliza un canal propietario de migración financiado, administrador verificado, journal y créditos previamente provisionados. Compara el envelope protegido antes y después del commit confirmado; solo después obtiene la clave de despliegue. Firma por separado el cómputo y el certificado SQL v1. No provisiona autoridad, no crea conexiones adicionales ni modifica SQL5/resource7.

## Composición revisada

- Wire/trust: JSON canónico estricto, límite 32 KiB, clave externa y dominio de firma independiente; sin confianza en claves del paquete o del peer. Claims separados de firma y consumo por proceso, identidad kernel/PID/binding/destino y rechazo de replay/copia/fork. Un fallo o commit incierto no habilita refund/reintento.
- Bootstrap/ledger: dos muestras reales Linux previas a SQL verifican RAM/CPU; storage permanece desconocido y no se fabrica CapacityObservation completa. La evidencia opaca reserva el arranque exacto y se vuelve a comprobar identidad/target tras el await. No habilita entregas ordinarias, shared ni nuevos slots SQL.
- Actor/monitor/PG: primer canal en el hilo original, reserva retenida, comparación exacta del envelope al entrar y durante el primer ciclo antes de ready/publicación. La barrera nativa de revisión está fuera de transacciones/locks. Tras validar ese ciclo, revisiones administrativas siguen la política dinámica heredada y la recuperación normal.
- SDK/proceso: prepare_original_control_owner y python -m cli_agent_orchestrator.runtime_channel.root_control consumen autoridad externa. Trust/policy privados y explícitos, comandos tipados refresh/stop, EOF y cierre positivo conservador. El supervisor de despliegue y la API central todavía no llaman este recorrido.
- Vecinos: política y samplers, bootstrap/repositorio, SQL origin/genesis, FundedControlSession, reserva/hilo/buzón original, runtime/inbox, diagnóstico HTTP/Bridge v1/v2. ResourceHeadroom conserva su import público por reexport desde servicios; el modelo puro evita dependencia models→services. No cambia wire HTTP/MCP ni requiere un nuevo Pact; el contrato de paquete y consumidores locales se prueba directamente.

## Evidencia ejecutada

**503 PASS**, cero fallos/errores/omisiones, 10 advertencias, 30 módulos y 341.349 s. Conserva exactamente 365 IDs relevantes de la unión aceptada de 1.705, conserva 91 casos adicionales de política/recursos con fuente idéntica a HEAD y colección independiente, y añade 47: 39 unitarios y 8 nativos. Los 111 unittest locales y los ocho nativos iniciales se solapan; no se suman. No se repitió la unión global completa.

Los ocho nativos incluyen API presente y siete modos: salud, revocación, revisión previa, revisión entre readback y monitor, clave errónea sin sampler/TCP, replay sin segunda muestra/conexión y CLI real con un único backend observado. El sampler de arranque es el Linux real; las pruebas heredadas de carga del buzón conservan cómputo sintético. La afirmación de jerarquía del envelope es explícita del operador de la fixture, no una prueba física de capacidad ni 25 modelos.

Los 16 Python/12 fuentes coinciden exactamente con el archivo de ejecución de 17 miembros y el controlador archivado; soporte/fuente/descriptor preservados. Black/isort de esos 16 y mypy frío de 463 fuentes pasan, sin cambiar configuración ni añadir supresiones. Cobertura seleccionada del paquete: 24.98%; no equivale a cobertura global. Arquitectura: cinco contratos conservados, cero rotos.

PostgreSQL 18.6 reutilizado con la misma identidad/perfil/keeper, mínimo de memoria 6.144 MiB intacto y 11158 MiB disponibles al admitir. Después del cierre: cero otros clientes, bases y roles de prueba. No se detuvo el keeper. Doce archivos Graphify ajenos preservados. Graphify privado parcial contrastado con fuente, revisión ECC Luna Max/Claude read sin hallazgos accionables restantes y conocimiento durable guardado mediante transacción Obsidian con lint estricto cero.

## Límites y siguiente corte

Quedan el supervisor de despliegue realmente financiado, transporte privado del paquete y wiring preSQL de la API central. init_db legado ya abrió SQL y no puede convertirse retroactivamente en root en ese PID. El permiso SQL y el cargo de cómputo son autoridades distintas. Shared, custodia, M1/M2/M3, integración SDK/UI restante y los diez gates originales siguen abiertos. No se introduce un máximo fijo de agentes o entregas; esta composición no demuestra escalado multinodo ni concurrencia de modelos.

[Validación](validation-t014-root-control-activation.json).
