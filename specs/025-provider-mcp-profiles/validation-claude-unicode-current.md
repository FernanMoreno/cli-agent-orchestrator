# Claude Unicode: diagnóstico nativo actual

2026-10-26. **PASS acotado** en `35a728c1367579db4f9bbebb665daae365d7be77`: un turno Unicode real, HTTP200 en21,204s, texto esperado conservado, recibo nativo `succeeded` con settlement y limpieza, join confirmado. Un caso pytest,0fallos/errores/omisiones. [Recibo estructurado](validation-claude-unicode-current.json).

Claude Code2.1.303, ejecutable fijado por SHA; modelo solicitado `claude-sonnet-4-6`, sin atestación independiente del modelo servido.1508archivos de fuente, intérprete, launcher y helpers fijados. Fuente/binario intactos antes/después. El texto Unicode esperado se comprueba como subcadena completa de `last_message`; no se exige igualdad de toda la respuesta ni JSON final del modelo. El recibo nativo conserva su contrato propio.

Se conservaron180s por turno, redeliveryFalse y memoria>=6144MiB antes del propietario, del padre y del turno.68,595s corresponden al proceso pytest y su limpieza después de preparar PostgreSQL; no incluyen la reconstrucción/revisión del lanzador ni el aprovisionamiento anterior. La petición se validó con el JSON Schema OpenAPI de la API real antes de crear el padre Claude, y un control negativo rechazó la forma incorrecta del primer intento. No se ejecutaron escenarios de cuota/continuación, mensajes, cancelación o matriz ampliadaT032.

## Cierre y conservación

La sesión padre fue borrada. API, terminales nativos, HOME y raíz tmux propios cerrados/ausentes. Dos procesos de sesión del sistema con entorno ilegible se excluyeron solo después de probar que nacieron antes del observador; ningún proceso nuevo de identidad incierta se acepta como ausente. Cero prestatarios SQL y cero bases privadas antes de cerrar PostgreSQL. Contenedor/red exactos ausentes y comandos destructivos ligados únicamente a sus IDs. El propietario acabó EXIT0; sin SIGKILL de API en R2. Los dos registros del operador seleccionados para la copia mantuvieron bytes y metadatos; las copias diagnósticas se eliminaron. El launcher ejecutado quedó archivado y su descriptor fue cerrado.

## Primer intento conservado

R1 falló con HTTP422 antes de lanzar el turno diagnóstico: el lanzador escrito por Codex usó `agent_profile/message`, mientras el productor exige `agent/prompt`. Además, un reemplazo global posterior a la revisión rompió la aridad de `write`, y el lector de ausencia trataba procesos de sesión anteriores como posibles trabajadores nuevos. Son defectos del harness, no de Claude. Se conservaron los archivos y resultados originales. La recuperación verificó ausencia API/native/tmux, cero prestatarios/bases y retiró solo el contenedor/red propios; después detuvo forzosamente al propietario inactivo de ese intento, cuyo bucle no ofrecía salida de recuperación. Esa recuperación es exclusivamente de cierre y mantiene R1 fallido. No se transfiere a R2 como aceptación ni se afirma un cierre limpio del primer propietario.

R2 corrigió el contrato contra OpenAPI real, el alcance por nacimiento de proceso y la llamada del escritor. Antes de lanzarlo pasaron siete bindings de firma, cinco controles de alcance y recolección de un caso. Claude revisó la base conGO después de corregir dos hallazgos; la tercera revisión R2 agotó su tiempo y no se cuenta comoGO. Luna revisó el diff final R2 y dioGO acotado.

## Límites

Este resultado actualiza el baseline deT031 con fuente actual y mayor inventario de hashes. No reproduce ni explica el504 histórico: T006/CL-01–04 continúan abiertos para ese diagnóstico. Tampoco acepta catálogo/autorización MCP del CLI nativo, Q/A/ACK/reinicio, mensajería externa `cao.work.message`, concurrencia ni gates globales. T032 ampliada sigue aplazada.

Revisión final de evidencia Luna: GO acotado; se registran explícitamente EXIT0 del propietario y SHA del intérprete. Composición actual5contratos conservados/0rotos. No hay cambio de código productivo ni contrato de servicio: el diagnóstico usa HTTP/recibos existentes y valida su entrada con OpenAPI. No requiere Pact nuevo ni regenerar Graphify; se preservan sus archivos ajenos. Los controles privados, el caso real y las comprobaciones de fuente/cierre cubren este resultado, sin afirmar cobertura global.
